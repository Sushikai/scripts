#!/usr/bin/env python3
"""
news_video_v8.py — 信息差视频生产流水线 v8.2

本版本改动：
  1. TTS：Edge TTS zh-CN-YunxiNeural（云希Neural，浑厚有力）+ 语速+30%，音调+5Hz
  2. 视频下载：yt-dlp 最高质量 bv*+ba/best → mp4
  3. 章节逻辑：MAX_TOPICS=10，每话题1视频1章节，不合并
  4. 章节栏底部：字体调小(18px)确保10个话题完整显示
"""

import os, sys, re, uuid, shutil, subprocess, asyncio, requests, json, hashlib, threading, glob
from pathlib import Path
from datetime import datetime
from datetime import datetime as _dt
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ══════════════════════════════════════════════════════════════════════════════
# 共享 Session 配置（所有网络请求统一复用）
# ══════════════════════════════════════════════════════════════════════════════
_session = requests.Session()
_session.mount(
    'https://',
    HTTPAdapter(
        max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})
    )
)

def _get_session() -> requests.Session:
    """全局单例 Session，确保所有请求共享连接池"""
    return _session

# ══════════════════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("/Users/kaikai/ai_video_project/news_outputs")
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
CHANNEL_NAME = "20岁还没开始环球旅行"
_TOPIC_SCRIPTS_CACHE = None
_WHISPER_MODEL = None
import threading
_WHISPER_MODEL_LOCK = threading.Lock()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

# 加载下载用cookie（账号B: UID 1650357577）
DOWNLOAD_COOKIES = {}
_cookies_file = "/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt"
if os.path.exists(_cookies_file):
    try:
        import json
        _data = json.loads(open(_cookies_file).read())
        DOWNLOAD_COOKIES = {k: v for k, v in _data.items() if k in (
            "SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID", "bili_ticket", "sid"
        )}
    except Exception:
        pass

TASK_ID = uuid.uuid4().hex[:8].replace("'", "").replace("`", "")

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

# ── B站上传凭证（账号B: UID 1650357577 "20岁还没开始环球旅行"）────────────────
def _load_upload_cookies():
    """从账号B cookie文件加载上传凭证"""
    paths = [
        Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt"),
    ]
    for p in paths:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, dict) and 'SESSDATA' in data and 'bili_jct' in data:
                    return data
            except Exception:
                pass
    return {}

BILIBILI_COOKIES = _load_upload_cookies()
if not BILIBILI_COOKIES:
    print("警告: 无法加载账号B cookie，使用降级方案")

def check_title_duplicated(new_title: str, channel_uid: str = "1650357577") -> bool:
    """
    检查B站已投稿中是否有相似标题，避免重复上传
    返回True表示有重复，False表示可以上传
    """
    # 完整浏览器 UA，避免 B站 风控返回 412
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    }

    try:
        uid = channel_uid
        url = f"https://api.bilibili.com/x/space/arc/search?mid={uid}&pn=1&jsonp=jsonp&callback=&order=pubdate&keyword=&order_version=&page_size=30"
        r = _get_session().get(url, headers=browser_headers, timeout=10)
        data = r.json()
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])

        # 提取日期和时段用于精确比对（早差/晚差视为不同视频）
        date_pattern = r'\d{4}[年.]?\d{1,2}[月.]?\d{1,2}'
        time_pattern = r'[早晚]差'
        new_dates = re.findall(date_pattern, new_title)
        new_times = re.findall(time_pattern, new_title)

        for v in vlist:
            old_title = v.get("title", "")
            old_dates = re.findall(date_pattern, old_title)
            old_times = re.findall(time_pattern, old_title)
            # 同日+同时段才视为重复（早差/晚差可同日上传）
            if new_dates and old_dates == new_dates and new_times == old_times:
                log(f"  ⚠️ 检测到同日同时段标题已上传: {old_title}")
                return True
        log(f"  ✅ 标题去重检查通过（检索了 {len(vlist)} 条视频）")
        return False
    except Exception as e:
        log(f"  ⚠️ 去重检查异常（允许上传）: {e}")
        return False  # 检查失败时默认允许上传，避免误杀

def verify_video_has_frames(path: str) -> bool:
    """解法1-10: 提取多帧截图检测亮度，确认有画面"""
    import numpy as np
    from PIL import Image
    timestamps = [5, 30, 60]
    for t in timestamps:
        frame_path = f"/tmp/vf_{uuid.uuid4().hex[:6]}.jpg"
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(t), "-i", path,
            "-vframes", "1", "-q:v", "2", frame_path
        ], capture_output=True, timeout=10)
        if r.returncode != 0 or not os.path.exists(frame_path):
            continue
        img = Image.open(frame_path)
        arr = np.array(img)
        brightness = arr.mean()
        os.remove(frame_path)
        if brightness > 15:  # 提高阈值避免黑帧误判
            return True
    return False

def simhash(text: str) -> str:
    """解法2-4: 计算文本simhash用于相似度检测"""
    import hashlib, struct
    text = re.sub(r'\s+', '', text.lower())
    vec = [0] * 128
    words = [text[i:i+2] for i in range(0, min(len(text), 50), 2)]
    for word in words:
        h = hashlib.md5(word.encode()).digest()
        for i in range(16):
            byte_val = h[i]
            for j in range(8):
                bit = (byte_val >> j) & 1
                idx = i * 8 + j
                vec[idx] += 1 if bit else -1
    fingerprint = sum(1 << i if v > 0 else 0 for i, v in enumerate(vec))
    return struct.pack('>QQ', fingerprint >> 64, fingerprint & 0xFFFFFFFFFFFFFFFF).hex()

def hamming_distance(h1: str, h2: str) -> int:
    """解法2-4: 计算两个simhash的海明距离"""
    b1 = bytes.fromhex(h1)
    b2 = bytes.fromhex(h2)
    xor = int.from_bytes(b1, 'big') ^ int.from_bytes(b2, 'big')
    return bin(xor).count('1')

# ══════════════════════════════════════════════════════════════════════════════
# 解法3: 扩展选题 → 5数据源 × 多关键词 = 15+条
# ══════════════════════════════════════════════════════════════════════════════

def get_today_date():
    return datetime.now().strftime("%Y年%m月%d日")

# ── 争议话题优先排序 ────────────────────────────────────────────────────
BORING_KEYWORDS = [
    # 官方媒体（语气正、难引发共鸣）
    "外交部", "中方回应", "央视新闻", "人民日报", "新华社", "官方通报",
    "召开会议", "政策发布", "稳步推进", "安全播出", "依法", "切实",
    "高度重视", "认真贯彻落实", "有关部门", "答记者问", "发表评论",
    # 中国领导人相关（容易导致退稿）
    "国家主席", "国家领导人", "总书记", "主席讲话", "领导人讲话",
    "中央领导", "政治局", "国务院", "国务院新闻",
]
# 完全禁止的话题（直接跳过）
BANNED_KEYWORDS = [
    "国家主席", "国家领导人", "总书记", "主席讲话", "政治局常委",
    "国务院总理", "国家副主席", "军委主席", "党和国家",
    "人民领袖", "伟大领袖", "习近", "彭丽媛", "李克强", "温家宝",
    "胡锦涛", "江泽民", "毛泽东", "邓小平", "周恩来", "朱镕基",
]
HOT_KEYWORDS = [
    "争议", "冲突", "爆发", "暴跌", "暴涨", "裁员", "倒闭", "揭秘",
    "曝光", "突发", "首次", "历史性", "惊人", "破局", "崩溃", "制裁",
    "对抗", "丑闻", "翻车", "打脸", "反转", "炸锅", "爆雷", "硬刚",
    "夺权", "逼宫", "内斗", "逃亡", "被捕", "通缉", "辟谣",
]


def get_hot_topics_v8(num: int = 20) -> list:
    """
    解法3-全: 抖音+微博+知乎+百度+微博要闻 = 目标15+条不重复话题
    """
    topics = []
    seen_keys = set()  # 用完整话题字符串去重

    # ── 数据源1: 抖音热搜（实测可访问）────────────────────────────────
    try:
        r = _get_session().get(
            "https://www.iesdouyin.com/aweme/v1/hot/search/list/",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)"},
            timeout=8
        )
        if r.status_code == 200:
            word_list = r.json().get("data", {}).get("word_list", [])
            for item in word_list[:num]:
                word = item.get("word", "")
                hot_val = item.get("hot_value", "")
                if word and len(word) >= 3:
                    key = word  # 用完整话题字符串去重，不用前8字
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "抖音热搜", "hot": hot_val})
            log(f"  抖音热搜: {len(word_list)}条")
    except Exception as e:
        log(f"  ⚠️ 抖音热搜: {e}")

    # ── 数据源2: 微博热搜（备用）─────────────────────────────────────
    try:
        r = _get_session().get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            band_list = r.json().get("data", {}).get("band_list", [])
            for item in band_list[:num]:
                word = item.get("word", "")
                if word and len(word) >= 3:
                    key = word
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "微博热搜"})
    except Exception as e:
        log(f"  ⚠️ 微博热搜: {e}")

    # ── 数据源3: 知乎热榜（备用）─────────────────────────────────────
    try:
        r = _get_session().get(
            "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            for item in r.json().get("data", [])[:num]:
                title = item.get("target", {}).get("title", "")
                if title and len(title) >= 4:
                    key = title
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": title, "source": "知乎热榜"})
    except Exception as e:
        log(f"  ⚠️ 知乎热搜: {e}")

    # ── 数据源4: 百度实时（备用）─────────────────────────────────────
    try:
        r = _get_session().get(
            "https://top.baidu.com/api?get=news&flag=1",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            news_list = r.json().get("data", {}).get("newsList", [])
            for item in news_list[:num//2]:
                word = item.get("word", "")
                if word and len(word) >= 3:
                    key = word
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "百度实时"})
    except Exception as e:
        log(f"  ⚠️ 百度实时: {e}")

    # ── 抖音优先 + MiniMax 动态打分排序 ─────────────────────────────────
    # 数据源权重：抖音12条，百度8条，微博5条，知乎5条
    # 每个话题用 MiniMax API 打分，并行处理，按分数降序
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 调整各源抓取数量（抖音优先）
    douyin_topics = [t for t in topics if t.get("source") == "抖音热搜"]
    baidu_topics = [t for t in topics if t.get("source") == "百度实时"]
    other_topics = [t for t in topics if t.get("source") not in ("抖音热搜", "百度实时")]

    # 优先抖音12条，百度8条，不足时用微博/知乎补充
    weighted = (douyin_topics[:12] + baidu_topics[:8] + other_topics[:5])
    if len(weighted) < 15:
        log(f"  ⚠️ 话题不足15条，仅 {len(weighted)} 条，将影响视频丰富度")

    def _score_one(t):
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from config.minimax_client import MiniMaxClient
            score = MiniMaxClient().score_topic(t["topic"])
            return (score, t)
        except Exception:
            return (50.0, t)

    scored = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_score_one, t): t for t in weighted}
        for future in as_completed(futures):
            try:
                result = future.result(timeout=30)
                scored.append(result)
            except Exception:
                scored.append((50.0, weighted[futures[future]]))

    scored.sort(key=lambda x: -x[0])
    diversified = [t for _, t in scored]

    log(f"  选题去重后: {len(diversified)}条")
    return diversified[:num]

# ══════════════════════════════════════════════════════════════════════════════
# 解法2: 稿子去重 → simhash + 多样句式模板
# ══════════════════════════════════════════════════════════════════════════════

# 解法2-2: 多样句式开场白模板池（每个话题可换不同说法）
INTRO_TEMPLATES = [
    "带你来快速了解一下{}。",
    "今天的热点，{}你听说了吗？",
    "先说一个你可能还不知道的事：{}。",
    "最近，{}在持续发酵。",
    "{}，这个消息值得关注。",
    "来聊聊{}这件事。",
    "先从{}说起。",
    "{}的消息出来了，一起看看。",
    "咱们先看一下{}的来龙去脉。",
    "{}，又上热搜了，一起了解一下。",
]

# ══════════════════════════════════════════════════════════════════════════════
# 解法2-3: 同事件归类（合并相似话题）+ 叙事逻辑排序
# ══════════════════════════════════════════════════════════════════════════════



def _get_whisper_model():
    """全局单例 WhisperModel，避免每次字幕都重新加载（启动慢约10秒）"""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        with _WHISPER_MODEL_LOCK:
            if _WHISPER_MODEL is None:
                from faster_whisper import WhisperModel
                _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _WHISPER_MODEL

def _fetch_baidu_hot_search() -> dict:
    """
    抓取百度热搜实时榜单（直接从 HTML 解析），
    返回 {topic: script} 字典，script = word + desc 组合
    """
    try:
        r = _get_session().get(
            "https://top.baidu.com/board?tab=realtime",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=10
        )
        if r.status_code != 200:
            log(f"  ⚠️ 百度热搜请求失败 status={r.status_code}")
            return {}

        import re

        # 提取所有 word（话题）和 desc（描述），按顺序一一配对
        words = re.findall(r'"word":\s*"([^"]{4,50})"', r.text)
        descs = re.findall(r'"desc":\s*"([^"]{5,200})"', r.text)

        scripts = {}
        n = min(len(words), len(descs))
        for i in range(n):
            word = words[i].strip()
            desc = re.sub(r'<[^>]+>', '', descs[i]).strip()[:150]
            if len(word) >= 4 and len(desc) >= 10:
                script = f"{word}。{desc}"
                scripts[word] = script

        if not scripts:
            log(f"  ⚠️ 百度热搜解析为空")
        else:
            log(f"  ✅ 百度热搜抓取成功，共 {len(scripts)} 条")
        return scripts
    except Exception as e:
        log(f"  ⚠️ 百度热搜抓取失败: {e}")
        return {}


def _fetch_bing_news(topic: str) -> str:
    """
    用 Bing 新闻搜索获取 topic 的新闻摘要，返回一段60-100字的播报文本
    """
    try:
        import urllib.parse
        q = urllib.parse.quote(topic)
        r = _get_session().get(
            f"https://cn.bing.com/news/search?q={q}&format=RSS",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=10
        )
        if r.status_code == 200:
            # 解析 RSS
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
            descs = re.findall(r'<description><!\[CDATA\[(.*?)\]\]></description>', r.text)
            if titles:
                # 取第一条新闻的标题和描述组合
                main_title = titles[0] if len(titles) > 0 else topic
                # 清理 HTML 标签
                desc = re.sub(r'<[^>]+>', '', descs[0]) if descs else ""
                desc = desc.strip()[:200]
                script = f"{main_title}。{desc}" if desc else main_title
                script = re.sub(r'\s+', ' ', script).strip()
                if len(script) > 30:
                    log(f"  📰 Bing新闻[{topic[:15]}...]: {script[:50]}...")
                    return script
    except Exception as e:
        log(f"  ⚠️ Bing搜索失败[{topic[:15]}]: {e}")
    return ""



def _call_minimax_script(topic: str) -> str:
    """用 MiniMax API 生成 60-80 字新闻播报文案，口语化像真人"""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from config.minimax_client import MiniMaxClient
        script = MiniMaxClient().generate_script(topic)
        if script and len(script) >= 10:
            return script
    except Exception:
        pass
    return ""


def generate_script_v8(topic: str, index: int) -> str:
    """
    优化版文案生成：
    强制调用 MiniMax API 生成 200-250 字长脚本，
    不使用百度热搜的短 desc 作为脚本内容。
    生成后写入缓存供评论使用。
    """
    global _TOPIC_SCRIPTS_CACHE

    # 强制调用 MiniMax 生成（每次都调用，不走缓存）
    log(f"  🤖 MiniMax脚本[{index+1}]: {topic[:20]}...")
    script = _call_minimax_script(topic)
    if script and len(script) >= 10:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script

    # MiniMax 失败时 Bing 新闻搜索兜底
    log(f"  🔍 Bing搜索兜底: {topic[:20]}...")
    script = _fetch_bing_news(topic)
    if script:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script

    # 最终兜底：话题本身展开
    log(f"  ⚠️ 兜底使用话题本身: {topic[:20]}...")
    fallback = f"{topic}。"
    _TOPIC_SCRIPTS_CACHE[topic] = fallback
    return fallback


def fetch_topic_scripts(topics_list: list = None) -> dict:
    """
    获取今日实时新闻话题列表（不生成脚本）：
    仅用于预热 _TOPIC_SCRIPTS_CACHE，脚本内容由 generate_script_v8 强制走 MiniMax。
    """
    global _TOPIC_SCRIPTS_CACHE
    if _TOPIC_SCRIPTS_CACHE is not None:
        return _TOPIC_SCRIPTS_CACHE
    
    scripts = {}
    
    # Step 1: 抓取百度热搜话题列表（仅作为 topic list，不作为 script）
    baidu_topics = _fetch_baidu_hot_search()
    
    # Step 2: 话题列表存入缓存（script 内容由 generate_script_v8 强制调用 MiniMax 生成）
    for topic in baidu_topics:
        if len(topic) >= 4:
            scripts[topic] = topic  # 仅存话题名，script 由 MiniMax 生成
    
    # Step 3: 补充传入 topics_list 中未匹配的条目
    if topics_list:
        for item in topics_list:
            topic = item if isinstance(item, str) else item.get("topic", "")
            if topic and topic not in scripts:
                scripts[topic] = topic  # 仅存话题名
    
    _TOPIC_SCRIPTS_CACHE = scripts
    log(f"  📊 fetch_topic_scripts 完成，共 {len(scripts)} 条话题")
    return scripts




# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# 维度④：视频搜索下载
# ══════════════════════════════════════════════════════════════════════════════

def search_bilibili_video(topic: str) -> str:
    """搜索B站视频，返回BV ID（3次重试+5秒等待）"""
    import time
    for attempt in range(3):
        try:
            import urllib.parse
            q = urllib.parse.quote(topic)
            r = _get_session().get(
                f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={q}&page=1&page_size=8",
                headers=HEADERS, timeout=15
            )
            d = r.json()
            if d.get("code") != 0:
                log(f"  ⚠️ B站搜索[{attempt+1}]失败: code={d.get('code')}，{2**(attempt+1)}秒后重试")
                time.sleep(2 ** (attempt + 1))
                continue
            for item in d.get("data", {}).get("result", []):
                if isinstance(item, dict) and item.get("result_type") == "video":
                    for v in item.get("data", [])[:3]:
                        bv = v.get("bvid", "")
                        dur = v.get("duration", "0:00")
                        try:
                            parts = dur.split(":")
                            secs = int(parts[0]) * 60 + int(parts[1])
                            if 10 <= secs <= 300:
                                log(f"  🎬 B站: [{bv}] {v.get('title','')[:30]} ({dur})")
                                return bv
                        except Exception:
                            pass
            log(f"  ⚠️ B站搜索[{attempt+1}]未找到合适视频，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
        except Exception as e:
            log(f"  ⚠️ B站搜索[{attempt+1}]异常: {e}，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
    log(f"  ⚠️ B站搜索最终失败: {topic[:15]}")
    return None

def download_bilibili_video(bvid: str, output_path: str, clip_dur: float = None) -> bool:
    """下载B站视频（yt-dlp最高质量：bv*+ba/best → mp4）"""
    try:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "-f", "bv*+ba/best",
            "--merge-output-format", "mp4",
            "--cookies-from-browser", "chrome",
            "--no-warnings",
            "-o", output_path,
            f"https://www.bilibili.com/video/{bvid}"
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
            log(f"  ✅ BV={bvid} {os.path.getsize(output_path)//1024}KB (yt-dlp)")
            return True
    except subprocess.TimeoutExpired:
        log(f"  ⚠️ BV={bvid} 下载超时")
    except Exception as e:
        log(f"  ⚠️ BV={bvid} 下载异常: {e}")
    return False

def generate_tts(script: str, output_path: str, index: int) -> bool:
    """Edge TTS zh-CN-YunxiNeural（云希Neural，浑厚有力）+ 语速+30%，音调+5Hz，3次重试"""
    import time, asyncio, tempfile, os
    for attempt in range(3):
        try:
            async def _run():
                import edge_tts
                communicate = edge_tts.Communicate(
                    script,
                    voice="zh-CN-YunxiNeural",
                    rate="+30%",
                    pitch="+5Hz",
                )
                await communicate.save(output_path)

            asyncio.run(_run())
            if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
                dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", output_path],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip() or 0)
                log(f"  ✅ 第{index+1}条音频(YunxiNeural): {dur:.0f}秒")
                return True
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠️ TTS第{attempt+1}次失败: {e}，{wait}秒后重试")
            time.sleep(wait)
    log(f"  ⚠️ TTS失败")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 维度⑥：字幕生成（SRT格式，兼容ASS滤镜）
# ══════════════════════════════════════════════════════════════════════════════

def generate_srt_from_audio(audio_path: str, srt_path: str, index: int, script: str = "") -> bool:
    """faster-whisper提取逐字时间轴（带重试），fallback为等时长分割"""
    import warnings
    warnings.filterwarnings("ignore", message="divide by zero", category=RuntimeWarning)
    warnings.filterwarnings("ignore", message="overflow encountered", category=RuntimeWarning)
    warnings.filterwarnings("ignore", message="invalid value encountered", category=RuntimeWarning)
    import time
    for attempt in range(3):
        try:
            model = _get_whisper_model()
            segments, _ = model.transcribe(audio_path, language="zh", word_timestamps=True)

            lines = []
            for seg_idx, seg in enumerate(segments):
                words = seg.words
                if not words:
                    continue
                start = words[0].start
                end = words[-1].end
                text = "".join(w.word.strip() for w in words)
                if not text:
                    continue
                def fmt(t):
                    h = int(t // 3600)
                    m = int((t % 3600) // 60)
                    s = int(t % 60)
                    ms = int((t % 1) * 1000)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                lines.append(f"{seg_idx + 1}\n{fmt(start)} --> {fmt(end)}\n{text}\n")

            if lines:
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                log(f"  ✅ 第{index+1}条字幕: {len(lines)}句")
                return True
            else:
                log(f"  ⚠️ 第{index+1}条字幕: Whisper未识别，使用等长fallback")
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠️ 第{index+1}条字幕第{attempt+1}次失败: {e}，{wait}秒后重试")
            time.sleep(wait)

    # Fallback: 等时长分割字幕
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 10)

        if dur <= 0:
            log(f"  ⚠️ 音频时长{dur}秒，无法分割字幕")
            return False

        # 按句子数等分
        sentences = script.split("。") if script else [""]
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = [""]  # 避免除零

        seg_dur = dur / len(sentences)
        lines = []
        for i, sent in enumerate(sentences):
            start = i * seg_dur
            end = (i + 1) * seg_dur
            def fmt(t):
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = int(t % 60)
                ms = int((t % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            lines.append(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{sent}\n")

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"  ⚡ 第{index+1}条字幕(等长fallback): {len(sentences)}句")
        return True
    except Exception as e:
        log(f"  ⚠️ 第{index+1}条字幕fallback也失败: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 维度⑦：字幕烧录（修复没画面+字幕问题）
# ══════════════════════════════════════════════════════════════════════════════

def srt_to_ass(srt_path: str, ass_path: str) -> bool:
    """解法4-1: SRT转ASS，提升字幕渲染兼容性"""
    try:
        import pysrt
        subs = pysrt.open(srt_path)
        
        # 查找可用的中文字体
        font_name = "Source Han Sans SC"
        font_paths_check = [
            "/tmp/SourceHanSansSC-Regular.otf",
            "/System/Library/Fonts/Supplemental/SourceHanSansSC-Regular.otf",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
        actual_font = "Arial"
        for fp in font_paths_check:
            if os.path.exists(fp):
                actual_font = fp
                break
        
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(
                "[Script Info]\nTitle: Generated\n"
                "[V4+ Styles]\n"
                # 改进版样式：白色字体+黑色描边+字幕距底部70px（参考原视频）
                # Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
                f"Style: Default,{actual_font},24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,70,1\n"
                "[Events]\nFormat: Layer,Start,End,Style,Text\n"
            )
            for sub in subs:
                start = format_ass_time(sub.start.ordinal / 1000.0)
                end = format_ass_time(sub.end.ordinal / 1000.0)
                text = sub.text.replace('\n', '\\N')
                # 白色描边黑色底 - 保留特殊字符转义
                text = text.replace('>', '&gt;').replace('<', '&lt;')
                f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
        return True
    except Exception as e:
        log(f"  ⚠️ SRT→ASS失败: {e}")
        return False

def format_ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def burn_subtitle_pil(video_path: str, srt_path: str, output_path: str, clip_dur: float, tts_audio_path: str, topic_title: str = "", segment_index: int = 1, total_segments: int = 1) -> bool:
    """
    字幕烧录布局（参考信息差视频截图）：
    1920x1080 分三层：
    - 字幕区 y=810-950：半透明黑条底，白色话题标题(大字左对齐) + 白色字幕正文(居中)
    - 章节栏 y=950-1080：深灰底，灰色小字"第X条/共Y条"左对齐
    """
    frame_dir = None
    cap = None
    try:
        import pysrt
        from PIL import Image, ImageDraw, ImageFont

        if not os.path.exists(srt_path):
            return False
        subs = pysrt.open(srt_path)

        width, height = 1920, 1080

        # 动态topic标题：只在clip的前30%时间段内显示，之后消失
        topic_show_until = clip_dur * 0.30

        # 字幕区：y=815-950，半透明黑条
        subtitle_bg_top = 815
        subtitle_bg_bottom = 950

        # 话题标题区：y=815-860（大字，白色，左对齐）
        topic_text_y = 822
        topic_font_size = 35

        # 字幕正文区：y=868-945（白色，居中）
        subtitle_text_y = 880  # 字幕区居中偏上（topic消失后占整个字幕区）
        subtitle_font_size = 34

        # 章节栏：y=950-1080，展示所有章节节点+动态进度推进
        chapter_bar_top = 950
        chapter_bar_height = height - chapter_bar_top  # 130px

        # timeline参数
        timeline_left = 60    # 时间轴左边界
        timeline_right = width - 60  # 时间轴右边界
        timeline_width = timeline_right - timeline_left

        # 找章节栏用的字体（18px）
        chapter_fnt_size = 18

        # 颜色
        topic_color = (255, 255, 255)
        subtitle_color = (255, 255, 255)
        chapter_text_color = (160, 160, 160)
        stroke_color = (0, 0, 0)
        chapter_bar_color = (30, 30, 30)

        # 找中文字体
        font_candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        fnt_path = None
        for fp in font_candidates:
            if os.path.exists(fp):
                try:
                    fnt_path = fp
                    break
                except Exception:
                    continue

        def make_font(size):
            if fnt_path:
                try:
                    return ImageFont.truetype(fnt_path, size)
                except Exception:
                    pass
            return ImageFont.load_default()

        fnt_topic = make_font(topic_font_size)
        fnt_sub = make_font(subtitle_font_size)
        fnt_chapter = make_font(18)

        frame_dir = f"/tmp/frames_{uuid.uuid4().hex[:6]}"
        os.makedirs(frame_dir, exist_ok=True)

        import cv2
        cap = cv2.VideoCapture(video_path)
        fps_in = cap.get(cv2.CAP_PROP_FPS) or 30
        max_frames = int(clip_dur * fps_in) + 30
        rendered = set()
        frame_idx = 0

        def stroke_text(draw, pos, text, font, fill, stroke_fill, width=2):
            x, y = pos
            for dx in range(-width, width + 1):
                for dy in range(-width, width + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
            draw.text(pos, text, font=font, fill=fill)

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx >= max_frames:
                log(f"  ⚠️ 帧数已达上限 {max_frames}，截断")
                break
            timestamp = frame_idx / fps_in

            current_sub = None
            for sub in subs:
                start_s = sub.start.ordinal / 1000.0
                end_s = sub.end.ordinal / 1000.0
                if start_s <= timestamp <= end_s:
                    current_sub = sub.text
                    break

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            draw = ImageDraw.Draw(pil_img)

            # 字幕区黑条背景
            draw.rectangle([0, subtitle_bg_top, width, subtitle_bg_bottom], fill=(0, 0, 0, 180))

            # 话题标题（白色大字，左对齐，2px黑描边）—— 只在前30%时间段显示
            if topic_title and timestamp < topic_show_until:
                stroke_text(draw, (30, topic_text_y), topic_title, fnt_topic, topic_color, stroke_color, width=2)

            # 字幕正文（白色居中，2px黑描边）—— 始终显示，随时间轴滚动
            if current_sub:
                bbox = draw.textbbox((0, 0), current_sub, font=fnt_sub)
                text_w = bbox[2] - bbox[0]
                text_x = (width - text_w) // 2
                # topic存在时字幕偏下，topic消失后字幕居中
                sub_y = subtitle_text_y if timestamp < topic_show_until else (subtitle_bg_top + (subtitle_bg_bottom - subtitle_bg_top - subtitle_font_size) // 2)
                stroke_text(draw, (text_x, sub_y), current_sub, fnt_sub, subtitle_color, stroke_color, width=2)
                rendered.add(frame_idx)

            # 章节栏：浅色进度背景条 + 时间轴节点动态推进
            draw.rectangle([0, chapter_bar_top, width, height], fill=(32, 32, 32))

            if total_segments >= 1:
                axis_y = chapter_bar_top + chapter_bar_height // 2
                elapsed = timestamp / clip_dur if clip_dur > 0 else 0
                progress_x = timeline_left + int(timeline_width * elapsed)
                # 当前章节（1到total_segments）
                current_seg = 1 if total_segments == 1 else max(1, min(total_segments, int(elapsed * (total_segments - 1)) + 1))

                # 时间轴背景线（浅灰色）
                draw.rectangle([timeline_left, axis_y - 1, timeline_right, axis_y + 1], fill=(80, 80, 80))
                # 已播放部分（亮灰）
                if progress_x > timeline_left:
                    draw.rectangle([timeline_left, axis_y - 2, progress_x, axis_y + 2], fill=(180, 180, 180))

                # 章节节点：等距分布，已过亮色，未到暗色
                for seg_j in range(1, total_segments + 1):
                    frac = (seg_j - 1) / (total_segments - 1) if total_segments > 1 else 0
                    node_x = timeline_left + int(timeline_width * frac)
                    node_color = (220, 220, 220) if seg_j <= current_seg else (100, 100, 100)
                    draw.ellipse([node_x - 5, axis_y - 5, node_x + 5, axis_y + 5], fill=node_color)

                # 时间轴：已播放部分亮色推进
                if progress_x > timeline_left:
                    draw.rectangle([timeline_left, axis_y - 2, progress_x, axis_y + 2], fill=(180, 180, 180))

                # 章节节点：等距分布，已过亮色，未到暗色
                for seg_j in range(1, total_segments + 1):
                    frac = (seg_j - 1) / (total_segments - 1) if total_segments > 1 else 0
                    node_x = timeline_left + int(timeline_width * frac)
                    node_color = (220, 220, 220) if seg_j <= current_seg else (100, 100, 100)
                    draw.ellipse([node_x - 5, axis_y - 5, node_x + 5, axis_y + 5], fill=node_color)

            pil_img.save(f"{frame_dir}/frame_{frame_idx:06d}.jpg", quality=90)
            frame_idx += 1

        cap.release()

        if frame_idx == 0:
            log(f"  ⚠️ 无帧可处理")
            return False

        log(f"  PIL烧录: {frame_idx}帧, {len(rendered)}帧有字幕")

        fps_out = min(fps_in, 30)
        frame_list_path = f"{frame_dir}/frames.txt"
        frame_files = sorted(glob.glob(f"{frame_dir}/*.jpg"))
        if not frame_files:
            log(f"  ⚠️ 无帧文件可处理")
            return False
        with open(frame_list_path, "w") as f:
            for ff in frame_files:
                f.write(f"file '{ff}'\n")
                f.write(f"duration {1.0/fps_out}\n")
        with open(frame_list_path, "a") as f:
            f.write(f"file '{frame_files[-1]}'\n")

        has_audio = tts_audio_path and os.path.exists(tts_audio_path)
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", frame_list_path]
        if has_audio:
            cmd += ["-i", tts_audio_path, "-map", "0:v", "-map", "1:a"]
        else:
            cmd += ["-map", "0:v"]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-profile:v", "high", "-level", "3.1",
            "-vf", "scale=1920:1080,setsar=1",
            "-c:a", "aac", "-b:a", "128k" if has_audio else "192k",
            "-t", str(clip_dur), "-pix_fmt", "yuv420p",
            output_path
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=int(clip_dur * 1.5 + 60))

        if r.returncode == 0:
            if frame_dir is not None and os.path.exists(frame_dir):
                shutil.rmtree(frame_dir, ignore_errors=True)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 5000
        else:
            log(f"  ⚠️ PIL烧录重编码失败: {r.stderr[-200:]}")
            return False

    except Exception as e:
        log(f"  ⚠️ PIL烧录异常: {e}")
        if cap is not None:
            cap.release()
        if frame_dir is not None and os.path.exists(frame_dir):
            shutil.rmtree(frame_dir, ignore_errors=True)
        return False


def burn_subtitle_ass(video_path: str, ass_path: str, output_path: str, clip_dur: float) -> bool:
    """
    解法4-3/4/5: FFmpeg ASS字幕滤镜（如果libass可用则用这个）
    解法4-6 fallback: 纯Python PIL方案
    """
    # 先检查libass是否可用
    check = subprocess.run(
        ["ffmpeg", "-filters"], capture_output=True, text=True
    )
    has_libass = "subtitles" in check.stdout or " ass " in check.stdout or " libass" in check.stdout

    if has_libass:
        return burn_subtitle_ass_ffmpeg(video_path, ass_path, output_path, clip_dur)
    else:
        # fallback: 从SRT烧录（需要先生成SRT）
        srt_path = ass_path.replace(".ass", ".srt")
        return burn_subtitle_pil(video_path, srt_path, output_path, clip_dur, "")


def burn_subtitle_ass_ffmpeg(video_path: str, ass_path: str, output_path: str, clip_dur: float) -> bool:
    """解法4-3/4: FFmpeg ASS字幕滤镜（libass可用时）"""
    try:
        # 检查字体
        font_path = "/tmp/SourceHanSansSC-Regular.otf"
        if not os.path.exists(font_path) or os.path.getsize(font_path) < 1000000:
            log("  下载 SourceHanSansSC 字体...")
            r = _get_session().get(
                "https://github.com/adobe-fonts/source-han-sans/raw/release/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf",
                timeout=60, stream=True
            )
            if r.status_code == 200:
                with open(font_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                if os.path.exists(font_path) and os.path.getsize(font_path) > 1000000:
                    log(f"  ✅ 字体下载成功: {os.path.getsize(font_path)//1024}KB")
                else:
                    log("  ⚠️ 字体下载可能失败，继续使用系统字体")

        # 解法4-4: 路径用shell转义
        ass_escaped = ass_path.replace("'", "'\\''")
        filter_str = f"ass={ass_escaped}:fontsdir=/tmp:original_size=1280x720"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex", f"[0:v]{filter_str}[out]",
            "-map", "[out]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-profile:v", "high", "-level", "3.1",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(clip_dur),
            output_path
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=int(clip_dur * 2 + 60))
        if r.returncode != 0:
            log(f"  ⚠️ ASS滤镜失败，尝试subtitles滤镜")
            ass_escaped2 = ass_path.replace("'", "'\\''")
            # 改进版字幕样式：白色24号字体，黑色描边，字幕距底部70px，章节栏距底部144px
            # 参考原视频：字幕在内容区底部上方70px，章节栏在底部1/3暗橄榄绿底
            cmd2 = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"subtitles='{ass_escaped2}':force_style='FontName=Source Han Sans SC,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,Outline=2,Shadow=2,Bold=0,MarginV=70'",
                "-map", "0:v", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(clip_dur),
                output_path
            ]
            r = subprocess.run(cmd2, capture_output=True, timeout=int(clip_dur * 2 + 60))
            if r.returncode != 0:
                log(f"  ⚠️ subtitles滤镜也失败: {r.stderr[-150:]}")
                return False
        return os.path.exists(output_path) and os.path.getsize(output_path) > 5000
    except Exception as e:
        log(f"  ⚠️ 烧录异常: {e}")
        return False

def verify_subtitles_burned(video_path: str) -> bool:
    """解法4-9: 提取帧检测字幕亮度"""
    import numpy as np
    from PIL import Image
    # 检查底部25%区域
    for t in [5, 15, 25]:
        frame_path = f"/tmp/sub_check_{uuid.uuid4().hex[:6]}.jpg"
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
            "-vframes", "1", "-q:v", "2", frame_path
        ], capture_output=True, timeout=10)
        if r.returncode != 0 or not os.path.exists(frame_path):
            continue
        img = Image.open(frame_path)
        arr = np.array(img)
        h, w = arr.shape[:2]
        bottom = arr[int(h * 0.75):, :, :]
        brightness = bottom.mean()
        os.remove(frame_path)
        if brightness > 20:  # 提高阈值避免字幕区域漏检
            return True
    return False

def verify_video_quality(video_path: str) -> dict:
    """
    质量检查门神：逐项检查，全部通过才返回 True
    返回 dict: {pass: bool, reasons: list}
    """
    import numpy as np
    from PIL import Image
    reasons = []
    checks = {
        "文件存在": os.path.exists(video_path),
        "文件>800KB": os.path.getsize(video_path) > 800 * 1024,
    }

    if not checks["文件存在"]:
        reasons.append("文件不存在")
        return {"pass": False, "reasons": reasons}

    if not checks["文件>800KB"]:
        reasons.append(f"文件太小: {os.path.getsize(video_path)//1024}KB")
        return {"pass": False, "reasons": reasons}

    # 检查时长、视频流、音频流
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_name,codec_type,width,height",
         "-of", "json", video_path],
        capture_output=True, text=True, timeout=15
    )
    try:
        data = json.loads(info.stdout)
    except Exception:
        reasons.append("ffprobe解析失败")
        return {"pass": False, "reasons": reasons}

    fmt = data.get("format", {})
    dur = float(fmt.get("duration", 0))
    checks["时长>10s"] = dur > 10
    if not checks["时长>10s"]:
        reasons.append(f"时长不足: {dur:.0f}s")

    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    checks["有视频流"] = has_video
    checks["有音频流"] = has_audio
    if not has_video:
        reasons.append("无视频流")
    if not has_audio:
        reasons.append("无音频流")

    # 检查画面（非黑帧）
    has_content = verify_video_has_frames(video_path)
    checks["有画面"] = has_content
    if not has_content:
        reasons.append("画面全黑/无帧")

    # 检查字幕（底部亮度）
    has_subs = verify_subtitles_burned(video_path)
    checks["有字幕"] = has_subs
    if not has_subs:
        reasons.append("字幕未烧录")

    # 分辨率抽查
    for s in streams:
        if s.get("codec_type") == "video":
            w, h = s.get("width", 0), s.get("height", 0)
            if w < 640 or h < 360:
                reasons.append(f"分辨率过低: {w}x{h}")
                break

    all_pass = all(checks.values())
    log(f"  🔍 质量检查: {'全部通过' if all_pass else '失败'}")
    for k, v in checks.items():
        log(f"     {'✅' if v else '❌'} {k}")
    if reasons:
        for r in reasons:
            log(f"     → {r}")
    return {"pass": all_pass, "reasons": reasons}

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def main(date_str: str = "today"):
    log(f"\n{'='*60}")
    log(f"📺 v8 启动 | 任务ID: {TASK_ID} | 日期: {date_str}")
    log(f"   修复：20条精炼版+字幕验证+concat filter")
    log(f"{'='*60}")

    # ── Step 1: 选题（今日百度热搜实时20条）──────────────
    log(f"\n① 选题（今日百度热搜 {date_str}）...")

    # 今日百度热搜实时话题（从 top.baidu.com 自动抓取，每条含 word+desc 播报内容）
    baidu_scripts = _fetch_baidu_hot_search()
    hot_topics = list(baidu_scripts.keys())[:10]

    # 构建话题列表（已通过 MiniMax 打分排序）
    topics = [{"topic": t, "bvid": None, "hot": baidu_scripts.get(t, "")} for t in hot_topics]

    # 限制为10个话题（10段×约60秒 = 约10分钟视频）
    MAX_TOPICS = 10
    topics = topics[:MAX_TOPICS]

    if len(topics) < 5:
        log("  ❌ 话题不足5条，退出")
        return

    global _TODAY_TOPICS
    _TODAY_TOPICS = topics
    log(f"  共 {len(topics)} 个话题:")
    for i, t in enumerate(topics):
        log(f"    {i+1}. {t['topic']}")

    # 预热脚本缓存（一次性从今日热搜获取，process_topic 就不需要再抓了）
    fetch_topic_scripts(topics)

    # ── Step 2: 并行处理所有话题 ─────────────────────────────────────────
    from concurrent.futures import ThreadPoolExecutor, as_completed
    log(f"\n② 并行处理 {len(topics)} 条话题（4线程）...")

    def process_topic(args):
        import traceback as _tb
        i, item = args
        topic = item["topic"]
        sid = f"{TASK_ID}_{i}"
        bv_id = item.get("bvid")
        if bv_id is not None:
            bv_id = str(bv_id)

        # 生成文案（TTS搜索都是网络I/O，并行跑）
        try:
            script = generate_script_v8(topic, i)
        except Exception as e:
            log(f"  ⚠️ 第{i+1}条 generate_script_v8 异常: {e}\n{_tb.format_exc()}")
            return None

        audio_path = str(OUTPUT_DIR / f"v8_audio_{sid}.m4a")
        if not generate_tts(script, audio_path, i):
            return None

        audio_dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 20)
        if audio_dur < 2:
            log(f"  ⚠️ 第{i+1}条音频{audio_dur:.1f}秒太短，跳过")
            return None

        bg_video_path = str(OUTPUT_DIR / f"v8_bgvideo_{sid}.mp4")
        bg_download_ok = False
        if bv_id:
            bg_download_ok = download_bilibili_video(bv_id, bg_video_path, clip_dur=audio_dur)
        else:
            searched_bv = search_bilibili_video(topic)
            if searched_bv:
                bg_download_ok = download_bilibili_video(searched_bv, bg_video_path, clip_dur=audio_dur)

        # 下载失败时生成纯黑背景（静默fallback，不浪费好的音频/TTS）
        if not bg_download_ok or not os.path.exists(bg_video_path) or os.path.getsize(bg_video_path) <= 5000:
            log(f"  ⚠️ 第{i+1}条 bg下载失败，使用纯黑背景")
            try:
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s=1280x720:d={audio_dur}",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-shortest",
                    bg_video_path
                ], capture_output=True, timeout=30)
            except Exception:
                pass

        srt_path = str(OUTPUT_DIR / f"v8_sub_{sid}.srt")
        ass_path = str(OUTPUT_DIR / f"v8_sub_{sid}.ass")
        generate_srt_from_audio(audio_path, srt_path, i, script)
        if os.path.exists(srt_path):
            srt_to_ass(srt_path, ass_path)

        return (i, topic, audio_path, srt_path, ass_path, bg_video_path, bv_id, audio_dur)

    segments = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_topic, (i, item)): i for i, item in enumerate(topics)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                if result:
                    segments.append(result)
                    log(f"  ✅ 第{idx+1}条 [{result[1][:15]}] 完成")
                else:
                    log(f"  ⚠️ 第{idx+1}条失败")
            except Exception as e:
                import traceback as _tb
                log(f"  ⚠️ 第{idx+1}条异常: {e}\n{_tb.format_exc()}")

    if not segments:
        log("❌ 没有可用片段")
        return

    log(f"\n  有效片段: {len(segments)}")

    # ── 智能合并短片段 ────────────────────────────────────────────────────────
    # 如果某个音频 < 30秒，自动和下一个相关话题合并
    MIN_CLIP_DURATION = 30  # 最低30秒
    MERGE_DURATION = 50      # 目标50-60秒

    def _smart_merge_segments(seg_list: list) -> list:
        """不做任何合并：每个话题独占一个片段，一一对应"""
        return seg_list

    def _parse_srt_time(t: str) -> float:
        """解析 SRT 时间格式（00:00:00,000）到秒"""
        t = t.strip().replace(",", ".")
        parts = t.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    def _format_srt_time(s: float) -> str:
        """将秒数格式化为 SRT 时间（00:00:00,000）"""
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int((s % 1) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    segments = _smart_merge_segments(segments)
    log(f"  合并后片段: {len(segments)}条")

    # ══════════════════════════════════════════════════════════════════════════════
    # 并行烧录 worker（crop + PIL字幕烧录 + 验证，全独立）
    # ══════════════════════════════════════════════════════════════════════════════
    def _process_single_clip(args):
        """在独立进程中处理单个片段：crop → burn → 验证"""
        i, topic, audio, srt, bg, dur, seg_idx = args
        sid = f"{TASK_ID}_{i}"
        clip_path = str(OUTPUT_DIR / f"v8_clip_{sid}.mp4")
        cropped_bg = str(OUTPUT_DIR / f"v8_crop_{sid}.mp4")

        # Step 1: ffmpeg 裁切
        crop_r = subprocess.run([
            "ffmpeg", "-y", "-i", bg, "-an",
            "-vf", "crop=iw*0.8:ih*0.8:iw*0.1:ih*0.1,scale=1280:720,setsar=1",
            "-t", str(dur), "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            cropped_bg
        ], capture_output=True, timeout=int(dur * 2 + 30))

        if crop_r.returncode != 0 or not os.path.exists(cropped_bg):
            return None

        try:
            # Step 2: PIL烧录字幕（segment_index = 显示编号, total = 总片段数）
            success = burn_subtitle_pil(
                cropped_bg,
                srt if os.path.exists(srt) else "",
                clip_path,
                dur,
                tts_audio_path=audio,
                topic_title=topic[:30],
                segment_index=seg_idx,
                total_segments=total_segs
            )
        except Exception as e:
            success = False

        # 清理裁切中间文件
        if os.path.exists(cropped_bg):
            os.remove(cropped_bg)

        if not success or not os.path.exists(clip_path):
            return None

        # Step 3: 验证画面
        if not verify_video_has_frames(clip_path):
            os.remove(clip_path)
            return None

        # Step 4: 验证字幕
        has_subs = verify_subtitles_burned(clip_path)
        size_kb = os.path.getsize(clip_path) // 1024
        return (clip_path, topic, dur, has_subs, size_kb)

    # ══════════════════════════════════════════════════════════════════════════════
    # 主流程
    # ══════════════════════════════════════════════════════════════════════════════
    log(f"\n③ 烧录字幕（{len(segments)}条并行{min(8, len(segments))}线程）...")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    clips = []
    clip_durations = []

    # 构造任务参数：只处理有背景视频的片段
    # total_segments 用原始话题总数，显示「第X条/共Y条」
    tasks = []
    for seg_i, (orig_idx, topic, audio, srt, ass, bg, bv, dur) in enumerate(segments):
        if bg and os.path.exists(bg) and os.path.getsize(bg) > 5000:
            display_num = len(tasks) + 1  # 显示编号从1开始
            tasks.append((orig_idx, topic, audio, srt, bg, dur, display_num))
    total_segs = len(segments)  # 用原始话题总数，不是有效片段数

    if tasks:
        # 烧录是CPU密集型，且PIL逐帧处理很慢，线程太多会互相拖累
        # 最多2个并行，每个占用近100% CPU
        burn_workers = min(2, len(tasks))
        with ThreadPoolExecutor(max_workers=burn_workers) as pool:
            futures = {pool.submit(_process_single_clip, t): t[0] for t in tasks}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    if result:
                        clip_path, topic, dur, has_subs, size_kb = result
                        log(f"  ✅ 第{idx+1}条 [{topic[:15]}] {size_kb}KB | 画面✓ | 字幕{'✓' if has_subs else '✗'}")
                        clips.append(clip_path)
                        clip_durations.append((topic, dur))
                    else:
                        log(f"  ⚠️ 第{idx+1}条烧录失败，跳过")
                except Exception as e:
                    log(f"  ⚠️ 第{idx+1}条异常: {e}")

    if not clips:
        log("❌ 所有片段失败")
        return

    # 拼接用 concat filter（不用 concat demuxer，MP4有兼容问题）
    log(f"\n④ 拼接 {len(clips)} 个片段...")

    final_mp4 = str(OUTPUT_DIR / f"【{CHANNEL_NAME}】{date_str}信息差_{TASK_ID}.mp4")

    # 构建 concat filter
    inputs = []
    for clip in clips:
        inputs.extend(["-i", clip])
    
    # filter_complex: [0:v][0:a][1:v][1:a]... concat=n=N:v=1:a=1[outv][outa]
    n = len(clips)
    filter_parts = ''.join([f"[{i}:v][{i}:a]" for i in range(n)])
    filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][outa]"
    
    cmd = [
        "ffmpeg", "-y"
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-profile:v", "high", "-level", "3.1",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        final_mp4
    ]

    r = subprocess.run(cmd, capture_output=True, timeout=600)

    global _CHAPTER_JSON
    _CHAPTER_JSON = None

    if os.path.exists(final_mp4):
        size_mb = os.path.getsize(final_mp4) / 1024 / 1024
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", final_mp4],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 0)

        # ── Step 4.5: 嵌入B站章节（模块级变量，上传时用）──────────────
        if clip_durations:
            log(f"\n④ 嵌入B站章节...")
            chapters_for_upload = []
            current_ts = 0.0
            for (topic_name, seg_dur) in clip_durations:
                chapters_for_upload.append({
                    "title": topic_name,
                    "start": int(current_ts)
                })
                current_ts += seg_dur

            chapter_json = json.dumps(chapters_for_upload, ensure_ascii=False)
            log(f"  章节数: {len(chapters_for_upload)}")
            for ch in chapters_for_upload:
                log(f"    {ch['start']}s: {ch['title'][:20]}")
            _CHAPTER_JSON = chapter_json

        # 解法1-9: 最终验证
        ok = verify_video_has_frames(final_mp4)
        log(f"\n{'='*60}")
        log(f"{'✅ v8 完成！' if ok else '⚠️ v8 完成（画面待验证）'}")
        log(f"⏱ 耗时: {dur:.0f}秒 ({dur/60:.1f}分钟)")
        log(f"📐 大小: {size_mb:.1f}MB")
        log(f"📁 {final_mp4}")
        log(f"实际片段: {len(clips)}条")
        log(f"{'='*60}")
        return final_mp4, clip_durations
    else:
        log("❌ 拼接失败")
        return None, []

if __name__ == "__main__":
    from datetime import date
    today = date.today()
    date_str = f"{today.year}年{today.month}月{today.day}日"
    date_short = f"{today.year}.{today.month}.{today.day}"

    log("\n" + "="*60)
    log("📺 v8 主流程开始")
    log("="*60)

    # ── Step 1-8: 生成视频 ─────────────────────────────
    _final_mp4, _clip_durations = main(date_str=date_str)

    if not _final_mp4 or not os.path.exists(_final_mp4):
        log("❌ 视频生成失败，跳过上传")
        sys.exit(1)

    _actual_count = len(_clip_durations)
    log(f"  视频实际包含 {_actual_count} 条新闻")

    # 解析章节 JSON（main() 里生成并存为全局变量）
    global _CHAPTER_JSON
    _chapters_for_upload = json.loads(_CHAPTER_JSON) if _CHAPTER_JSON else []

    # ── Step 9: 质量检查（门神）───
    log("\n🔍 质量检查中...")
    qc = verify_video_quality(_final_mp4)
    if not qc["pass"]:
        log(f"\n❌ 质量检查未通过，删除劣质视频，不上传")
        log(f"   失败原因: {', '.join(qc['reasons'])}")
        os.remove(_final_mp4)
        sys.exit(1)

    log(f"\n✅ 质量检查通过，视频已生成")
    log(f"   视频路径: {_final_mp4}")
    log(f"   包含 {len(_clip_durations)} 条新闻")
    print(f"\n[V8_GENERATED] {_final_mp4}")

    # ── Step 9.5: 清理本次运行的临时中间文件 ────────────────────────
    log("\n🧹 清理临时文件...")
    _task_id_pattern = TASK_ID  # capture current task id
    _cleaned = 0
    for _f in OUTPUT_DIR.glob("v8_*"):
        if _f.is_file():
            try:
                _f.unlink()
                _cleaned += 1
            except Exception:
                pass
    log(f"   清理了 {_cleaned} 个临时文件")

    # ── Step 10: 上传B站 ─────────────────────────────────────
    # （已启用，由主流程单独处理上传）
    import json as _json, asyncio as _asyncio
    try:
        import bilibili_api as _bapi
        from bilibili_api.clients.HTTPXClient import HTTPXClient as _HTTPXClient
        _bapi.register_client('httpx', _HTTPXClient)
        _bapi.select_client('httpx')
        from bilibili_api import video_uploader, Credential as _Credential
    except ImportError as e:
        log(f"❌ bilibili_api 未安装或导入失败: {e}")
        log("   请运行: pip install bilibili-api-python")
        sys.exit(1)

    _cookies = BILIBILI_COOKIES  # 使用脚本顶部写死的凭证
    _cred = _Credential(
        sessdata=_cookies['SESSDATA'],
        bili_jct=_cookies['bili_jct'],
        buvid3=_cookies['buvid3'],
    )

    _TASK_ID = uuid.uuid4().hex[:8]
    # 动态生成标题：日期 + 最具争议性话题
    _top_topic = _clip_durations[0][0] if _clip_durations else "今日热点速递"
    _time_mark = "早差" if _dt.now().hour < 12 else "晚差"
    _title = f"【{_time_mark}信息差】{date_short}：{_top_topic}…今日热点速递"

    # ── Step 10.5: 上传前去重检查 ─────────────────────────────────
    if check_title_duplicated(_title):
        log(f"\n⚠️ 今日已上传相似标题，跳过上传（请手动确认）")
        log(f"   标题: {_title}")
        sys.exit(0)  # 退出不删除视频，留给用户手动处理

    # 动态生成描述（用实际片段数）
    _topics_preview = ", ".join([
        t[0] for t in _clip_durations[:10]
    ])

    _desc = f"""📰 今日信息差日报 | {date_str} | {_actual_count}条热点

{_topics_preview}
…（更多热点见视频）

#信息差 #新闻汇总 #每日热点 #{today.year}"""

    _tags = ["信息差", "新闻汇总", "每日热点", str(today.year)]

    # ── 评论内容：移到 _upload() 内部，等 process_all_topics() 完成后缓存有了再生成

    async def _upload():
        # 注意：bilibili_api 上传时 desc 来自 VideoUploaderPage.description（不是 VideoMeta.desc）
        # title 也只来自 VideoUploaderPage.title（VideoMeta 的会被忽略）
        _page = video_uploader.VideoUploaderPage(
            path=_final_mp4,
            title=_title,
            description=_desc,
        )
        _cover_path = "/tmp/cover.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", _final_mp4, "-ss", "00:00:01", "-vframes", "1", "-q:v", "2", _cover_path],
            capture_output=True, timeout=15
        )
        from bilibili_api import Picture as _Picture
        _cover = _Picture.from_file(_cover_path)

        _meta = video_uploader.VideoMeta(
            tid=201,
            title=_title,      # bilibili_api ignores this; set via VideoUploaderPage
            desc=_desc,        # bilibili_api ignores this; set via VideoUploaderPage
            cover=_cover,
            tags=_tags,
            original=True,
            source='网络',
            no_reprint=True,
            up_close_danmu=False,
            up_close_reply=False,
        )
        _uploader = video_uploader.VideoUploader(
            pages=[_page],
            meta=_meta,
            credential=_cred,
        )
        print(f"\n开始上传 {_final_mp4}...", flush=True)
        _ret = await _uploader.start()
        print(f"上传结果: {_ret}", flush=True)

        # ── 设置B站章节 ─────────────────────────────────────────────
        _bv = _ret.get('bvid', _ret) if isinstance(_ret, dict) else _ret
        if _bv and _bv.startswith('BV'):
            print(f"设置章节: bvid={_bv}", flush=True)
            try:
                from bilibili_api import bvid2aid
                _aid = bvid2aid(_bv)
                _sess = _get_session()
                _view_resp = _sess.get(
                    f"https://api.bilibili.com/x/web-interface/view?bvid={_bv}",
                    headers=HEADERS, cookies=_cred, timeout=10
                )
                _view_data = _view_resp.json()
                _cid = _view_data.get("data", {}).get("cid", _aid)

                # 构建章节 param（需要 end 时间）
                _contents = []
                for _ch in (_chapters_for_upload or []):
                    _contents.append({
                        "title": _ch["title"],
                        "start": _ch["start"],
                        "end": 0   # 占位，下一步计算
                    })
                # 填 end：下一个的 start - 1，最后一个用视频总时长
                for _i in range(len(_contents)):
                    if _i < len(_contents) - 1:
                        _contents[_i]["end"] = _contents[_i + 1]["start"]
                    else:
                        _contents[_i]["end"] = int(sum(d for _, d in _clip_durations))

                _chapter_param = json.dumps({
                    "type": 1,
                    "contents": _contents
                }, ensure_ascii=False)

                _chapter_payload = {
                    "bvid": _bv,
                    "aid": _aid,
                    "cid": _cid,
                    "csrf": _cookies.get('bili_jct', ''),
                    "param": _chapter_param,
                }
                _ch_resp = _sess.post(
                    "https://api.bilibili.com/x/vas/dlc_act/act/portal/EditContent",
                    data=_chapter_payload,
                    headers=HEADERS, timeout=10
                )
                _ch_result = _ch_resp.json()
                if _ch_result.get("code") == 0:
                    print(f"✅ 章节设置成功 ({len(_contents)} 个)", flush=True)
                else:
                    print(f"⚠️ 章节设置失败: {_ch_result.get('message', _ch_result)}", flush=True)
            except Exception as _e:
                print(f"⚠️ 章节设置异常: {_e}", flush=True)

        # ── 生成评论内容（用实际进视频的片段话题）────────
        _c_lines = [f"📰 今日信息差 | {date_str} | 共 {_actual_count} 条热点", ""]
        for _ii, (_tp, _dur) in enumerate(_clip_durations):
            _scr = _TOPIC_SCRIPTS_CACHE.get(_tp, "") if _TOPIC_SCRIPTS_CACHE else ""
            _brief = ""
            if _scr:
                _rest = _scr[len(_tp):].strip()
                if _rest.startswith("。"):
                    _rest = _rest[1:].strip()
                _brief = _rest[:60].rstrip("、。")
            _c_lines.append(f"{_ii+1}. 【{_tp}】")
            if _brief:
                _c_lines.append(f"   {_brief}")
        _c_lines.append("")
        _c_lines.append(f"#信息差 #新闻汇总 #每日热点 #{today.year}")
        _comment_text = "\n".join(_c_lines)

        # 发评论（20条标题+简介）
        _bv = _ret.get('bvid', _ret) if isinstance(_ret, dict) else _ret
        _oid = None
        try:
            import requests as _req
            _r = _req.get(f"https://api.bilibili.com/x/web-interface/view?bvid={_bv}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            _rd = _r.json()
            if _rd.get("code") == 0 and "data" in _rd:
                _oid = _rd["data"].get("cid")
            else:
                print(f"获取cid失败: code={_rd.get('code')}, message={_rd.get('message', 'N/A')}", flush=True)
        except Exception as _e:
            print(f"获取cid失败: {_e}", flush=True)

        if _oid:
            from bilibili_api import comment as _comment
            _c = _comment.Comment(oid=_oid, type_=_comment.CommentResourceType.VIDEO,
                                 credential=_cred)
            _cr = await _c.send(_comment_text)
            print(f"评论已发: {_cr.get('rpid', 'N/A')}", flush=True)

        return _ret

    _result = _asyncio.run(_upload())
    print(f"\n✅ 上传完成! bvid={_result}", flush=True)
