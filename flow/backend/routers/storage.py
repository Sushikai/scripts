"""/api/storage:扫描多个根路径,聚合磁盘用量 + top-N 大文件/子目录。

R16 加 — Dashboard 看到哪些目录吃空间,能立刻删。
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Query, Request

from .. import _constants as C
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["storage"])


# 监控路径 — 视频生产相关的所有大目录
PATHS = [
    ("info_gap_outputs", os.path.expanduser("~/ai_video_project/news_outputs")),
    ("fengge_uploads", os.path.expanduser("~/ai_video_upload")),
    ("tiktok_downloads", os.path.expanduser("~/tiktok_automation")),
    ("fan_hunter", os.path.expanduser("~/ai_video_project/fan_hunter")),
    ("flow_outputs", os.path.join(C.PROJECT_ROOT(), "outputs")),
    ("flow_logs", os.path.join(C.PROJECT_ROOT(), "logs")),
    ("flow_db", C.DB_PATH()),
    ("cloudflared_logs", "/tmp"),
    ("hermes_logs", os.path.expanduser("~/.hermes/logs")),
]


def _du(p: Path) -> int:
    """du -sk → bytes。失败返 0。"""
    try:
        out = subprocess.check_output(["du", "-sk", str(p)], timeout=8).decode()
        kb = int(out.split()[0])
        return kb * 1024
    except Exception:
        return 0


def _human_size(b: int) -> str:
    if b < 1024: return f"{b} B"
    if b < 1024 ** 2: return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3: return f"{b / 1024 / 1024:.1f} MB"
    return f"{b / 1024 / 1024 / 1024:.2f} GB"


def _top_files(p: Path, limit: int = 5) -> list[dict]:
    """递归找 limit 个最大文件。"""
    out: list[tuple[int, str]] = []
    if not p.exists():
        return []
    try:
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            try:
                sz = f.stat().st_size
            except Exception:
                continue
            out.append((sz, str(f)))
    except Exception:
        pass
    out.sort(key=lambda x: x[0], reverse=True)
    return [{"path": path, "size_bytes": sz, "size_human": _human_size(sz)} for sz, path in out[:limit]]


@router.get("/storage")
async def storage_info(request: Request):
    """每个监控路径:bytes + human + top-5 最大文件。"""
    items: list[dict] = []
    for name, path_str in PATHS:
        p = Path(path_str)
        if not p.exists():
            items.append({
                "name": name,
                "path": path_str,
                "exists": False,
                "size_bytes": 0,
                "size_human": "—",
                "top_files": [],
            })
            continue
        # 路径是文件 (e.g. flow.db) 用 stat 否则用 du
        if p.is_file():
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
        else:
            size = _du(p)
        items.append({
            "name": name,
            "path": path_str,
            "exists": True,
            "is_file": p.is_file(),
            "size_bytes": size,
            "size_human": _human_size(size),
            "top_files": _top_files(p, 5) if p.is_dir() else [],
        })
    items.sort(key=lambda x: x["size_bytes"], reverse=True)
    total = sum(x["size_bytes"] for x in items)
    return with_trace(request, {
        "items": items,
        "total_bytes": total,
        "total_human": _human_size(total),
        "count": len(items),
        "ts": int(__import__("time").time() * 1000),
    })


@router.get("/storage/disk")
async def disk_info(request: Request):
    """根分区用量 (df -P /)。"""
    try:
        out = subprocess.check_output(["df", "-P", "/"], timeout=2).decode().splitlines()
        if len(out) >= 2:
            fields = out[1].split()
            total = int(fields[1]) * 1024
            used = int(fields[2]) * 1024
            avail = int(fields[3]) * 1024
            pct = int(fields[4].rstrip("%"))
            return with_trace(request, {
                "total_bytes": total,
                "used_bytes": used,
                "avail_bytes": avail,
                "used_human": _human_size(used),
                "avail_human": _human_size(avail),
                "total_human": _human_size(total),
                "pct": pct,
            })
    except Exception:
        pass
    return with_trace(request, {"error": "df failed"})