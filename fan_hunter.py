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

import os, sys, re, json, time, random, subprocess, requests, urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import List, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 配置 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = Path("/Users/kaikai/ai_video_project/fan_hunter")
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# MiniMax API 配置（从 settings.json 读取环境变量）
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "MiniMax-M2.7")

def load_bili_cookies():
    """从 /Users/kaikai/scripts/20岁还没赚够100w_cookies.txt 加载（兼容 list 和 dict 格式）"""
    try:
        with open('/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data}
        elif isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f"加载 cookies 失败: {e}")
        return {}

BILI_COOKIES = load_bili_cookies()

# 搜索关键词（目标领域）
SEARCH_KEYWORDS = [
    "环球旅行", "出境游", "国外生活", "海外华人",
    "峰哥", "信息差", "跨境电商", "海外工作",
    "签证攻略", "移民", "留学", "海外定居",
]

# 每日限制
MAX_USERS_PER_RUN = 7       # 每次处理7个目标用户
MAX_LIKES_PER_USER = 10    # 每个用户最多点赞10条评论 → 7×10=70
MAX_VIDEOS_TO_SCRAPE = 30  # 最多抓取30个视频
MAX_COMMENT_USERS = 300    # 最多从视频评论中收集300个用户
DAYS_LOOKBACK = 60            # 60天内，增加可点赞内容
MIN_COMMENTS_THRESHOLD = 1   # 用户至少要有1条评论才纳入候选

# 轻量模式（定时任务用）
LIGHT_MODE_USERS = 10        # 轻量模式每次处理10个目标用户

# 延时设置（避免风控）
MIN_DELAY = 1.0
MAX_DELAY = 3.0

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
                # 安全过滤：去掉疑似提示词泄漏的词
                leak_patterns = [
                    "用户让我", "以B站", "回复粉丝", "直接输出",
                    "角色设定", "系统提示", "结合上下文", "请以",
                ]
                safe = [k for k in keywords if not any(p in k for p in leak_patterns)]
                if safe:
                    log(f"  🤖 AI 生成关键词: {safe}")
                    return safe

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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def rand_delay():
    """随机延时，避免风控"""
    t = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(t)

def get_session():
    """创建请求session（带自动重试）"""
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    })
    return s

def search_videos(keyword: str, session: requests.Session, limit: int = 50) -> list:
    """搜索视频，返回 [{bvid, title, aid}]"""
    try:
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
                for v in item.get("data", [])[:10]:
                    bvid = v.get("bvid", "")
                    title = re.sub(r'<[^>]+>', '', v.get("title", ""))
                    aid = v.get("aid", 0)
                    if bvid:
                        results.append({"bvid": bvid, "title": title, "aid": aid})
        return results
    except Exception:
        return []


def get_popular_videos(session: requests.Session, limit: int = 100, cookies: dict = None) -> list:
    """获取全站热门视频（不限制关键词）"""
    try:
        r = session.get(
            "https://api.bilibili.com/x/web-interface/ranking/v2?type=all&pn=1&ps=50",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com",
            },
            cookies=cookies,
            timeout=10
        )
        data = r.json()
        if data.get("code") == 0:
            videos = []
            for v in data.get("data", {}).get("list", [])[:limit]:
                videos.append({"bvid": v.get("bvid", ""), "title": v.get("title", ""), "aid": v.get("aid", 0)})
            return videos
        return []
    except Exception:
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
    """获取用户最近N天的评论"""
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp())
    try:
        # 使用 /x/v2/reply/main?mode=3 获取用户评论历史，oid=uid, type=1
        r = session.get(
            f"https://api.bilibili.com/x/v2/reply/main?mode=3&oid={uid}&type=1&ps=20&jsonp=jsonp",
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
            for c in d.get("data", {}).get("replies", []) or []:
                ctime = c.get("ctime", 0)
                rpid = c.get("rpid", 0)
                oid = c.get("oid", 0)
                content = re.sub(r'<[^>]+>', '', c.get("content", ""))
                uname = c.get("member", {}).get("uname", "")
                if ctime >= cutoff and rpid and oid:
                    comments.append({
                        "rpid": rpid,
                        "oid": oid,
                        "ctime": ctime,
                        "content": content[:50],
                        "uname": uname,
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
        except Exception:
            pass
    return set()

def save_liked_comment(rpid: int):
    """记录已点赞的评论"""
    f = get_liked_comments_file()
    liked = load_liked_comments()
    liked.add(rpid)
    with open(f, 'w') as fp:
        json.dump({"rpid_list": list(liked), "updated": datetime.now().isoformat()}, fp)

def log_like_detail(uname: str, content: str, video_title: str, bvid: str, rpid: int):
    """记录每次点赞详情到 CSV"""
    csv_path = OUTPUT_DIR / "like_log.csv"
    file_exists = csv_path.exists()
    try:
        with open(csv_path, 'a', encoding='utf-8') as f:
            if not file_exists:
                f.write("timestamp,uname,content,video_title,bvid,rpid\n")
            # 转义引号
            content_escaped = content.replace('"', '""')
            video_title_escaped = video_title.replace('"', '""')
            f.write(f'"{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}","{uname}","{content_escaped}","{video_title_escaped}","{bvid}","{rpid}"\n')
    except Exception as e:
        log(f"  ⚠️ 写点赞日志失败: {e}")

def load_replied_uids() -> set:
    """从回复脚本的历史记录中加载已回复过的用户UID"""
    replied_file = Path("/Users/kaikai/.hermes/instances/video_processor/bili_replied_real.json")
    if not replied_file.exists():
        replied_file = Path("/tmp/bili_replied_real.json")
    if not replied_file.exists():
        return set()
    try:
        with open(replied_file) as f:
            data = json.load(f)
        # keys are source_ids which are rpid strings; we don't have uid directly
        # Instead load from the replied store to get user nicknames and map to uids
        return set()
    except Exception:
        return set()

def get_replied_me_uids(session: requests.Session, cookies: dict) -> set:
    """获取所有回复过我（给我发过私信/评论）的用户UID"""
    uids = set()
    try:
        for page in range(1, 10):
            r = session.get(
                f"https://api.bilibili.com/x/msgfeed/reply?pn={page}&ps=20",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
                cookies=cookies,
                timeout=10
            )
            items = r.json().get('data', {}).get('items', [])
            if not items:
                break
            for item in items:
                user = item.get('user', {})
                uid = str(user.get('mid', ''))
                if uid:
                    uids.add(uid)
            time.sleep(0.5)
    except Exception:
        pass
    return uids


def get_liked_me_uids(session: requests.Session, cookies: dict) -> set:
    """获取最近点赞过我内容的用户UID（从我的通知列表获取）"""
    uids = set()
    try:
        for page in range(1, 5):
            r = session.get(
                f"https://api.bilibili.com/x/msgfeed/like?pn={page}&ps=20",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
                cookies=cookies,
                timeout=10
            )
            items = r.json().get('data', {}).get('items', [])
            if not items:
                break
            for item in items:
                user = item.get('user', {})
                uid = str(user.get('mid', ''))
                if uid:
                    uids.add(uid)
            time.sleep(0.5)
    except Exception:
        pass
    return uids


def get_dm_uids(session: requests.Session, cookies: dict) -> set:
    """获取发过私信给我的用户UID"""
    uids = set()
    try:
        for page in range(1, 5):
            r = session.get(
                f"https://api.bilibili.com/x/msgfeed/private?pn={page}&ps=20",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
                cookies=cookies,
                timeout=10
            )
            items = r.json().get('data', {}).get('items', [])
            if not items:
                break
            for item in items:
                user = item.get('user', {})
                uid = str(user.get('mid', ''))
                if uid:
                    uids.add(uid)
            time.sleep(0.5)
    except Exception:
        pass
    return uids


def get_my_followers(session: requests.Session, cookies: dict) -> set:
    """获取我的关注列表用户UID（已经互相关注的）"""
    uids = set()
    try:
        mid = cookies.get("DedeUserID", "")
        if not mid:
            return uids
        for page in range(1, 5):
            r = session.get(
                f"https://api.bilibili.com/x/relation/followers?mid={mid}&pn={page}&ps=20",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
                cookies=cookies,
                timeout=10
            )
            items = r.json().get('data', {}).get('list', [])
            if not items:
                break
            for item in items:
                uid = str(item.get('mid', ''))
                if uid:
                    uids.add(uid)
            time.sleep(0.5)
    except Exception:
        pass
    return uids


# ── 主流程 ──────────────────────────────────────────────────────────────────

def score_user(uid: str, comments: list, keyword_match_count: int, interaction_level: int = 0) -> float:
    """计算用户成为粉丝的可能性分数
    interaction_level: 0=无互动, 1=点赞过我, 2=回复过我, 3=私信过我, 4=已互关
    """
    if not comments:
        return 0.0

    now = int(time.time())
    recent_cutoff = now - 3 * 86400  # 3天内
    week_cutoff = now - 7 * 86400     # 7天内

    score = 0.0

    # 最高优先级：互动过的用户
    priority_boost = {
        4: 5000.0,   # 已互关
        3: 3000.0,   # 发过私信
        2: 2000.0,   # 回复过我
        1: 1000.0,   # 点赞过我
    }
    score += priority_boost.get(interaction_level, 0.0)

    # 信号1: 评论数量（越活跃越可能成为粉丝）
    comment_count = len(comments)
    score += min(comment_count / 10, 2.0) * 1.0

    # 信号2: 最近活动时间（越近越可能看到你的点赞）
    recent_comments = [c for c in comments if c.get("ctime", 0) >= recent_cutoff]
    very_recent = [c for c in comments if c.get("ctime", 0) >= week_cutoff]
    score += len(recent_comments) * 3.0 + len(very_recent) * 1.0

    # 信号3: 内容关键词匹配（评论涉及目标领域的）
    target_keywords = ["峰哥", "环球", "出国", "海外", "信息差", "移民", "留学", "签证", "旅游", "跨境", "定居"]
    matched = 0
    for c in comments:
        content = c.get("content", "").lower()
        for kw in target_keywords:
            if kw.lower() in content:
                matched += 1
                break
    score += min(matched, 10) * 0.5

    # 信号4: 在目标视频评论（峰哥等核心关键词视频）
    score += keyword_match_count * 2.0

    return score


def main(light_mode: bool = False):
    target_users_limit = LIGHT_MODE_USERS if light_mode else MAX_USERS_PER_RUN
    log(f"\n{'='*60}")
    log(f"🔥 粉丝发掘脚本启动{' [轻量模式]' if light_mode else ''}")
    log(f"{'='*60}")

    cookies = BILI_COOKIES
    if not cookies.get("SESSDATA"):
        log("❌ 未找到有效的B站 Cookie，无法执行点赞")
        return

    session = get_session()

    # ── 阶段1: 获取热门视频 + 关键词搜索目标领域视频 ─────────────────
    log(f"\n① 获取热门视频 + 搜索目标领域...")

    all_videos = get_popular_videos(session, limit=100, cookies=cookies)
    log(f"  获取到 {len(all_videos)} 个热门视频")

    # 额外：用关键词搜索目标领域视频
    TARGET_KEYWORDS = [
        "环球旅行", "出境游", "国外生活", "海外华人",
        "峰哥", "信息差", "跨境电商", "海外工作",
        "签证攻略", "移民", "留学", "海外定居",
    ]
    seen = set(v["bvid"] for v in all_videos)
    keyword_videos = []
    for kw in TARGET_KEYWORDS:
        results = search_videos(kw, session, limit=20)
        for v in results:
            if v["bvid"] not in seen:
                seen.add(v["bvid"])
                keyword_videos.append(v)
                if len(keyword_videos) >= 50:
                    break
        if len(keyword_videos) >= 50:
            break

    log(f"  关键词找到 {len(keyword_videos)} 个视频")

    # 合并后去重，优先保留关键词视频（目标领域），最多 MAX_VIDEOS_TO_SCRAPE 个
    merged = keyword_videos.copy()
    seen2 = set(v["bvid"] for v in merged)
    for v in all_videos:
        if v["bvid"] not in seen2:
            merged.append(v)
            if len(merged) >= MAX_VIDEOS_TO_SCRAPE:
                break
    unique_videos = merged
    log(f"  去重后共 {len(unique_videos)} 个视频（目标领域优先）")

    # ── 阶段1.5: 获取与我互动过的用户（最高优先级）─────────
    log(f"\n①.5 获取与我互动过的用户（最高优先级）...")
    replied_me_uids = get_replied_me_uids(session, cookies)
    liked_me_uids = get_liked_me_uids(session, cookies)
    dm_uids = get_dm_uids(session, cookies)
    follower_uids = get_my_followers(session, cookies)

    # 计算每个用户的互动等级
    all_interactive_uids = replied_me_uids | liked_me_uids | dm_uids | follower_uids
    uid_interaction_level = {}
    for uid in all_interactive_uids:
        level = 0
        if uid in follower_uids:
            level = max(level, 4)
        if uid in dm_uids:
            level = max(level, 3)
        if uid in replied_me_uids:
            level = max(level, 2)
        if uid in liked_me_uids:
            level = max(level, 1)
        uid_interaction_level[uid] = level

    log(f"  回复过我: {len(replied_me_uids)} | 点赞过我: {len(liked_me_uids)} | 私信过我: {len(dm_uids)} | 互关: {len(follower_uids)}")

    # ── 阶段2: 抓取评论者并打分 ─────────────────────────────────
    log(f"\n② 抓取评论者并计算粉丝潜力分数...")
    user_comments = defaultdict(list)  # uid -> [comments]

    for v in unique_videos:
        if len(user_comments) >= MAX_COMMENT_USERS:
            log(f"  已收集 {MAX_COMMENT_USERS} 个用户，停止抓取更多视频评论")
            break
        comments = get_video_comments(v["bvid"], session, cookies, limit=20)
        for c in comments:
            uid = c["uid"]
            c["uname"] = c.get("uname", "")
            c["bvid"] = v["bvid"]
            c["title"] = v["title"]
            user_comments[uid].append(c)
        log(f"  [{v['bvid']}] {v['title'][:20]}: {len(comments)} 条评论 (累计用户: {len(user_comments)})")
        rand_delay()

    log(f"  共发现 {len(user_comments)} 个评论用户（限制 {MAX_COMMENT_USERS} 个）")

    # ── 阶段3: 打分排序，筛选Top用户 ──────────────────────────
    log(f"\n③ 计算粉丝潜力分数并排序...")
    user_scores = []
    for uid, comments in user_comments.items():
        if len(comments) < MIN_COMMENTS_THRESHOLD:
            continue
        interaction_level = uid_interaction_level.get(uid, 0)
        score = score_user(uid, comments, keyword_match_count=0, interaction_level=interaction_level)
        uname = comments[0].get("uname", uid) if comments else uid
        user_scores.append((score, uid, uname, comments))

    # 互动过但不在视频评论里的用户，直接从他们个人空间拉取评论
    MAX_INTERACTIVE_FETCH = 50  # 最多从个人空间拉取50个互动用户的评论
    for uid in list(all_interactive_uids)[:MAX_INTERACTIVE_FETCH]:
        if uid not in user_comments:
            interaction_level = uid_interaction_level.get(uid, 0)
            if interaction_level > 0:
                recent = get_user_recent_comments(uid, session, cookies, days=DAYS_LOOKBACK)
                if recent:
                    uname = recent[0].get("uname", uid)
                    user_scores.append((score_user(uid, recent, keyword_match_count=0, interaction_level=interaction_level), uid, uname, recent))
    log(f"  额外从个人空间拉取了 {min(len(all_interactive_uids), MAX_INTERACTIVE_FETCH)} 个互动用户")

    # 按分数降序
    user_scores.sort(key=lambda x: x[0], reverse=True)

    # 将互动过的用户排到前面
    interactive_users = [(s, u, n, c) for s, u, n, c in user_scores if uid_interaction_level.get(u, 0) > 0]
    non_interactive_users = [(s, u, n, c) for s, u, n, c in user_scores if uid_interaction_level.get(u, 0) == 0]

    # 互动优先，最多考察 target_users_limit*10 个用户（确保有足够候选找到未点赞评论）
    all_users = interactive_users + non_interactive_users
    all_users = all_users[:target_users_limit * 10]

    log(f"  候选用户 {len(all_users)} 个（互动: {len(interactive_users)}，非互动: {len(non_interactive_users)}）")

    # ── 阶段4: 点赞直到成功70个 ───────────────────────────────
    TARGET_LIKES = 70
    log(f"\n④ 对 {len(all_users)} 个用户进行点赞（目标：成功 {TARGET_LIKES} 个赞）...")
    liked_file = get_liked_comments_file()
    liked_rpids = load_liked_comments()
    log(f"  已有点赞记录: {len(liked_rpids)} 条")

    total_liked = 0
    total_users = 0
    cutoff_time = int((datetime.now() - timedelta(days=DAYS_LOOKBACK)).timestamp())

    for score, uid, uname, comments in all_users:
        if total_liked >= TARGET_LIKES:
            log(f"  ✅ 已达到目标 {TARGET_LIKES} 个赞，停止点赞")
            break

        # 互动用户优先，直接拉取个人空间评论（他们的回复可能不在热门视频里）
        if uid_interaction_level.get(uid, 0) > 0:
            # 互动用户：直接拉取个人空间评论
            recent = get_user_recent_comments(uid, session, cookies, days=DAYS_LOOKBACK)
            user_to_like = [c for c in recent if c["rpid"] not in liked_rpids]
            if not user_to_like:
                # 尝试视频评论里已有的
                user_to_like = [c for c in comments if c.get("ctime", 0) >= cutoff_time and c["rpid"] not in liked_rpids]
        else:
            # 普通用户：先用视频评论，再用个人空间补充
            user_to_like = [c for c in comments if c.get("ctime", 0) >= cutoff_time and c["rpid"] not in liked_rpids]
            if len(user_to_like) < MAX_LIKES_PER_USER:
                recent = get_user_recent_comments(uid, session, cookies, days=DAYS_LOOKBACK)
                for c in recent:
                    if c["rpid"] not in liked_rpids and c not in user_to_like:
                        user_to_like.append(c)
            user_to_like = user_to_like[:MAX_LIKES_PER_USER]

        if not user_to_like:
            log(f"  ⏭ @{uname} ({uid}): 无可点赞评论（视频评论{len(comments)}条，近期{len([c for c in comments if c.get('ctime',0)>=cutoff_time])}条，已赞{len([c for c in comments if c['rpid'] in liked_rpids])}条）")
            continue

        log(f"\n  @{uname} ({uid}): 点赞 {len(user_to_like)} 条 [分数:{score:.1f}]")
        for c in user_to_like:
            ok = like_comment(c["rpid"], c["oid"], session, cookies)
            if ok:
                save_liked_comment(c["rpid"])
                liked_rpids.add(c["rpid"])
                log_like_detail(
                    uname=c.get("uname", uname),
                    content=c.get("content", ""),
                    video_title=c.get("title", ""),
                    bvid=c.get("bvid", ""),
                    rpid=c["rpid"]
                )
                log(f"    ✅ {c['content'][:25]}...")
                total_liked += 1
            else:
                log(f"    ⚠️ 点赞失败 rpid={c['rpid']}，加入跳过列表")
                liked_rpids.add(c["rpid"])
            rand_delay()

        total_users += 1

    # ── 完成 ─────────────────────────────────────────────────────
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