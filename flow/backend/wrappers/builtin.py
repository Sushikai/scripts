"""内置占位 wrappers:让 /api/tools 和 JobRunner 在 batch 2 之前能跑起来。

每个 wrapper 跑 mock sleep + log,后续 batch 用 info_gap / fengge / tiktok / material 替换。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.builtin")


class _DryRunWrapper(ToolWrapper):
    """通用占位 wrapper:每步 sleep + 写 log。"""

    def __init__(self, tool_id: str, name: str, description: str, steps: list[str], step_seconds: float = 0.5):
        self.tool_id = tool_id
        self.name = name
        self.description = description
        self.steps = steps
        self._step_seconds = step_seconds

    async def run_step(
        self,
        step: str,
        params: dict,
        *,
        progress_cb: Callable[[float, Optional[str]], None],
        log_cb: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> dict:
        log_cb(f"[{self.tool_id}/{step}] start params={list(params.keys())}")
        n = 5
        for i in range(n):
            if is_cancelled():
                log_cb(f"[{self.tool_id}/{step}] cancelled")
                raise RuntimeError("cancelled")
            await asyncio.sleep(self._step_seconds / n)
            progress_cb((i + 1) / n, f"step {step} {i+1}/{n}")
            log_cb(f"[{self.tool_id}/{step}] chunk {i+1}/{n}")
        log_cb(f"[{self.tool_id}/{step}] done")
        return {"output": f"/tmp/{self.tool_id}_{step}_output.txt", "ts": int(time.time() * 1000)}


def register_builtin(register_fn: Callable[[str, ToolWrapper], None]) -> None:
    """注册 4 个 wrapper:全部用真实实现,默认 dry_run=True。"""
    from .info_gap import InfoGapWrapper
    from .fengge import FenggeWrapper
    from .tiktok_story import TikTokStoryWrapper
    from .material_collector import MaterialCollectorWrapper
    register_fn("info_gap", InfoGapWrapper(dry_run=True))
    register_fn("fengge", FenggeWrapper(dry_run=True))
    register_fn("tiktok_story", TikTokStoryWrapper(dry_run=True))
    register_fn("material_collector", MaterialCollectorWrapper(dry_run=True))