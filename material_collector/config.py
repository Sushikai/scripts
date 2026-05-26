#!/usr/bin/env python3
"""
material_collector - 短视频素材自动采集系统
支持平台：抖音 / B站 / 小红书
风格：火花宝宝（可爱萌娃）+ 不存在的小镇（荒诞探险）
"""

from __future__ import annotations

import os
from pathlib import Path

# ============ 路径配置 ============
PROJECT_ROOT = Path(__file__).parent
MATERIALS_DIR = PROJECT_ROOT / "materials"
MATERIALS_RAW = MATERIALS_DIR / "raw"
MATERIALS_PROCESSED = MATERIALS_DIR / "processed"
DB_DIR = PROJECT_ROOT / "database"
LOGS_DIR = PROJECT_ROOT / "logs"

for d in [MATERIALS_RAW, MATERIALS_PROCESSED, DB_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DB_DIR / "materials.db"
VECTOR_DB_PATH = DB_DIR / "chroma_db"

# ============ 平台配置 ============
PLATFORMS = {
    "douyin": {
        "package": "com.ss.android.ugc.aweme",
        "activity": ".main.MainActivity",
        "search_keywords": ["宝宝可爱", "萌娃日常", "火花宝宝", "小孩搞笑", "亲子萌"],
    },
    "bilibili": {
        "package": "tv.danmaku.bili",
        "activity": ".MainActivity",
        "search_keywords": ["萌娃", "可爱宝宝", "亲子", "火花宝宝"],
    },
    "xiaohongshu": {
        "package": "com.xingin.xhs",
        "activity": ".index.v2.IndexActivityV2",
        "search_keywords": ["宝宝", "萌娃", "亲子", "火花宝宝"],
    },
}

# ============ ADB 配置 ============
ADB_HOST = "127.0.0.1"
ADB_PORT = 16384  # MuMu Player Pro 默认端口
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920

# MuMu 模拟器屏幕参数（根据实际调整）
SWIPE_DURATION = 300       # ms
SWIPE_INTERVAL = 1.5       # 秒，每次滑动后等待
SCREENSHOT_QUALITY = 85    # jpeg 质量

# ============ OCR 配置 ============
OCR_CONFIG = {
    "engine": "paddle",  # paddle | baidu
    "paddle": {
        "use_gpu": True,
        "use_slim": True,
        "lang": "ch",
    },
    "baidu": {
        "api_key": os.getenv("BAIDU_OCR_API_KEY", ""),
        "secret_key": os.getenv("BAIDU_OCR_SECRET_KEY", ""),
        "endpoint": "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic",
    },
    "min_text_height": 15,      # 过滤太小文字（像素）
    "confidence_threshold": 0.6,
}

# ============ AI 处理配置（Ollama） ============
OLLAMA_CONFIG = {
    "base_url": "http://localhost:11434",
    "model": "qwen2.5:32b-instruct-q4_K_M",  # 主模型
    "backup_model": "gemma3:4b",              # 备用模型
    "timeout": 120,
    "retry_times": 3,
}

# ============ 内容风格配置 ============
STYLE_PROMPTS = {
    "火花宝宝": {
        "keywords": ["宝宝", "萌娃", "可爱", "小孩", "儿童", "宝贝", "娃", "童言"],
        "mood": "温暖、治愈、可爱、萌趣",
        "topic_examples": ["宝宝吃饭", "宝宝睡觉", "宝宝学走路", "宝宝说话", "亲子互动"],
    },
    "不存在的小镇": {
        "keywords": ["探险", "荒诞", "奇幻", "魔法", "怪物", "谜题", "奇怪", "诡异", "梦境"],
        "mood": "荒诞、奇幻、神秘、幽默",
        "topic_examples": ["奇幻探险", "梦境故事", "魔法世界", "神秘小镇", "荒诞日常"],
    },
}

# ============ 采集配置 ============
COLLECTOR_CONFIG = {
    "max_duration": 3600,        # 最大运行时长（秒），默认1小时
    "max_per_keyword": 50,       # 每个关键词最大采集条数
    "dedup_window": 3600,        # 去重时间窗口（秒）
    "auto_scroll_count": 30,     # 每次搜索后自动滑动次数
    "skip_existing": True,       # 跳过已采集的内容
    "screenshot_interval": 2.0,   # 截图间隔（秒）
    "content_types": {
        "subtitle": True,         # 采集字幕/文字
        "comment": True,         # 采集评论
        "danmu": True,           # 采集弹幕（B站）
        "description": True,      # 采集视频描述
    },
}

# ============ Chroma 向量库配置 ============
CHROMA_CONFIG = {
    "persist_directory": str(VECTOR_DB_PATH),
    "collection_name": "video_materials",
    "distance_metric": "cosine",
}

# ============ 日志配置 ============
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    "file": LOGS_DIR / "collector.log",
    "max_bytes": 10 * 1024 * 1024,
    "backup_count": 5,
}