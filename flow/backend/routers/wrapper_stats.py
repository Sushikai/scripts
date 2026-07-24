"""/api/wrapper-stats:按 wrapper / tool_id 聚合运行统计(总运行/成功/失败/平均时长)。"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["wrapper-stats"])


@router.get("/wrapper-stats")
async def wrapper_stats(request: Request, days: int = Query(30, le=365)):
    """按 tool_id 聚合:总 job 数 / 各状态数 / 平均时长 / 7 日 trend。"""
    cutoff = int(time.time() * 1000) - days * 86400 * 1000
    per_tool = defaultdict(lambda: {"total": 0, "done": 0, "failed": 0, "cancelled": 0, "running": 0, "pending": 0, "durations": [], "by_day": defaultdict(int)})

    for proj in db.project_list(limit=2000):
        tool = proj.get("tool_id") or "?"
        for job in db.job_list_by_project(proj["id"]):
            started = job.get("started_at") or 0
            finished = job.get("finished_at") or 0
            status = job.get("status") or "unknown"
            per_tool[tool]["total"] += 1
            if status in per_tool[tool]:
                per_tool[tool][status] += 1
            # 时长(只算 done)
            if status == "done" and started and finished:
                per_tool[tool]["durations"].append(finished - started)
            # 按天
            if started >= cutoff:
                day = time.strftime("%Y-%m-%d", time.localtime(started / 1000))
                per_tool[tool]["by_day"][day] += 1

    items = []
    for tool, s in per_tool.items():
        total_done = s["done"]
        avg_ms = 0
        if s["durations"]:
            avg_ms = int(sum(s["durations"]) / len(s["durations"]))
        success_rate = (total_done / s["total"]) if s["total"] else 0.0
        by_day = sorted(s["by_day"].items(), key=lambda kv: kv[0], reverse=True)[:7]
        by_day.reverse()
        items.append({
            "tool_id": tool,
            "total": s["total"],
            "done": s["done"],
            "failed": s["failed"],
            "cancelled": s["cancelled"],
            "running": s["running"],
            "pending": s["pending"],
            "success_rate": round(success_rate, 4),
            "avg_duration_ms": avg_ms,
            "by_day": [{"date": d, "count": c} for d, c in by_day],
        })

    items.sort(key=lambda x: x["total"], reverse=True)
    return with_trace(request, {"items": items, "count": len(items), "days": days})


@router.get("/wrapper-stats/{tool_id}")
async def wrapper_stat_detail(request: Request, tool_id: str):
    """单个 wrapper 的详细统计。"""
    items = []
    durations = []
    by_day = defaultdict(int)
    for proj in db.project_list(limit=2000):
        if proj.get("tool_id") != tool_id:
            continue
        for job in db.job_list_by_project(proj["id"]):
            items.append({
                "job_id": job.get("id"),
                "project_id": job.get("project_id"),
                "step": job.get("step"),
                "status": job.get("status"),
                "progress": job.get("progress", 0),
                "started_at": job.get("started_at"),
                "finished_at": job.get("finished_at"),
            })
            if job.get("status") == "done" and job.get("started_at") and job.get("finished_at"):
                durations.append(job["finished_at"] - job["started_at"])
            if job.get("started_at"):
                day = time.strftime("%Y-%m-%d", time.localtime(job["started_at"] / 1000))
                by_day[day] += 1
    items.sort(key=lambda x: x["started_at"] or 0, reverse=True)
    avg_ms = int(sum(durations) / len(durations)) if durations else 0
    by_day_sorted = sorted(by_day.items(), key=lambda kv: kv[0], reverse=True)[:30]
    by_day_sorted.reverse()
    return with_trace(request, {
        "tool_id": tool_id,
        "total": len(items),
        "avg_duration_ms": avg_ms,
        "by_day": [{"date": d, "count": c} for d, c in by_day_sorted],
        "recent_jobs": items[:20],
    })