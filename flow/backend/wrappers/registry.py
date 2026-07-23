"""wrapper 注册中心:tool_id → ToolWrapper 实例。

每个 wrapper 负责 1 个工具的所有 steps,提供 run_step() 异步函数。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from ..envelope import Code

_logger = logging.getLogger("flow.wrappers")

_REGISTRY: dict[str, "ToolWrapper"] = {}


def register(tool_id: str, wrapper: "ToolWrapper") -> None:
    _REGISTRY[tool_id] = wrapper
    _logger.info("wrapper registered: %s (%s)", tool_id, wrapper.__class__.__name__)


def get_wrapper(tool_id: str) -> "ToolWrapper":
    if tool_id not in _REGISTRY:
        raise KeyError(f"tool_id {tool_id} not registered")
    return _REGISTRY[tool_id]


def list_tools() -> list[dict]:
    return [
        {
            "tool_id": w.tool_id,
            "name": w.name,
            "description": w.description,
            "steps": w.steps,
        }
        for w in _REGISTRY.values()
    ]


class ToolWrapper:
    """所有工具 wrapper 的抽象基类。"""

    tool_id: str = ""
    name: str = ""
    description: str = ""
    steps: list[str] = []

    async def run_step(
        self,
        step: str,
        params: dict,
        *,
        progress_cb: Callable[[float, Optional[str]], None],
        log_cb: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> dict:
        """单步执行。子类必须实现。"""
        raise NotImplementedError

    # 默认 wrapper.run_step 是 alias(为了兼容 router 里的 .run_step 写法)
    async def run(
        self,
        params: dict,
        *,
        progress_cb,
        log_cb,
        is_cancelled,
    ) -> dict:
        """默认全流程跑完所有 steps。"""
        raise NotImplementedError