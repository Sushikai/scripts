"""/api/assets:扫多个输出根,聚合视频/音频素材,带标签/大小/mtime。

R12 加 — Library view 不再是占位。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Query, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["assets"])

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".flv", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 多个扫描根 — 用 "source" 标签
ROOTS = [
    ("info_gap", os.path.expanduser("~/ai_video_project/news_outputs")),
    ("info_gap_fan", os.path.expanduser("~/ai_video_project/fan_hunter")),
    ("fengge", os.path.expanduser("~/ai_video_upload")),
    ("tiktok", os.path.expanduser("~/tiktok_automation")),
    ("flow_outputs", os.path.join(os.path.dirname(__file__), "..", "..", "outputs")),
]

SCAN_LIMIT_PER_ROOT = 200


def _human_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024:.1f} MB"
    return f"{b / 1024 / 1024 / 1024:.2f} GB"


def _classify(ext: str) -> str:
    if ext in VIDEO_EXTS: return "video"
    if ext in AUDIO_EXTS: return "audio"
    if ext in IMAGE_EXTS: return "image"
    return "other"


def _safe_title(name: str) -> str:
    """文件名去 hash + 后缀,做可读标题。"""
    base = re.sub(r"^(v\d+_|bgvideo_|video_|clip_|final_|out_)", "", name)
    base = re.sub(r"_[a-f0-9]{6,}\.", ".", base)
    base = re.sub(r"_", " ", base)
    base = re.sub(r"\.(mp4|mov|webm|mkv|flv|ts|mp3|m4a|wav|aac|jpg|jpeg|png|webp)$", "", base, flags=re.IGNORECASE)
    return base.strip()[:80] or name


def _scan(root_path: str, source: str) -> list[dict]:
    """递归扫一个根,返资产列表。限 SCAN_LIMIT_PER_ROOT 防爆。"""
    p = Path(root_path)
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            kind = _classify(ext)
            if kind == "other":
                continue
            try:
                stat = f.stat()
            except Exception:
                continue
            out.append({
                "source": source,
                "path": str(f),
                "name": f.name,
                "title": _safe_title(f.name),
                "kind": kind,
                "ext": ext,
                "size_bytes": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "mtime": int(stat.st_mtime * 1000),
            })
    except Exception:
        pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:SCAN_LIMIT_PER_ROOT]


@router.get("/assets")
async def list_assets(
    request: Request,
    source: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, le=1000),
):
    """聚合所有根 + 过滤 source/kind,按 mtime 倒序。"""
    all_items: list[dict] = []
    for src, root in ROOTS:
        all_items.extend(_scan(root, src))
    if source:
        all_items = [x for x in all_items if x["source"] == source]
    if kind:
        all_items = [x for x in all_items if x["kind"] == kind]
    all_items.sort(key=lambda x: x["mtime"], reverse=True)
    all_items = all_items[:limit]
    by_source: dict[str, int] = {}
    for it in all_items:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1
    return with_trace(request, {
        "items": all_items,
        "count": len(all_items),
        "by_source": by_source,
        "roots": [{"source": s, "path": r, "exists": Path(r).exists()} for s, r in ROOTS],
    })