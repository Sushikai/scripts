"""/api/queue:实时 JobRunner 队列状态 — 当前 inflight + 待处理 DB。

R13 加 — Dashboard 看到一个 Job 启动时,知道是哪个 worker 在跑、还排几个。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import _constants as C
from ..db import repo as db
from ..envelope import with_trace
from ..services.job_runner import get_runner

router = APIRouter(prefix="/api", tags=["queue"])


def _project_name(pid: str | None) -> str:
    if not pid:
        return "?"
    try:
        p = db.project_get(pid)
        return (p or {}).get("name") or pid
    except Exception:
        return pid


@router.get("/queue")
async def queue_status(request: Request):
    """inflight 当前 + DB pending + 容量利用率。"""
    runner = get_runner()
    inflight = []
    for job_id, prog in runner._inflight.items():
        try:
            j = db.job_get(job_id) or {}
        except Exception:
            j = {}
        inflight.append({
            "job_id": job_id,
            "project_id": j.get("project_id"),
            "project_name": _project_name(j.get("project_id")),
            "step": j.get("step"),
            "progress": prog.progress,
            "status": prog.status,
            "started_at": j.get("started_at"),
        })
    # DB 中 pending 状态的 jobs(还没进 inflight)
    pending: list[dict] = []
    try:
        for proj in db.project_list(limit=500):
            for j in db.job_list_by_project(proj["id"]):
                if j.get("status") == "pending":
                    pending.append({
                        "job_id": j["id"],
                        "project_id": proj["id"],
                        "project_name": proj.get("name") or proj["id"],
                        "step": j.get("step"),
                        "created_at": j.get("started_at"),
                    })
    except Exception:
        pass
    pending = pending[:20]
    return with_trace(request, {
        "inflight": inflight,
        "inflight_count": len(inflight),
        "pending": pending,
        "pending_count": len(pending),
        "max_concurrent": C.JOB_MAX_CONCURRENT,
        "utilization": min(1.0, len(inflight) / max(1, C.JOB_MAX_CONCURRENT)),
        "queue_depth": runner._queue.qsize(),
        "ts": int(__import__("time").time() * 1000),
    })