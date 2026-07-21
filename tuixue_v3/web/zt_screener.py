"""
web/zt_screener.py — 涨停板次日溢价策略 实时选股 + 结果呈现。

提供 FastAPI router 供 server.py 挂载：
  GET  /api/zt/backtest     — 运行回测
  POST /api/zt/backtest     — 启动回测任务
  GET  /api/zt/optimize     — 运行优化
  GET  /api/zt/params       — 当前参数
  POST /api/zt/params       — 更新参数
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as systime
from datetime import datetime
from typing import Any

log = logging.getLogger("tuixue_v3.zt_screener")


def register(app):
    """挂载 ZT 策略路由到 FastAPI app。"""

    # ── 缓存 ──
    _bt_tasks: dict[str, dict] = {}
    _inst_params: dict | None = None

    def _get_params() -> dict:
        """返回当前 ZT 参数。优先用实例级覆盖的（通过 POST 设置）。"""
        if _inst_params is not None:
            return _inst_params
        from .. import zt_config as cfg
        return dict(cfg.OPTIMAL_PARAMS)

    def _run_bt(params: dict) -> dict:
        """执行回测（同步，池内跑）。"""
        from .. import zt_backtest as zt_mod
        return zt_mod.run_zt_backtest(**params)

    # ── GET /api/zt/params — 当前参数 ──
    @app.get("/api/zt/params")
    async def api_zt_params():
        return {"params": _get_params()}

    # ── POST /api/zt/params — 更新参数 ──
    @app.post("/api/zt/params")
    async def api_zt_params_update(body: dict):
        nonlocal _inst_params
        _inst_params = {**_get_params(), **body}
        return {"ok": True, "params": _inst_params}

    # ── GET /api/zt/backtest — 同步回测 ──
    @app.get("/api/zt/backtest")
    async def api_zt_backtest_get(
        start: str | None = None,
        end: str | None = None,
    ):
        """同步运行回测（适配合适超时）。"""
        from .. import zt_config as cfg
        from .server import envelope
        params = _get_params().copy()
        params["start"] = start or cfg.ZT_START
        params["end"] = end or cfg.ZT_END
        params["sample"] = 0

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_bt, params)
        return envelope(data=result)

    # ── POST /api/zt/backtest — 异步回测 ──
    @app.post("/api/zt/backtest")
    async def api_zt_backtest_post(body: dict):
        """后台启动回测任务。"""
        from .. import zt_config as cfg
        params = _get_params().copy()
        params["start"] = body.get("start", cfg.ZT_START)
        params["end"] = body.get("end", cfg.ZT_END)
        params["sample"] = body.get("sample", 0)

        run_id = f"zt_bt_{datetime.now().strftime('%H%M%S')}_{systime.time_ns() % 100000}"
        _bt_tasks[run_id] = {"status": "running", "ts": datetime.now().isoformat()}

        def _task():
            try:
                result = _run_bt(params)
                _bt_tasks[run_id] = {"status": "done", "result": result}
            except Exception as e:
                _bt_tasks[run_id] = {"status": "error", "error": str(e)}

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _task)
        return {"run_id": run_id}

    # ── GET /api/zt/optimize — 优化 ──
    @app.get("/api/zt/optimize")
    async def api_zt_optimize(
        iterations: int = 100,
        start: str | None = None,
        end: str | None = None,
    ):
        """运行优化。"""
        from .. import zt_config as cfg
        from .. import zt_optimizer as opt_mod

        params = dict(
            start=start or cfg.ZT_START,
            end=end or cfg.ZT_OPTIMIZE_WINDOW_END,
            iterations=iterations,
            population=min(50, iterations // 2),
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: opt_mod.run_optimize(**params))
        return result

    # ── GET /api/zt/status — 任务状态 ──
    @app.get("/api/zt/status")
    async def api_zt_status(run_id: str | None = None):
        if run_id:
            return _bt_tasks.get(run_id, {"status": "unknown"})
        return {
            k: {"status": v.get("status"), "ts": v.get("ts")}
            for k, v in _bt_tasks.items()
        }

    log.info("ZT 策略路由已注册")
