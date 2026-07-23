"""tiktok_story wrapper 测试:dry-run + 真包导入 + 6 步契约。"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_wrapper_registered():
    from backend.wrappers.builtin import register_builtin
    from backend.wrappers.registry import register, get_wrapper
    register_builtin(register)
    w = get_wrapper("tiktok_story")
    assert w.tool_id == "tiktok_story"
    assert "fetch" in w.steps
    assert len(w.steps) == 6


def test_6_steps_defined():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)
    expected = ["fetch", "download", "subtitle", "crop", "upload_bili", "upload_douyin"]
    assert w.steps == expected


def test_dry_run_fetch():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)

    async def main():
        progress = []
        logs = []
        result = await w.run_step(
            "fetch", {"source": "tiktok"},
            progress_cb=lambda p, m: progress.append(p),
            log_cb=lambda m: logs.append(m),
            is_cancelled=lambda: False,
        )
        assert "output" in result
        assert result["step"] == "fetch"
        assert progress[-1] == 1.0
        os.unlink(result["output"])
    asyncio.run(main())


def test_dry_run_all_six_steps():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)

    async def main():
        for step in w.steps:
            progress = []
            result = await w.run_step(
                step, {},
                progress_cb=lambda p, m: progress.append(p),
                log_cb=lambda m: None,
                is_cancelled=lambda: False,
            )
            assert "output" in result, f"step {step} no output"
            assert result["step"] == step
            assert progress[-1] == 1.0
            os.unlink(result["output"])
    asyncio.run(main())


def test_dry_run_can_be_cancelled():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)

    async def main():
        cancel = [False]
        async def trigger():
            await asyncio.sleep(0.05)
            cancel[0] = True
        t = asyncio.create_task(trigger())
        try:
            await w.run_step(
                "download", {},
                progress_cb=lambda p, m: None,
                log_cb=lambda m: None,
                is_cancelled=lambda: cancel[0],
            )
            assert False, "should have raised"
        except RuntimeError as e:
            assert "cancelled" in str(e).lower()
        await t
    asyncio.run(main())


def test_real_mode_loads_module():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=False)
    w._ensure_imported()
    assert w._mod_tt is not None
    assert hasattr(w._mod_tt, "run_pipeline")


def test_wrapper_metadata_for_api():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)
    assert w.tool_id
    assert w.name
    assert w.description
    assert isinstance(w.steps, list)
    assert len(w.steps) > 0


def test_unknown_step_raises():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)

    async def main():
        try:
            await w.run_step("bogus", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
            assert False
        except ValueError as e:
            assert "bogus" in str(e)
    asyncio.run(main())


def test_dry_run_artifacts_are_real_files():
    from backend.wrappers.tiktok_story import TikTokStoryWrapper
    w = TikTokStoryWrapper(dry_run=True)

    async def main():
        result = await w.run_step("upload_bili", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
        p = Path(result["output"])
        assert p.exists()
        assert p.read_text().startswith("dry-run output")
        p.unlink()
    asyncio.run(main())