"""/api/jobs 路由:创建 / 查询 / 取消 / SSE 流。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from .. import _constants as C
from ..db import repo as db
from ..envelope import Code, ok, with_trace
from ..services.job_runner import JobSpec, get_runner

router = APIRouter(prefix="/api", tags=["jobs"])
_logger = logging.getLogger("flow.jobs")


@router.post("/jobs")
async def create_job(request: Request, body: dict):
    """创建 + 提交一个 Job。"""
    project_id = body.get("project_id")
    step = body.get("step")
    params = body.get("params") or {}
    run_fn_name = body.get("run_fn")  # 工具 wrapper 提供
    if not project_id or not step:
        raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": "project_id+step required"})

    # 通过 run_fn 名字查 wrapper(注册中心)
    from ..wrappers.registry import get_wrapper
    try:
        wrapper = get_wrapper(body.get("tool_id"))
    except KeyError as e:
        raise HTTPException(status_code=404, detail={"code": "TOOL_NOT_FOUND", "message": str(e)})

    spec = JobSpec(
        project_id=project_id,
        step=step,
        params=params,
        run_fn=wrapper.run_step,
    )
    job_id = await get_runner().submit(spec)
    db.project_update_status(project_id, "running")
    return with_trace(request, {"job_id": job_id, "project_id": project_id, "step": step}, status=202)


@router.get("/jobs")
async def list_jobs(request: Request, project_id: Optional[str] = None, limit: int = Query(50, le=200)):
    """列 Job(可按 project_id 过滤)。"""
    if project_id:
        items = db.job_list_by_project(project_id)
    else:
        items = []
        for proj in db.project_list(limit=limit):
            items.extend(db.job_list_by_project(proj["id"]))
    items.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
    return with_trace(request, {"items": items[:limit], "count": len(items[:limit])})


@router.get("/job/{job_id}")
async def get_job(request: Request, job_id: str):
    j = db.job_get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": job_id})
    # 拼接 progress(实时进度)
    prog = get_runner().progress(job_id)
    if prog:
        j["realtime_progress"] = prog.progress
        j["realtime_status"] = prog.status
        j["realtime_log_tail"] = prog.log_lines[-20:]
    return with_trace(request, j)


@router.post("/job/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str):
    j = db.job_get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": job_id})
    ok_ = get_runner().cancel(job_id)
    return with_trace(request, {"job_id": job_id, "cancelled": ok_, "status": "cancelling"})


@router.get("/job/{job_id}/stream")
async def stream_job(request: Request, job_id: str):
    """SSE 推送 Job 进度 / 日志 / 状态变化。"""
    j = db.job_get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": job_id})

    queue: asyncio.Queue = get_runner().subscribe(job_id)

    async def gen():
        # 立即推一份当前状态
        yield {"event": "snapshot", "data": json.dumps(_job_snapshot(job_id), ensure_ascii=False, default=str)}
        last_log_ts = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=C.SSE_HEARTBEAT_SEC)
                    yield {"event": msg.get("type", "msg"), "data": json.dumps(msg, ensure_ascii=False, default=str)}
                    if msg.get("type") == "status" and msg.get("status") in ("done", "failed", "cancelled"):
                        yield {"event": "end", "data": json.dumps({"status": msg["status"]})}
                        break
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({"ts": int(time.time() * 1000)})}
                    # 主动拉新日志(降级兜底)
                    prog = get_runner().progress(job_id)
                    if prog:
                        new_logs = [l for l in prog.log_lines if l["ts"] > last_log_ts]
                        if new_logs:
                            yield {"event": "logs", "data": json.dumps({"logs": new_logs})}
                            last_log_ts = new_logs[-1]["ts"]
                        yield {"event": "progress", "data": json.dumps({"progress": prog.progress, "status": prog.status})}
                    # 已结束但没收到 status 通知(老 job)— 主动查 DB 兜底
                    cur = db.job_get(job_id)
                    if cur and cur.get("status") in ("done", "failed", "cancelled"):
                        yield {"event": "status", "data": json.dumps({"type": "status", "status": cur["status"]})}
                        yield {"event": "end", "data": json.dumps({"status": cur["status"]})}
                        break
        finally:
            get_runner().unsubscribe(job_id, queue)

    return EventSourceResponse(gen())


def _job_snapshot(job_id: str) -> dict:
    j = db.job_get(job_id) or {"id": job_id}
    prog = get_runner().progress(job_id)
    if prog:
        j["realtime_progress"] = prog.progress
        j["realtime_log_tail"] = prog.log_lines[-30:]
    return j