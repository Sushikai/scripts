#!/usr/bin/env python3
"""
B站私信监控Bot - 自动巡查私信并智能回复
依赖 httpx 和 asyncio
"""
import asyncio
import fcntl
import hashlib
import json
import os
import time
import httpx
from datetime import datetime
from urllib.parse import urlencode, quote
from playwright.async_api import async_playwright

LOCK_FILE = "/tmp/bili_dm_monitor.lock"
_instance = os.environ.get('BILIBILI_INSTANCE', '')
if _instance:
    LOCK_FILE = f"/tmp/bili_dm_monitor_{_instance}.lock"
    _base = os.path.expanduser(f"~/.hermes/instances/{_instance}")
    _work = os.path.join(_base, "work")
    os.makedirs(_work, exist_ok=True)
    _cookie_path = os.path.join(_work, "bilibili_cookies.json")
    if os.path.exists(_cookie_path):
        COOKIES_FILE = str(_cookie_path)
        SENT_MESSAGES_FILE = str(os.path.join(_work, "bili_dm_sent_messages_v2.json"))
else:
    _base = None
    _work = None

# Load .env_minimax if API key not already set
if not os.environ.get("MINIMAX_API_KEY"):
    env_path = os.path.expanduser("~/.env_minimax")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v

SENT_MESSAGES_FILE = "/tmp/bili_dm_sent_messages_v2.json"

# 多账号支持（仅在 _instance 未设置时才用默认）
if not _instance:
    _instance = os.environ.get('BILIBILI_INSTANCE', '')
    COOKIES_FILE = "/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt"
    if _instance:
        _base = os.path.expanduser(f"~/.hermes/instances/{_instance}")
        _work = os.path.join(_base, "work")
        os.makedirs(_work, exist_ok=True)
        _cookie_path = os.path.join(_work, "bilibili_cookies.json")
        if os.path.exists(_cookie_path):
            COOKIES_FILE = str(_cookie_path)
            SENT_MESSAGES_FILE = str(os.path.join(_work, "bili_dm_sent_messages_v2.json"))

COOKIES_FILE = os.environ.get('BILIBILI_DM_COOKIE_FILE', COOKIES_FILE)
if os.environ.get('BILIBILI_DM_SENT_FILE'):
    SENT_MESSAGES_FILE = os.environ.get('BILIBILI_DM_SENT_FILE')

# Bilibili DM API endpoints
SESSION_LIST_URL = "https://api.vc.bilibili.com/session_svr/v1/session_svr/get_sessions"
SESSION_MSG_URL = "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
SEND_MSG_URL = "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# WBI keys (from known open-source implementations, updated periodically)
# These may need to be refreshed if signing fails
WBI_KEYS = {
    "img_key": "ea5b86f53d39cb32ab56cef9e3e67a70",
    "sub_key": "70c1a4e0e0e02c3c76814a5d2b045946"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://message.bilibili.com/",
    "Origin": "https://message.bilibili.com"
}

# Table for WBI mixing
MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 20, 44, 54, 28, 14, 34, 56, 4, 25, 63, 57, 62, 51, 30,
    36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36,
    24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24,
    6, 64, 46, 11, 60
]

async def get_wbi_keys(cookies):
    """Fetch fresh WBI keys from Bilibili nav API"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(NAV_URL, cookies=cookies, headers=HEADERS, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                wbi_img = data['data']['wbi_img']
                # Extract keys from URL paths - e.g. https://i0.hdslb.com/bfs/wbi/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.png
                img_url = wbi_img.get('img_url', '')
                sub_url = wbi_img.get('sub_url', '')
                # Extract filename without extension
                import os
                img_key = os.path.basename(img_url).split('.')[0]
                sub_key = os.path.basename(sub_url).split('.')[0]
                return {'img_key': img_key, 'sub_key': sub_key}
        return None
    except Exception as e:
        print(f"  获取WBI keys失败: {e}")
        return None

def mixin_key(orig):
    """Mixin key obfuscation"""
    result = []
    for i in MIXIN_KEY_TAB:
        if i < len(orig):
            result.append(orig[i])
    return ''.join(result)

def get_wbi_sign(params, img_key, sub_key):
    """Generate WBI signing token"""
    mil = mixin_key(img_key + sub_key)
    temp = ''
    for i in range(len(mil)):
        temp += mil[i]
        if i < len(params):
            temp += params[i]
    
    # Use the first 32 chars of MD5 of temp as wbi token
    # Then append remaining params
    full = temp + str(int(time.time()))
    wbi_token = hashlib.md5(full.encode()).hexdigest()
    return wbi_token

def build_wbi_url(base_url, params, cookies):
    """Build URL with WBI signing"""
    # Get fresh wbi keys or use cached
    wbi_img = None
    try:
        import asyncio
        wbi_img = asyncio.get_event_loop().run_until_complete(get_wbi_keys(cookies))
    except Exception:
        wbi_img = None
    
    if wbi_img:
        img_key = wbi_img['img_key']
        sub_key = wbi_img['sub_key']
    else:
        img_key = WBI_KEYS['img_key']
        sub_key = WBI_KEYS['sub_key']
    
    # Sort params
    sorted_params = sorted(params.items())
    param_str = urlencode(sorted_params, safe='/:?=')
    
    # Calculate w_rid (wbi signing)
    # The signing combines the param string with the mixed wbi key
    mil = mixin_key(img_key + sub_key)
    
    # Build the string to sign
    half_len = len(mil) // 2
    sign_str = mil[:half_len] + param_str + mil[half_len:]
    wbi_sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    # Build final params with wbi signing
    final_params = dict(sorted_params)
    final_params['wbi_sign'] = wbi_sign
    
    return f"{base_url}?{urlencode(final_params, safe='/:?=')}", final_params


def load_cookies():
    with open(COOKIES_FILE, 'r') as f:
        cookies_data = json.load(f)
    # 支持两种格式：(1) list of {name, value} objects, (2) dict of {name: value}
    if isinstance(cookies_data, list):
        return {c['name']: c['value'] for c in cookies_data}
    elif isinstance(cookies_data, dict):
        return cookies_data
    else:
        raise ValueError(f"Unknown cookies format: {type(cookies_data)}")

def load_sent_messages():
    if not os.path.exists(SENT_MESSAGES_FILE):
        return {}
    try:
        with open(SENT_MESSAGES_FILE, 'r') as f:
            data = json.load(f)
        for k, v in data.items():
            if isinstance(v, dict) and 'user_msg_key' in v:
                data[k] = [v]
        return data
    except:
        return {}

def save_sent_messages(data):
    with open(SENT_MESSAGES_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False)

def _get_msg_key(last_msg):
    """从last_msg提取唯一标识：优先msg_key，其次sender_uid+timestamp"""
    msg_key = last_msg.get('msg_key') or last_msg.get('msgId') or last_msg.get('msg_id')
    if msg_key:
        return str(msg_key)
    # fallback：用 sender_uid + timestamp 作为伪唯一键
    uid = last_msg.get('sender_uid', 0)
    ts = last_msg.get('timestamp', 0)
    return f"{uid}_{ts}"

def is_user_msg_already_replied(session_key, last_msg):
    """检查是否已经回复过这个用户消息（用唯一msg_key判断）"""
    data = load_sent_messages()
    msg_key = _get_msg_key(last_msg)
    if session_key not in data:
        return False
    # 48小时窗口
    now = time.time()
    cutoff = now - 48 * 3600
    for entry in data[session_key]:
        if entry.get('user_msg_key') == msg_key and entry.get('ts', 0) > cutoff:
            return True
    return False

def record_user_msg_replied(session_key, last_msg):
    """记录已处理的用户消息，用唯一msg_key标识"""
    data = load_sent_messages()
    now = time.time()
    cutoff = now - 48 * 3600
    msg_key = _get_msg_key(last_msg)
    if session_key in data:
        data[session_key] = [e for e in data[session_key] if e.get('ts', 0) > cutoff]
    else:
        data[session_key] = []
    data[session_key].append({'user_msg_key': msg_key, 'ts': now})
    for k in list(data.keys()):
        data[k] = [e for e in data[k] if e.get('ts', 0) > cutoff]
        if not data[k]:
            del data[k]
    save_sent_messages(data)

def get_conversation_context(messages):
    """
    从消息历史构建多轮对话文本。
    最多使用最近 12 条消息（6轮对话），格式为：
      对方：xxx
      我：xxx
    这样 LLM 能清楚看到对话的来龙去脉。
    """
    if not messages:
        return ""
    sorted_msgs = sorted(messages, key=lambda x: x.get('timestamp', 0))
    recent = sorted_msgs[-12:]  # 最多12条，约6轮
    lines = []
    for msg in recent:
        sender = "我" if msg.get('is_mine') else "对方"
        lines.append(f"{sender}：{msg.get('content', '')}")
    return "\n".join(lines)

def generate_reply(sender_name, context, msg_content):
    """
    生成多轮对话回复。
    context 是该会话的完整近期消息历史，
    msg_content 是最新一条需要回复的消息。
    LLM 应该把 context 当作对话背景，结合 msg_content 生成自然的延续。
    """
    system_prompt = """你是一个B站网友，正在和朋友私聊。回复要自然、友好、简短（50字以内）。
永远保持友好态度，不要骂人、不要讽刺、不要嘲讽对方。
不要每条都回复，如果对方只是在正常聊天，你也可以简单回应或发表情。
不要指明别人是骗子。
骗子特征：主动搭讪、询问隐私、提及金钱/充值/转账、说辞矛盾、过度热情。
如果是骗子：继续套话，装傻提问，不露馅。
如果是正经人：友好交流，正常聊天。
如果是系统通知：不回复。

重要：你正在和对方进行多轮对话。请结合对话的完整上下文自然地继续聊天，
不要重复已经说过的内容，不要跑题或重新开头。"""

    user_prompt = f"""【对话历史】
{context if context else "（暂无历史，这是第一条消息）"}

【对方最新消息】
{msg_content}

请作为对方的B站朋友，结合上面的对话历史，自然地继续回复这条最新消息。
直接输出回复内容，不要前缀，不要解释。"""

    # Try Ollama first (local, multi-model fallback)
    try:
        import requests as _req
        for _model in ["qwen2.5:32b-instruct-q4_K_M", "gemma3:4b", "deepseek-r1:1.5b"]:
            try:
                resp = _req.post(
                    "http://localhost:11434/v1/chat/completions",
                    headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                    json={
                        "model": _model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "max_tokens": 100,
                        "temperature": 0.8
                    },
                    timeout=30
                )
                if resp.status_code == 200:
                    result = resp.json()
                    reply = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if reply:
                        import re
                        final_candidates = re.split(r'(?:^## |^结论[：:]\s*)', reply, flags=re.MULTILINE)
                        return (final_candidates[-1] if len(final_candidates) > 1 else reply).strip()
                    # qwen3.5 reasoning fallback
                    reasoning = result.get('choices', [{}])[0].get('message', {}).get('reasoning', '')
                    if reasoning:
                        txt = reasoning.split("|")[-1].strip()
                        txt = re.sub(r'\[.*?\]\s*', '', txt).strip()
                        if txt:
                            return txt[:100]
            except: pass
    except Exception as e:
        print(f"Ollama failed: {e}")

    # Try Minimax second
    minimax_key = os.environ.get("MINIMAX_API_KEY")
    if minimax_key:
        try:
            resp = requests.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={"Authorization": f"Bearer {minimax_key}", "Content-Type": "application/json"},
                json={
                    "model": "MiniMax-M2.7",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 500,
                    "temperature": 0.8
                },
                timeout=30
            )
            if resp.status_code == 200:
                result = resp.json()
                reply = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                if not reply:
                    reply = result.get('choices', [{}])[0].get('message', {}).get('reasoning_content', '')
                if reply:
                    return reply.strip()
        except Exception as e:
            print(f"Minimax API failed: {e}")
    
    # Try OpenAI compatible endpoint (last fallback)
    openai_key = os.environ.get("OPENAI_API_KEY")
    openai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key, base_url=openai_base)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=500,
                temperature=0.8
            )
            reply = response.choices[0].message.content.strip()
            return reply.strip('"\'')
        except Exception as e:
            print(f"OpenAI API failed: {e}")
    
    return None

def extract_msg_content(m):
    """从消息中提取文本内容"""
    content = m.get('content', '')
    if not content:
        return ''
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed.get('content', '')
            return str(parsed)
        except:
            return content
    elif isinstance(content, dict):
        return content.get('content', '')
    return str(content)

def api_get_sessions(cookies):
    """获取私信会话列表"""
    params = {
        "session_type": 4,
        "group_fold": 1,
        "unfollow_fold": 0,
        "sort_rule": 2,
        "build": 0,
        "mobi_app": "web"
    }
    resp = httpx.get(SESSION_LIST_URL, cookies=cookies, headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('code') != 0:
        return None
    return data.get('data', {}).get('session_list', []) or data.get('data', {}).get('list', [])

def api_get_messages(cookies, talker_id, session_type=1, size=20):
    """获取与某人的消息历史"""
    params = {
        "talker_id": talker_id,
        "session_type": session_type,
        "begin_seqno": 0,
        "size": size
    }
    resp = httpx.get(SESSION_MSG_URL, cookies=cookies, headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('code') != 0:
        return None
    return data.get('data', {}).get('messages', []) or data.get('data', {}).get('msg_list', [])

async def browser_send_message(talker_id, content, session_name=None, max_retries=3):
    """通过Playwright浏览器自动化发送私信
    
    Args:
        talker_id: 对方用户ID
        content: 消息内容
        session_name: 会话名称（已弃用，直接通过URL导航）
        max_retries: 最大重试次数
    """
    with open(COOKIES_FILE) as f:
        cookies_raw = json.load(f)
    if isinstance(cookies_raw, dict):
        cookies_list = [{'name': k, 'value': v} for k, v in cookies_raw.items()]
    else:
        cookies_list = cookies_raw
    
    for c in cookies_list:
        if 'domain' not in c:
            c['domain'] = '.bilibili.com'
        if 'path' not in c:
            c['path'] = '/'
        # Fix expires: -1 for session, must be positive int
        exp = c.get('expires', -1)
        try:
            exp_int = int(exp)
        except (ValueError, TypeError):
            exp_int = -1
        if exp_int <= 0:
            c['expires'] = -1
        else:
            c['expires'] = exp_int
    
    for attempt in range(max_retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                    args=['--disable-web-security', '--no-sandbox']
                )
                context = await browser.new_context()
                await context.add_cookies(cookies_list)
                page = await context.new_page()
                
                # 直接导航到私信会话页面
                conversation_url = f'https://message.bilibili.com/#/whisper/mid{talker_id}'
                try:
                    await page.goto('https://message.bilibili.com/', timeout=60000, wait_until='domcontentloaded')
                except:
                    await browser.close()
                    await asyncio.sleep(2)
                    continue
                
                await page.wait_for_timeout(5000)
                
                # 通过 hash 导航触发 SPA（用 goto 会导致 SPA 路由丢失）
                await page.evaluate(f"window.location.hash = '#/whisper/mid{talker_id}'")
                await page.wait_for_timeout(5000)  # 等待 SPA 渲染会话

                # 填写编辑器：先点击激活，再用 keyboard type 触发 Vue 响应
                editor_selector = '.brt-editor'
                editor = page.locator(editor_selector).first
                try:
                    await editor.click(timeout=5000)
                    await page.wait_for_timeout(500)
                    await editor.type(content, delay=30)
                    await page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"    编辑器交互异常: {e}")

                # 点击发送按钮
                send_btn_selector = '[class*="_SendBtn_"]'
                send_btn = page.locator(send_btn_selector).first
                if await send_btn.count() > 0:
                    try:
                        cls = await send_btn.get_attribute('class', timeout=5000) or ''
                        if '_IsDisabled' not in cls:
                            await send_btn.click(timeout=5000)
                            await page.wait_for_timeout(2000)
                            print(f"    点击发送成功")
                        else:
                            print(f"    发送按钮仍禁用 (Vue状态未更新)")
                    except Exception as e:
                        print(f"    发送按钮点击异常: {e}")
                
                await browser.close()
                return True
                
        except Exception as e:
            print(f"  浏览器发送异常: {e}")
            await asyncio.sleep(2)
    
    return False

async def api_send_message(cookies, talker_id, content, session_name=None):
    """发送私信（已弃用，改用browser_send_message）"""
    return await browser_send_message(talker_id, content, session_name=session_name)

async def process_conversations():
    # Acquire lock to prevent concurrent runs
    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已有实例在运行，跳过")
        lock_fd.close()
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始巡查私信...")
    
    try:
        cookies = load_cookies()
    except Exception as e:
        print(f"加载cookies失败: {e}")
        return
    
    try:
        sessions = api_get_sessions(cookies)
        if sessions is None:
            print("获取会话列表失败: API返回错误 (可能需要刷新cookies)")
            return
        print(f"发现 {len(sessions)} 个私信会话")
    except Exception as e:
        print(f"获取会话列表失败: {e}")
        return
    
    if not sessions:
        print("没有私信会话")
        return
    
    processed = 0
    for sess in sessions:
        try:
            talker_id = sess.get('talker_id')
            if not talker_id:
                continue

            sender_name = sess.get('talker_name', str(talker_id))
            session_key = str(talker_id)
            my_uid = int(cookies.get('DedeUserID', 0))

            # 使用 session 的 last_msg（这个 API 是可用的）
            last_msg = sess.get('last_msg', {})
            if not last_msg:
                continue
            msg_content = extract_msg_content(last_msg)
            if not msg_content:
                continue

            # 判断 last_msg 是否来自对方
            sender_uid = last_msg.get('sender_uid', 0)
            is_from_me = (sender_uid == my_uid)

            # 系统通知跳过
            try:
                parsed = json.loads(msg_content) if isinstance(msg_content, str) else msg_content
                if isinstance(parsed, dict) and 'title' in parsed:
                    continue
            except:
                pass

            # 核心判断：只有对方发来的消息才回复
            if is_from_me:
                # last_msg 是自己发的，但对方可能已经回复了
                # 需要获取完整消息历史来确认最新消息确实是对方的
                history = api_get_messages(cookies, talker_id, session_type=1)
                if not history:
                    print(f"  [{sender_name}] 获取历史失败，跳过")
                    continue
                sorted_history = sorted(history, key=lambda x: x.get('timestamp', 0))
                latest_msg = sorted_history[-1] if sorted_history else {}
                latest_sender = latest_msg.get('sender_uid', 0)
                latest_is_from_me = (latest_sender == my_uid)
                if latest_is_from_me:
                    # 我已经回复过这条会话了（最新消息是自己发的）
                    print(f"  [{sender_name}] 最新消息是我发的，无新回复")
                    continue
                # 最新消息来自对方，使用这条消息
                last_msg = latest_msg
                msg_content = extract_msg_content(last_msg)
                if not msg_content:
                    print(f"  [{sender_name}] 消息内容为空，跳过")
                    continue

            # 去重检查：防止对同一条消息重复回复
            if is_user_msg_already_replied(session_key, last_msg):
                print(f"  已回复过该消息，跳过 {sender_name}")
                continue

            # last_msg 来自对方，但需要通过获取完整消息历史来确认最新消息确实是对方的
            # （unread_count 可能不准确，session 的 last_msg 也可能有延迟）
            history = api_get_messages(cookies, talker_id, session_type=1)
            if not history:
                print(f"  获取消息历史失败，跳过 {sender_name}")
                continue

            # 取最近一条消息（按 timestamp 排序）
            sorted_history = sorted(history, key=lambda x: x.get('timestamp', 0))
            latest_msg = sorted_history[-1] if sorted_history else {}
            latest_sender = latest_msg.get('sender_uid', 0)
            latest_is_from_me = (latest_sender == my_uid)

            if latest_is_from_me:
                # 我已经回复过这条会话了（最新消息是自己发的）
                continue

            # 最新消息来自对方，判断为需要回复
            # 从完整历史中提取可用消息（过滤掉系统通知等）
            valid_msgs = []
            for m in sorted_history[-24:]:  # 最近24条
                c = extract_msg_content(m)
                if not c:
                    continue
                try:
                    parsed = json.loads(c) if isinstance(c, str) else c
                    if isinstance(parsed, dict) and 'title' in parsed:
                        continue  # 跳过系统通知
                except:
                    pass
                is_mine = (m.get('sender_uid', 0) == my_uid)
                valid_msgs.append({'content': c, 'timestamp': m.get('timestamp', 0), 'is_mine': is_mine})

            if not valid_msgs:
                print(f"  消息历史为空或全为系统通知，跳过 {sender_name}")
                continue

            msg_content = extract_msg_content(latest_msg) if latest_msg else ''
            context = get_conversation_context(valid_msgs)
            print(f"会话 {sender_name}: 对方新消息: {msg_content[:40]}...")

            reply = generate_reply(sender_name, context, msg_content)

            if not reply:
                print(f"  无法生成回复")
                continue

            print(f"  对方: {msg_content[:50]}")
            print(f"  我方: {reply[:50]}")

            try:
                if await api_send_message(cookies, talker_id, reply, session_name=sender_name):
                    record_user_msg_replied(session_key, latest_msg)
                    print(f"  回复已发送")
                    processed += 1
                else:
                    print(f"  发送失败")
            except Exception as e:
                print(f"  发送失败: {e}")

        except Exception as e:
            print(f"处理会话出错: {e}")
            continue

    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    lock_fd.close()

    print(f"处理完成，共发送 {processed} 条回复")

def main():
    """Entry point for cron job"""
    asyncio.run(process_conversations())

if __name__ == "__main__":
    asyncio.run(process_conversations())
