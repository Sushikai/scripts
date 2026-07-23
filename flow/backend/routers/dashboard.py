"""/api/dashboard:总览 KPI。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
async def dashboard(request: Request):
    """今日 KPI:项目总数、今日 Job 数、今日上传数、成功率。"""
    projects_total = len(db.project_list(limit=1000))
    today_start = _today_start_ms()
    jobs_today = _count_jobs_since(today_start)
    uploads_today = _count_uploads_since(today_start)
    success_rate = _success_rate(today_start)
    return with_trace(request, {
        "stats": {
            "projects_total": projects_total,
            "jobs_today": jobs_today,
            "uploads_today": uploads_today,
            "success_rate": success_rate,
        },
        "ts": int(time.time() * 1000),
    })


def _today_start_ms() -> int:
    import datetime
    now = datetime.datetime.now()
    return int(datetime.datetime(now.year, now.month, now.day).timestamp() * 1000)


def _count_jobs_since(start_ms: int) -> int:
    conn = db._conn()
    try:
        row = conn.execute("SELECT COUNT(*) c FROM jobs WHERE created_at >= ?", (start_ms,)).fetchone()
        return row["c"] if row else 0
    except Exception:
        return 0


def _count_uploads_since(start_ms: int) -> int:
    conn = db._conn()
    try:
        row = conn.execute("SELECT COUNT(*) c FROM uploads WHERE created_at >= ?", (start_ms,)).fetchone()
        return row["c"] if row else 0
    except Exception:
        return 0


def _success_rate(start_ms: int) -> float:
    conn = db._conn()
    try:
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
            "COUNT(*) AS total "
            "FROM jobs WHERE created_at >= ? AND status IN ('done','failed','cancelled')",
            (start_ms,),
        ).fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return round(row["done"] / row["total"], 4)
    except Exception:
        return 0.0