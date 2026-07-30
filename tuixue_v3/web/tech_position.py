"""
web/tech_position.py — 个股技术面位置派生

2026-07-30: 为 AI 深度判断 (适不适合卖) 提供技术面维度
- 60 日高点回撤 / 52 周新低距离 / MA 乖离率 / 突破/支撑带
- 纯本地计算,无外部依赖
"""
from __future__ import annotations

import math
from typing import Iterable


def _safe_float(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def compute_tech_position(kline_rows: Iterable[dict], current_price: float | None = None) -> dict:
    """输入 K 线 (从 stock_kline_loader 来) + 可选当前价 → 输出技术位置字典。

    K 线格式 (与 stock_kline_loader 一致):
      {date, open, high, low, close, volume, change_pct, ma5, ma10, ma20, ma60, ...}

    返回字段:
      has_data:        bool — 是否有足够数据
      bars:            int  — 实际可用 K 线条数
      current_price:   float
      high_60d / low_60d / high_252d / low_252d
      pullback_from_60d_high_pct   float — 负=创 60 日新高,正=已回撤
      distance_to_252d_low_pct     float — 正=在 52 周低之上 (越低越强支撑)
      pct_position_60d             float — 当前在 60 日区间内的位置 [0, 100]%
      pct_position_252d            float — 当前在 252 日区间内的位置 [0, 100]%
      bias_ma5 / bias_ma20 / bias_ma60  float — MA 乖离率 (%)
      ma5_above_ma20: bool         — 多头排列 (MA5 > MA20)
      ma20_above_ma60: bool        — 多头排列 (MA20 > MA60)
      breakout_zone: bool          — 在 60 日新高 ±2% 范围内
      support_zone: bool           — 在 60 日新低 ±5% 范围内
      volatility_20d_pct           float — 20 日振幅/收盘价平均值 (近似 ATR%)
      trend_label: str             — "新高突破 / 强势上行 / 多头排列 / 震荡整理 / 高位回撤 / 破位下行 / 无明显趋势"
    """
    rows = list(kline_rows or [])
    if not rows:
        return {
            "has_data": False, "bars": 0, "current_price": 0.0,
            "high_60d": 0.0, "low_60d": 0.0,
            "high_252d": 0.0, "low_252d": 0.0,
            "pullback_from_60d_high_pct": None,
            "distance_to_252d_low_pct": None,
            "pct_position_60d": None,
            "pct_position_252d": None,
            "bias_ma5": None, "bias_ma20": None, "bias_ma60": None,
            "ma5_above_ma20": False, "ma20_above_ma60": False,
            "breakout_zone": False, "support_zone": False,
            "volatility_20d_pct": None,
            "trend_label": "无足够数据",
        }

    # 当前价优先用 current_price 参数, 否则用最后一日 close
    cp = _safe_float(current_price) or _safe_float(rows[-1].get("close"))

    # 取窗口 (升序排列, oldest first → newest last)
    closes = [_safe_float(r.get("close")) for r in rows]
    highs = [_safe_float(r.get("high")) for r in rows]
    lows = [_safe_float(r.get("low")) for r in rows]
    n = len(closes)

    # 60 日 / 252 日窗口 (少于 60 或 252 时全用)
    win_60 = closes[-60:] if n >= 60 else closes
    win_252 = closes  # 全量

    high_60 = max(highs[-60:]) if n > 0 else 0.0
    low_60 = min([l for l in lows[-60:] if l > 0]) if n > 0 else 0.0
    high_252 = max(highs)
    low_252 = min([l for l in lows if l > 0])

    # 距高点回撤 (%)
    pullback_60 = ((cp - high_60) / high_60 * 100) if high_60 else 0.0
    dist_252_low = ((cp - low_252) / low_252 * 100) if low_252 else 0.0

    # 区间位置 (0=在最低,100=在最高)
    pct_pos_60 = ((cp - low_60) / (high_60 - low_60) * 100) if high_60 > low_60 else 50.0
    pct_pos_252 = ((cp - low_252) / (high_252 - low_252) * 100) if high_252 > low_252 else 50.0
    pct_pos_60 = max(0.0, min(100.0, pct_pos_60))
    pct_pos_252 = max(0.0, min(100.0, pct_pos_252))

    # MA 乖离率 — MA5/20/60 在最后一根 K 线上
    last = rows[-1]
    ma5 = _safe_float(last.get("ma5"))
    ma20 = _safe_float(last.get("ma20"))
    ma60 = _safe_float(last.get("ma60"))
    bias_ma5 = ((cp - ma5) / ma5 * 100) if ma5 else 0.0
    bias_ma20 = ((cp - ma20) / ma20 * 100) if ma20 else 0.0
    bias_ma60 = ((cp - ma60) / ma60 * 100) if ma60 else 0.0

    # 突破/支撑判定
    breakout = (high_60 > 0) and (abs(cp - high_60) / high_60 * 100 <= 2.0)
    support = (low_60 > 0) and (abs(cp - low_60) / low_60 * 100 <= 5.0)

    # 20 日 ATR 近似 — (high-low) 平均 / 收盘均值
    last_20 = rows[-20:] if n >= 20 else rows
    ranges = [(_safe_float(r.get("high")) - _safe_float(r.get("low"))) for r in last_20]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    avg_close_20 = sum([_safe_float(r.get("close")) for r in last_20]) / len(last_20) if last_20 else 0.0
    volatility_20d = (avg_range / avg_close_20 * 100) if avg_close_20 > 0 else 0.0

    # 趋势判定 (7 类)
    if n < 5:
        label = "无明显趋势"
    elif breakout and bias_ma5 > 0 and bias_ma20 > 0:
        label = "新高突破"
    elif bias_ma5 > 5 and bias_ma20 > 5 and ma5 > ma20:
        label = "强势上行"
    elif ma5 >= ma20 >= ma60 and abs(bias_ma5) < 5 and abs(bias_ma20) < 8:
        label = "多头排列"
    elif abs(bias_ma5) < 3 and abs(bias_ma20) < 5:
        label = "震荡整理"
    elif pullback_60 > 10 and pullback_60 < 30 and bias_ma20 > 0:
        label = "高位回撤"
    elif pullback_60 >= 30 or bias_ma60 < -10:
        label = "破位下行"
    else:
        label = "震荡整理"

    return {
        "has_data": True,
        "bars": n,
        "current_price": round(cp, 3),
        "high_60d": round(high_60, 3),
        "low_60d": round(low_60, 3),
        "high_252d": round(high_252, 3),
        "low_252d": round(low_252, 3),
        "pullback_from_60d_high_pct": round(pullback_60, 2),
        "distance_to_252d_low_pct": round(dist_252_low, 2),
        "pct_position_60d": round(pct_pos_60, 1),
        "pct_position_252d": round(pct_pos_252, 1),
        "bias_ma5": round(bias_ma5, 2),
        "bias_ma20": round(bias_ma20, 2),
        "bias_ma60": round(bias_ma60, 2),
        "ma5_above_ma20": ma5 > ma20 if ma5 else False,
        "ma20_above_ma60": ma20 > ma60 if ma20 else False,
        "breakout_zone": breakout,
        "support_zone": support,
        "volatility_20d_pct": round(volatility_20d, 2),
        "trend_label": label,
    }
