"""Job 调度:asyncio.Queue + N worker + cancel + progress callback + 持久化。

每类工具提供 ToolWrapper,JobRunner 调 wrapper.run(params, progress_cb)。
进度 / 日志 / 产物通过 db.repo 持久化,SSE 由 routers/jobs.py 订阅。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .. import _constants as C
from ..db import repo as db

_logger = logging.getLogger("flow.jobs")


# === Job 描述 ===
@dataclass
class JobSpec:
    project_id: str
    step: str
    run_fn: Callable[..., Awaitable[dict]]
    params: dict = field(default_factory=dict)


# === Progress 回调 ===
@dataclass
class JobProgress:
    job_id: str
    progress: float = 0.0          # 0..1
    status: str = "pending"
    log_lines: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    error: str | None = None

    def emit_log(self, line: str) -> None:
        line = str(line).rstrip("\n")
        ts = int(time.time() * 1000)
        self.log_lines.append({"ts": ts, "line": line})
        # 控制台也打一份
        _logger.info("[job=%s] %s", self.job_id, line)

    def update(self, progress: float, msg: str | None = None) -> None:
        self.progress = max(0.0, min(1.0, float(progress)))
        if msg:
            self.emit_log(msg)
        db.job_set_progress(self.job_id, self.progress)
        # 通知订阅者
        from .job_runner import get_runner
        try:
            get_runner()._notify(self.job_id, {"type": "progress", "progress": self.progress, "msg": msg})
        except Exception:
            pass


# === JobRunner ===
class JobRunner:
    def __init__(self, max_concurrent: int = C.JOB_MAX_CONCURRENT):
        self._queue: asyncio.Queue[JobSpec | None] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._inflight: dict[str, JobProgress] = {}
        self._cancelled: set[str] = set()
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._max = max_concurrent
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._max):
            t = asyncio.create_task(self._worker_loop(i))
            self._workers.append(t)
        _logger.info("JobRunner started, %d workers", self._max)

    async def stop(self) -> None:
        for _ in range(self._max):
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._started = False

    async def submit(self, spec: JobSpec) -> str:
        """提交一个 Job,返回 job_id。"""
        j = db.job_create(spec.project_id, spec.step)
        self._inflight[j["id"]] = JobProgress(job_id=j["id"])
        await self._queue.put(spec)
        return j["id"]

    def progress(self, job_id: str) -> Optional[JobProgress]:
        return self._inflight.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """标记取消(实际停止在 worker 检查时生效)。"""
        if job_id in self._inflight:
            self._cancelled.add(job_id)
            db.job_set_cancelled(job_id)
            return True
        db.job_set_cancelled(job_id)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    def subscribe(self, job_id: str) -> asyncio.Queue:
        """订阅某个 job_id 的状态变化,返回 Queue。

        Queue 收到的消息:{type: 'progress'|'status', ...}
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=C.SSE_MAX_QUEUE)
        self._subs.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        """取消订阅。"""
        subs = self._subs.get(job_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                self._subs.pop(job_id, None)

    def _notify(self, job_id: str, msg: dict) -> None:
        """通知所有订阅者。"""
        for q in list(self._subs.get(job_id, [])):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # 满了就丢弃最旧的
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            spec = await self._queue.get()
            if spec is None:
                break
            await self._run_one(worker_id, spec)

    async def _run_one(self, worker_id: int, spec: JobSpec) -> None:
        # 找到最新 pending job
        jobs = db.job_list_by_project(spec.project_id)
        target = next((j for j in jobs if j["step"] == spec.step and j["status"] == "pending"), None)
        if not target:
            _logger.warning("worker %d: no pending job for %s/%s", worker_id, spec.project_id, spec.step)
            return
        job_id = target["id"]
        prog = self._inflight[job_id]

        if self.is_cancelled(job_id):
            prog.status = "cancelled"
            self._notify(job_id, {"type": "status", "status": "cancelled"})
            return

        db.job_set_running(job_id)
        prog.status = "running"
        self._notify(job_id, {"type": "status", "status": "running"})
        prog.emit_log(f"worker {worker_id} starting step={spec.step}")

        try:
            artifacts = await spec.run_fn(
                spec.step,
                spec.params,
                progress_cb=prog.update,
                log_cb=prog.emit_log,
                is_cancelled=lambda: self.is_cancelled(job_id),
            )
            if self.is_cancelled(job_id):
                prog.status = "cancelled"
                db.job_set_cancelled(job_id)
                self._notify(job_id, {"type": "status", "status": "cancelled"})
                return
            prog.artifacts = artifacts or {}
            prog.status = "done"
            db.job_set_done(job_id, prog.artifacts)
            prog.update(1.0, f"step {spec.step} done")
            self._notify(job_id, {"type": "status", "status": "done"})
        except Exception as e:
            if self.is_cancelled(job_id):
                prog.status = "cancelled"
                db.job_set_cancelled(job_id)
                self._notify(job_id, {"type": "status", "status": "cancelled"})
            else:
                _logger.exception("job %s failed", job_id)
                prog.status = "failed"
                prog.error = str(e)[:500]
                db.job_set_failed(job_id, prog.error)
                self._notify(job_id, {"type": "status", "status": "failed", "error": prog.error})
        finally:
            # 保留 inflight 给 SSE 客户端最后一次查询
            pass


# === 全局单例 ===
_RUNNER: JobRunner | None = None


def get_runner() -> JobRunner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = JobRunner()
    return _RUNNER


async def start_runner_once() -> None:
    await get_runner().start()


async def stop_runner_once() -> None:
    if _RUNNER:
        await _RUNNER.stop()