"""material_collector wrapper 测试:dry-run + 真包 + 4 步契约。"""

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
    w = get_wrapper("material_collector")
    assert w.tool_id == "material_collector"
    assert "web_collect" in w.steps
    assert len(w.steps) == 4


def test_4_steps_defined():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)
    expected = ["web_collect", "adb_collect", "process", "export_assets"]
    assert w.steps == expected


def test_dry_run_web_collect():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)

    async def main():
        progress = []
        result = await w.run_step(
            "web_collect", {"platforms": ["douyin"]},
            progress_cb=lambda p, m: progress.append(p),
            log_cb=lambda m: None,
            is_cancelled=lambda: False,
        )
        assert "output" in result
        assert result["step"] == "web_collect"
        assert progress[-1] == 1.0
        os.unlink(result["output"])
    asyncio.run(main())


def test_dry_run_all_four_steps():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)

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
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)

    async def main():
        cancel = [False]
        async def trigger():
            await asyncio.sleep(0.05)
            cancel[0] = True
        t = asyncio.create_task(trigger())
        try:
            await w.run_step(
                "web_collect", {},
                progress_cb=lambda p, m: None,
                log_cb=lambda m: None,
                is_cancelled=lambda: cancel[0],
            )
            assert False
        except RuntimeError as e:
            assert "cancelled" in str(e).lower()
        await t
    asyncio.run(main())


def test_real_mode_loads_module():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=False)
    w._ensure_imported()
    assert w._mod is not None
    assert hasattr(w._mod, "create_collector")


def test_wrapper_metadata_for_api():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)
    assert w.tool_id
    assert w.name
    assert w.description
    assert isinstance(w.steps, list)
    assert len(w.steps) > 0


def test_unknown_step_raises():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)

    async def main():
        try:
            await w.run_step("bogus", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
            assert False
        except ValueError as e:
            assert "bogus" in str(e)
    asyncio.run(main())


def test_dry_run_artifacts_are_real_files():
    from backend.wrappers.material_collector import MaterialCollectorWrapper
    w = MaterialCollectorWrapper(dry_run=True)

    async def main():
        result = await w.run_step("export_assets", {}, progress_cb=lambda p, m: None, log_cb=lambda m: None, is_cancelled=lambda: False)
        p = Path(result["output"])
        assert p.exists()
        assert p.read_text().startswith("dry-run output")
        p.unlink()
    asyncio.run(main())