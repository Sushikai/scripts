#!/usr/bin/env python3
"""
B站视频哲学评论Bot
自动巡查自己的视频，如果没有评论（或评论数少），用LLM生成哲学内容并发送评论。
使用Playwright直接抓取页面，绕过WBI签名风控。
"""
import asyncio
import json
import os
import re
import httpx
import requests
from playwright.async_api import async_playwright
from bilibili_api import comment, Credential
from bilibili_api.comment import CommentResourceType
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COOKIES_FILE = "/tmp/bilibili_cookies.json"
DONE_FILE = "/tmp/bili_philosophy_commented.json"

# 重试 session
_session = requests.Session()
_session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})))

def load_cookies():
    if not os.path.exists(COOKIES_FILE):
        print(f"Cookie 文件不存在: {COOKIES_FILE}")
        return {}
    try:
        with open(COOKIES_FILE, 'r') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data}
        elif isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f"加载 cookies 失败: {e}")
        return {}

def load_done():
    if not os.path.exists(DONE_FILE):
        return {}
    try:
        with open(DONE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_done(done):
    with open(DONE_FILE, 'w') as f:
        json.dump(done, f, ensure_ascii=False)

def generate_philosophy(text, prompt_type="comment"):
    """调用LLM生成哲学风格内容"""
    minimax_key = os.environ.get("MINIMAX_API_KEY")
    if not minimax_key:
        print("    警告: MINIMAX_API_KEY 未设置")
        return None

    if prompt_type == "comment":
        system_prompt = """你是一个深邃的哲学家，正在为B站视频写评论。
风格要求：
- 哲学思辨，富有洞察，50字以内
- 不媚俗，不鸡汤，不装逼
- 读起来像一个人在独立思考后的真实感悟
- 不要emoji，不要太文艺腔
- 可以联系视频标题，但不能太牵强"""
        user_prompt = f"视频标题：{text}\n请为这个视频写一条哲学风格的评论，直接输出内容，不超过50字，不要前缀。"
    else:
        system_prompt = """你是一个深邃的哲学家，正在为B站视频写简介。
风格要求：
- 哲学思辨，富有洞察，80字以内
- 不媚俗，不鸡汤，不装逼
- 读起来像一个人在独立思考后的真实表达
- 不要emoji，不要太文艺腔
- 不要太具体说视频内容，要抽象地概括一种氛围或思考"""
        user_prompt = f"视频标题：{text}\n请为这个视频写一条哲学风格的简介，直接输出内容，不超过80字，不要前缀。"

    # Try MiniMax first
    try:
        resp = httpx.post(
            "https://api.minimax.chat/v1/text/chatcompletion_v2",
            headers={"Authorization": f"Bearer {minimax_key}", "Content-Type": "application/json"},
            json={
                "model": "MiniMax-M2.7",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 150 if prompt_type == "desc" else 100,
                "temperature": 0.9
            },
            timeout=30
        )
        result = resp.json()
        txt = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if txt:
            return txt
    except Exception as e:
        print(f"    MiniMax调用失败: {e}")

    # Fallback to Ollama
    for _model in ["qwen2.5:32b-instruct-q4_K_M", "gemma3:4b", "deepseek-r1:1.5b"]:
        try:
            r = _session.post("http://localhost:11434/v1/chat/completions",
                headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                json={"model": _model, "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ], "max_tokens": 100, "temperature": 0.9, "stream": False},
                timeout=25)
            d = r.json()
            msg = d.get("choices", [{}])[0].get("message", {})
            txt = msg.get("content", "").strip()
            if txt:
                return txt
            reasoning = msg.get("reasoning", "")
            if reasoning:
                txt = reasoning.split("|")[-1].strip()
                txt = re.sub(r'\[.*?\]\s*', '', txt).strip()
                if txt:
                    return txt
        except Exception as e:
            print(f"    Ollama 模型 {_model} 调用失败: {e}")
    return None

async def fetch_video_list(cookies, uid):
    """用Playwright访问空间页，抓取视频bvid/title/comment_count"""
    cookies_list = cookies
    playwright_cookies = []
    for name in ['SESSDATA', 'bili_jct', 'Buvid3', 'DedeUserID', 'buvid_fp']:
        val = cookies_list.get(name)
        if val:
            playwright_cookies.append({
                'name': name,
                'value': val,
                'domain': '.bilibili.com',
                'path': '/'
            })

    videos = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(playwright_cookies)
        page = await context.new_page()

        await page.goto(f'https://space.bilibili.com/{uid}/video', wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(2000)

        for _ in range(3):
            await page.evaluate('window.scrollBy(0, 800)')
            await page.wait_for_timeout(800)

        cards = await page.query_selector_all('.bili-video-card')
        print(f'  找到 {len(cards)} 个视频卡片')

        for card in cards:
            try:
                title_el = await card.query_selector('.bili-video-card__info--title')
                title = await title_el.inner_text() if title_el else ''
                title = title.strip()

                link = await card.get_attribute('href') or ''
                aid_match = re.search(r'/video/(\w+)', link)
                bvid = aid_match.group(1) if aid_match else ''

                stat_els = await card.query_selector_all('.bili-video-card__stats--item')
                comment_count = 0
                if len(stat_els) >= 2:
                    comment_text = await stat_els[1].inner_text()
                    comment_count = int(re.sub(r'\D', '', comment_text)) or 0

                if bvid and title:
                    videos.append({
                        'bvid': bvid,
                        'title': title,
                        'comment': comment_count
                    })
            except Exception as e:
                print(f'  解析卡片失败: {e}')
                continue

        await browser.close()

    return videos

async def update_video_desc(bvid, new_desc, cookies):
    playwright_cookies = []
    for name in ['SESSDATA', 'bili_jct', 'Buvid3', 'DedeUserID']:
        val = cookies.get(name)
        if val:
            playwright_cookies.append({
                'name': name,
                'value': val,
                'domain': '.bilibili.com',
                'path': '/'
            })

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(playwright_cookies)
        page = await context.new_page()

        await page.goto(f'https://www.bilibili.com/video/{bvid}', wait_until='networkidle', timeout=20000)
        await page.wait_for_timeout(1500)

        result = await page.evaluate(f"""
            async () => {{
                const csrf = document.cookie.match(/bili_jct=([^;]+)/)?.[1] || '';
                const aid = window.__INITIAL_STATE__?.videoData?.aid;
                if (!aid || !csrf) return {{code: -1, msg: 'no aid or csrf'}};
                try {{
                    const resp = await fetch('https://api.bilibili.com/x/vas/edit/dynamic', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/x-www-form-urlencoded', 'Referer': location.href}},
                        body: new URLSearchParams({{
                            aid: aid,
                            csrf: csrf,
                            desc: {repr(new_desc)}
                        }})
                    }});
                    return await resp.json();
                }} catch(e) {{ return {{code: -1, msg: e.message}}; }}
            }}
        """)
        print(f"  更新简介结果: {result}")
        await browser.close()
        return result.get('code') == 0

async def post_comment(aid, text, cred):
    try:
        if not str(aid).isdigit():
            from bilibili_api import bvid2aid
            aid = await bvid2aid(aid)
        result = await comment.send_comment(
            text=text,
            oid=aid,
            type_=CommentResourceType.VIDEO,
            credential=cred
        )
        code = result.get('code', -1)
        if code == 0:
            print(f"  评论成功")
        else:
            print(f"  评论失败: code={code}, {result.get('message', '')}")
        return code == 0
    except Exception as e:
        print(f"  评论异常: {e}")
        return False

async def main():
    cookies = load_cookies()
    if not cookies:
        print("无法加载 cookies，退出")
        return
    done = load_done()
    uid = cookies.get('DedeUserID')
    if not uid:
        print("无法获取 DedeUserID，退出")
        return

    print("用Playwright抓取视频列表...")
    videos = await fetch_video_list(cookies, uid)
    print(f"获取到 {len(videos)} 个视频")

    cred = Credential(
        sessdata=cookies['SESSDATA'],
        bili_jct=cookies['bili_jct'],
        buvid3=cookies.get('buvid3')
    )

    needs_comment = [v for v in videos if v.get('comment', 0) < 3]
    print(f"\n评论<3的视频: {len(needs_comment)} 个")

    for v in videos:
        bvid = v['bvid']
        title = v.get('title', '')
        comment_count = v.get('comment', 0)

        print(f"\n处理: {title[:40]} (bvid={bvid}, 评论={comment_count})")

        if bvid in done:
            print("  已处理过，跳过")
            continue

        if comment_count < 3:
            print("  正在生成哲学评论...")
            philosophy = generate_philosophy(title, "comment")
            if not philosophy:
                print("  生成失败，跳过")
                continue
            print(f"  哲学评论: {philosophy}")

            ok = await post_comment(bvid, philosophy, cred)
            if ok:
                done[bvid] = philosophy
                save_done(done)
            await asyncio.sleep(3)

        else:
            done[bvid] = "skipped"
            save_done(done)

    print("\n全部完成！")

if __name__ == "__main__":
    asyncio.run(main())