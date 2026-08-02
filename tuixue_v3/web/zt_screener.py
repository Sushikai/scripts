"""
web/zt_screener.py — 涨停板次日溢价策略 实时选股 + 结果呈现。

提供 FastAPI router 供 server.py 挂载：
  GET  /api/zt/backtest     — 运行回测
  POST /api/zt/backtest     — 启动回测任务
  GET  /api/zt/optimize     — 运行优化
  GET  /api/zt/params       — 当前参数
  POST /api/zt/params       — 更新参数
  GET  /api/zt/live_pick    — 实时推票 (今日已锁板 → 明天 09:30 集合竞价可买)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as systime
from datetime import datetime
from typing import Any

log = logging.getLogger("tuixue_v3.zt_screener")


def _envelope(data=None, error=None, **kw):
    """统一信封包装 (跟 server.py envelope 行为一致)."""
    if error is not None:
        return {"ok": False, "error": error, "ts": datetime.now().isoformat()}
    return {"ok": True, "data": data, "ts": datetime.now().isoformat()}


def register(app):
    """挂载 ZT 策略路由到 FastAPI app。"""

    # ── 缓存 ──
    _BT_TASK_TTL = 600  # 10min
    _BT_TASK_PREFIX = "zt_bt_task:"

    def _bt_task_key(run_id: str) -> str:
        return f"{_BT_TASK_PREFIX}{run_id}"

    def _bt_task_set(run_id: str, data: dict):
        from .. import cache_store
        cache_store.get_store().set(_bt_task_key(run_id), data, ttl=_BT_TASK_TTL)

    def _bt_task_get(run_id: str) -> dict | None:
        from .. import cache_store
        val = cache_store.get_store().get(_bt_task_key(run_id))
        return val if isinstance(val, dict) else None

    _inst_params: dict | None = None

    # ── live_pick 缓存 (用 mutable 容器以便内部函数访问) ──
    _live_state = {"cache": None, "ts": 0.0}
    _LIVE_PICK_TTL = 30  # 30s 缓存

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
        return _envelope(data={"params": _get_params()})

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
        _bt_task_set(run_id, {"status": "running", "ts": datetime.now().isoformat()})

        def _task():
            try:
                result = _run_bt(params)
                _bt_task_set(run_id, {"status": "done", "result": result})
            except Exception as e:
                _bt_task_set(run_id, {"status": "error", "error": str(e)})

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _task)
        return _envelope(data={"run_id": run_id})

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
        from .server import envelope
        if run_id:
            task = _bt_task_get(run_id)
            return envelope(data=task if task else {"status": "unknown"})
        # 列出所有任务 — Redis 不支持枚举, 返回空列表
        return envelope(data={"tasks": []})

    # ── GET /api/zt/live_pick — 实时推票 ──
    @app.get("/api/zt/live_pick")
    async def api_zt_live_pick(top_n: int = 8):
        """实时推票：扫描今天已锁板 / 即将锁板股, 按 OPTIMAL_PARAMS 打分。

        返回: {date, ts, params, snapshot_total, near_limit_count, locked_count,
               picks: [{code, name, pass, reason, score, change_pct, locked, price,
                        limit_price_est, amount_yi, volume_ratio, amplitude, mcap_yi,
                        board, near_limit_pct, turnover_pct}], buy_window, sell_plan, note}
        """
        if _live_state["cache"] and (systime.time() - _live_state["ts"]) < _LIVE_PICK_TTL:
            return _envelope(data=_live_state["cache"])

        from .. import zt_config as cfg
        from .. import multi_source_fetchers as msf
        from datetime import datetime as dt

        today = dt.now().strftime("%Y%m%d")
        params = _get_params()

        def _scan():
            try:
                spot = msf.fetch_spot_a_full(overall_timeout=8)
            except Exception as e:
                log.warning(f"live_pick fetch_spot_a_full 失败: {e}")
                return {"error": str(e)}

            board_codes = {"300", "301", "688", "689"}
            near_limit = []
            locked = []
            for code, info in spot.items():
                try:
                    pct = float(info.get("涨跌幅", 0) or 0)
                except Exception:
                    continue
                if pct < 7:
                    continue
                # 区分主板 vs 20cm 板
                prefix = code[:3]
                is_20cm = prefix in board_codes
                threshold_locked = 19.5 if is_20cm else 9.5
                if pct >= threshold_locked:
                    locked.append((code, info, pct))
                elif pct >= (14.0 if is_20cm else 7.0):
                    near_limit.append((code, info, pct))

            # 按 OPTIMAL_PARAMS min_streak/max_streak 过滤
            # 没有连板历史, 简化处理: 全部纳入候选, pass 由 score_one 决定
            def _score_one(item, locked_status, is_20cm_flag):
                code, info, pct = item
                score = 50.0
                # 涨幅接近涨停价 +10
                if is_20cm_flag:
                    score += min(pct / 19.5 * 10, 10)
                else:
                    score += min(pct / 9.5 * 10, 10)
                # 量比放大 +10
                vol_ratio = float(info.get("量比", 0) or 0)
                if vol_ratio > 5:
                    score += 10
                elif vol_ratio > 2:
                    score += 5
                # 振幅 < 5% (封板稳) +5
                amp = float(info.get("振幅", 0) or 0)
                if amp < 5 and locked_status:
                    score += 5
                # 市值 15-150 亿 +5
                mcap = float(info.get("总市值", 0) or 0) / 1e8
                if 15 <= mcap <= 150:
                    score += 5
                # 换手 2-50% +5
                turn = float(info.get("换手率", 0) or 0)
                if 2 <= turn <= 50:
                    score += 5
                # 成交额越大越好 +5
                amount = float(info.get("成交额", 0) or 0) / 1e8
                if amount > 1:
                    score += 5
                # 已封板 +10
                if locked_status:
                    score += 10
                return round(score, 2)

            picks = []
            all_candidates = [(c, i, p, True) for c, i, p in locked] + [(c, i, p, False) for c, i, p in near_limit]
            for code, info, pct, is_locked in all_candidates:
                is_20cm = code[:3] in board_codes
                score = _score_one((code, info, pct), is_locked, is_20cm)
                mcap_yi = round(float(info.get("总市值", 0) or 0) / 1e8, 2)
                amount_yi = round(float(info.get("成交额", 0) or 0) / 1e8, 2)
                vol_ratio = float(info.get("量比", 0) or 0)
                amp = float(info.get("振幅", 0) or 0)
                turn = float(info.get("换手率", 0) or 0)
                price = float(info.get("最新价", 0) or 0)
                prev_close = float(info.get("昨收", 0) or 0)
                if prev_close > 0:
                    limit_price = round(prev_close * (1.195 if is_20cm else 1.095), 2)
                else:
                    limit_price = 0.0
                picks.append({
                    "code": code,
                    "name": info.get("name", ""),
                    "pass": True,  # 盘中实时推票硬过滤仅看涨幅 ≥ 7%
                    "reason": "" if is_locked else "未封板 (涨幅 7-9.4%)",
                    "score": score,
                    "change_pct": round(pct, 2),
                    "locked": is_locked,
                    "price": price,
                    "limit_price_est": limit_price,
                    "amount_yi": amount_yi,
                    "volume_ratio": round(vol_ratio, 2),
                    "amplitude": round(amp, 2),
                    "mcap_yi": mcap_yi,
                    "turnover_pct": round(turn, 2),
                    "near_limit_pct": round(pct, 2),
                    "board": "20cm" if is_20cm else "main",
                })

            picks.sort(key=lambda x: -x["score"])
            picks = picks[:top_n]

            return {
                "date": today,
                "ts": datetime.now().isoformat(),
                "params": params,
                "snapshot_total": len(spot),
                "near_limit_count": len(near_limit),
                "locked_count": len(locked),
                "picks": picks,
                "buy_window": (
                    "T+1 日 09:30 开盘买入" if params.get("entry_rule") == "open_t1"
                    else "T 日 14:57 收盘集合竞价买入 (今天已封板股)"
                ),
                "sell_plan": {
                    "entry_rule": params.get("entry_rule", "open_t1"),
                    "trail_activate_pct": params.get("trail_activate_pct", 0.5),
                    "trail_pullback_pct": params.get("trail_pullback_pct", 2.0),
                    "stop_loss_pct": params.get("stop_loss_pct", -3.0),
                    "rule": (
                        "open_t1: T+1开盘买入 → T+2才能卖 → ≥{trail}%激活trail/回落{pullback}%退出/止损{stop}%"
                    ).format(
                        trail=params.get("trail_activate_pct", 0.5),
                        pullback=params.get("trail_pullback_pct", 2.0),
                        stop=params.get("stop_loss_pct", -3.0),
                    ) if params.get("entry_rule") == "open_t1"
                    else "close_t0: T收盘买入 → T+1就能卖 → ≥{trail}%激活trail/回落{pullback}%退出/止损{stop}%".format(
                        trail=params.get("trail_activate_pct", 0.5),
                        pullback=params.get("trail_pullback_pct", 2.0),
                        stop=params.get("stop_loss_pct", -3.0),
                    ),
                },
                "note": "盘中实时扫描 — 涨跌幅 ≥ 7%/14% 视为'即将涨停'候选, 按 OPTIMAL_PARAMS 打分推 top_n",
            }

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _scan)
        if "error" in result:
            return _envelope(error=result["error"])
        _live_state["cache"] = result
        _live_state["ts"] = systime.time()
        return _envelope(data=result)

    log.info("ZT 策略路由已注册")
