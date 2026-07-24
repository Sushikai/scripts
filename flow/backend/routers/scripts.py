"""/api/scripts:列出 /Users/kaikai/scripts/ 下所有视频相关脚本(路径 + 存在性 + 大小 + mtime)。

只读文件系统,不发网络请求。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["scripts"])

SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()

# 已知脚本清单:按 category 分组
SCRIPTS = [
    # 主流水线 wrapper (flow 内置, 用 subprocess / import 调用)
    {"name": "fengge_pipeline", "path": "/Users/kaikai/scripts/video/fengge_pipeline.py", "category": "wrapper"},
    {"name": "info_gap_pipeline", "path": "/Users/kaikai/scripts/info_gap_pipeline/main.py", "category": "wrapper"},
    {"name": "tiktok_story_bili", "path": "/Users/kaikai/scripts/tiktok_story_bili/upload_bili.py", "category": "wrapper"},
    {"name": "material_collector", "path": "/Users/kaikai/scripts/material_collector", "category": "wrapper"},
    # 评论/回复/粉丝
    {"name": "bilibili_reply_v17", "path": "/Users/kaikai/scripts/comment/bilibili_reply_v17.py", "category": "comment"},
    {"name": "fan_hunter", "path": "/Users/kaikai/scripts/fan_hunter.py", "category": "comment"},
    {"name": "fan_conversion_analytics", "path": "/Users/kaikai/scripts/fan_conversion_analytics.py", "category": "comment"},
    # 上传/工具
    {"name": "bilibili_upload", "path": "/Users/kaikai/ai_video_upload/bilibili_upload.py", "category": "upload"},
    {"name": "bilibili_follower_monitor", "path": "/Users/kaikai/scripts/bilibili_utils", "category": "upload"},
    {"name": "voice_clone", "path": "/Users/kaikai/scripts/voice_clone.py", "category": "voice"},
    {"name": "voice_api", "path": "/Users/kaikai/scripts/voice_api", "category": "voice"},
    # 公共工具
    {"name": "bilibili_utils", "path": "/Users/kaikai/scripts/bilibili_utils", "category": "lib"},
    {"name": "lib_common", "path": "/Users/kaikai/scripts/tuixue_v3/lib_common.py", "category": "lib"},
]


def _human_size(b: int) -> str:
    if b < 1024:
        return str(b) + " B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024:.2f} MB"
    return f"{b / 1024 / 1024 / 1024:.2f} GB"


def _check(p: str) -> dict:
    path = Path(p)
    info = {"exists": False, "mtime": None, "size_bytes": 0, "kind": "?", "size_human": "—"}
    if not path.exists():
        info["kind"] = "missing"
        return info
    info["exists"] = True
    stat = path.stat()
    info["mtime"] = int(stat.st_mtime * 1000)
    info["size_bytes"] = stat.st_size
    info["size_human"] = _human_size(stat.st_size)
    info["kind"] = "dir" if path.is_dir() else "file"
    return info


@router.get("/scripts")
async def list_scripts(request: Request):
    items = []
    for s in SCRIPTS:
        info = _check(s["path"])
        items.append({**s, **info})
    return with_trace(request, {"items": items, "count": len(items), "root": str(SCRIPTS_ROOT)})


@router.get("/scripts/category/{category}")
async def scripts_by_category(request: Request, category: str):
    items = [s for s in SCRIPTS if s.get("category") == category]
    return with_trace(request, {"items": items, "count": len(items), "category": category})