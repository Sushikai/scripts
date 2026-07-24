"""/api/comments:评论/回复/粉丝发掘相关(读 fan_hunter_actions.jsonl + 转化统计)。

只读已落盘的数据文件,不调外部 API。所有端点返回 envelope。
"""

from __future__ import annotations

import csv
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api/comments", tags=["comments"])

SCRIPTS_DIR = Path("/Users/kaikai/scripts")
ACTIONS_FILE = SCRIPTS_DIR / "fan_hunter_actions.jsonl"
ANALYTICS_DIR = SCRIPTS_DIR / "fan_conversion_analytics"
FOLLOWER_SNAPSHOT = ANALYTICS_DIR / "follower_snapshot.json"
CONVERSION_SUMMARY = ANALYTICS_DIR / "conversion_summary.json"


def _read_actions_tail(limit: int = 100) -> list[dict]:
    """从 jsonl 文件尾部读 limit 行(高效)。"""
    if not ACTIONS_FILE.exists():
        return []
    try:
        # tail 实现:大文件不一次性读
        with ACTIONS_FILE.open("rb") as f:
            # 找文件末尾
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") < limit + 1:
                read_size = min(block, size)
                size -= read_size
                f.seek(size)
                data = f.read(read_size) + data
            lines = data.splitlines()[-limit:]
            out = []
            for line in lines:
                try:
                    out.append(json.loads(line.decode("utf-8", errors="ignore")))
                except Exception:
                    continue
            return out
    except Exception:
        return []


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@router.get("/actions")
async def list_actions(
    request: Request,
    limit: int = Query(50, le=500),
    action: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
):
    """最近 fan_hunter 动作(从 jsonl 尾部读 limit 行)。

    action: 过滤动作类型(like / reply / follow / dm)
    since: ISO 时间戳,只返回 >= 此时间的记录
    """
    items = _read_actions_tail(limit=limit * 3 if (action or since) else limit)
    if action:
        items = [a for a in items if a.get("action") == action]
    if since:
        items = [a for a in items if a.get("timestamp", "") >= since]
    items = items[-limit:]
    items.reverse()  # 最新的在前面
    return with_trace(request, {"items": items, "count": len(items), "limit": limit})


@router.get("/stats")
async def stats(request: Request):
    """汇总数据:动作总数、按动作类型分、按天分、按视频分。"""
    items = _read_actions_tail(limit=5000)
    total = len(items)

    # 按动作类型
    by_action = Counter(a.get("action", "?") for a in items)
    # 按天分(最近 7 天)
    today = _today_str()
    by_day = defaultdict(int)
    for a in items:
        ts = (a.get("timestamp") or "")[:10]
        if ts:
            by_day[ts] += 1
    last7 = sorted(by_day.items(), key=lambda kv: kv[0], reverse=True)[:7]
    last7.reverse()
    # 按 bvid 分(取 top 10)
    by_video = Counter()
    for a in items:
        bvid = a.get("bvid") or ""
        if bvid:
            by_video[(bvid, a.get("video_title", "")[:40])] += 1
    top_videos = [
        {"bvid": k[0], "title": k[1], "count": v}
        for k, v in by_video.most_common(10)
    ]
    # 今日动作数
    today_count = sum(1 for a in items if (a.get("timestamp") or "").startswith(today))
    return with_trace(request, {
        "total": total,
        "today_count": today_count,
        "by_action": dict(by_action),
        "by_day": [{"date": d, "count": c} for d, c in last7],
        "top_videos": top_videos,
    })


@router.get("/conversion")
async def conversion(request: Request):
    """读最新 conversion_summary.json + follower_snapshot.json。"""
    summary = {}
    snapshot = {}
    if CONVERSION_SUMMARY.exists():
        try:
            summary = json.loads(CONVERSION_SUMMARY.read_text())
        except Exception:
            pass
    if FOLLOWER_SNAPSHOT.exists():
        try:
            snapshot = json.loads(FOLLOWER_SNAPSHOT.read_text())
        except Exception:
            pass
    return with_trace(request, {
        "summary": summary,
        "snapshot": snapshot,
    })


@router.get("/daily-reports")
async def daily_reports(
    request: Request,
    days: int = Query(7, le=30),
):
    """读最近 N 天的 daily_report_*.csv。"""
    out = []
    if not ANALYTICS_DIR.exists():
        return with_trace(request, {"items": [], "count": 0})
    files = sorted(ANALYTICS_DIR.glob("daily_report_*.csv"), reverse=True)[:days]
    for f in files:
        try:
            with f.open() as fp:
                reader = csv.DictReader(fp)
                rows = list(reader)
            for r in rows:
                out.append({"file": f.name, **r})
        except Exception:
            continue
    return with_trace(request, {"items": out, "count": len(out)})