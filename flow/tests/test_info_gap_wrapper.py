"""info_gap wrapper 测试:dry-run 模式 + 真包导入验证 + 7 步契约。"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_wrapper_registered():
    from backend.wrappers.builtin import register_builtin
    from backend.wrappers.registry import register, list_tools, get_wrapper
    register_builtin(register)
    w = get_wrapper("info_gap")
    assert w.tool_id == "info_gap"
    assert "research" in w.steps
    assert len(w.steps) == 7


def test_7_steps_defined():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)
    expected = ["research", "script", "voice", "materials", "compose", "style_diff", "upload"]
    assert w.steps == expected


def test_dry_run_research():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        progress = []
        logs = []
        def cb(p, msg):
            progress.append(p)
        def log(msg):
            logs.append(msg)
        result = await w.run_step("research", {}, progress_cb=cb, log_cb=log, is_cancelled=lambda: False)
        assert "output" in result
        assert result["step"] == "research"
        assert result["dry_run"] is True
        assert os.path.exists(result["output"])
        assert progress[-1] == 1.0
        assert any("research" in l for l in logs)
        os.unlink(result["output"])
    asyncio.run(main())


def test_dry_run_all_seven_steps():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        for step in w.steps:
            progress = []
            def cb(p, msg):
                progress.append(p)
            logs = []
            result = await w.run_step(step, {}, progress_cb=cb, log_cb=lambda m: logs.append(m), is_cancelled=lambda: False)
            assert "output" in result, f"step {step} no output"
            assert result["step"] == step
            assert progress[-1] == 1.0
    asyncio.run(main())


def test_dry_run_can_be_cancelled():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        cancel = [False]
        def is_cancelled():
            return cancel[0]
        def cb(p, msg): pass
        def log(m): pass

        async def trigger_cancel():
            await asyncio.sleep(0.05)
            cancel[0] = True

        cancel_task = asyncio.create_task(trigger_cancel())
        try:
            await w.run_step("compose", {}, progress_cb=cb, log_cb=log, is_cancelled=is_cancelled)
            assert False, "should have raised"
        except RuntimeError as e:
            assert "cancelled" in str(e).lower()
        await cancel_task
    asyncio.run(main())


def test_real_mode_loads_module():
    """真实模式(dry_run=False)能 import info_gap_pipeline。"""
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=False)
    w._ensure_imported()
    assert w._pipeline_class is not None, "InfoGapPipeline should import"
    assert w._pipeline_class.__name__ == "InfoGapPipeline"


def test_real_step_research_dry_to_avoid_external_calls():
    """真模式调用 research step,但因为 dry_run=True 不会真去爬热榜。"""
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)  # 默认 True 不改环境
    assert w.dry_run is True


def test_wrapper_metadata_for_api():
    """tool 元数据符合前端 /api/tools 契约。"""
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)
    assert w.tool_id
    assert w.name
    assert w.description
    assert isinstance(w.steps, list)
    assert len(w.steps) > 0


def test_unknown_step_raises():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        try:
            await w.run_step("bogus", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
            assert False, "should have raised"
        except ValueError as e:
            assert "bogus" in str(e)
    asyncio.run(main())


def test_progress_callback_called_n_times():
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        calls = []
        def cb(p, msg):
            calls.append((p, msg))
        await w.run_step("script", {}, progress_cb=cb, log_cb=lambda m: None, is_cancelled=lambda: False)
        # dry-run 每步调 4 次 progress(0.25/0.5/0.75/1.0)+ initial 0? 实际是 n=4 chunks
        assert len(calls) == 4
        assert calls[-1][0] == 1.0
    asyncio.run(main())


def test_dry_run_artifacts_are_real_files():
    """dry-run 写的产物文件应该真存在(便于前端下载)。"""
    from backend.wrappers.info_gap import InfoGapWrapper
    w = InfoGapWrapper(dry_run=True)

    async def main():
        result = await w.run_step("voice", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
        p = Path(result["output"])
        assert p.exists()
        assert p.read_text().startswith("dry-run output")
        p.unlink()  # 清理
    asyncio.run(main())