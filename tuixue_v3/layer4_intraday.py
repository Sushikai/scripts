"""
tuixue_v3/layer4_intraday.py
Layer 4：日内分时资金承接（最终关卡）
- 9:30-10:30 时间段 ≥ 70% 在均价线上方
- 烂板剔除：高位反复炸板 → 一律剔除
- 14:40 后尾盘偷袭 → 剔除
- 打板标的附加：仅 9:30-10:30 换手封板 + 封单 ≥ 流通市值 1% + 非无量一字

回测模式：历史分时不可得，标记 pass_detail 但不硬阻断（软通过）
实盘模式：拉今日分时 K + 涨停池，严格按规则过滤
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from typing import Any

import pandas as pd

from . import config as cfg
from . import data_layer as dl

log = logging.getLogger("tuixue_v3.layer4")


def _parse_time(t: Any) -> dtime:
    """parse '09:35' / '09:35:30' / datetime.time → datetime.time"""
    if isinstance(t, dtime):
        return t
    if isinstance(t, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(t, fmt).time()
            except ValueError:
                continue
    if hasattr(t, "hour"):
        return dtime(t.hour, t.minute, getattr(t, "second", 0))
    return dtime(9, 30)


def _is_above_avg_pct(df: pd.DataFrame, window_start: str = "09:30", window_end: str = "10:30") -> tuple[bool, dict]:
    """
    9:30-10:30 时段 ≥ 70% 时间价格 ≥ 分时均价线
    df: 1 分钟 K，列含 '时间'/'close'/'avg' 或 '成交均价'
    """
    if df is None or df.empty:
        return False, {"reason": "无分时数据"}

    # 列名兼容
    time_col = next((c for c in ["时间", "time", "日期", "日期时间"] if c in df.columns), None)
    close_col = next((c for c in ["收盘", "close", "最新价"] if c in df.columns), None)
    avg_col = next((c for c in ["均价", "avg", "成交均价", "平均价"] if c in df.columns), None)

    if not time_col or not close_col:
        return False, {"reason": f"列缺失 time={time_col} close={close_col}"}

    df = df.copy()
    df["_t"] = df[time_col].apply(_parse_time)

    ws = _parse_time(window_start)
    we = _parse_time(window_end)
    win = df[(df["_t"] >= ws) & (df["_t"] <= we)]

    if win.empty:
        return False, {"reason": f"窗口 {window_start}-{window_end} 无数据"}

    if avg_col and avg_col in win.columns:
        above = (win[close_col] >= win[avg_col]).sum()
    else:
        # 退化：自算累计均价
        win = win.copy()
        win["_cum_vol"] = win["成交量"].cumsum()
        win["_cum_amt"] = (win[close_col] * win["成交量"]).cumsum()
        win["_avg"] = win["_cum_amt"] / win["_cum_vol"].replace(0, pd.NA)
        above = (win[close_col] >= win["_avg"].fillna(win[close_col])).sum()

    total = len(win)
    pct = above / total if total > 0 else 0
    return pct >= cfg.L4_ABOVE_AVG_RATIO, {
        "above_count": int(above),
        "total_count": int(total),
        "above_ratio": round(pct, 3),
        "window": f"{window_start}-{window_end}",
    }


def _check_late_pump(df: pd.DataFrame, limit_up_pool: list[dict] | None = None) -> tuple[bool, dict]:
    """14:40 后尾盘偷袭拉升 → 剔除"""
    if df is None or df.empty:
        return True, {"skip": "no data"}

    time_col = next((c for c in ["时间", "time"] if c in df.columns), None)
    close_col = next((c for c in ["收盘", "close"] if c in df.columns), None)
    if not time_col or not close_col:
        return True, {"skip": "no cols"}

    df = df.copy()
    df["_t"] = df[time_col].apply(_parse_time)
    cutoff = _parse_time(cfg.L4_LATE_PUMP_CUTOFF)

    late = df[df["_t"] >= cutoff]
    if late.empty:
        return True, {"skip": "late window empty"}

    # 价格涨幅：14:40 收盘 vs 14:30 收盘
    early_cutoff = dtime(14, 30)
    early = df[df["_t"] <= early_cutoff]
    if early.empty:
        return True, {"skip": "no early"}
    p_early = float(early.iloc[-1][close_col])
    p_late = float(late.iloc[-1][close_col])
    pct = (p_late - p_early) / p_early * 100 if p_early > 0 else 0
    # 14:40 后拉升 > 2% 视为偷袭
    return pct <= 2.0, {"late_pump_pct": round(pct, 2)}


def _check_zt_special(df: pd.DataFrame, zt_pool_row: dict | None) -> tuple[bool, dict]:
    """
    打板标的附加约束：
    - 9:30-10:30 完成换手封板（非无量一字）
    - 封单金额 ≥ 流通市值 1%
    """
    if zt_pool_row is None:
        return True, {"skip": "非涨停"}

    # 封单 / 流通市值
    sealed_amount = float(zt_pool_row.get("封单金额", zt_pool_row.get("sealed_amount", 0)) or 0)
    free_mv = float(zt_pool_row.get("流通市值", zt_pool_row.get("free_mv", 0)) or 0)
    if free_mv <= 0:
        return True, {"skip": "无流通市值字段"}
    sealed_ratio = sealed_amount / free_mv
    if sealed_ratio < cfg.L4_ZT_MIN_SEALED_RATIO:
        return False, {
            "reason": f"封单占比 {sealed_ratio:.3%} < {cfg.L4_ZT_MIN_SEALED_RATIO:.1%}",
            "sealed_amount": sealed_amount,
            "free_mv": free_mv,
        }

    # 换手封板（非无量一字）：当日换手率 ≥ 5%
    tr = float(zt_pool_row.get("换手率", zt_pool_row.get("turnover_rate", 0)) or 0)
    if tr < cfg.L4_ZT_MIN_TURNOVER_PCT:
        return False, {
            "reason": f"打板换手 {tr:.2f}% < {cfg.L4_ZT_MIN_TURNOVER_PCT}%（疑似一字无量）",
            "turnover_rate": tr,
        }

    # 封板时间窗：涨停时间 ≤ 10:30（来自涨停池的"首次封板时间"字段）
    first_seal = zt_pool_row.get("首次封板时间", zt_pool_row.get("first_seal_time"))
    if first_seal:
        try:
            seal_t = _parse_time(first_seal)
            cutoff = _parse_time(cfg.L4_ZT_EARLY_CUTOFF)
            if seal_t > cutoff:
                return False, {
                    "reason": f"封板时间 {first_seal} > {cfg.L4_ZT_EARLY_CUTOFF}",
                    "first_seal_time": first_seal,
                }
        except Exception:
            pass

    return True, {"sealed_ratio": round(sealed_ratio, 4), "turnover_rate": tr}


def _check_broken_limit(zt_pool_row: dict | None) -> tuple[bool, dict]:
    """反复炸板：炸板次数 > 1 → 烂板剔除"""
    if zt_pool_row is None:
        return True, {"skip": "非涨停"}
    broken_count = int(zt_pool_row.get("炸板次数", zt_pool_row.get("broken_count", 0)) or 0)
    if broken_count > cfg.L4_MAX_BROKEN_LIMIT:
        return False, {
            "reason": f"炸板 {broken_count} 次 > {cfg.L4_MAX_BROKEN_LIMIT}（烂板）",
            "broken_count": broken_count,
        }
    return True, {"broken_count": broken_count}


def screen(stocks: list[dict], date_str: str | None = None, mode: str = "live") -> tuple[list[dict], dict]:
    """
    mode: "live" 严格分时检查 / "backtest" 软通过（历史分时不可得）
    """
    stats = {
        "input": len(stocks),
        "below_avg": 0,
        "late_pump": 0,
        "broken_limit": 0,
        "zt_seal_fail": 0,
        "passed": 0,
    }

    if mode == "backtest":
        # 回测模式：所有分时条件软通过，只记录说明
        for s in stocks:
            s["pass_detail"] = s.get("pass_detail", "") + " | L4: 回测模式软通过（历史分时不可得）"
        stats["passed"] = len(stocks)
        stats["mode"] = "backtest"
        log.info(f"Layer4 (backtest mode): {stats}")
        return stocks, stats

    # 实盘：拉涨停池
    zt_pool = dl.fetch_limit_up_pool(date_str)
    zt_map = {row.get("代码", row.get("code", "")): row for row in zt_pool}

    passed = []
    degraded = 0
    for s in stocks:
        code = s["code"]
        df = s.get("_intraday_df")
        if df is None:
            df = dl.fetch_intraday(code, date_str)
        zt_row = zt_map.get(code)

        # 分时数据不可用时（akshare 限频/盘后无数据）— 软通过 + caveat
        if df is None or (hasattr(df, "empty") and df.empty):
            degraded += 1
            s["intraday_above"] = {"above_ratio": None, "degraded": True, "reason": "分时数据不可达"}
            s["late_pump"] = {"degraded": True}
            s["broken_limit"] = {"degraded": True}
            s["zt_seal"] = {"degraded": True}
            s["pass_detail"] = s.get("pass_detail", "") + " | L4: 分时降级通过（akshare 限频/盘后）"
            passed.append(s)
            continue

        # 1) 分时承接
        above_ok, above_detail = _is_above_avg_pct(df)
        if not above_ok:
            stats["below_avg"] += 1
            continue

        # 2) 尾盘偷袭
        late_ok, late_detail = _check_late_pump(df)
        if not late_ok:
            stats["late_pump"] += 1
            continue

        # 3) 炸板
        brk_ok, brk_detail = _check_broken_limit(zt_row)
        if not brk_ok:
            stats["broken_limit"] += 1
            continue

        # 4) 打板附加
        zt_ok, zt_detail = _check_zt_special(df, zt_row)
        if not zt_ok:
            stats["zt_seal_fail"] += 1
            continue

        s["intraday_above"] = above_detail
        s["late_pump"] = late_detail
        s["broken_limit"] = brk_detail
        s["zt_seal"] = zt_detail
        s["pass_detail"] = s.get("pass_detail", "") + f" | L4: 分时承接 above={above_detail.get('above_ratio')}"
        passed.append(s)

    stats["passed"] = len(passed)
    stats["degraded"] = degraded
    stats["mode"] = "live"
    log.info(f"Layer4 (live mode): {stats}")
    return passed, stats