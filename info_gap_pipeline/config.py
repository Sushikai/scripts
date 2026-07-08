"""配置中心"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUTS_DIR = BASE_DIR / "outputs"
TEMP_DIR = BASE_DIR / "temp"
LOGS_DIR = BASE_DIR / "logs"

# 子目录
BGM_DIR = DATA_DIR / "bgm"
CACHE_DIR = DATA_DIR / "cache"

# 确保目录存在
for d in [DATA_DIR, OUTPUTS_DIR, TEMP_DIR, LOGS_DIR, BGM_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 参考视频锚定（BV1EY7k6aEPg — 所有参数以此为准）
REFERENCE_BVID = "BV1EY7k6aEPg"
REFERENCE_VIDEO_URL = f"https://www.bilibili.com/video/{REFERENCE_BVID}/"
# 视频参数（横向16:9，锚定到BV1EY7k6aEPg）
VIDEO_WIDTH = 1920   # 横向宽度
VIDEO_HEIGHT = 1080  # 横向高度
VIDEO_FPS = 30
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "128k"

# 裁剪边距（去除水印）- 不再需要，因为我们用文字幻灯而非真实视频
CROP_MARGIN = 0.0

# BGM设置
BGM_VOLUME = 0.15  # 背景音乐音量（不抢戏）
BGM_FADE_DURATION = 2.0  # 淡入淡出秒数

# TTS设置
TTS_VOICE = "zh-CN-XiaoyiNeural"  # 更年轻活泼的女声，更接近参考视频风格
TTS_RATE = "+90%"  # 在+70%基础上再加快20%，约7.5chars/s
TTS_PITCH = "+0Hz"

# Whisper设置
WHISPER_MODEL = "small"  # small平衡速度与精度（medium太慢，small足够）
WHISPER_LANGUAGE = "zh"

# 视频时长容差（秒）
DURATION_TOLERANCE = 0.5

# bilibili cookies
BILIBILI_COOKIES_FILE = Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt")
# yt-dlp下载用cookies（Netscape格式，fengge 自动维护的持久化文件）
BILIBILI_COOKIES_NETSCAPE = Path.home() / ".hermes" / "cookies" / "bilibili_netscape.txt"

# MiniMax LLM (使用现有配置)
import sys, importlib.util
# 直接加载 ~/scripts/config/llm_config.py
_llm_cfg_path = Path("/Users/kaikai/scripts/config/llm_config.py")
_spec = importlib.util.spec_from_file_location("llm_config", _llm_cfg_path)
_llm_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm_module)
MINIMAX_CONFIG = _llm_module.MINIMAX_CONFIG

LLM_CONFIG = MINIMAX_CONFIG

# LLM脚本生成配置
SCRIPT_MAX_TOKENS = 800   # 增加到800，保证4-6分钟视频文案充足
SCRIPT_TEMPERATURE = 1.0

# 调度配置
SCHEDULE_TIMES = ["08:00", "12:00", "17:30"]  # 每天三次

# ── 话题源配置（按优先级）────────────────────────────────────────────────────
TOPIC_SOURCES = [
    "bilibili",   # B站热榜（必选）
    "douyin",     # 抖音热榜（必选）
    "baidu",      # 百度热搜
    "weibo",      # 微博热搜
    "twitter",    # Twitter/X热点
    "news",       # 财经/科技新闻API
]
# 每个slot取多少条候选话题
TOPICS_PER_SLOT = 8  # 每次运行取8条候选话题（信息差过滤后剩3-5条）

# ── BGM配置（默认背景音乐，低音不抢戏）───────────────────────────────────────
import os
_default_bgm = os.path.join(os.path.dirname(__file__), "data", "bgm", "info_gap_bgm_test.mp3")
DEFAULT_BGM_PATH = _default_bgm if os.path.exists(_default_bgm) else None

# 日志配置
LOG_FILE = LOGS_DIR / "pipeline.log"
LOG_LEVEL = "INFO"

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 5 # 秒

# User-Agent列表（防封）
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ── Telegram通知配置 ─────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "8685598400:AAFRNNfxLb6KJPoGL_9SS1UqiJ50s5uRUP8"
TELEGRAM_CHAT_ID = "8579393409"

# ── 防封禁：多IP代理池（轮换使用）───────────────────────────────────────────
# 格式：http://user:pass@host:port 或 http://host:port
# 可填写多个，pipeline每次随机选用一个
PROXY_POOL = [
    # "http://127.0.0.1:7890",   # 本地梯子（注释掉则不启用代理）
]
PROXY_ROTATE_INTERVAL = 300  # 每5分钟换一次IP

# ── 邮件告警配置 ─────────────────────────────────────────────────────────────
EMAIL_ALERT_ENABLED = False  # 设为True启用邮件告警
EMAIL_SMTP_HOST = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587
EMAIL_FROM = "your_email@gmail.com"
EMAIL_TO = ["arthur@example.com"]
EMAIL_USER = "your_email@gmail.com"
EMAIL_PASSWORD = "your_app_password"  # Gmail需要App Password

# ── A/B测试标题/缩略图配置 ──────────────────────────────────────────────────
AB_TEST_ENABLED = True
AB_TEST_VARIANTS = 3  # 每次生成几个标题变体
THUMBNAIL_PROMPT_TEMPLATE = (
    "信息差新闻视频封面，标题：「{title}」，"
    "风格：写实新闻风，高对比度，大字体居中，背景模糊，"
    "避免版权人物/logo，纯色或渐变背景，16:9比例"
)

# ── SEO优化配置 ─────────────────────────────────────────────────────────────
SEO_TAGS = ["信息差", "新闻", "科普", "财经", "科技", "国际", "热点", "干货"]
SEO_DESCRIPTION_TEMPLATE = (
    "「{title}」信息差视频，3分钟了解你不知道的事。每日更新，"
    "发现数据背后的真相，让你在信息洪流中领先一步。"
)

# ── 结尾Logo配置 ─────────────────────────────────────────────────────────────
LOGO_PATH = BASE_DIR / "data" / "logo.png"  # 结尾LOGO图片（透明背景PNG）
if not LOGO_PATH.exists():
    LOGO_PATH = None  # 不存在则跳过结尾Logo