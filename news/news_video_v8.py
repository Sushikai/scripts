#!/usr/bin/env python3
"""
news_video_v8.py — 信息差视频生产流水线 v8.1

本版本改动：
  1. TTS女声：zh-CN-XiaoxiaoNeural（晓晓，活泼有节奏感）
  2. 字幕布局：MarginV=70px（参考原视频距底87-116px位置）
  3. ASS字幕：MarginV=70px（srt_to_ass + subtitles滤镜两处）
  4. 去重检查：上传前调用 check_title_duplicated()
  5. 章节元数据：按新闻片段生成B站章节（title=新闻标题, start=时间戳）
  6. 实时抓取：百度热搜20条，每条独立背景视频+BV素材

功能：选题→写稿→TTS→下载→剪辑→字幕烧录→拼接→章节→上传去重→发评论
"""

import os, sys, re, uuid, shutil, subprocess, asyncio, requests, json, hashlib, threading
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

# 加载下载用cookie（素材账号 UID:140289989，仅用于下载）
DOWNLOAD_COOKIES = {}
_cookies_file = "/Users/kaikai/.hermes/secrets/bilibili_cookies_netscape.txt"
if os.path.exists(_cookies_file):
    with open(_cookies_file) as _f:
        for line in _f:
            line = line.strip()
            if line and not line.startswith('#') and '\t' in line:
                parts = line.split('\t')
                if len(parts) >= 6:
                    _name = parts[5].strip()
                    _value = parts[6].strip() if len(parts) > 6 else ''
                    DOWNLOAD_COOKIES[_name] = _value

TASK_ID = uuid.uuid4().hex[:8].replace("'", "").replace("`", "")

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

# ── B站上传凭证（主账号 UID 1650357577 "20岁还没开始环球旅行"）────────────────
BILIBILI_COOKIES = {
    "SESSDATA": "1b71344f%2C1794931778%2C7c347%2A52CjADKO-zY4oXSI-RHIglupU3i8erxjrJRzgwV7fKslNBXbNqTo5XY_LH6C7FssaeSM0SVjJnbWU3QVB4amNXOVZxVl9uWGlKZUlBNXZvYi1Fbkt5YmREendYNnZ2RWpNbGl2NTR0MVZ6a2FUTHpLd1ZFXzZfTWpQNkVRcWQwc1VfM2pYa0tVTm5RIIEC",
    "bili_jct": "82ecef2eb4e924cd031ffd29ed65093d",
    "buvid3": "1E52EDA5-3E2D-8D62-7CB1-610840052F2D68049infoc",
    "DedeUserID": "1650357577",
}

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
    "外交部", "中方回应", "央视新闻", "人民日报", "新华社", "官方通报",
    "召开会议", "政策发布", "稳步推进", "安全播出", "依法", "切实",
    "高度重视", "认真贯彻落实", "有关部门", "答记者问", "发表评论",
]
HOT_KEYWORDS = [
    "争议", "冲突", "爆发", "暴跌", "暴涨", "裁员", "倒闭", "揭秘",
    "曝光", "突发", "首次", "历史性", "惊人", "破局", "崩溃", "制裁",
    "对抗", "丑闻", "翻车", "打脸", "反转", "炸锅", "爆雷", "硬刚",
    "夺权", "逼宫", "内斗", "逃亡", "被捕", "通缉", "辟谣",
]

def _topic_score(t: dict) -> tuple:
    """返回(分数, 话题)，分数越高越优先"""
    topic = t.get("topic", "")
    score = 0
    for kw in HOT_KEYWORDS:
        if kw in topic:
            score += 5
    for kw in BORING_KEYWORDS:
        if kw in topic:
            score -= 3
    hot = t.get("hot", "") or ""
    try:
        score += min(int(float(hot)) // 10000, 3)
    except:
        pass
    return (score, topic)

def _sort_topics_by_controversy(topics: list, num: int = 20) -> list:
    """争议话题优先：分数降序，取前num条"""
    scored = [(_topic_score(t), t) for t in topics]
    scored.sort(key=lambda x: -x[0][0])
    return [t for _, t in scored[:num]]

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

    # ── 解法3-10: 争议话题优先 ────────────────────────────────────────
    # 打分逻辑：争议/冲突/突发/曝光类话题加分，央视官媒语气扣分
    # 注：直接使用全局 BORING_KEYWORDS / HOT_KEYWORDS / _topic_score

    # 按source多样性打散，避免单一源垄断，同时按争议分数排序
    # 先过滤掉分数<-5的极无聊话题
    scored = [(_topic_score(t), t) for t in topics]
    # 分数降序，同分数内按源轮换
    scored.sort(key=lambda x: -x[0])
    # 取分数最高的话题重新建池
    topics_sorted = [t for _, t in scored]

    # 按source打散
    diversified = []
    by_source = {}
    for t in topics_sorted:
        src = t["source"]
        if src not in by_source:
            by_source[src] = []
        by_source[src].append(t)
    pool_idx = 0
    source_keys = list(by_source.keys())
    while len(diversified) < num and len(diversified) < len(topics):
        src = source_keys[pool_idx % len(source_keys)]
        pool_idx += 1
        if by_source[src]:
            diversified.append(by_source[src].pop(0))

    # ── 解法3-10 fallback: 保底15条 ───────────────────────────────────
    if len(diversified) < 15:
        log(f"  ⚠️ 话题不足15条，仅 {len(diversified)} 条，将影响视频丰富度")

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

# 解法2-3: 同事件归类（合并相似话题）
EVENT_GROUPS = {
    "欧盟关税": ["欧盟加征关税", "欧盟对华关税", "欧盟汽车关税", "中欧贸易摩擦", "欧洲关税"],
    "特斯拉裁员": ["特斯拉全球裁员", "特斯拉中国裁员", "特斯拉裁员", "马斯克裁员"],
    "保时捷布加迪": ["保时捷出售布加迪", "保时捷布加迪", "保时捷股权", "布加迪出售"],
    "辽宁车祸": ["辽宁重大交通事故", "辽宁车祸", "辽宁交通事故", "辽宁事故"],
    "A股上涨": ["A股收涨", "A股三大指数", "沪指3400", "A股重回3400", "沪深两市"],
    "比亚迪": ["比亚迪电动车", "比亚迪关税", "比亚迪欧洲", "国产电动车"],
    "浏阳烟花": ["浏阳烟花厂爆炸", "浏阳爆炸", "烟花厂事故"],
    "豆包AI": ["豆包付费", "豆包AI", "字节豆包", "豆包订阅"],
}

def merge_similar_events(topics: list) -> list:
    """解法2-3: 将相似话题归类，保留信息量最大的"""
    merged = []
    used = set()
    for t in topics:
        topic = t["topic"]
        if topic in used:
            continue
        # 检查是否属于已归类事件组
        found_group = None
        for group_name, group_topics in EVENT_GROUPS.items():
            if any(gt in topic or topic in gt for gt in group_topics):
                found_group = group_name
                break
        if found_group and found_group not in used:
            # 保留组名（信息量最丰富的话题标题）
            merged.append({"topic": found_group, "source": t["source"], "group": found_group})
            used.add(found_group)
        else:
            merged.append(t)
            used.add(topic)
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# 简化版兜底模板（SCRIPT_TEMPLATES_V8）— 60-100字准确播报风格
# 不再使用冗长的 intro/content/footer 结构，直接一段式播报
# ══════════════════════════════════════════════════════════════════════════════

# 每条模板 = 一段60-100字的自然播报文字
# 风格：像真实新闻主播一样，简洁、有节奏、不废话
SCRIPT_TEMPLATES_V8 = {
    "浏阳烟花厂爆炸": "昨晚浏阳一烟花厂发生爆炸，已确认造成26人死亡、61人受伤。事故发生在晚间作业时段，现场火光冲天，威力巨大。被困群众已全部救出，伤者送医救治。事故原因初步判断为生产车间违规作业，调查仍在进行。",
    "长沙市委市政府致歉": "五一假期长沙多个景区人流量远超承载极限引发混乱，市政府今天公开道歉，承诺完善预警机制、加强客流管控。旅游部门紧急出台新規，要求全市景区严格执行限流，强制启动预约参观制度。",
    "广交会": "第135届广交会在广州闭幕，出口成交额同比增长近14%，参展企业数量创历史新高。多个企业反馈，来自一带一路沿线国家的订单增长尤为明显，新兴市场正在成为新的增长引擎。",
    "苹果/iPhone": "多方消息证实，苹果公司正在与多家半导体供应商洽谈建立新合作关系，降低对单一供应商的依赖。如果协议最终达成，可能重塑当前半导体产业格局，预计在未来数月内完成谈判。",
    "奔跑吧/跑鞋": "最新一期《奔跑吧》节目揭露运动鞋市场虚假宣传问题，超九成受测产品存在参数虚标。消协发布消费预警，提醒消费者以实际试穿体验为准，多家被点名品牌表示将自查整改。",
    "军犬": "某部队救援演练中，一只军犬与曾经的训导员久别重逢却扭头走开，引发网友热议。训导员发视频解释，这是狗在应激状态下的正常反应，并不代表情感疏离，许多养狗网友表示感同身受。",
    "彩电": "最新统计数据显示，去年国内彩电全渠道销量仅2700多万台，创近十年新低。智能手机和平板电脑的普及抢占了大屏娱乐市场，主流厂商正加速向智能化转型，但价格战仍在持续，利润空间被进一步压缩。",
    "谢娜": "某大型颁奖典礼后台，多位明星集体为谢娜送上花篮祝贺，粉丝们纷纷拍照打卡。作为国内知名主持人，谢娜长期活跃于综艺舞台，当晚发表获奖感言感谢观众多年支持，称会继续努力带来更好节目。",
    "世乒赛": "世界乒乓球团体锦标赛激战正酣，澳大利亚队出人意料地变阵派出年轻小将。主教练表示大赛是年轻选手成长的最好舞台。中国队尽管整体实力占优，也不敢轻敌派出全主力，其他协会队伍同样表现出色。",
    "印度/巴基斯坦": "印度与巴基斯坦边境局势出现新的紧张态势，双方在争议地区发生多次小规模对峙。印度军方加强了边境巡逻，多国呼吁双方保持克制，通过对话协商解决分歧。",
    "特朗普": "特朗普再掀政治波澜，其支持者在多个州组织大规模集会表达不满。特朗普本人在社交媒体上连续发声重申政治主张。最新民调显示特朗普在共和党内的支持率依然领先，2026年中期选举可能再次成为美国政治分水岭。",
    "中东": "中东地区紧张局势持续升级，多方角力引发国际社会高度关注。主要产油国已就可能出现的供应中断制定预案，能源分析师警告如果冲突扩大国际油价可能面临显著上行压力，联合国等多方正在积极斡旋。",
    "欧盟/关税": "欧盟正式通过对华电动车加征关税决议，税率最高超35%。中国商务部回应称将采取一切必要措施维护企业合法权益。多家中国车企表示将积极开拓其他市场，业内人士指出这将加速中国车企在欧洲的本地化建厂进程。",
    "特斯拉": "特斯拉全球裁员计划持续推进，中国区也受波及，裁员比例约为15%，主要涉及销售和售后部门。比亚迪等本土品牌竞争激烈，特斯拉市场份额遭到蚕食，特斯拉官方表示裁员是为提高运营效率应对行业价格战。",
    "华为": "华为新品发布会引发行业广泛关注，多项技术指标达到行业领先水平。知情人士透露华为在芯片设计领域取得新突破，性能超出市场预期。分析师认为这标志着华为正在全面回归全球高端手机市场。",
    "地震": "中国地震台网正式测定今日某地发生5.8级地震，震源深度较浅，多地震感明显。地震造成部分地区房屋受损，暂无人员死亡报告。消防和医疗救援队伍已赶赴现场，地质专家提醒未来72小时需警惕较强余震。",
    "暴雨/洪水": "受强对流天气影响，南方多省出现大暴雨过程，部分河流超警戒水位。防汛部门启动一级预警要求沿线居民紧急转移，多地出现城市内涝，农田受淹超十万公顷。气象部门预计未来三天南方仍有持续性强降雨。",
    "俄罗斯": "俄罗斯与西方国家在多个议题上分歧加剧，双方互相施加新一轮制裁。能源管道过境问题成为近期博弈焦点，国际油价和天然气价格因此出现明显波动，欧洲能源安全压力再度上升，各方呼吁通过外交途径化解分歧。",
    "乌克兰": "乌克兰局势最新进展牵动全球目光，各方在关键问题上仍存在根本分歧，和平前景尚不明朗。联合国秘书长再次呼吁停火推动政治解决方案，西方国家继续提供军事援助，人道主义危机持续恶化，平民伤亡报告不断增加。",
    "AI/人工智能": "多家科技巨头近期发布新一代AI模型，在推理能力和多模态处理上有显著提升。国家相关部门出台AI产业发展指导意见，明确数据安全和算法合规要求，业内认为这将引导行业健康有序发展。",
    "保时捷": "保时捷宣布出售布加迪部分股权，财报数据显示2025年保时捷利润暴跌超九成，主要受中国市场销量下滑和价格战影响。保时捷中国区负责人多次强调品牌坚持不降价、不国产的策略不变，业内人士分析品牌溢价正在受到挑战。",
    "豆包AI": "字节跳动旗下豆包正式推出付费订阅服务，标准版每月68元，高级版定价更高。官方声明称免费基础服务维持不变，付费方案主要为有深度使用需求的用户设计。豆包同时宣布与多家教育机构合作推出AI辅助学习功能。",
    "红果短剧": "红果短剧会员可免费观看平台全部内容，但受版权限制部分仍需单独付费。平台方表示会员定价综合考虑了用户付费习惯和内容成本。红果短剧还宣布与多家影视制作公司的独家合作计划，未来三个月内将上线数百部独播内容。",
    "广州楼市": "广州二手房市场连续两个月网签突破一万套，成交回暖趋势明显。业内人士分析购房信心正在恢复，改善型需求成为市场主力，银行下调贷款利率进一步降低了购房成本，多个热门区域出现排队看房现象。",
    "电影票房": "2026年全国电影票房已突破135亿元，五一档期单日票房破6亿元，观影场次达233万场，均创历年同期新高。国产影片表现强劲，多部作品口碑与票房双丰收，优质内容和观影意愿回归是市场复苏的主要驱动力。",
    "汤姆斯杯": "中国羽毛球队在汤姆斯杯决赛中以3比1击败法国队，历史上第12次捧起冠军奖杯。本届赛事多名年轻选手获得出场机会通过高水平比赛历练明显成长，主教练表示队伍整体状态良好，将继续为即将到来的国际大赛做好准备。",
    "辽宁车祸": "辽宁省某路段发生一起重大交通事故，核载6人的面包车实载21人，碰撞路边树木后侧翻。事故已造成8人死亡、13人不同程度受伤，伤者被紧急送医救治。交警部门正在调查事故原因，涉事车辆涉嫌严重超载。",
    "返程": "五一假期最后一天，全国多地迎来返程高峰，绕城高速和主要进城通道出现阶段性拥堵，部分高速路网长时间高位运行。铁路部门加开多趟临客满足出行需求，各地交管部门全员上岗疏导交通。",
    "新能源汽车": "财政部等多部门联合发布新能源汽车补贴新政，大幅提高续航里程和能量密度补贴门槛。业内人士指出新政将加速淘汰低端产能，推动行业向高质量发展转型，比亚迪、特斯拉等头部企业表示欢迎。",
    "养老金": "多省陆续公布2026年养老金调整方案，继续采取定额调整、挂钩调整和倾斜调整相结合的办法。企业退休人员月人均养老金将继续提高，调整幅度略高于去年，参保人员可通过当地社保App查询个人到账情况。",
    "A股": "A股三大指数今日集体收涨，沪指重新站上3400点整数关口，沪深两市成交额突破1.2万亿元，外资延续净流入态势。科技股和新能源板块领涨，分析师认为政策暖风和业绩预期改善是本轮上涨的主要驱动力。",
}


# ── 实时热搜新闻抓取（优化点1）────────────────────────────────────────────
# 优先从百度热搜实时获取，若无结果再用 Bing 新闻搜索
# 缓存结果避免重复请求，每次运行只抓一次
import threading

_TOPIC_SCRIPTS_CACHE = None  # 全局缓存
_TOPIC_SCRIPTS_LOCK = threading.Lock()  # 缓存读写锁

_WHISPER_MODEL = None  # WhisperModel 全局缓存，避免重复加载
_WHISPER_MODEL_LOCK = threading.Lock()

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


def fetch_topic_scripts(topics_list: list = None) -> dict:
    """
    获取今日实时新闻脚本：
    1. 优先从百度热搜榜单获取话题 → 自动生成60-80字播报脚本
    2. 对于传入的 topics_list 中无法匹配的话题，再用 Bing 新闻搜索补充
    返回 {topic: script} 字典
    """
    global _TOPIC_SCRIPTS_CACHE
    # 快速路径：无锁读取（已初始化时）
    if _TOPIC_SCRIPTS_CACHE is not None:
        return _TOPIC_SCRIPTS_CACHE

    # 慢路径：加锁初始化（只有一个线程执行）
    with _TOPIC_SCRIPTS_LOCK:
        # 双重检查（其他线程可能已初始化）
        if _TOPIC_SCRIPTS_CACHE is not None:
            return _TOPIC_SCRIPTS_CACHE
    
    scripts = {}
    
    # Step 1: 抓取百度热搜
    baidu_topics = _fetch_baidu_hot_search()
    
    # Step 2: 对热搜话题生成脚本（用话题名本身作为种子，结合常见新闻结构生成）
    for topic in baidu_topics:
        if len(topic) >= 4:  # 过滤短标题
            # 保留百度热搜返回的真实脚本（word + desc 组合）
            # _generate_natural_script 仅作为完全没有 desc 时的兜底
            scripts[topic] = baidu_topics[topic]
    
    # Step 3: 补充传入 topics_list 中未匹配的条目（用 Bing 新闻搜索）
    if topics_list:
        for item in topics_list:
            topic = item if isinstance(item, str) else item.get("topic", "")
            if topic and topic not in scripts:
                script = _fetch_bing_news(topic)
                if script:
                    scripts[topic] = script
                else:
                    scripts[topic] = _generate_natural_script(topic)
    
    _TOPIC_SCRIPTS_CACHE = scripts
    log(f"  📊 fetch_topic_scripts 完成，共 {len(scripts)} 条脚本")
    return scripts


def _generate_natural_script(topic: str) -> str:
    """
    兜底脚本生成：当百度/Bing 都拿不到 desc 时使用。
    生成60-90字新闻播报风格文案，不含"你怎么看"等营销废话。
    """
    clean = re.sub(r'^(【.*?】|🔥|#\w+)', '', topic).strip()
    if not clean:
        return "近日此事引发关注，相关情况持续更新中。"
    if len(clean) <= 8:
        return f"{clean}。此事引发关注，相关情况正在持续更新。"
    # 话题本身超过8字时，围绕话题生成一句有信息量的播报
    return f"{clean}。此事引发广泛热议，相关详情正在持续更新。"


# ══════════════════════════════════════════════════════════════════════════════
# 本地大模型支持（Ollama / LocalAI）— 让脚本更像真人说话
# ══════════════════════════════════════════════════════════════════════════════
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434/api/generate")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5:7b")

def call_local_llm(prompt: str, timeout: int = 60) -> str:
    """
    调用本地Ollama大模型生成更自然的脚本
    环境变量:
      LOCAL_LLM_URL  - Ollama API地址 (默认 http://localhost:11434/api/generate)
      LOCAL_LLM_MODEL - 模型名称 (默认 qwen2.5:7b)
    """
    try:
        payload = {
            "model": LOCAL_LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.8,
                "num_predict": 200,
            }
        }
        r = _get_session().post(
            LOCAL_LLM_URL,
            json=payload,
            timeout=timeout
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("response", "").strip()
    except Exception as e:
        log(f"  ⚠️ 本地LLM调用失败: {e}")
    return ""


def generate_script_local(topic: str) -> str:
    """
    用本地大模型生成更像真人说话风格的新闻脚本
    60-100字，口语化，有节奏感
    """
    prompt = f"""你是一个B站信息差视频的文案写作助手。
请围绕话题「{topic}」写一段60-100字的新闻播报文案。

要求：
1. 像朋友聊天一样自然，不要像官方通稿
2. 有信息量，能让人快速了解发生了什么
3. 可以有点小情绪（惊讶、有趣、震惊等）
4. 不要加"你怎么看"、"欢迎评论区留言"等引导话
5. 直接输出文案，不要前缀

文案："""
    
    result = call_local_llm(prompt)
    if result and len(result) >= 10:
        # 清理可能的大模型输出格式
        result = re.sub(r'^(文案|回答|结果)：\s*', '', result)
        result = result.strip()
        return result
    return ""


def generate_script_v8(topic: str, index: int) -> str:
    """
    优化版文案生成：
    优先从实时百度热搜缓存获取 → Bing新闻搜索 → 本地大模型 → 本地模板兜底
    """
    global _TOPIC_SCRIPTS_CACHE

    # 实时缓存优先（本次运行只抓一次）
    if _TOPIC_SCRIPTS_CACHE is None:
        # main() 已调用 fetch_topic_scripts()，这里只做兜底
        fetch_topic_scripts()
    
    # 精确匹配
    if topic in _TOPIC_SCRIPTS_CACHE:
        log(f"  📝 实时脚本[{index+1}]: {topic[:15]}...")
        return _TOPIC_SCRIPTS_CACHE[topic]
    
    # Bing 新闻搜索兜底
    log(f"  🔍 未缓存，尝试Bing搜索: {topic[:20]}...")
    script = _fetch_bing_news(topic)
    if script:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script
    
    # 本地大模型生成（更真人化）
    log(f"  🤖 尝试本地大模型生成: {topic[:20]}...")
    script = generate_script_local(topic)
    if script and len(script) >= 15:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script
    
    # 兜底模板（已简化）
    topic_clean = re.sub(r'[^\w\u4e00-\u9fff]+', '', topic)
    for key, template in SCRIPT_TEMPLATES_V8.items():
        if key in topic or topic in key:
            log(f"  📝 模板兜底[{index+1}]: {topic[:15]}...")
            return template

    # 最终兜底：话题本身展开
    log(f"  ⚠️ 完全无脚本，使用话题本身: {topic[:20]}...")
    return _generate_natural_script(topic)


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
                        except:
                            pass
            log(f"  ⚠️ B站搜索[{attempt+1}]未找到合适视频，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
        except Exception as e:
            log(f"  ⚠️ B站搜索[{attempt+1}]异常: {e}，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
    log(f"  ⚠️ B站搜索最终失败: {topic[:15]}")
    return None

def download_bilibili_video(bvid: str, output_path: str, clip_dur: float = None) -> bool:
    """下载B站视频（带cookie提高质量）"""
    try:
        r_view = _get_session().get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            headers=HEADERS, cookies=DOWNLOAD_COOKIES, timeout=10
        )
        d_view = r_view.json()
        if d_view.get("code") != 0:
            return False
        cid = d_view.get("data", {}).get("cid")
        if not cid:
            pages = d_view.get("data", {}).get("pages", [])
            if pages:
                cid = pages[0].get("cid")
        if not cid:
            return False

        r_play = _get_session().get(
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=64&fnval=0",
            headers=HEADERS, cookies=DOWNLOAD_COOKIES, timeout=10
        )
        d_play = r_play.json()
        if d_play.get("code") != 0:
            return False
        urls = d_play.get("data", {}).get("durl", [])
        if not urls or not urls[0].get("url"):
            return False

        video_url = urls[0]["url"]
        # 解法1-3/4/5: 固定H.264 high profile编码参数
        cmd = [
            "ffmpeg", "-y",
            "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://www.bilibili.com/\r\n",
            "-i", video_url,
            "-t", str(clip_dur) if clip_dur else "999",
            # 解法1-3: H.264 high profile，兼容性最好
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-profile:v", "high", "-level", "3.1",
            # 解法1-5: 帧率锁定30fps
            "-r", "30",
            # 解法1-6: 强制SAR=1:1
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
            # 解法1-7: 固定音频参数
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            # 解法1-8: movflags确保快速启动
            "-movflags", "+faststart",
            "-fps_mode", "cfr",
            output_path
        ]
        r3 = subprocess.run(cmd, capture_output=True, timeout=int((clip_dur or 60) * 2 + 60))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
            log(f"  ✅ BV={bvid} {os.path.getsize(output_path)//1024}KB")
            return True
    except subprocess.TimeoutExpired:
        log(f"  ⚠️ BV={bvid} 下载超时")
    except Exception as e:
        log(f"  ⚠️ BV={bvid} 下载异常: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 维度⑤：TTS配音
# ══════════════════════════════════════════════════════════════════════════════

def generate_tts(script: str, output_path: str, index: int) -> bool:
    """edge-tts配音（稍慢语速，新闻讲解节奏）+ 3次重试"""
    import time
    for attempt in range(3):
        try:
            import edge_tts
            async def do_tts():
                # 晓晓女声：新闻播报节奏，语速加快(+10%)
                communicate = edge_tts.Communicate(script, "zh-CN-XiaoxiaoNeural", rate="+10%")
                await communicate.save(output_path)
            asyncio.run(do_tts())
            if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
                dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", output_path],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip() or 0)
                log(f"  ✅ 第{index+1}条音频: {dur:.0f}秒")
                return True
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠️ TTS第{attempt+1}次失败: {e}，{wait}秒后重试")
            time.sleep(wait)
    # Fallback: macOS say command (offline, works without network)
    try:
        import tempfile
        script_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', encoding='utf-8', delete=False)
        script_file.write(script)
        script_file.flush()
        script_file.close()
        aiff_path = output_path.replace('.m4a', '.aiff')
        result = subprocess.run(
            ['say', '-v', 'Flo', '-f', script_file.name, '-o', aiff_path],
            capture_output=True, timeout=60
        )
        os.unlink(script_file.name)
        if result.returncode == 0 and os.path.exists(aiff_path):
            # Convert AIFF to M4A
            subprocess.run(
                ['afconvert', aiff_path, '-f', 'm4af', '-d', 'aac', output_path],
                capture_output=True, timeout=30
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
                dur = float(subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', output_path],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip() or 0)
                log(f"  ✅ 第{index+1}条音频(Flo): {dur:.0f}秒 [say fallback]")
                return True
    except Exception as e2:
        log(f"  ⚠️ say fallback也失败: {e2}")
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
    v9版本字幕烧录，布局参考2026.05.08信息差视频：
    - 字幕区：内容区底部上方约70px，白色字体(RGB≈200,201,165)
    - 章节栏：底部1/3高度，暗橄榄绿底(RGB≈69,76,55)，白色文字
    - 分辨率：1280×720（16:9）
    """
    frame_dir = None
    cap = None
    try:
        import pysrt
        from PIL import Image, ImageDraw, ImageFont

        # 找中文字体（macOS兼容路径）
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        fnt = None
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    fnt = ImageFont.truetype(fp, 34)  # 字体增大20%
                    log(f"  使用字体: {os.path.basename(fp)}")
                    break
                except:
                    continue
        if fnt is None:
            fnt = ImageFont.load_default()

        # 解析SRT
        if not os.path.exists(srt_path):
            return False
        subs = pysrt.open(srt_path)

        # 分辨率 1280×720（16:9）
        width, height = 1280, 720
        # 底部1/10高度 = 章节栏区域（y=648到720），避免遮挡内容
        chapter_bar_height = int(height / 10)  # 72px（原来1/3太大）
        chapter_bar_top = height - chapter_bar_height  # 648
        # 字幕区：白底黑字，蒙版占底部1/10，字幕文字占蒙版内7/10
        subtitle_bg_top = int(height * 0.9)   # 底部1/10起始（648px，蒙版高72px）
        subtitle_bg_bottom = height             # 720
        # 章节栏颜色（白色背景配黑字或暗橄榄绿均可，这里跟随白底黑字风格）
        chapter_bar_color = (255, 255, 255)
        # 章节文字颜色（黑色）
        chapter_text_color = (0, 0, 0)
        # 字幕文字颜色（黑色）
        subtitle_color = (0, 0, 0)
        # 字幕背景（白色）
        subtitle_bg_color = (255, 255, 255)

        # 找中文字体
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        fnt = None
        fnt_path = None
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    fnt = ImageFont.truetype(fp, 31)  # 字体增大20%
                    fnt_path = fp
                    log(f"  字幕字体: {os.path.basename(fp)}")
                    break
                except:
                    continue
        if fnt is None:
            fnt = ImageFont.load_default()
            fnt_path = "default"

        # 找章节栏用的字体（可用同一字体但稍大）
        try:
            chapter_fnt = ImageFont.truetype(fnt_path, 22)
        except:
            chapter_fnt = fnt

        # 创建临时目录存放帧
        frame_dir = f"/tmp/frames_{uuid.uuid4().hex[:6]}"
        os.makedirs(frame_dir, exist_ok=True)

        # 用OpenCV提取帧 + 逐帧烧录
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps_in = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        frame_idx = 0
        rendered = set()  # 记录已烧录的字幕序号
        max_frames = int(clip_dur * fps_in) + 30  # 防止损坏视频导致帧数爆炸

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            # 安全上限，防止OOM
            if frame_idx >= max_frames:
                log(f"  ⚠️ 帧数已达上限 {max_frames}，截断")
                break
            timestamp = frame_idx / fps_in

            # 检查需要渲染的字幕
            current_sub = None
            for sub in subs:
                start_s = sub.start.ordinal / 1000.0
                end_s = sub.end.ordinal / 1000.0
                if start_s <= timestamp <= end_s:
                    current_sub = sub.text
                    break

            # PIL绘制
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            draw = ImageDraw.Draw(pil_img)

            # v9新布局：底部1/10绘制章节栏（暗橄榄绿底+白色文字+章节编号+话题标题）
            if topic_title:
                draw.rectangle([0, chapter_bar_top, width, height], fill=chapter_bar_color)
                # 章节文字：第X条/共Y条 | 话题标题
                chapter_text = f"第{segment_index}条/共{total_segments}条 | {topic_title}"
                bbox = draw.textbbox((0, 0), chapter_text, font=chapter_fnt)
                title_w = bbox[2] - bbox[0]
                title_h_val = bbox[3] - bbox[1]
                title_x = (width - title_w) // 2
                title_y = chapter_bar_top + (chapter_bar_height - title_h_val) // 2
                # 黑色描边
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if dx != 0 or dy != 0:
                            draw.text((title_x + dx, title_y + dy), chapter_text, font=chapter_fnt, fill=(0, 0, 0))
                # 白色主文字
                draw.text((title_x, title_y), chapter_text, font=chapter_fnt, fill=chapter_text_color)

            # 字幕区：无蒙版，黄色粗体字幕，居中显示
            if current_sub:
                draw = ImageDraw.Draw(pil_img)
                # 计算字幕位置（画面居中）
                bbox = draw.textbbox((0, 0), current_sub, font=fnt)
                text_w = bbox[2] - bbox[0]
                text_h_actual = bbox[3] - bbox[1]
                text_x = (width - text_w) // 2
                text_y = (height - text_h_actual) // 2
                # 深色描边
                for dx in [-2, -1, 0, 1, 2]:
                    for dy in [-2, -1, 0, 1, 2]:
                        if dx != 0 or dy != 0:
                            draw.text((text_x + dx, text_y + dy), current_sub, font=fnt, fill=(80, 60, 0))
                # 黄色主文字
                draw.text((text_x, text_y), current_sub, font=fnt, fill=(255, 220, 0))
                rendered.add(frame_idx)

            # 保存帧
            pil_img.save(f"{frame_dir}/frame_{frame_idx:06d}.jpg", quality=90)
            frame_idx += 1

        cap.release()
        if frame_dir is not None and os.path.exists(frame_dir):
            shutil.rmtree(frame_dir, ignore_errors=True)

        if frame_idx == 0:
            log(f"  ⚠️ 无帧可处理")
            return False

        log(f"  PIL烧录: {frame_idx}帧, {len(rendered)}帧有字幕")

        # 解法4-6: 用FFmpeg将帧序列重新编码（不用字幕滤镜）
        # 注意: 使用glob模式
        # 输入0: PIL生成的帧序列(已裁切10%+字幕)
        # 输入1: TTS音频
        fps_out = min(fps_in, 30)
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps_out),
            "-pattern_type", "glob",
            "-i", f"{frame_dir}/*.jpg",
            "-i", tts_audio_path,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-profile:v", "high", "-level", "3.1",
            "-vf", "scale=1280:720,setsar=1",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(clip_dur),
            "-pix_fmt", "yuv420p",
            output_path
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=int(clip_dur * 1.5 + 60))

        # 清理帧目录
        if frame_dir is not None and os.path.exists(frame_dir):
            shutil.rmtree(frame_dir, ignore_errors=True)

        if r.returncode == 0:
            return os.path.exists(output_path) and os.path.getsize(output_path) > 5000
        else:
            log(f"  ⚠️ PIL烧录重编码失败: {r.stderr[-150:]}")
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
    except:
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
    hot_topics = list(baidu_scripts.keys())[:6]

    # 构建话题列表并争议排序优先
    topics = [{"topic": t, "bvid": None} for t in hot_topics]
    topics = _sort_topics_by_controversy(topics, num=20)

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
    log(f"\n② 并行处理 {len(topics)} 条话题（8线程）...")

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
        if bv_id:
            download_bilibili_video(bv_id, bg_video_path, clip_dur=audio_dur)
        else:
            searched_bv = search_bilibili_video(topic)
            if searched_bv:
                download_bilibili_video(searched_bv, bg_video_path, clip_dur=audio_dur)

        srt_path = str(OUTPUT_DIR / f"v8_sub_{sid}.srt")
        ass_path = str(OUTPUT_DIR / f"v8_sub_{sid}.ass")
        generate_srt_from_audio(audio_path, srt_path, i, script)
        if os.path.exists(srt_path):
            srt_to_ass(srt_path, ass_path)

        return (i, topic, audio_path, srt_path, ass_path, bg_video_path, bv_id, audio_dur)

    segments = []
    with ThreadPoolExecutor(max_workers=8) as pool:
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
    # 动态生成标题：从今天真实话题中取前3条
    _title_topics = [t["topic"] for t in _TODAY_TOPICS[:3]]
    _title_topics_str = "、".join(_title_topics)
    _time_mark = "早差" if _dt.now().hour < 12 else "晚差"
    _title = f"【{_time_mark}信息差】{date_short}：{_title_topics_str}…今日热点速递"

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
        _page = video_uploader.VideoUploaderPage(
            path=_final_mp4,
            title=_title,
            description=""
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
            title=_title,
            desc=_desc,
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
