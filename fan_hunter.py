#!/usr/bin/env python3
"""
粉丝发掘脚本 - 通过点赞吸引潜在粉丝

逻辑：
1. 搜索目标领域的热门视频
2. 抓取视频下的评论者（活跃用户）
3. 获取这些用户最近7天的评论
4. 给每个用户最近7条评论点赞（留下印象）
5. 记录已点赞的评论，避免重复操作

目标：发现并激活潜在粉丝，通过互动建立连接
"""

import os, sys, re, json, time, random, subprocess, requests
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

# ── 配置 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = Path("/Users/kaikai/ai_video_project/fan_hunter")
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# MiniMax API 配置（从 settings.json 读取环境变量）
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "MiniMax-M2.7")

# 从 fengge_pipeline.py 加载 cookies（更稳定，避免硬编码）
COOKIES_FILE = Path("/Users/kaikai/scripts/video/fengge_pipeline.py")

def load_bili_cookies():
    """从 fengge_pipeline.py 提取 cookies"""
    if COOKIES_FILE.exists():
        content = COOKIES_FILE.read_text()
        # 简单解析 COOKIES = {...} 块
        import ast
        try:
            # 提取 COOKIES 赋值语句
            start = content.find("COOKIES = {")
            if start >= 0:
                # 找到配对的括号
                brace_start = content.find("{", start)
                depth = 0
                end = brace_start
                for i, c in enumerate(content[brace_start:]):
                    if c == "{": depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            end = brace_start + i + 1
                            break
                cookie_str = content[start:end]
                ns = {}
                exec(cookie_str, ns)
                return ns.get("COOKIES", {})
        except Exception as e:
            print(f"解析 cookies 失败: {e}")
    return {}

BILI_COOKIES = load_bili_cookies()

# 搜索关键词（目标领域）
SEARCH_KEYWORDS = [
    "环球旅行", "出境游", "国外生活", "海外华人",
    "峰哥", "信息差", "跨境电商", "海外工作",
    "签证攻略", "移民", "留学", "海外定居",
]

# 每日限制
MAX_USERS_PER_RUN = 20      # 每次最多处理20个用户
MAX_LIKES_PER_USER = 7     # 每个用户最多点赞7条评论
DAYS_LOOKBACK = 7           # 只看最近7天的评论

# 轻量模式（定时任务用）
LIGHT_MODE_USERS = 5        # 轻量模式每次处理5个用户

# 延时设置（避免风控）
MIN_DELAY = 2.0
MAX_DELAY = 5.0

# ── AI 搜索词生成 ────────────────────────────────────────────────────────────

def generate_keywords_with_ai(topic: str, num: int = 8) -> List[str]:
    """调用 MiniMax AI 模型生成搜索关键词"""
    if not ANTHROPIC_AUTH_TOKEN:
        log("⚠️ 未配置 ANTHROPIC_AUTH_TOKEN，使用默认关键词")
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(
            base_url=ANTHROPIC_BASE_URL,
            api_key=ANTHROPIC_AUTH_TOKEN,
        )

        prompt = f"""你是一个B站内容运营专家。请根据以下主题生成 {num} 个适合在B站搜索的关键词，用于发现目标受众。

主题：{topic}

要求：
1. 关键词要多样化，覆盖不同角度（人群、地点、行为、话题）
2. 每个关键词2-6个字，适合B站搜索
3. 输出JSON数组格式，只输出关键词，不要其他内容
4. 确保关键词有搜索价值，能找到活跃用户

示例输出：["环球旅行", "海外工作", "移民生活", "留学日常"]"""

        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"}
        )

        result_text = ""
        for block in response.content:
            if hasattr(block, 'text') and block.text:
                result_text = block.text
                break
        # 尝试解析 JSON 数组
        if result_text.startswith("["):
            keywords = json.loads(result_text)
            if isinstance(keywords, list) and keywords:
                log(f"  🤖 AI 生成关键词: {keywords}")
                return keywords

        log(f"  ⚠️ AI 返回格式异常，使用默认关键词")
    except Exception as e:
        log(f"  ⚠️ AI 调用失败: {e}")

    return []

# 搜索关键词（默认列表，AI 生成失败时备用）
DEFAULT_KEYWORDS = [
    "环球旅行", "出境游", "国外生活", "海外华人",
    "峰哥", "信息差", "跨境电商", "海外工作",
    "签证攻略", "移民", "留学", "海外定居",
]

# AI 主题（用于生成更精准的关键词）
AI_TOPIC = "海外华人在B站的热门内容方向"

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def rand_delay():
    """随机延时，避免风控"""
    t = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(t)

def get_session():
    """创建请求session"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    })
    return s

def search_videos(keyword: str, session: requests.Session, limit: int = 10) -> list:
    """搜索视频，返回 [{bvid, title, aid}]"""
    try:
        import urllib.parse
        q = urllib.parse.quote(keyword)
        r = session.get(
            f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={q}&page=1&page_size={limit}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com",
            },
            timeout=10
        )
        data = r.json()
        if data.get("code") != 0:
            return []
        results = []
        for item in data.get("data", {}).get("result", []):
            if item.get("result_type") == "video":
                for v in item.get("data", [])[:3]:
                    bvid = v.get("bvid", "")
                    title = re.sub(r'<[^>]+>', '', v.get("title", ""))
                    aid = v.get("aid", 0)
                    if bvid:
                        results.append({"bvid": bvid, "title": title, "aid": aid})
        return results
    except Exception as e:
        log(f"  ⚠️ 搜索失败[{keyword[:10]}]: {e}")
        return []

def get_video_comments(bvid: str, session: requests.Session, cookies: dict, limit: int = 20) -> list:
    """获取视频评论，返回评论者信息"""
    try:
        r = session.get(
            f"https://api.bilibili.com/x/v2/reply?type=1&oid={bvid}&pn=1&ps={limit}&sort=2",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{bvid}",
            },
            cookies=cookies,
            timeout=10
        )
        data = r.json()
        if data.get("code") != 0:
            return []
        comments = []
        replies = data.get("data", {}).get("replies", []) or []
        for reply in replies:
            if not reply:
                continue
            uname = reply.get("member", {}).get("uname", "")
            uid = reply.get("member", {}).get("mid", 0)
            rpid = reply.get("rpid", 0)
            ctime = reply.get("ctime", 0)
            content = re.sub(r'<[^>]+>', '', reply.get("content", {}).get("message", ""))
            if uid and uname and rpid:
                # oid 是评论所在视频的 aid
                oid = reply.get("oid", 0)
                comments.append({
                    "uid": str(uid),
                    "uname": uname,
                    "rpid": rpid,
                    "ctime": ctime,
                    "content": content[:50],
                    "bvid": bvid,
                    "oid": oid,
                })
        return comments
    except Exception as e:
        return []

def get_user_recent_comments(uid: str, session: requests.Session, cookies: dict, days: int = 7) -> list:
    """获取用户最近7天的评论"""
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp())
    try:
        # 搜索该用户的评论
        r = session.get(
            f"https://api.bilibili.com/x/space/comment?uid={uid}&pn=1&ps=20&type=1",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": f"https://space.bilibili.com/{uid}",
            },
            cookies=cookies,
            timeout=10
        )
        d = r.json()
        comments = []
        if d.get("code") == 0:
            for c in d.get("data", {}).get("comments", []) or []:
                ctime = c.get("ctime", 0)
                rpid = c.get("rpid", 0)
                oid = c.get("oid", 0)
                content = re.sub(r'<[^>]+>', '', c.get("content", ""))
                if ctime >= cutoff and rpid and oid:
                    comments.append({
                        "rpid": rpid,
                        "oid": oid,
                        "ctime": ctime,
                        "content": content[:50],
                    })
        return comments
    except Exception as e:
        return []

def like_comment(rpid: int, oid: int, session: requests.Session, cookies: dict) -> bool:
    """点赞评论"""
    try:
        csrf = cookies.get("bili_jct", "")
        r = session.post(
            "https://api.bilibili.com/x/v2/reply/action",
            data={
                "oid": oid,
                "type": 1,
                "rpid": rpid,
                "action": 1,  # 1=点赞
                "csrf": csrf,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
            },
            cookies=cookies,
            timeout=10
        )
        data = r.json()
        code = data.get("code")
        if code == 0:
            return True
        elif code == -101:
            log("  ⚠️ 未登录，无法点赞")
            return False
        else:
            log(f"  ⚠️ 点赞失败 code={code}")
            return False
    except Exception as e:
        log(f"  ⚠️ 点赞异常: {e}")
        return False

def get_liked_comments_file() -> Path:
    """获取已点赞记录文件"""
    return OUTPUT_DIR / "liked_comments.json"

def load_liked_comments() -> set:
    """加载已点赞的评论ID"""
    f = get_liked_comments_file()
    if f.exists():
        try:
            with open(f) as fp:
                data = json.load(fp)
                return set(data.get("rpid_list", []))
        except:
            pass
    return set()

def save_liked_comment(rpid: int):
    """记录已点赞的评论"""
    f = get_liked_comments_file()
    liked = load_liked_comments()
    liked.add(rpid)
    with open(f, 'w') as fp:
        json.dump({"rpid_list": list(liked), "updated": datetime.now().isoformat()}, fp)

# ── 主流程 ──────────────────────────────────────────────────────────────────

def main(light_mode: bool = False):
    limit = LIGHT_MODE_USERS if light_mode else MAX_USERS_PER_RUN
    log(f"\n{'='*60}")
    log(f"🔥 粉丝发掘脚本启动{' [轻量模式]' if light_mode else ''}")
    log(f"{'='*60}")

    cookies = BILI_COOKIES
    if not cookies.get("SESSDATA"):
        log("❌ 未找到有效的B站 Cookie，无法执行点赞")
        return

    session = get_session()

    # ── 阶段1: 搜索目标领域视频 ─────────────────────────────────────
    log(f"\n① 搜索目标领域视频...")

    # 尝试用 AI 生成关键词，失败则用默认列表
    search_keywords = generate_keywords_with_ai(AI_TOPIC, num=10)
    if not search_keywords:
        search_keywords = DEFAULT_KEYWORDS
        log(f"  使用默认关键词: {search_keywords[:3]}...")

    all_videos = []
    for kw in search_keywords:
        videos = search_videos(kw, session, limit=5)
        all_videos.extend(videos)
        log(f"  关键词「{kw}」: 找到 {len(videos)} 个视频")
        rand_delay()

    # 去重
    seen = set()
    unique_videos = []
    for v in all_videos:
        if v["bvid"] not in seen:
            seen.add(v["bvid"])
            unique_videos.append(v)
    log(f"  共 {len(unique_videos)} 个去重视频")

    # ── 阶段2: 抓取评论者 ───────────────────────────────────────────
    log(f"\n② 抓取评论者...")
    user_comments = defaultdict(list)  # uid -> [comments]
    for v in unique_videos[:15]:  # 最多处理15个视频
        comments = get_video_comments(v["bvid"], session, cookies, limit=20)
        for c in comments:
            uid = c["uid"]
            user_comments[uid].append({
                "uname": c["uname"],
                "rpid": c["rpid"],
                "oid": c.get("oid", 0) or v.get("aid", 0),
                "bvid": c["bvid"],
                "content": c["content"],
                "ctime": c["ctime"],
            })
        log(f"  [{v['bvid']}] {v['title'][:20]}: {len(comments)} 条评论")
        rand_delay()

    log(f"  共发现 {len(user_comments)} 个评论用户")

    # ── 阶段3: 获取用户最近评论并点赞 ─────────────────────────────
    log(f"\n③ 挖掘用户最近7天评论并点赞...")
    liked_file = get_liked_comments_file()
    liked_rpids = load_liked_comments()
    log(f"  已有点赞记录: {len(liked_rpids)} 条")

    total_liked = 0
    total_users = 0
    cutoff_time = int((datetime.now() - timedelta(days=DAYS_LOOKBACK)).timestamp())

    user_list = list(user_comments.keys())
    random.shuffle(user_list)

    for uid in user_list[:limit]:
        comments = user_comments[uid]
        uname = comments[0]["uname"] if comments else uid

        # 获取用户最近的评论（7天内）
        recent = get_user_recent_comments(uid, session, cookies, days=DAYS_LOOKBACK)

        # 也加上从视频评论里抓到的
        all_recent = list(recent)
        seen_rpids = set(c["rpid"] for c in recent)
        for c in comments:
            if c["ctime"] >= cutoff_time and c["rpid"] not in liked_rpids and c["rpid"] not in seen_rpids:
                all_recent.append(c)
                seen_rpids.add(c["rpid"])

        # 去重且过滤
        seen_rpids = set()
        to_like = []
        for c in all_recent:
            if c["rpid"] in seen_rpids or c["rpid"] in liked_rpids:
                continue
            seen_rpids.add(c["rpid"])
            to_like.append(c)

        # 限制每个用户点赞数
        to_like = to_like[:MAX_LIKES_PER_USER]

        if not to_like:
            log(f"  ⏭ {uname}: 无新评论可点赞")
            continue

        log(f"\n  @{uname} ({uid}): 点赞 {len(to_like)} 条")
        for c in to_like:
            ok = like_comment(c["rpid"], c["oid"], session, cookies)
            if ok:
                save_liked_comment(c["rpid"])
                liked_rpids.add(c["rpid"])
                log(f"    ✅ {c['content'][:25]}...")
                total_liked += 1
            else:
                log(f"    ⚠️ 点赞失败 rpid={c['rpid']}")
            rand_delay()

        total_users += 1

    # ── 完成 ─────────────────────────────────────────────────────────
    log(f"\n{'='*60}")
    log(f"✅ 粉丝发掘完成")
    log(f"   处理用户: {total_users}")
    log(f"   总点赞: {total_liked}")
    log(f"   已记录: {len(liked_rpids)} 条")
    log(f"{'='*60}")

if __name__ == "__main__":
    import sys
    light = "--light" in sys.argv or "-l" in sys.argv
    main(light_mode=light)