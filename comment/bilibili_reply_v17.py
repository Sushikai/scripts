#!/usr/bin/env python3
"""
B站评论回复 - MiniMax 大模型智能版 v18
精确回复到对方下方 + M2.7 稳定调用
支持 shared_state 任务派发模式
"""
import fcntl, json, os, random, signal, sys, time
from datetime import datetime
from io import StringIO
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 导入公共工具模块
sys.path.insert(0, str(Path(__file__).parent))
from bilibili_utils import (
    smart_truncate as _smart_truncate, make_session, atomic_write, LockFile,
    CooldownManager, VideoTitleCache, ConversationCache
)

# ==================== 多账号隔离支持 ====================
_INSTANCE = os.environ.get('BILIBILI_INSTANCE', '')
if _INSTANCE:
    _BASE = Path.home() / ".hermes" / "instances" / _INSTANCE
    _INSTANCE_SECRETS = _BASE / "secrets"
    _INSTANCE_WORK = _BASE / "work"
    _INSTANCE_WORK.mkdir(parents=True, exist_ok=True)
else:
    _INSTANCE_SECRETS = None
    _INSTANCE_WORK = None

# ==================== Agent B shared_state 集成 ====================
AGENT_ID = "B"
if _INSTANCE_WORK:
    STATE_FILE = _INSTANCE_WORK / "hermes_tasks.json"
    LOCK_FILE_STATE = _INSTANCE_WORK / "hermes_tasks.lock"
else:
    STATE_FILE = Path("/tmp/hermes_tasks.json")
    LOCK_FILE_STATE = Path("/tmp/hermes_tasks.lock")

def _acquire_state_lock():
    lock_fd = open(LOCK_FILE_STATE, 'w')
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
    return lock_fd

def _release_state_lock(lock_fd):
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    lock_fd.close()

def _read_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {"tasks": {}, "agents": {}}
    return {"tasks": {}, "agents": {}}

def _write_state(state):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STATE_FILE)

def claim_task():
    lock = _acquire_state_lock()
    try:
        state = _read_state()
        for task_id, task in state["tasks"].items():
            if task["agent"] == AGENT_ID and task["status"] == "pending":
                task["status"] = "running"
                task["started_at"] = time.strftime('%Y-%m-%dT%H:%M:%S')
                _write_state(state)
                return task
        return None
    finally:
        _release_state_lock(lock)

def update_task(task_id, status=None, output=None, error=None):
    lock = _acquire_state_lock()
    try:
        state = _read_state()
        if task_id not in state["tasks"]:
            return
        task = state["tasks"][task_id]
        if status:
            task["status"] = status
        if output is not None:
            task["output"] = output
        if error:
            task["error"] = error
        if status in ("done", "error"):
            task["finished_at"] = time.strftime('%Y-%m-%dT%H:%M:%S')
        _write_state(state)
    finally:
        _release_state_lock(lock)

def check_and_dispatch():
    task = claim_task()
    if task:
        log(f"🎯 收到任务: {task['task_id']} | 类型: {task['type']} | 描述: {task['description']}")
        return task
    return None

# ==================== Cookie 加载 ====================
import http.cookies
if _INSTANCE_SECRETS:
    _json = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")
    COOKIES_FILE = _json if _json.exists() else None
else:
    COOKIES_FILE = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")

def load_cookies():
    json_file = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text(encoding='utf-8'))
            if isinstance(data, dict) and 'SESSDATA' in data:
                return data
        except Exception:
            pass
    json_file2 = Path.home() / ".bilibili_cookies.json"
    if json_file2.exists():
        try:
            data = json.loads(json_file2.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return {item['name']: item['value'] for item in data if 'name' in item and 'value' in item}
        except Exception:
            pass
    return {}

COOKIES = load_cookies()
SESSDATA = COOKIES.get("SESSDATA", "")
BILI_JCT = COOKIES.get("bili_jct", "")
BUVID3 = COOKIES.get("buvid3", "")

HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com"
}

# MiniMax 配置
MINIMAX_API_KEY = "sk-cp-ZbbMkbX_A0Rmc3JvDro_uuw8L-g4vc3MnWWCMg__tPSEYB_btil94MTUq9zPncWlSKli5GDkQ7xTB4o_8niFbznFRYNaxTTMVIbVVsje92OiVT6T1rDCGeg"
MINIMAX_MODEL = "MiniMax-M2.7"
MINIMAX_BASE = "https://api.minimax.chat/v1/chat/completions"
MAX_CONTENT_LEN = 80

REPLIED_FILE = _INSTANCE_WORK / "bili_replied_real.json" if _INSTANCE_WORK else Path("/tmp/bili_replied_real.json")
LOG_FILE = _INSTANCE_WORK / "bili_reply_v17.log" if _INSTANCE_WORK else Path("/tmp/bili_reply_v17.log")
LOCK_FILE = _INSTANCE_WORK / "bili_reply_v17.lock" if _INSTANCE_WORK else Path("/tmp/bili_reply_v17.lock")

session = make_session()

# ==================== 工具函数 ====================
_real_stdout = sys.stdout

def acquire_lock():
    try:
        lfd = open(LOCK_FILE, 'w')
        fcntl.flock(lfd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lfd.write(str(os.getpid()))
        lfd.flush()
        return lfd
    except BlockingIOError:
        print("[v18] 另一个进程正在运行，退出")
        sys.exit(0)

def sig_handler(s, f):
    save_all()
    print("\n[v18] 收到信号，保存并退出", file=_real_stdout, flush=True)
    sys.exit(0)

signal.signal(signal.SIGALRM, signal.SIG_IGN)
signal.signal(signal.SIGTERM, sig_handler)
signal.signal(signal.SIGINT, sig_handler)
signal.alarm(0)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

# ==================== Ollama 本地模型（优先）====================
OLLAMA_BASE = "http://localhost:11434/v1"
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_API_KEY = "ollama"
OLLAMA_SYSTEM = "你在B站评论区跟网友聊天，就像朋友闲聊。语气轻松自然，可以自黑或开玩笑，结合上下文自然聊天。长度控制在60字以内，直接输出回复内容，不要解释。"

def call_ollama(prompt: str, system: str = "") -> str:
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system if system else OLLAMA_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.85,
            "max_tokens": 80,
            "stream": False
        }
        r = requests.post(
            OLLAMA_BASE,
            headers={"Authorization": f"Bearer {OLLAMA_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        if r.status_code == 200:
            data = r.json()
            content = (data.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
            if content:
                import re
                # 统一用同一个后处理函数
                content = _filter_thinking_content(content)
                paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
                result = paragraphs[-1] if paragraphs else content
                result = re.sub(r'^[\s]*(分析|思考|结论)[:：]\s*', '', result)
                log(f"  Ollama OK: {result[:40]}")
                return result
        else:
            log(f"  Ollama 失败: {r.status_code}")
    except Exception as e:
        log(f"  Ollama 异常: {e}")
    return None

# ==================== MiniMax 大模型（兜底）====================
def call_minimax(prompt: str, system_content: str = "") -> str:
    """优先 Ollama，失败则用 MiniMax。system_content 传入多轮对话上下文"""
    result = call_ollama(prompt)
    if result:
        return result

    log("  ⚠️ Ollama 失败，切换 MiniMax")
    if not system_content:
        system_content = "你在B站评论区跟网友聊天，就像朋友闲聊一样。注意：1）语气轻松自然，可以自黑或开玩笑 2）结合上下文和聊天历史往下聊，不要重复说过的话 3）长度控制在60字以内"

    try:
        payload = {
            "model": "MiniMax-M2.7",
            "messages": [
                {"role": "system", "name": "B站网友", "content": system_content},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.85,
            "max_tokens": 300,
            "stream": False
        }

        r = session.post(
            MINIMAX_BASE,
            headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15
        )

        log(f"  MiniMax 请求状态: {r.status_code} | 耗时 {r.elapsed.total_seconds():.2f}s")

        if r.status_code == 200:
            data = r.json()

            base_resp = data.get('base_resp', {})
            if base_resp.get('status_code') != 0:
                log(f"  ❌ MiniMax 业务错误: {base_resp.get('status_code')} - {base_resp.get('status_msg')}")
                return None

            if "choices" in data and data["choices"]:
                msg = data["choices"][0]["message"]
                finish = data["choices"][0].get("finish_reason", "")
                content = (msg.get("content") or "").strip()

                if not content:
                    log(f"  ⚠️ MiniMax 返回空内容: finish={finish}")
                    return None

                import re
                content = re.sub(r'\s+', ' ', content).strip()
                content = _filter_thinking_content(content)

                if len(content) < 4:
                    log(f"  ⚠️ 内容太短({len(content)}字)，跳过: {content[:30]}")
                    return None

                if len(content) > MAX_CONTENT_LEN:
                    content = _smart_truncate(content, MAX_CONTENT_LEN)

                return content
            elif "data" in data and isinstance(data["data"], dict) and "reply" in data["data"]:
                return data["data"]["reply"].strip()
            else:
                log(f"  ⚠️ 返回格式异常: {data.keys()}")

        else:
            log(f"  ❌ MiniMax 错误 {r.status_code}: {r.text[:900]}")

    except Exception as e:
        log(f"  ❌ MiniMax 调用异常: {type(e).__name__} - {e}")

    log("  ⚠️ 使用兜底回复（大模型调用失败）")
    return "哈哈收到！这条评论太有灵性了，继续往下聊呗 😂 你最喜欢这期哪一段？"


def _filter_thinking_content(content: str) -> str:
    """统一过滤 Ollama/MiniMax 输出中的思考过程泄漏"""
    import re
    content = re.sub(r'<think>[\s\S]*?</think>', '', content)           # 标签内思考块
    content = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', content) # xml标签思考块
    content = re.sub(r'<refLECTION>[\s\S]*?</reflection>', '', content)
    content = re.sub(r'Thinking Process:[\s\S]*', '', content)          # 行首Thinking Process
    content = re.sub(r'思考过程[:：]?[\s\S]*', '', content)             # 中文思考过程
    content = re.sub(r'推理过程[:：]?[\s\S]*', '', content)             # 推理过程
    content = re.sub(r'\*[\s\S]*?\*', '', content)                    # 残留*内容
    content = re.sub(r'\[\s\S]*?\]', '', content)                    # 残留[内容
    content = re.sub(r'#{1,3}\s[^\n]*\n', '', content)                # 行首# 标题
    content = re.sub(r'---[\s\S]*', '', content)                        # ---分隔线后内容
    # 取最后一段非空内容（防止前面残留分析文本）
    paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
    content = paragraphs[-1] if paragraphs else content.strip()

    # 最终安全扫描：检测提示词泄漏，命中则跳过发送
    leak_patterns = [
        r'用户让我以B站UP主的身份',
        r'用户让我以B站UP主',
        r'用户让我以.*身份',
        r'以B站UP主的身份',
        r'以B站UP主身份',
        r'回复粉丝评论',
        r'角色设定',
        r'系统提示词',
        r'你是B站UP主',
        r'你是B站',
        r'直接输出回复内容',
        r'长度控制在\d+字以内',
        r'就像朋友闲聊',
        r'语气轻松自然',
        r'结合上下文',
        r'请以.*身份',
        r'根据你.*的角色',
        r'结合.*上下文',
        r'结合粉丝.*聊天',
        r'请直接生成',
        r'请输出.*内容',
        r'不要解释',
        r'省略.*解释',
    ]
    for pat in leak_patterns:
        if re.search(pat, content):
            log(f"  🛡️ 内容安全拦截（提示词泄漏）：{content[:60]}")
            return None  # 返回None表示跳过发送

    return content.strip()

def generate_smart_reply(uname: str, user_comment: str, video_title: str = "", parent_comment: str = "", chat_history: list = None) -> str:
    if not user_comment.strip():
        log("  ⚠️ 空评论内容，跳过")
        return None

    if chat_history is None:
        chat_history = []

    conversation_prompt = build_conversation_prompt(uname, user_comment, chat_history, video_title, parent_comment)

    # 构建系统提示（含多轮上下文），用于 MiniMax fallback
    system_content = (
        "你在B站评论区跟粉丝聊天，就像朋友闲聊一样。注意："
        "1）语气轻松自然，可以自黑或开玩笑 "
        "2）结合上下文和聊天历史往下聊，不要重复说过的话 "
        "3）长度控制在60字以内，幽默自然"
    )
    if chat_history:
        system_content += " 近期对话：\n" + "\n".join(
            f"{'[粉丝]' if m['role']=='user' else '[你]'} {m['content']}"
            for m in chat_history[-12:]
        )

    prompt = f"""【任务】你正在B站评论区跟粉丝聊天，这是你们的多轮对话。

{conversation_prompt}

要求：
1. 直接输出评论文字，**不要**加任何分析、解释、前缀
2. 像朋友闲聊一样自然，结合上下文往下聊，不要重复说过的话
3. 40-60字以内，幽默自然
4. 如果之前聊过这个话题，要接着之前的内容继续，不要跑题或重新开头"""

    reply = call_minimax(prompt, system_content)
    if reply is None:
        log("  ⚠️ 大模型返回 None，跳过此条评论")
        return None

    if len(reply.strip()) < 5:
        log(f"  ⚠️ 内容太短或为空: '{reply}'，跳过")
        return None

    if len(reply) > 120:
        reply = _smart_truncate(reply, 120)

    # 过滤掉分析过程类内容（AI 把思考过程输出了），直接跳过不回复
    skip_patterns = [
        '让我分析', '根据上文', '首先', '其次', '总结', '综合来看', '【分析】', '【回复】',
        '这个问题', '评论上下文', '视频标题是关于', '之前有一个故事',
        '为什么会回复这种东西', '请你扮演', '你是一个', '作为你的',
        '好的，', '好的，让我', '好的我', '我来帮你', '我来分析',
        'Step ', 'Step1', 'Step2', 'First,', 'First ', 'Firstly',
        '```', '**', '## ', '---',
    ]
    for p in skip_patterns:
        if reply.startswith(p) or '**' in reply or '```' in reply or '---' in reply:
            log(f"  ⚠️ 过滤掉分析过程内容: {reply[:30]}...，跳过此条")
            return None

    return reply


# ==================== 防限速冷却控制 ====================
MIN_COMMENT_INTERVAL = 60
MAX_COMMENT_INTERVAL = 120
COOLDOWN_STORE_FILE = _INSTANCE_WORK / "bili_comment_cooldown.json" if _INSTANCE_WORK else Path("/tmp/bili_comment_cooldown.json")

_cooldown_data = {}

def _load_cooldown():
    global _cooldown_data
    try:
        if COOLDOWN_STORE_FILE.exists():
            _cooldown_data = json.loads(COOLDOWN_STORE_FILE.read_text(encoding='utf-8'))
    except Exception:
        _cooldown_data = {}

def _save_cooldown():
    try:
        atomic_write(COOLDOWN_STORE_FILE, json.dumps(_cooldown_data, ensure_ascii=False, indent=2))
    except Exception:
        pass

def wait_for_cooldown():
    _load_cooldown()
    last_ts = _cooldown_data.get('last_comment_ts', 0)
    elapsed = time.time() - last_ts
    if elapsed < MIN_COMMENT_INTERVAL:
        wait_time = MIN_COMMENT_INTERVAL - elapsed + random.uniform(1, 5)
        log(f"  ⏳ 等待冷却时间... ({wait_time:.1f}秒)")
        time.sleep(wait_time)

# ==================== 视频标题缓存（批量预取优化 N+1）====================
title_cache = VideoTitleCache(
    _INSTANCE_WORK / "video_title_cache.json" if _INSTANCE_WORK else Path("/tmp/video_title_cache.json")
)

def get_video_title(aid: str) -> str:
    return title_cache.get(aid, session, COOKIES, HEADERS)

# ==================== 获取评论的父评论内容 ====================
def get_parent_comment(aid: str, parent_rpid: str) -> str:
    if not parent_rpid or parent_rpid == '0':
        return ""
    for attempt in range(2):
        try:
            r = session.get(
                f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&ps=1&pn=1&root={parent_rpid}",
                headers=HEADERS, cookies=COOKIES, timeout=8
            )
            data = r.json()
            replies = data.get('data', {}).get('replies', [])
            if replies:
                return replies[0].get('content', {}).get('message', '')
            return ""
        except Exception:
            if attempt == 0:
                time.sleep(1)
                continue
            return ""

# ==================== 对话历史缓存（结构化存储）====================
conv_cache = ConversationCache(
    str(_INSTANCE_WORK / "bili_conversation_cache.json") if _INSTANCE_WORK else "/tmp/bili_conversation_cache.json"
)

def get_chat_history(root_id: str) -> list:
    return conv_cache.get(root_id)

def add_to_chat_history(root_id: str, uname: str, user_comment: str, my_reply: str):
    conv_cache.add(root_id, user_comment, my_reply)

def build_conversation_prompt(uname: str, user_comment: str, chat_history: list, video_title: str = "", parent_comment: str = "") -> str:
    lines = []
    if video_title:
        lines.append(f"视频标题：{video_title}")
    if parent_comment and parent_comment != user_comment:
        lines.append(f"这条评论是在回复：「{parent_comment}」")
    lines.append(f"当前对话：")
    lines.append(f"粉丝「{uname}」：{user_comment}")

    if chat_history:
        lines.append("")
        lines.append("--- 近期对话历史（按时间顺序）---")
        for msg in chat_history[-12:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"  粉丝「{uname}」：{content}")
            else:
                lines.append(f"  UP主（你）：{content}")
        lines.append("---")
        lines.append(f"现在你要继续回复粉丝「{uname}」，结合上面的对话上下文，自然地接着聊下去。")
    else:
        lines.append("（这是首次互动，没有对话历史）")

    return "\n".join(lines)

# ==================== 发送B站回复 ====================
def send_reply(oid: str, root: str, parent: str, content: str) -> bool:
    wait_for_cooldown()

    for attempt in range(2):
        try:
            r = session.post(
                "https://api.bilibili.com/x/v2/reply/add",
                data={
                    "oid": oid, "type": 1, "message": content,
                    "plat": 1, "root": root, "parent": parent,
                    "csrf": BILI_JCT
                },
                headers=HEADERS, cookies=COOKIES, timeout=10
            )
            j = r.json()
            if j.get('code') == 0:
                _cooldown_data['last_comment_ts'] = time.time()
                _save_cooldown()
                return True
            elif j.get('code') == 12014:
                log(f"  ⏳ CD限制，等待90秒后重试...")
                time.sleep(90)
                continue
            elif j.get('code') == 12051:
                return False
            elif j.get('code') == 12066:
                log(f"  B站拒绝内容(code=12066)，跳过")
                return False
            else:
                log(f"  B站返回错误: {j.get('message')} (code={j.get('code')})")
                return False
        except Exception as e:
            log(f"  发送失败: {e}")
            if attempt == 0:
                time.sleep(3)
                continue
            return False

    return False


# ==================== 加载/保存 ====================
_store = {}

def load():
    global _store
    try:
        if REPLIED_FILE.exists():
            _store = json.loads(REPLIED_FILE.read_text(encoding='utf-8'))
    except Exception:
        _store = {}

def save_all():
    atomic_write(REPLIED_FILE, json.dumps(_store, ensure_ascii=False, indent=2))
    log(f"  💾 已保存（累计 {len(_store)} 条）")


# ==================== 主逻辑 ====================
_current_task_id = None

dispatched_task = check_and_dispatch()
if dispatched_task:
    _current_task_id = dispatched_task["task_id"]

_lock_fd = acquire_lock()
load()

_real_stdout = sys.stdout
_stdout_buffer = StringIO()
sys.stdout = _stdout_buffer

log("=== v18 MiniMax 精确回复版启动 ===")
def process_reply_messages():
    log("══ 开始处理「回复我的」消息（MiniMax v18 精确回复版） ══")
    sent_count = 0
    processed = 0

    for page in range(1, 7):
        try:
            r = session.get(
                f"https://api.bilibili.com/x/msgfeed/reply?pn={page}&ps=20",
                headers=HEADERS, cookies=COOKIES, timeout=10
            )
            items = r.json().get('data', {}).get('items', [])
        except Exception as e:
            log(f"  获取第{page}页失败: {e}")
            continue

        if not items:
            break

        # 批量预取视频标题
        aids = [str(item.get('item', {}).get('subject_id', '')) for item in items if item.get('item', {}).get('subject_id')]
        title_cache.prefetch(aids, session, COOKIES, HEADERS)

        for item in items:
            processed += 1
            inner = item.get('item', {})
            subject_id = str(inner.get('subject_id', ''))
            source_id = str(inner.get('source_id', ''))
            target_id = str(inner.get('target_id', '0'))
            root_id = str(inner.get('root_id', '0'))
            item_type = inner.get('type', '')
            user_comment = inner.get('source_content', '').strip()
            uname = item.get('user', {}).get('nickname', '粉丝')

            if not (subject_id and source_id and user_comment):
                continue

            if source_id in _store:
                continue

            if item_type == 'reply' and target_id != '0':
                root = root_id if root_id != '0' else target_id
                parent = target_id
            else:
                root = source_id
                parent = source_id

            video_title = get_video_title(subject_id)
            parent_comment = get_parent_comment(subject_id, parent) if parent != source_id else ""
            chat_history = get_chat_history(root)

            reply_text = generate_smart_reply(uname, user_comment, video_title, parent_comment, chat_history)
            if reply_text is None:
                continue

            bad_prefixes = ['好的，用户', '粉丝昵称是', '请直接生成', '以B站UP主', '你是B站UP主', '回复粉丝', '根据上文', '结合粉丝']
            if any(reply_text.startswith(p) for p in bad_prefixes):
                log(f"  ⚠️ MiniMax 返回异常内容，跳过: {reply_text[:40]}...")
                continue

            reply_with_mention = f"@{uname} {reply_text}"
            if len(reply_with_mention) > MAX_CONTENT_LEN:
                suffix_len = len(reply_text) - (len(reply_with_mention) - MAX_CONTENT_LEN)
                suffix_len = max(suffix_len, 10)
                reply_with_mention = f"@{uname} {reply_text[:suffix_len]}…"

            log(f"  → [{uname}] {user_comment[:35]}...  →  {reply_with_mention[:50]}...")

            if send_reply(subject_id, root, parent, reply_with_mention):
                _store[source_id] = time.strftime('%Y-%m-%d %H:%M:%S')
                add_to_chat_history(root, uname, user_comment, reply_text)
                save_all()
                sent_count += 1
                time.sleep(random.uniform(4.0, 6.8))

    log(f"  本次扫描 {processed} 条消息，智能回复 {sent_count} 条")
    return sent_count


try:
    sent = process_reply_messages()
    save_all()
    if sent > 0:
        log(f"✅ 本轮完成：智能回复 {sent} 条 | 历史累计 {len(_store)} 条")

    if _current_task_id:
        update_task(_current_task_id, status="done", output={
            "replied_count": sent,
            "total_replied": len(_store)
        })
    sys.stdout = _real_stdout
    _stdout_buffer.seek(0)
    output = _stdout_buffer.getvalue()
    print(output, end='')
finally:
    try:
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

