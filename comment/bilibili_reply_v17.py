#!/usr/bin/env python3
"""
B站评论回复 - MiniMax 大模型智能版 v18
修复版：精确回复到对方下方 + 49元标准版 M2.7 稳定调用
Agent B 任务入口：支持 shared_state 任务派发模式
优化版：使用 bilibili_utils.py 公共模块
"""

import fcntl, json, os, random, signal, subprocess, sys, time
from io import StringIO
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 导入公共工具模块
sys.path.insert(0, str(Path(__file__).parent))
try:
    from bilibili_utils import smart_truncate as _smart_truncate, make_session, atomic_write, LockFile, CooldownManager, VideoTitleCache, ConversationCache
except ImportError:
    # 降级：定义本地版本
    def _smart_truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        punct = '。！？；\n'
        cutoff = max_len
        for i in range(max_len - 1, max(max_len - 15, 0), -1):
            if text[i] in punct:
                cutoff = i + 1
                break
        result = text[:cutoff]
        if cutoff < len(text) - 1 and result[-1] not in '。！？':
            result = result.rstrip('，、；') + '…'
        elif result[-1] not in '。！？…':
            result = result.rstrip('，、；') + '…'
        return result

# ==================== 多账号隔离支持 ====================
# 通过环境变量 BILIBILI_INSTANCE 切换实例
# 例如: BILIBILI_INSTANCE=fengge_b
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
        except:
            return {"tasks": {}, "agents": {}}
    return {"tasks": {}, "agents": {}}

def _write_state(state):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STATE_FILE)

def claim_task():
    """Agent B 领取一个 pending 任务"""
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
    """更新任务状态"""
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
    """检查是否有派发到本Agent的任务，有则领取执行"""
    task = claim_task()
    if task:
        log(f"🎯 收到任务: {task['task_id']} | 类型: {task['type']} | 描述: {task['description']}")
        return task
    return None

# ==================== Cookie 加载（统一用 A账号）====================
import http.cookies
if _INSTANCE_SECRETS:
    # 实例专属 cookie（同时支持netscape和dict格式）
    _ns = _INSTANCE_SECRETS / "bilibili_cookies.netscape.txt"
    _dict = _INSTANCE_SECRETS / "bilibili_cookies.txt"
    if _ns.exists():
        COOKIES_FILE = _ns
    elif _dict.exists():
        COOKIES_FILE = _dict
    else:
        COOKIES_FILE = _ns  # 会在load_cookies里fallback
else:
    COOKIES_FILE = Path.home() / ".hermes" / "secrets" / "bilibili_cookies_A.netscape.txt"

def load_cookies():
    """
    优先从 /tmp/bilibili_cookies.json (dict格式) 加载完整cookies；
    备选 ~/.bilibili_cookies.json (list格式)；
    最后才用 netscape 文件补充缺失字段。
    """
    import json as _json

    # 优先尝试 /tmp/bilibili_cookies.json（dict格式）
    json_file = Path("/tmp/bilibili_cookies.json")
    if json_file.exists():
        try:
            data = _json.loads(json_file.read_text(encoding='utf-8'))
            if isinstance(data, dict) and 'SESSDATA' in data:
                return data  # 完整dict直接返回
        except Exception:
            pass

    # 备选 ~/.bilibili_cookies.json（list格式）
    json_file2 = Path.home() / ".bilibili_cookies.json"
    if json_file2.exists():
        try:
            data = _json.loads(json_file2.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return {item['name']: item['value'] for item in data if 'name' in item and 'value' in item}
        except Exception:
            pass

    # 最终fallback：手动解析 Netscape 格式
    cookies = {}
    netscape_file = Path.home() / ".hermes" / "secrets" / "bilibili_cookies_A.netscape.txt"
    if not netscape_file.exists():
        netscape_file = Path.home() / ".hermes" / "secrets" / "bilibili_cookies_netscape.txt"
    try:
        with open(netscape_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies

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
MAX_CONTENT_LEN = 80   # B站评论最大长度（留余量）

REPLIED_FILE = _INSTANCE_WORK / "bili_replied_real.json" if _INSTANCE_WORK else Path("/tmp/bili_replied_real.json")
LOG_FILE     = _INSTANCE_WORK / "bili_reply_v17.log" if _INSTANCE_WORK else Path("/tmp/bili_reply_v17.log")
LOCK_FILE    = _INSTANCE_WORK / "bili_reply_v17.lock" if _INSTANCE_WORK else Path("/tmp/bili_reply_v17.lock")

session = requests.Session()
session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429,500,502,503,504})))

# ==================== 工具函数 ====================
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

# alarm disabled for cron stability
signal.signal(signal.SIGALRM, signal.SIG_IGN)
signal.signal(signal.SIGTERM, sig_handler)
signal.signal(signal.SIGINT, sig_handler)
signal.alarm(0)

def log(msg):
    ts = time.strftime('%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

def atomic_write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(data, encoding='utf-8')
    tmp.replace(path)

def _smart_truncate(text: str, max_len: int) -> str:
    """在分句标点附近截断，保证句子完整性"""
    if len(text) <= max_len:
        return text
    # 优先在分句标点处截断
    punct = '。！？；\n'
    cutoff = max_len
    for i in range(max_len - 1, max_len - 15, -1):
        if text[i] in punct:
            cutoff = i + 1
            break
    result = text[:cutoff]
    if cutoff < len(text) - 1 and result[-1] not in '。！？':
        result = result.rstrip('，、；') + '…'
    elif result[-1] not in '。！？…':
        result = result.rstrip('，、；') + '…'
    return result

# ==================== Ollama 本地模型（优先）====================
OLLAMA_BASE = "http://localhost:11434/v1"
OLLAMA_MODEL = "gemma3:4b"
OLLAMA_API_KEY = "ollama"
OLLAMA_SYSTEM = "你在B站评论区跟网友聊天，就像朋友闲聊。语气轻松自然，可以自黑或开玩笑，结合上下文自然聊天。长度控制在60字以内，直接输出回复内容，不要解释。"

def call_ollama(prompt: str, system: str = "") -> str:
    """调用本地 Ollama 模型"""
    try:
        import requests
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
                # 清理思考过程残留（Ollama/MiniMax 常用 **...** 或 <think> 格式）
                content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)  # **思考** → 思考
                content = re.sub(r'<think>[\s\S]*?</think>', '', content, flags=re.DOTALL)  # 移除 <think>...</think>
                content = re.sub(r'Thinking Process:.*', '', content, flags=re.DOTALL)
                content = re.sub(r'思考过程[:：]?.*', '', content, flags=re.DOTALL)
                content = re.sub(r'推理过程[:：]?.*', '', content, flags=re.DOTALL)
                # 取最后非空段落（通常思考过程在前，实际回复在后）
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
def call_minimax(prompt: str) -> str:
    """优先 Ollama，失败则用 MiniMax"""

    # 先试 Ollama
    result = call_ollama(prompt)
    if result:
        return result

    # Ollama 失败，fallback 到 MiniMax
    log("  ⚠️ Ollama 失败，切换 MiniMax")
    try:
        payload = {
            "model": "MiniMax-M2.7",
            "messages": [
                {
                    "role": "system",
                    "name": "B站网友",
                    "content": "你在B站评论区跟网友聊天，就像朋友闲聊一样。注意：1）语气轻松自然，可以自黑或开玩笑 2）结合上下文和聊天历史往下聊，不要重复说过的话 3）长度控制在60字以内"
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.85,
            "max_tokens": 300,
            "stream": False
        }

        r = session.post(
            MINIMAX_BASE,
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=15
        )

        log(f"  MiniMax 请求状态: {r.status_code} | 耗时 {r.elapsed.total_seconds():.2f}s")

        if r.status_code == 200:
            data = r.json()
            log(f"  返回 keys: {list(data.keys())}")

            # 先检查业务状态码
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
                # 清理 MiniMax 思考过程残留
                content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)
                content = re.sub(r'<think>[\s\S]*?', '', content, flags=re.DOTALL)
                content = re.sub(r'推理过程[:：]?.*', '', content, flags=re.DOTALL)

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
                log(f"  完整返回片段: {str(data)[:700]}...")

        else:
            log(f"  ❌ MiniMax 错误 {r.status_code}: {r.text[:900]}")

    except Exception as e:
        log(f"  ❌ MiniMax 调用异常: {type(e).__name__} - {e}")

    log("  ⚠️ 使用兜底回复（大模型调用失败）")
    return "哈哈收到！这条评论太有灵性了，继续往下聊呗 😂 你最喜欢这期哪一段？"


def generate_smart_reply(uname: str, user_comment: str, video_title: str = "", parent_comment: str = "", chat_history: list = None) -> str:
    """生成智能回复，包含多轮对话上下文"""
    if not user_comment.strip():
        return "兄弟/姐妹突然冒出一个空弹幕，我直接笑死 😂 说点啥呗？"

    if chat_history is None:
        chat_history = []

    # 构建多轮对话 prompt
    conversation_prompt = build_conversation_prompt(uname, user_comment, chat_history, video_title, parent_comment)

    prompt = f"""【任务】你正在B站评论区跟粉丝聊天，这是你们的多轮对话。

{conversation_prompt}

要求：
1. 直接输出评论文字，**不要**加任何分析、解释、前缀
2. 像朋友闲聊一样自然，结合上下文往下聊，不要重复说过的话
3. 40-60字以内，幽默自然
4. 如果之前聊过这个话题，要接着之前的内容继续，不要跑题或重新开头"""

    reply = call_minimax(prompt)
    if reply is None:
        log("  ⚠️ 大模型返回 None，跳过此条评论")
        return None

    # 内容太短（可能被B站判定为空白）也跳过
    if len(reply.strip()) < 5:
        log(f"  ⚠️ 内容太短或为空: '{reply}'，跳过")
        return None

    if len(reply) > 120:
        reply = _smart_truncate(reply, 120)

    # 过滤掉分析过程类内容（AI 把思考过程输出了）
    skip_patterns = ['让我分析', '根据上文', '首先', '其次', '总结', '综合来看', '【分析】', '【回复】', '**']
    for p in skip_patterns:
        if reply.startswith(p) or reply.startswith('好的，') or reply.startswith('好的，让我') or '**' in reply:
            log(f"  ⚠️ 过滤掉分析过程内容: {reply[:30]}...")
            return "哈哈，这个角度有意思 😂 继续聊~"

    return reply


# ==================== 防限速冷却控制 ====================
# B站发评论有冷却时间，以下参数控制节奏
MIN_COMMENT_INTERVAL = 60   # 发评论最小间隔（秒），B站通常要求60秒以上
MAX_COMMENT_INTERVAL = 120  # 最大间隔（秒）
COOLDOWN_STORE_FILE = _INSTANCE_WORK / "bili_comment_cooldown.json" if _INSTANCE_WORK else Path("/tmp/bili_comment_cooldown.json")

# 全局上次评论时间戳
_last_comment_ts = 0
_cooldown_data = {}

def _load_cooldown():
    global _cooldown_data
    try:
        if COOLDOWN_STORE_FILE.exists():
            _cooldown_data = json.loads(COOLDOWN_STORE_FILE.read_text(encoding='utf-8'))
    except:
        _cooldown_data = {}

def _save_cooldown():
    try:
        atomic_write(COOLDOWN_STORE_FILE, json.dumps(_cooldown_data, ensure_ascii=False, indent=2))
    except:
        pass

def _get_last_comment_ts():
    """获取上次评论时间戳"""
    return _cooldown_data.get('last_comment_ts', 0)

def _record_comment():
    """记录本次评论时间"""
    _cooldown_data['last_comment_ts'] = time.time()
    _save_cooldown()

def wait_for_cooldown():
    """等待冷却时间到期"""
    global _last_comment_ts
    _load_cooldown()
    last_ts = _get_last_comment_ts()
    elapsed = time.time() - last_ts
    if elapsed < MIN_COMMENT_INTERVAL:
        wait_time = MIN_COMMENT_INTERVAL - elapsed + random.uniform(1, 5)
        log(f"  ⏳ 等待冷却时间... ({wait_time:.1f}秒)")
        time.sleep(wait_time)

# ==================== 获取视频标题 ====================
def get_video_title(aid: str) -> str:
    """通过 aid 获取视频标题"""
    try:
        r = session.get(
            f"https://api.bilibili.com/x/web-interface/view?aid={aid}",
            headers=HEADERS, cookies=COOKIES, timeout=8
        )
        data = r.json()
        if data.get('code') == 0:
            return data.get('data', {}).get('title', '')
    except:
        pass
    return ""

# ==================== 获取评论的父评论内容 ====================
def get_parent_comment(aid: str, parent_rpid: str) -> str:
    """获取父评论内容，了解用户是在回复谁"""
    if not parent_rpid or parent_rpid == '0':
        return ""
    try:
        r = session.get(
            f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&ps=1&pn=1&root={parent_rpid}",
            headers=HEADERS, cookies=COOKIES, timeout=8
        )
        data = r.json()
        replies = data.get('data', {}).get('replies', [])
        if replies:
            return replies[0].get('content', {}).get('message', '')
    except:
        pass
    return ""

# ==================== 获取对话线程历史（多轮对话结构）====================
CONVERSATION_CACHE_FILE = _INSTANCE_WORK / "bili_conversation_cache.json" if _INSTANCE_WORK else Path("/tmp/bili_conversation_cache.json")

def load_conversation_cache():
    try:
        if CONVERSATION_CACHE_FILE.exists():
            return json.loads(CONVERSATION_CACHE_FILE.read_text(encoding='utf-8'))
    except:
        pass
    return {}

def save_conversation_cache(cache):
    try:
        atomic_write(CONVERSATION_CACHE_FILE, json.dumps(cache, ensure_ascii=False, indent=2))
    except:
        pass

def get_chat_history(aid: str, root_id: str, uname: str, user_comment: str) -> list:
    """
    获取该视频/该话题下最近的多轮对话历史。
    返回结构化的消息列表，每条消息含 role 和 content，
    这样才能真正做多轮对话。
    key = "{aid}_{root_id}" 追踪同一个话题下的所有对话
    """
    cache = load_conversation_cache()
    key = f"{aid}_{root_id}"
    return cache.get(key, [])

def add_to_chat_history(aid: str, root_id: str, uname: str, user_comment: str, my_reply: str):
    """
    把本次互动加入对话历史缓存（结构化存储，支持多轮）
    """
    cache = load_conversation_cache()
    key = f"{aid}_{root_id}"
    if key not in cache:
        cache[key] = []
    # 每条消息单独存，含 role 标识
    cache[key].append({"role": "user", "content": user_comment})
    cache[key].append({"role": "assistant", "content": my_reply})
    # 保留最近 20 条消息（约10轮对话）
    cache[key] = cache[key][-20:]
    save_conversation_cache(cache)

def build_conversation_prompt(uname: str, user_comment: str, chat_history: list, video_title: str = "", parent_comment: str = "") -> str:
    """
    把多轮对话历史构建为一个连续的 prompt，
    让 LLM 理解这是多轮对话而不是孤立的一条消息。
    """
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
        for msg in chat_history[-12:]:  # 最多12条
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
    # 先检查并等待冷却
    wait_for_cooldown()

    try:
        r = session.post(
            "https://api.bilibili.com/x/v2/reply/add",
            data={
                "oid": oid,
                "type": 1,
                "message": content,
                "plat": 1,
                "root": root,
                "parent": parent,
                "csrf": BILI_JCT
            },
            headers=HEADERS,
            cookies=COOKIES,
            timeout=10
        )
        j = r.json()
        if j.get('code') == 0:
            _record_comment()
            return True
        elif j.get('code') == 12014:
            # cd时间未到，等待更长时间后重试
            log(f"  ⏳ CD限制，等待90秒后重试...")
            time.sleep(90)
            return False
        elif j.get('code') == 12051:
            return False
        elif j.get('code') == 12066:
            log(f"  B站拒绝内容(code=12066)，可能是空内容或敏感词，跳过")
            return False
        else:
            log(f"  B站返回错误: {j.get('message')} (code={j.get('code')})")
            return False
    except Exception as e:
        log(f"  发送失败: {e}")
        return False


# ==================== 加载/保存 ====================
_store = {}

def load():
    global _store
    try:
        if REPLIED_FILE.exists():
            _store = json.loads(REPLIED_FILE.read_text(encoding='utf-8'))
    except:
        _store = {}

def save_all():
    atomic_write(REPLIED_FILE, json.dumps(_store, ensure_ascii=False, indent=2))
    log(f"  💾 已保存（累计 {len(_store)} 条）")


# ==================== 主逻辑 ====================
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

        for item in items:
            processed += 1
            inner = item.get('item', {})
            subject_id = str(inner.get('subject_id', ''))
            source_id  = str(inner.get('source_id', ''))
            target_id  = str(inner.get('target_id', '0'))
            root_id    = str(inner.get('root_id', '0'))
            item_type  = inner.get('type', '')
            user_comment = inner.get('source_content', '').strip()
            uname = item.get('user', {}).get('nickname', '粉丝')

            if not (subject_id and source_id and user_comment):
                continue

            # 用 source_id（评论 rpid）去重，避免重复回复
            if source_id in _store:
                continue

            # ==================== 精确回复逻辑 ====================
            # 核心：parent 绝对不能为 0！parent=0 会在视频下创建新根评论（另起一楼）
            # B站的 parent 参数直接决定回复挂到哪条评论下方
            #
            # type=video (target_id=0): 用户直接评论视频
            #   → 把用户的这条新评论当作根，回复直接挂到它下方
            #   root=source_id, parent=source_id
            # type=reply: 用户回复了某条评论
            #   → 回复挂到 target_id 那条评论下方（用户回复的目标）
            #   root=root_id, parent=target_id
            if item_type == 'reply' and target_id != '0':
                root = root_id if root_id != '0' else target_id
                parent = target_id    # 精准：挂到用户回复的那条评论下方
            else:
                root = source_id
                parent = source_id    # 关键修复：用户评论下方，而不是创建新根

            # ==================== 获取上下文信息 ====================
            video_title = get_video_title(subject_id)
            parent_comment = get_parent_comment(subject_id, parent) if parent != source_id else ""
            chat_history = get_chat_history(subject_id, root, uname, user_comment)

            reply_text = generate_smart_reply(uname, user_comment, video_title, parent_comment, chat_history)
            if reply_text is None:
                continue

            # 内容校验：过滤乱码/异常回复
            bad_prefixes = ['好的，用户', '粉丝昵称是', '请直接生成', '以B站UP主', '你是B站UP主', '回复粉丝', '根据上文', '结合粉丝']
            if any(reply_text.startswith(p) for p in bad_prefixes):
                log(f"  ⚠️ MiniMax 返回异常内容，跳过: {reply_text[:40]}...")
                continue

            # 在回复内容前加 @mention，精准提醒对方
            reply_with_mention = f"@{uname} {reply_text}"
            if len(reply_with_mention) > MAX_CONTENT_LEN:
                # 如果加上 @mention 超长，从回复文本末尾截断（保留 @username 完整）
                suffix_len = len(reply_text) - (len(reply_with_mention) - MAX_CONTENT_LEN)
                suffix_len = max(suffix_len, 10)
                reply_with_mention = f"@{uname} {reply_text[:suffix_len]}…"

            log(f"  → [{uname}] {user_comment[:35]}...  →  {reply_with_mention[:50]}...")

            if send_reply(subject_id, root, parent, reply_with_mention):
                _store[source_id] = time.strftime('%Y-%m-%d %H:%M:%S')
                add_to_chat_history(subject_id, root, uname, user_comment, reply_text)
                save_all()
                sent_count += 1
                time.sleep(random.uniform(4.0, 6.8))

    log(f"  本次扫描 {processed} 条消息，智能回复 {sent_count} 条")
    return sent_count


# ==================== 执行 ====================
_current_task_id = None

# 先尝试领取任务（派发模式）
dispatched_task = check_and_dispatch()
if dispatched_task:
    _current_task_id = dispatched_task["task_id"]

_lock_fd = acquire_lock()
load()

# 重定向 stdout 到缓冲区，结束时统一输出
_real_stdout = sys.stdout
_stdout_buffer = StringIO()
sys.stdout = _stdout_buffer

log("=== v18 MiniMax 精确回复版启动 ===")

try:
    sent = process_reply_messages()
    save_all()
    if sent > 0:
        log(f"✅ 本轮完成：智能回复 {sent} 条 | 历史累计 {len(_store)} 条")

    # 更新任务状态
    if _current_task_id:
        update_task(_current_task_id, status="done", output={
            "replied_count": sent,
            "total_replied": len(_store)
        })
    # 恢复 stdout 并输出
    sys.stdout = _real_stdout
    _stdout_buffer.seek(0)
    output = _stdout_buffer.getvalue()
    print(output, end='')
    # 无新回复时静默退出，不打印任何内容（避免cron汇报）
finally:
    try:
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
    except:
        pass