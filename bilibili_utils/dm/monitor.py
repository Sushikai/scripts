#!/usr/bin/env python3
"""
B站私信监控Bot - 自动巡查私信并智能回复（多账号版）
依赖 httpx 和 asyncio
"""
import asyncio
import fcntl
import hashlib
import json
import os
import time
import httpx
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, quote

LOCK_FILE_BASE = "/tmp/bili_dm_monitor"

# 多账号 Cookie 文件
ALL_COOKIE_FILES = [
    Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt"),
    Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt"),
    Path("/Users/kaikai/scripts/tiktok_story_bili/那那天下雨了_cookies.txt"),
    Path("/Users/kaikai/scripts/tiktok_story_bili/风走了叶落_cookies.txt"),
]

# 动作日志（用于转化分析）
DM_ACTIONS = Path("/Users/kaikai/scripts/dm_actions.jsonl")

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

# Bilibili DM API endpoints
SESSION_LIST_URL = "https://api.bilibili.com/session_svr/v1/session_svr/get_sessions"
SESSION_MSG_URL = "https://api.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

WBI_KEYS = {
    "img_key": "ea5b86f53d39cb32ab56cef9e3e67a70",
    "sub_key": "70c1a4e0e0e02c3c76814a5d2b045946"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://message.bilibili.com/",
    "Origin": "https://message.bilibili.com"
}

MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 20, 44, 54, 28, 14, 34, 56, 4, 25, 63, 57, 62, 51, 30,
    36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36,
    24, 6, 64, 46, 11, 60, 51, 30, 36, 24, 6, 64, 46, 11, 60, 51, 30, 36, 24,
    6, 64, 46, 11, 60
]

def _load_json_cookies(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'SESSDATA' in data:
            return data
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data if 'name' in c and 'value' in c}
    except Exception:
        pass
    return {}

def _load_netscape_cookies(path: Path) -> dict:
    cookies = {}
    try:
        for line in path.read_text(encoding='utf-8').split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies

def _load_cookies_from_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding='utf-8').strip()
        if text.startswith('{') or text.startswith('['):
            return _load_json_cookies(path)
        else:
            return _load_netscape_cookies(path)
    except Exception:
        return {}

def _validate_account(cookies: dict) -> bool:
    if not cookies.get("SESSDATA"):
        return False
    try:
        r = httpx.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookies, headers=HEADERS, timeout=8
        )
        j = r.json()
        return j.get("code") == 0 and j.get("data", {}).get("isLogin") == True
    except Exception:
        return False

def load_all_accounts():
    accounts = []
    for i, path in enumerate(ALL_COOKIE_FILES):
        cookies = _load_cookies_from_file(path)
        if not cookies.get("SESSDATA"):
            print(f"  账号{i+1} [{path.name}] 无SESSDATA，跳过")
            continue
        if not _validate_account(cookies):
            print(f"  账号{i+1} [{path.name}] 验证失败，跳过")
            continue
        uname = ""
        try:
            r = httpx.get("https://api.bilibili.com/x/web-interface/nav", cookies=cookies, timeout=8)
            uname = r.json().get("data", {}).get("uname", path.stem[:10])
        except Exception:
            uname = path.stem[:10]
        print(f"  账号{i+1} [{uname}] 加载成功")
        accounts.append({
            "name": path.stem[:15],
            "cookies": cookies,
            "cookies_file": str(path),
        })
    return accounts

def _write_temp_netscape(cookies: dict) -> str:
    """将cookies字典写入临时netscape格式文件，返回路径"""
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        domain = ".bilibili.com"
        flag = "TRUE" if name in ("b_lsid", "buvid3", "SESSDATA") else "FALSE"
        path = "/"
        secure = "TRUE"
        expires = "9999999999"
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    tmp.write("\n".join(lines))
    tmp.close()
    return tmp.name

# ==================== WBI ====================
async def get_wbi_keys(cookies):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(NAV_URL, cookies=cookies, headers=HEADERS, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                wbi_img = data['data']['wbi_img']
                img_url = wbi_img.get('img_url', '')
                sub_url = wbi_img.get('sub_url', '')
                import os
                img_key = os.path.basename(img_url).split('.')[0]
                sub_key = os.path.basename(sub_url).split('.')[0]
                return {'img_key': img_key, 'sub_key': sub_key}
        return None
    except Exception as e:
        print(f"  获取WBI keys失败: {e}")
        return None

def mixin_key(orig):
    result = []
    for i in MIXIN_KEY_TAB:
        if i < len(orig):
            result.append(orig[i])
    return ''.join(result)

# ==================== Per-account sent messages ====================
def load_sent_messages(name: str) -> dict:
    f = Path(f"/tmp/bili_dm_sent_{name[:8]}.json")
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text())
        for k, v in data.items():
            if isinstance(v, dict) and 'user_msg_key' in v:
                data[k] = [v]
        return data
    except:
        return {}

def save_sent_messages(name: str, data: dict):
    f = Path(f"/tmp/bili_dm_sent_{name[:8]}.json")
    f.write_text(json.dumps(data, ensure_ascii=False))

# ==================== Message helpers ====================
def _get_msg_key(last_msg):
    msg_key = last_msg.get('msg_key') or last_msg.get('msgId') or last_msg.get('msg_id')
    if msg_key:
        return str(msg_key)
    uid = last_msg.get('sender_uid', 0)
    ts = last_msg.get('timestamp', 0)
    return f"{uid}_{ts}"

def is_user_msg_already_replied(name, session_key, last_msg):
    data = load_sent_messages(name)
    msg_key = _get_msg_key(last_msg)
    if session_key not in data:
        return False
    now = time.time()
    cutoff = now - 48 * 3600
    for entry in data[session_key]:
        if entry.get('user_msg_key') == msg_key and entry.get('ts', 0) > cutoff:
            return True
    return False

def record_user_msg_replied(name, session_key, last_msg):
    data = load_sent_messages(name)
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
    save_sent_messages(name, data)

def get_conversation_context(messages):
    if not messages:
        return ""
    sorted_msgs = sorted(messages, key=lambda x: x.get('timestamp', 0))
    recent = sorted_msgs[-12:]
    lines = []
    for msg in recent:
        sender = "我" if msg.get('is_mine') else "对方"
        lines.append(f"{sender}：{msg.get('content', '')}")
    return "\n".join(lines)

def extract_msg_content(m):
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

# ==================== LLM Reply Generation ====================
def generate_reply(sender_name, context, msg_content):
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

    # Try Ollama
    try:
        import requests as _req
        for _model in ["qwen2.5:32b-instruct-q4_K_M", "gemma3:4b", "deepseek-r1:1.5b"]:
            try:
                resp = _req.post(
                    "http://localhost:11434/v1/chat/completions",
                    headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                    json={"model": _model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "max_tokens": 100, "temperature": 0.8},
                    timeout=30
                )
                if resp.status_code == 200:
                    result = resp.json()
                    reply = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if reply:
                        import re
                        final_candidates = re.split(r'(?:^## |^结论[：:]\s*)', reply, flags=re.MULTILINE)
                        return (final_candidates[-1] if len(final_candidates) > 1 else reply).strip()
                    reasoning = result.get('choices', [{}])[0].get('message', {}).get('reasoning', '')
                    if reasoning:
                        txt = reasoning.split("|")[-1].strip()
                        txt = re.sub(r'\[.*?\]\s*', '', txt).strip()
                        if txt:
                            return txt[:100]
            except: pass
    except Exception as e:
        print(f"Ollama failed: {e}")

    # Try Minimax
    minimax_key = os.environ.get("MINIMAX_API_KEY")
    if minimax_key:
        try:
            resp = httpx.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={"Authorization": f"Bearer {minimax_key}", "Content-Type": "application/json"},
                json={"model": "MiniMax-M2.7", "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "max_tokens": 500, "temperature": 0.8},
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

    return None

# ==================== API ====================
def api_get_sessions(cookies):
    params = {
        "session_type": 4, "group_fold": 1, "unfollow_fold": 0,
        "sort_rule": 2, "build": 0, "mobi_app": "web"
    }
    resp = httpx.get(SESSION_LIST_URL, cookies=cookies, headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('code') != 0:
        return None
    return data.get('data', {}).get('session_list', []) or data.get('data', {}).get('list', [])

def api_get_messages(cookies, talker_id, session_type=1, size=20):
    params = {
        "talker_id": talker_id, "session_type": session_type,
        "begin_seqno": 0, "size": size
    }
    resp = httpx.get(SESSION_MSG_URL, cookies=cookies, headers=HEADERS, params=params, timeout=15)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('code') != 0:
        return None
    return data.get('data', {}).get('messages', []) or data.get('data', {}).get('msg_list', [])

# ==================== Browser Send (Playwright) ====================
from playwright.async_api import async_playwright

async def browser_send_message(talker_id, content, cookies_file):
    """通过Playwright发送私信，使用指定的cookie文件"""
    with open(cookies_file) as f:
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
        exp = c.get('expires', -1)
        try:
            exp_int = int(exp)
        except (ValueError, TypeError):
            exp_int = -1
        c['expires'] = -1 if exp_int <= 0 else exp_int

    for attempt in range(3):
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

                await page.goto('https://message.bilibili.com/', timeout=60000, wait_until='domcontentloaded')
                await page.wait_for_timeout(5000)
                await page.evaluate(f"window.location.hash = '#/whisper/mid{talker_id}'")
                await page.wait_for_timeout(5000)

                editor = page.locator('.brt-editor').first
                try:
                    await editor.click(timeout=5000)
                    await page.wait_for_timeout(500)
                    await editor.type(content, delay=30)
                    await page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"    编辑器交互异常: {e}")

                send_btn = page.locator('[class*="_SendBtn_"]').first
                if await send_btn.count() > 0:
                    try:
                        cls = await send_btn.get_attribute('class', timeout=5000) or ''
                        if '_IsDisabled' not in cls:
                            await send_btn.click(timeout=5000)
                            await page.wait_for_timeout(2000)
                            print(f"    发送成功")
                    except Exception as e:
                        print(f"    发送按钮异常: {e}")

                await browser.close()
                return True
        except Exception as e:
            print(f"  浏览器发送异常: {e}")
            await asyncio.sleep(2)
    return False

def _log_dm_action(talker_id, talker_name, reply):
    try:
        with open(DM_ACTIONS, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "uid": str(talker_id),
                "uname": talker_name,
                "action": "dm",
                "reply_preview": reply[:50],
                "timestamp": datetime.now().isoformat()
            }, ensure_ascii=False) + '\n')
    except Exception:
        pass

# ==================== Per-account processing ====================
async def process_account(acc):
    name = acc["name"]
    cookies = acc["cookies"]
    cookies_file = acc["cookies_file"]
    lock_file = f"{LOCK_FILE_BASE}_{name[:8]}.lock"

    lock_fd = open(lock_file, 'w')
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{name}] 已有实例在运行，跳过")
        lock_fd.close()
        return 0

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{name}] 开始巡查私信...")

    try:
        sessions = api_get_sessions(cookies)
        if sessions is None:
            print(f"  [{name}] 获取会话列表失败")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
            return 0
        print(f"  [{name}] 发现 {len(sessions)} 个私信会话")
    except Exception as e:
        print(f"  [{name}] 获取会话列表失败: {e}")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        return 0

    if not sessions:
        print(f"  [{name}] 没有私信会话")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        return 0

    processed = 0
    my_uid = int(cookies.get('DedeUserID', 0))

    for sess in sessions:
        try:
            talker_id = sess.get('talker_id')
            if not talker_id:
                continue
            sender_name = sess.get('talker_name', str(talker_id))
            session_key = str(talker_id)

            last_msg = sess.get('last_msg', {})
            if not last_msg:
                continue
            msg_content = extract_msg_content(last_msg)
            if not msg_content:
                continue

            sender_uid = last_msg.get('sender_uid', 0)
            is_from_me = (sender_uid == my_uid)

            try:
                parsed = json.loads(msg_content) if isinstance(msg_content, str) else msg_content
                if isinstance(parsed, dict) and 'title' in parsed:
                    continue
            except:
                pass

            if is_from_me:
                history = api_get_messages(cookies, talker_id, session_type=1)
                if not history:
                    continue
                sorted_history = sorted(history, key=lambda x: x.get('timestamp', 0))
                latest_msg = sorted_history[-1] if sorted_history else {}
                latest_sender = latest_msg.get('sender_uid', 0)
                if latest_sender == my_uid:
                    continue
                last_msg = latest_msg
                msg_content = extract_msg_content(last_msg)
                if not msg_content:
                    continue

            if is_user_msg_already_replied(name, session_key, last_msg):
                continue

            history = api_get_messages(cookies, talker_id, session_type=1)
            if not history:
                continue

            sorted_history = sorted(history, key=lambda x: x.get('timestamp', 0))
            latest_msg = sorted_history[-1] if sorted_history else {}
            latest_sender = latest_msg.get('sender_uid', 0)

            if latest_sender == my_uid:
                continue

            valid_msgs = []
            for m in sorted_history[-24:]:
                c = extract_msg_content(m)
                if not c:
                    continue
                try:
                    parsed = json.loads(c) if isinstance(c, str) else c
                    if isinstance(parsed, dict) and 'title' in parsed:
                        continue
                except:
                    pass
                is_mine = (m.get('sender_uid', 0) == my_uid)
                valid_msgs.append({'content': c, 'timestamp': m.get('timestamp', 0), 'is_mine': is_mine})

            if not valid_msgs:
                continue

            msg_content = extract_msg_content(latest_msg) if latest_msg else ''
            context = get_conversation_context(valid_msgs)
            print(f"  [{name}] 会话 {sender_name}: {msg_content[:40]}...")

            reply = generate_reply(sender_name, context, msg_content)
            if not reply:
                print(f"  [{name}] 无法生成回复")
                continue

            print(f"  [{name}] 对方: {msg_content[:50]}")
            print(f"  [{name}] 我方: {reply[:50]}")

            try:
                if await browser_send_message(talker_id, reply, cookies_file):
                    record_user_msg_replied(name, session_key, latest_msg)
                    _log_dm_action(talker_id, sender_name, reply)
                    print(f"  [{name}] 回复已发送")
                    processed += 1
                else:
                    print(f"  [{name}] 发送失败")
            except Exception as e:
                print(f"  [{name}] 发送异常: {e}")

        except Exception as e:
            print(f"  [{name}] 处理会话出错: {e}")
            continue

    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    lock_fd.close()
    print(f"  [{name}] 处理完成，共发送 {processed} 条回复")
    return processed

# ==================== Main ====================
async def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] B站私信监控Bot启动（多账号版）")

    accounts = load_all_accounts()
    if not accounts:
        print("没有可用账号，退出")
        return

    total = 0
    for acc in accounts:
        # 除了主账号，其他账号1小时只能运行1次
        if "20岁还没赚够100" not in acc["name"]:
            cd_file = Path(f"/tmp/bili_dm_cd_{acc['name'][:8]}.json")
            try:
                if cd_file.exists():
                    last = json.loads(cd_file.read_text()).get("last_run_ts", 0)
                    if time.time() - last < 3600:
                        print(f"[{acc['name']}] ⏳ 冷却中（距上次运行不足1小时），跳过")
                        continue
            except:
                pass
        try:
            n = await process_account(acc)
            total += n if n else 0
            # 保存cooldown
            if n > 0 and "20岁还没赚够100" not in acc["name"]:
                cd_file = Path(f"/tmp/bili_dm_cd_{acc['name'][:8]}.json")
                cd_file.write_text(json.dumps({"last_run_ts": time.time()}))
        except Exception as e:
            print(f"[{acc['name']}] 执行出错: {e}")
            continue

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 全部完成，总计发送 {total} 条回复")

if __name__ == "__main__":
    asyncio.run(main())
