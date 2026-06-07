#!/usr/bin/env python3
"""
B站评论回复 - 浏览器方案 v18
修复CSRF-111错误：通过Playwright浏览器自动化发送评论回复
"""
import asyncio
import fcntl
import json
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.async_api import async_playwright
# bilibili_api has circular import issues with Python 3.14, implement aid2bvid locally
# Source: bilibili_api.utils.aid_bvid_transformer
_XOR_CODE = 23442827791579
_MASK_CODE = 2251799813685247
_MAX_AID = 1 << 51
_BASE = 58
_BV_LEN = 12
_DATA = ["F","c","w","A","P","N","K","T","M","u","g","3","G","V","5","L","j","7","E","J","n","H","p","W","s","x","4","t","b","8","h","a","Y","e","v","i","q","B","z","6","r","k","C","y","1","2","m","U","S","D","Q","X","9","R","d","o","Z","f"]

def aid2bvid(aid: int) -> str:
    bytes_arr = ["B", "V", "1", "0", "0", "0", "0", "0", "0", "0", "0", "0"]
    bv_idx = _BV_LEN - 1
    tmp = (_MAX_AID | aid) ^ _XOR_CODE
    while int(tmp) != 0:
        bytes_arr[bv_idx] = _DATA[int(tmp % _BASE)]
        tmp //= _BASE
        bv_idx -= 1
    bytes_arr[3], bytes_arr[9] = bytes_arr[9], bytes_arr[3]
    bytes_arr[4], bytes_arr[7] = bytes_arr[7], bytes_arr[4]
    return "".join(bytes_arr)

# Load env
_env_path = os.path.expanduser("~/.env_minimax")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for _line in f:
            _line = _line.strip()
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ[_k] = _v

COOKIES_FILE = "/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt"
REPLIED_FILE = Path("/tmp/bili_replied_real.json")
LOG_FILE     = Path("/tmp/bili_reply_v18.log")
LOCK_FILE    = Path("/tmp/bili_reply_v18.lock")

MINIMAX_API_KEY=os.environ.get("MINIMAX_API_KEY", "") or "sk-cp-ZbbMkbX_A0Rmc3JvDro_uuw8L-g4vc3MnWWCMg__tPSEYB_btil94MTUq9zPncWlSKli5GDkQ7xTB4o_8niFbznFRYNaxTTMVIbVVsje92OiVT6T1rDCGeg"
MINIMAX_BASE    = "https://api.minimax.chat/v1/chat/completions"
OLLAMA_BASE    = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL   = "gemma3:4b"
def _filter_thinking_content(content: str) -> str:
    """统一过滤思考过程泄漏"""
    content = re.sub(r'<think>[\s\S]*?</think>', '', content, flags=re.DOTALL)
    content = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', content, flags=re.DOTALL)
    content = re.sub(r'<reflection>[\s\S]*?</reflection>', '', content, flags=re.DOTALL)
    content = re.sub(r'Thinking Process:[\s\S]*', '', content, flags=re.DOTALL)
    content = re.sub(r'思考过程[:：]?[\s\S]*', '', content, flags=re.DOTALL)
    content = re.sub(r'推理过程[:：]?[\s\S]*', '', content, flags=re.DOTALL)
    content = re.sub(r'\*[\s\S]*?\*', '', content, flags=re.DOTALL)
    content = re.sub(r'\[[\s\S]*?\]', '', content, flags=re.DOTALL)
    content = re.sub(r'#{1,3}\s[^\n]*\n', '', content)
    content = re.sub(r'---[\s\S]*', '', content, flags=re.DOTALL)
    paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
    content = paragraphs[-1] if paragraphs else content.strip()
    leak_patterns = [
        r'^分析', r'^思考', r'^结论', r'^推理', r'^首先', r'^其次', r'^第三',
        r'^第一步', r'^第二步', r'^第三步',
        r'用户让我', r'用户要求', r'请以.*身份', r'以B站UP主',
        r'你是B站UP主', r'你是B站', r'回复粉丝评论', r'角色设定',
        r'系统提示词', r'直接输出回复', r'长度控制', r'就像朋友闲聊',
        r'语气轻松自然', r'结合上下文', r'省略.*解释', r'不要解释',
        r'根据上文', r'根据.*上下文', r'结合粉丝.*聊天',
        r'```', r'\*\*', r'^## ', r'^---\s*$',
        r'^1\.\s', r'^2\.\s', r'^3\.\s', r'^\d+\.\s',
    ]
    for pat in leak_patterns:
        if re.search(pat, content):
            return None
    return content.strip()

MAX_CONTENT_LEN = 80

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

def acquire_lock():
    try:
        lfd = open(LOCK_FILE, 'w')
        fcntl.flock(lfd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lfd.write(str(os.getpid()))
        lfd.flush()
        return lfd
    except BlockingIOError:
        log("[v18] 另一个进程正在运行，退出")
        sys.exit(0)

def sig_handler(s, f):
    save_all()
    log("\n[v18] 收到信号，保存并退出")
    sys.exit(0)

signal.signal(signal.SIGALRM, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)
signal.signal(signal.SIGINT, sig_handler)
signal.alarm(280)

def load_cookies():
    """Load cookies as list of dicts for Playwright. Supports both JSON and Netscape formats."""
    try:
        with open(COOKIES_FILE, encoding="utf-8") as f:
            first_line = f.readline().strip()

        if first_line.startswith("# Netscape") or first_line.startswith("#HTTP"):
            # Netscape format - parse as dict for httpx, return list for playwright
            cookies_dict = {}
            cookies_list = []
            with open(COOKIES_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        name = parts[5]
                        value = parts[6]
                        domain = parts[0]
                        path = parts[2] if len(parts) > 2 else "/"
                        cookies_dict[name] = value
                        cookie = {"name": name, "value": value, "domain": domain, "path": path}
                        cookies_list.append(cookie)
            return cookies_list, cookies_dict
        else:
            # JSON format
            with open(COOKIES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                cookies = data
            elif isinstance(data, dict):
                cookies = [{'name': k, 'value': v} for k, v in data.items()]
            else:
                return [], {}
            for c in cookies:
                if 'domain' not in c:
                    c['domain'] = '.bilibili.com'
                if 'path' not in c:
                    c['path'] = '/'
            cookies_dict = {c['name']: c['value'] for c in cookies}
            return cookies, cookies_dict
    except Exception as e:
        log(f"加载 cookies 失败: {e}")
        return [], {}

def load_cookies_dict():
    """Load cookies as dict for httpx"""
    _, d = load_cookies()
    return d

COOKIES_LIST, COOKIES_DICT = load_cookies()
BILI_JCT = COOKIES_DICT.get("bili_jct", "")

_session = requests.Session()
_session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com"
}

async def call_ollama(prompt: str) -> str:
    """Fallback via local Ollama - multi-model fallback"""
    for _model in ["qwen2.5:32b-instruct-q4_K_M", "gemma3:4b", "deepseek-r1:1.5b"]:
        try:
            import requests
            payload = {
                "model": _model,
                "messages": [
                    {"role": "system", "content": "你是B站UP主，风格幽默、沙雕、亲切，带点自黑。回复要自然、接地气、结合粉丝评论内容往下聊，长度控制在70字以内，适当抛出问题或引导三连。不要重复粉丝原话。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.85,
                "max_tokens": 220,
                "stream": False
            }
            r = requests.post(
                OLLAMA_BASE,
                headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
                json=payload,
                timeout=25
            )
            if r.status_code == 200:
                data = r.json()
                msg = data.get('choices', [{}])[0].get('message', {})
                content = msg.get('content', '').strip()
                if not content:
                    # qwen3.5 reasoning fallback
                    reasoning = msg.get('reasoning', '')
                    if reasoning:
                        content = reasoning.split("|")[-1].strip()
                        content = re.sub(r'\[.*?\]\s*', '', content).strip()
                if content:
                    content = _filter_thinking_content(content)
                    if content and len(content) > MAX_CONTENT_LEN:
                        content = content[:MAX_CONTENT_LEN-1] + '…'
                    return content
        except: pass
    return None

async def call_minimax(prompt: str) -> str:
    try:
        payload = {
            "model": "MiniMax-M2.7",
            "messages": [
                {"role": "system", "name": "B站UP主",
                 "content": "你是B站UP主，风格幽默、沙雕、亲切，带点自黑。回复要自然、接地气、结合粉丝评论内容往下聊，长度控制在70字以内，适当抛出问题或引导三连。不要重复粉丝原话。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.85,
            "max_tokens": 220,
            "stream": False
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                MINIMAX_BASE,
                headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
                json=payload
            )
        log(f"  MiniMax 请求状态: {r.status_code} | 耗时 {r.elapsed.total_seconds():.2f}s")
        if r.status_code == 200:
            data = r.json()
            reply = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if reply:
                reply = _filter_thinking_content(reply)
                if reply and len(reply) > MAX_CONTENT_LEN:
                    reply = reply[:MAX_CONTENT_LEN-1] + '…'
                return reply
        log(f"  ⚠️ MiniMax 返回异常")
    except Exception as e:
        log(f"  ❌ MiniMax 调用异常: {e}")
    return None

async def generate_smart_reply(username: str, user_comment: str) -> str:
    if not user_comment.strip():
        return None
    prompt = f"粉丝昵称：{username}\n粉丝评论：{user_comment}\n\n请直接生成一条自然的B站回复（不要加引号，不要解释）："
    reply = await call_minimax(prompt)
    if not reply:
        # Fallback to Ollama
        reply = await call_ollama(prompt)
    if reply and len(reply.strip()) < 5:
        return None
    return reply

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

async def send_reply_via_browser(oid: str, content: str, cookies_list: list, max_retries=2) -> bool:
    """Send comment reply via Playwright browser automation"""
    for attempt in range(max_retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--disable-web-security', '--no-sandbox']
                )
                context = await browser.new_context()
                await context.add_cookies(cookies_list)
                page = await context.new_page()

                bvid = oid if oid.startswith('BV') else aid2bvid(int(oid))
                url = f"https://www.bilibili.com/video/{bvid}"
                try:
                    await page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2000)
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    log(f"  导航失败: {e}")
                    await browser.close()
                    return False

                # Wait for comment section to load
                await page.wait_for_timeout(6000)

                # Scroll to bottom where comments are
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(3000)

                # Wait until comments are loaded (not showing loading text)
                try:
                    loading = page.locator('text="正在玩命加载…"')
                    if await loading.count() > 0:
                        await loading.wait_for(state="hidden", timeout=10000)
                except:
                    pass

                # Look for comment list items and try to reply to a specific comment
                # First, scroll to comments area
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
                await page.wait_for_timeout(2000)

                # Click first reply button we can find on a comment
                reply_btn_clicked = False
                reply_btn_selectors = [
                    '[class*="reply"] span',
                    '[class*="reply-text"]',
                    'button:has-text("回复")',
                    '[class*="action"] span:has-text("回复")',
                    'span:has-text("回复")',
                ]
                for sel in reply_btn_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.count() > 0 and await btn.is_visible(timeout=2000):
                            await btn.click()
                            reply_btn_clicked = True
                            log(f"  点击回复按钮成功")
                            await page.wait_for_timeout(2000)
                            break
                    except:
                        continue
                    if reply_btn_clicked:
                        break

                if not reply_btn_clicked:
                    log(f"  未找到回复按钮")
                    await browser.close()
                    return False

                await page.wait_for_timeout(2000)

                # Now find the reply textarea in the popup/dialog - it may be inside an iframe
                reply_filled = False
                textarea_selectors = [
                    'textarea[placeholder*="请先"]',
                    'textarea[placeholder*="回复"]',
                    'textarea[placeholder*="评论"]',
                    'textarea[class*="reply"]',
                    'textarea[class*="comment"]',
                    'iframe textarea',
                    'div[class*="reply"] textarea',
                    'textarea',
                ]
                for sel in textarea_selectors:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0 and await el.is_visible(timeout=3000):
                            await el.fill(content)
                            reply_filled = True
                            log(f"  填写回复内容成功: {sel}")
                            await page.wait_for_timeout(500)
                            break
                    except:
                        continue

                if not reply_filled:
                    # Try clicking in iframes
                    try:
                        frames = page.frames
                        for frame in frames:
                            try:
                                for sel in textarea_selectors:
                                    el = frame.locator(sel).first
                                    if await el.count() > 0 and await el.is_visible(timeout=2000):
                                        await el.fill(content)
                                        reply_filled = True
                                        log(f"  填写回复内容成功(iframe): {sel}")
                                        await page.wait_for_timeout(500)
                                        break
                            except:
                                continue
                            if reply_filled:
                                break
                    except Exception as e:
                        log(f"  iframe 查找异常: {e}")

                if reply_filled:
                    await page.wait_for_timeout(1000)
                    # Click send button
                    btn_selectors = [
                        '[class*="send"]',
                        '[class*="submit"]',
                        'button:has-text("发")',
                        'button:has-text("发送")',
                        'span:has-text("发送")',
                    ]
                    for sel in btn_selectors:
                        try:
                            btn = page.locator(sel).first
                            if await btn.count() > 0 and await btn.is_visible(timeout=2000):
                                await btn.click()
                                log(f"  点击发送按钮成功: {sel}")
                                await page.wait_for_timeout(2000)
                                await browser.close()
                                return True
                        except:
                            continue
                    # Try in iframes too
                    if not reply_filled:
                        try:
                            frames = page.frames
                            for frame in frames:
                                for sel in btn_selectors:
                                    try:
                                        btn = frame.locator(sel).first
                                        if await btn.count() > 0 and await btn.is_visible(timeout=2000):
                                            await btn.click()
                                            log(f"  点击发送按钮成功(iframe): {sel}")
                                            await page.wait_for_timeout(2000)
                                            await browser.close()
                                            return True
                                    except:
                                        continue
                        except:
                            pass

                await browser.close()

        except Exception as e:
            log(f"  浏览器发送异常 (attempt {attempt+1}): {e}")
            try:
                await browser.close()
            except:
                pass

    return False

def send_reply_via_api(oid: str, content: str, root: str, parent: str) -> bool:
    """Send reply via B站 API (avoids CSRF-111 by using requests session with cookie jar)"""
    for attempt in range(2):
        try:
            r = _session.post(
                "https://api.bilibili.com/x/v2/reply/add",
                data={
                    "oid": oid, "type": 1, "message": content,
                    "plat": 1, "root": root, "parent": parent,
                    "csrf": BILI_JCT
                },
                headers=HEADERS, cookies=COOKIES_DICT, timeout=10
            )
            j = r.json()
            if j.get('code') == 0:
                log(f"  ✅ API发送成功")
                return True
            elif j.get('code') == 12014:
                log(f"  ⏳ CD限制，等待90秒后重试...")
                time.sleep(90)
                continue
            elif j.get('code') == 12051:
                log(f"  B站回复失败(code=12051)")
                return False
            elif j.get('code') == 12066:
                log(f"  B站拒绝内容(code=12066)，跳过")
                return False
            else:
                log(f"  B站返回错误: {j.get('message')} (code={j.get('code')})")
                return False
        except Exception as e:
            log(f"  API发送异常: {e}")
            if attempt == 0:
                time.sleep(3)
                continue
            return False
    return False

async def process_reply_messages():
    log("══ 开始处理「回复我的」消息（浏览器方案 v18） ══")
    sent_count = 0
    processed = 0

    log(f"已加载 {len(COOKIES_LIST)} 个Cookie")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(1, 7):
            try:
                r = await client.get(
                    f"https://api.bilibili.com/x/msgfeed/reply?pn={page}&ps=20",
                    headers=HEADERS, cookies=COOKIES_DICT
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

                if source_id in _store:
                    continue

                reply_text = await generate_smart_reply(uname, user_comment)
                if reply_text is None:
                    continue

                log(f"  → [{uname}] {user_comment[:35]}...  →  {reply_text[:40]}...")

                # Try API-based sending (bypasses CSRF-111 by using session cookie jar)
                root_id = str(inner.get('root_id', '0'))
                sent = send_reply_via_api(subject_id, reply_text, source_id, source_id)
                if sent:
                    _store[source_id] = time.strftime('%Y-%m-%d %H:%M:%S')
                    save_all()
                    sent_count += 1
                    log(f"  ✅ API发送成功")
                else:
                    log(f"  ❌ API发送失败（未记录，将重试）")
                
                await asyncio.sleep(random.uniform(4.0, 6.0))

    log(f"  本次扫描 {processed} 条消息，智能回复 {sent_count} 条")
    return sent_count

if __name__ == "__main__":
    _lock_fd = acquire_lock()
    load()
    log("=== v18 浏览器方案启动 ===")
    
    sent = asyncio.run(process_reply_messages())
    save_all()
    log(f"✅ 本轮完成：智能回复 {sent} 条 | 历史累计 {len(_store)} 条")
    
    try:
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
    except:
        pass
