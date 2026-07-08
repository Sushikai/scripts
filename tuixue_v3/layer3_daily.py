"""
tuixue_v3/layer3_daily.py
Layer 3：日线趋势形态深度过滤
- MA5 > MA10 > MA20 > MA60（多头排列）
- 20 日累计涨幅 < 35%
- 量价结构：上涨放量 / 回调缩量（回调量 ≤ 拉升均量 50%）
- 换手 5%-15%
- 底部箱体突破，上方无密集套牢峰
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from . import config as cfg

log = logging.getLogger("tuixue_v3.layer3")


def _ensure_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """复用 lib_common.calc_indicators 算 MA/MACD/VOL"""
    if df is None or df.empty:
        return df
    if "MA5" not in df.columns:
        from . import lib_common as lc
        df = lc.calc_indicators(df.copy(), ma_periods=(5, 10, 20, 60))
    return df


# 安全 import：避免 lib_common 失败时整个模块挂掉
try:
    from . import lib_common as lc
    _HAS_LC = True
except Exception as e:
    log.warning(f"lib_common 不可用: {e}")
    _HAS_LC = False


def _check_ma_alignment(df: pd.DataFrame) -> tuple[bool, dict]:
    """MA5 > MA10 > MA20 > MA60，股价在 MA5 之上 — MA60 可选"""
    need = [f"MA{p}" for p in (5, 10, 20, 60)]
    if not all(c in df.columns for c in need):
        return False, {"reason": "指标缺失"}
    last = df.iloc[-1]
    vals = {c: float(last[c]) for c in need if pd.notna(last[c])}
    if cfg.L3_REQUIRE_MA60:
        if len(vals) != 4:
            return False, {"reason": "指标为 NaN", "vals": vals}
        need_check = ("MA5", "MA10", "MA20", "MA60")
    else:
        # 缺 MA60 时只检查 MA5/10/20
        if not all(k in vals for k in ("MA5", "MA10", "MA20")):
            return False, {"reason": "核心 MA 指标缺失", "vals": vals}
        need_check = ("MA5", "MA10", "MA20")

    price = float(last["收盘"])
    # 严格多头：链式比较
    chain_ok = all(vals[need_check[i]] > vals[need_check[i+1]] for i in range(len(need_check)-1))
    above_ma5 = price > vals["MA5"]
    ok = chain_ok and above_ma5
    return ok, vals


def _check_20d_gain(df: pd.DataFrame) -> tuple[bool, dict]:
    """近 20 个交易日阶段累计涨幅 < 35%"""
    if df is None or len(df) < 21:
        return False, {"reason": "数据不足"}
    price_now = float(df.iloc[-1]["收盘"])
    price_20d_ago = float(df.iloc[-21]["收盘"])
    if price_20d_ago <= 0:
        return False, {"reason": "基准价为 0"}
    gain = (price_now - price_20d_ago) / price_20d_ago * 100
    return gain < cfg.L3_GAIN_20D_MAX_PCT, {"gain_pct": round(gain, 2), "limit": cfg.L3_GAIN_20D_MAX_PCT}


def _check_turnover(df: pd.DataFrame) -> tuple[bool, dict]:
    """换手率 min ≤ tr ≤ max。数据缺失（0 或 NaN）放行，避免上游没返回该字段时全军覆没。"""
    if df is None or len(df) < 1 or "换手率" not in df.columns:
        return True, {"turnover_pct": None, "reason": "字段缺失,放行"}
    tr = float(df.iloc[-1]["换手率"] or 0)
    if tr <= 0 or pd.isna(df.iloc[-1]["换手率"]):
        # 数据源没给换手率（如 akshare stock_zh_a_hist 不返回此字段），放行
        return True, {"turnover_pct": None, "reason": "上游字段为0,放行"}
    ok = cfg.L3_TURN_OVER_MIN_PCT <= tr <= cfg.L3_TURN_OVER_MAX_PCT
    return ok, {"turnover_pct": round(tr, 2)}


def _check_volume_structure(df: pd.DataFrame) -> tuple[bool, dict]:
    """上涨放量 / 回调缩量：回调段均量 ≤ 上涨段均量 × 50%"""
    if df is None or len(df) < 15:
        return False, {"reason": "数据不足"}

    recent = df.tail(15).copy()
    # 用收盘价 vs 前日收盘判定涨跌方向
    recent["up"] = recent["收盘"].diff() > 0
    up_vols = recent.loc[recent["up"], "成交量"]
    down_vols = recent.loc[~recent["up"], "成交量"]

    if len(up_vols) == 0 or len(down_vols) == 0:
        return False, {"reason": "无涨跌分段"}

    up_avg = float(up_vols.mean())
    down_avg = float(down_vols.mean())
    if up_avg <= 0:
        return False, {"reason": "上涨均量为 0"}

    ratio = down_avg / up_avg
    ok = ratio <= cfg.L3_PULLBACK_VOL_MAX_RATIO
    return ok, {"up_avg_vol": round(up_avg, 0), "down_avg_vol": round(down_avg, 0), "down_to_up_ratio": round(ratio, 2)}


def _check_breakout(df: pd.DataFrame) -> tuple[bool, dict]:
    """突破：当前收盘价 > 过去 L3_BREAKOUT_LOOKBACK 日最高价 × 0.95（视为突破前期箱体）"""
    if df is None or len(df) < cfg.L3_BREAKOUT_LOOKBACK + 1:
        return False, {"reason": "数据不足"}
    recent_close = float(df.iloc[-1]["收盘"])
    lookback_high = float(df.iloc[-cfg.L3_BREAKOUT_LOOKBACK - 1: -1]["最高"].max())
    if lookback_high <= 0:
        return False, {"reason": "历史高为 0"}

    # 突破 = 当前价 ≥ 前 N 日最高 × 95%
    threshold = lookback_high * 0.95
    ok = recent_close >= threshold

    # 检查上方无密集套牢（粗略：突破后回踩缩量 + MA5 上方）
    above_ma5 = recent_close > float(df.iloc[-1].get("MA5", recent_close * 0.99))
    return ok and above_ma5, {
        "lookback_high": round(lookback_high, 2),
        "current": round(recent_close, 2),
        "threshold": round(threshold, 2),
    }


def screen(stocks: list[dict], date_str: str | None = None) -> tuple[list[dict], dict]:
    stats = {
        "input": len(stocks),
        "ma_fail": 0,
        "gain_fail": 0,
        "turnover_fail": 0,
        "vol_struct_fail": 0,
        "breakout_fail": 0,
        "passed": 0,
    }

    passed = []
    for s in stocks:
        df = s.get("_df_ref")
        if df is None or df.empty:
            stats["ma_fail"] += 1
            continue

        if _HAS_LC:
            df = lc.calc_indicators(df, ma_periods=(5, 10, 20, 60))
            s["_df_ref"] = df

        # MA 多头
        ma_ok, ma_detail = _check_ma_alignment(df)
        if not ma_ok:
            stats["ma_fail"] += 1
            continue

        # 20 日涨幅
        gain_ok, gain_detail = _check_20d_gain(df)
        if not gain_ok:
            stats["gain_fail"] += 1
            continue

        # 换手
        tr_ok, tr_detail = _check_turnover(df)
        if not tr_ok:
            stats["turnover_fail"] += 1
            continue

        # 量价结构
        vs_ok, vs_detail = _check_volume_structure(df)
        if not vs_ok:
            stats["vol_struct_fail"] += 1
            continue

        # 突破（可选）
        if getattr(cfg, "L3_REQUIRE_BREAKOUT", False):
            bo_ok, bo_detail = _check_breakout(df)
            if not bo_ok:
                stats["breakout_fail"] += 1
                continue
        else:
            bo_detail = {"skipped": True}

        s["ma_detail"] = ma_detail
        s["gain_20d_pct"] = gain_detail["gain_pct"]
        s["turnover_pct"] = tr_detail["turnover_pct"]
        s["vol_struct"] = vs_detail
        s["breakout_detail"] = bo_detail
        tr_str = "n/a" if tr_detail["turnover_pct"] is None else f"{tr_detail['turnover_pct']:.1f}%"
        s["pass_detail"] = s.get("pass_detail", "") + f" | L3: MA多头 20d+{gain_detail['gain_pct']:.1f}% tr={tr_str}"
        passed.append(s)

    stats["passed"] = len(passed)
    log.info(f"Layer3: {stats}")
    return passed, stats