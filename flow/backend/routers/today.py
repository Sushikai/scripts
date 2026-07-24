"""/api/today:今日 24 小时 × 多子系统 时间线聚合。

R15 加 — Dashboard 一行看清每个小时发生了什么。
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Request

from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["today"])


def _hour_key(ts_ms: int) -> int:
    """ts_ms → 0..23 桶 (本地时区)。"""
    if not ts_ms or ts_ms <= 0:
        return -1
    t = time.localtime(ts_ms / 1000)
    return t.tm_hour


def _safe_iso(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(ts_ms / 1000))


def _job_buckets(cutoff: int) -> dict[int, list[dict]]:
    """扫描 jobs,按 started_at 落 24 桶。"""
    buckets: dict[int, list[dict]] = defaultdict(list)
    try:
        for proj in db.project_list(limit=500):
            for j in db.job_list_by_project(proj["id"]):
                sa = j.get("started_at") or 0
                if sa < cutoff:
                    continue
                h = _hour_key(sa)
                if h < 0:
                    continue
                buckets[h].append({
                    "name": proj.get("name", "?"),
                    "tool": proj.get("tool_id", "?"),
                    "step": j.get("step", "?"),
                    "status": j.get("status", "?"),
                    "ts": sa,
                })
    except Exception:
        pass
    return buckets


def _upload_buckets(cutoff: int) -> dict[int, list[dict]]:
    """从 jobs artifacts 抽取 upload_step (upload / upload_bili / upload_douyin) 落桶。"""
    buckets: dict[int, list[dict]] = defaultdict(list)
    try:
        for proj in db.project_list(limit=500):
            for j in db.job_list_by_project(proj["id"]):
                if j.get("step") not in ("upload", "upload_bili", "upload_douyin"):
                    continue
                if j.get("status") != "done":
                    continue
                fa = j.get("finished_at") or 0
                if fa < cutoff:
                    continue
                h = _hour_key(fa)
                if h < 0:
                    continue
                buckets[h].append({
                    "name": proj.get("name", "?"),
                    "tool": proj.get("tool_id", "?"),
                    "ts": fa,
                })
    except Exception:
        pass
    return buckets


@router.get("/today")
async def today_timeline(request: Request):
    """24 小时桶 + jobs / uploads / crons 计数 + 该小时 sample。"""
    cutoff = int(time.time() * 1000) - 86400 * 1000
    job_buckets = _job_buckets(cutoff)
    upload_buckets = _upload_buckets(cutoff)
    # cron 计数 — 复用 crons 模块
    cron_count = 0
    try:
        from . import crons as crons_mod
        loaded = crons_mod._list_loaded()
        if crons_mod.LAUNCH_AGENTS.exists():
            for plist_path in crons_mod.LAUNCH_AGENTS.glob("*.plist"):
                plist = crons_mod._parse_plist(plist_path)
                if not plist:
                    continue
                label = plist.get("Label", plist_path.stem)
                if not crons_mod._is_relevant(label):
                    continue
                pid, _ = loaded.get(label, ("-", "-"))
                if pid not in ("-", ""):
                    cron_count += 1
    except Exception:
        pass

    hours = []
    for h in range(24):
        jobs = job_buckets.get(h, [])
        ups = upload_buckets.get(h, [])
        hours.append({
            "hour": h,
            "label": f"{h:02d}:00",
            "jobs": len(jobs),
            "uploads": len(ups),
            "samples": [
                {"name": j["name"], "tool": j["tool"], "step": j["step"], "status": j["status"]}
                for j in jobs[:3]
            ] + [
                {"name": u["name"], "tool": u["tool"], "step": "upload"}
                for u in ups[:3]
            ],
        })
    total_jobs = sum(h["jobs"] for h in hours)
    total_uploads = sum(h["uploads"] for h in hours)
    peak_hour = max(hours, key=lambda h: h["jobs"] + h["uploads"]) if hours else None
    return with_trace(request, {
        "hours": hours,
        "total_jobs": total_jobs,
        "total_uploads": total_uploads,
        "cron_running": cron_count,
        "peak_hour": peak_hour["label"] if peak_hour else None,
        "ts": int(time.time() * 1000),
    })