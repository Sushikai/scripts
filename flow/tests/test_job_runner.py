"""JobRunner 单元测试:队列、状态机、cancel、progress。"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fresh():
    import importlib
    tmp = Path(tempfile.mkdtemp(prefix="flow_job_"))
    os.environ["FLOW_DB"] = str(tmp / "flow.db")
    os.environ["FLOW_CACHE_DB"] = str(tmp / "cache.db")

    # 重置相关模块
    import backend.db.repo as r
    importlib.reload(r)
    import backend.services.job_runner as jr
    importlib.reload(jr)
    return jr, r


def test_runner_lifecycle():
    jr, _ = _fresh()
    runner = jr.JobRunner(max_concurrent=2)
    assert runner._max == 2


def test_progress_dataclass():
    jr, _ = _fresh()
    p = jr.JobProgress(job_id="x")
    p.emit_log("hello")
    assert len(p.log_lines) == 1
    assert p.log_lines[0]["line"] == "hello"
    p.update(0.5, "halfway")
    assert p.progress == 0.5
    assert len(p.log_lines) == 2


def test_progress_clamps_to_0_1():
    jr, _ = _fresh()
    p = jr.JobProgress(job_id="x")
    p.update(2.0)
    assert p.progress == 1.0
    p.update(-0.5)
    assert p.progress == 0.0


def test_cancel_marks_cancelled():
    jr, _ = _fresh()
    runner = jr.JobRunner()
    runner._cancelled.add("abc")
    assert runner.is_cancelled("abc") is True
    assert runner.is_cancelled("zzz") is False


def test_async_submit_and_progress():
    """submit 一个简单 Job,等它完成。"""
    jr, db = _fresh()
    runner = jr.JobRunner(max_concurrent=1)

    async def main():
        await runner.start()

        async def fake_run(step, params, *, progress_cb, log_cb, is_cancelled):
            log_cb("start")
            for i in range(3):
                if is_cancelled():
                    return {"cancelled": True}
                await asyncio.sleep(0.05)
                progress_cb((i + 1) / 3, f"step {i}")
            return {"ok": True}

        p = db.project_create("test_tool", "job_test", {})
        spec = jr.JobSpec(project_id=p["id"], step="x", run_fn=fake_run)
        jid = await runner.submit(spec)

        # 等 0.5s
        for _ in range(20):
            await asyncio.sleep(0.05)
            prog = runner.progress(jid)
            if prog and prog.status in ("done", "failed"):
                break
        assert prog.status == "done"
        assert prog.progress == 1.0
        await runner.stop()

    asyncio.run(main())


def test_async_cancel_actually_interrupts():
    jr, db = _fresh()
    runner = jr.JobRunner(max_concurrent=1)

    async def main():
        await runner.start()

        async def slow_run(step, params, *, progress_cb, log_cb, is_cancelled):
            for i in range(100):
                if is_cancelled():
                    raise RuntimeError("cancelled")
                await asyncio.sleep(0.02)
            return {"done": True}

        p = db.project_create("test_tool", "cancel_test", {})
        spec = jr.JobSpec(project_id=p["id"], step="x", run_fn=slow_run)
        jid = await runner.submit(spec)
        await asyncio.sleep(0.05)  # 让 worker 开始
        runner.cancel(jid)
        # 等结束
        for _ in range(30):
            await asyncio.sleep(0.05)
            prog = runner.progress(jid)
            if prog and prog.status in ("done", "failed", "cancelled"):
                break
        # 因为 raise,被 catch 到 failed
        assert prog.status in ("failed", "cancelled")
        await runner.stop()

    asyncio.run(main())


def test_async_concurrent_jobs():
    """两个并发 Job 在 max_concurrent=2 下能并行。"""
    jr, db = _fresh()
    runner = jr.JobRunner(max_concurrent=2)

    async def main():
        await runner.start()

        async def task(sleep_s):
            async def run(step, params, *, progress_cb, log_cb, is_cancelled):
                await asyncio.sleep(sleep_s)
                return {"sleep": sleep_s}
            return run

        p1 = db.project_create("test_tool", "c1", {})
        p2 = db.project_create("test_tool", "c2", {})
        j1 = await runner.submit(jr.JobSpec(project_id=p1["id"], step="x", run_fn=await task(0.2)))
        j2 = await runner.submit(jr.JobSpec(project_id=p2["id"], step="x", run_fn=await task(0.2)))
        t0 = time.perf_counter()
        for _ in range(40):
            await asyncio.sleep(0.05)
            p1_prog = runner.progress(j1)
            p2_prog = runner.progress(j2)
            if p1_prog and p2_prog and p1_prog.status == "done" and p2_prog.status == "done":
                break
        elapsed = time.perf_counter() - t0
        # 并行应该 < 0.4s(若串行会 0.4s)
        assert elapsed < 0.35, f"should run in parallel, took {elapsed:.2f}s"
        await runner.stop()

    asyncio.run(main())


def test_get_runner_singleton():
    jr, _ = _fresh()
    a = jr.get_runner()
    b = jr.get_runner()
    assert a is b


def test_worker_loop_handles_none():
    jr, _ = _fresh()
    runner = jr.JobRunner(max_concurrent=1)

    async def main():
        await runner.start()
        await runner.stop()  # 应能正常退出

    asyncio.run(main())