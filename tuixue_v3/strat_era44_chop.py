#!/usr/bin/env python3
"""
tuixue_v3/strat_era44_chop.py
Ship 100/100 — 量化 era 2026 高级策略 #44 (终极)

Choppiness Index Strategy (市场震荡度)

设计:
ATR_sum = sum(TR, n)  where TR = max(high-low, |high-prev_close|, |low-prev_close|)
range = highest_high(n) - lowest_low(n)
CI = 100 × ln(ATR_sum / range) / ln(n)

CI ∈ [0, 100]:
- CI > 61.8: 市场震荡/无趋势
- CI < 38.2: 强趋势

信号:
- CI < 38.2: 趋势行情 → buy (顺势)
- CI > 61.8: 区间行情 → sell/hold (观望)
- 与方向结合 → 趋势确认

输入: {code: list[(high, low, close)]}
输出: signal 列表

2026-08-03 Ship 100 — 10000 轮迭代 P5 完结撒花 🎉
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class ChoppinessSignal:
    code: str
    side: str
    current: float
    ci: float
    atr_sum: float
    range_high_low: float
    trend_strength: str       # "trending" | "choppy" | "normal"
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "ci": self.ci,
            "atr_sum": self.atr_sum,
            "range_high_low": self.range_high_low,
            "trend_strength": self.trend_strength,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_choppiness(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    window: int = 14,
) -> Optional[tuple[float, float, float]]:
    """Choppiness Index + ATR sum + range"""
    n = min(len(highs), len(lows), len(closes))
    if n < window + 1:
        return None

    sub_h = highs[-(window + 1):]
    sub_l = lows[-(window + 1):]
    sub_c = closes[-(window + 1):]

    atr_sum = 0.0
    for i in range(1, len(sub_h)):
        tr = max(
            sub_h[i] - sub_l[i],
            abs(sub_h[i] - sub_c[i - 1]),
            abs(sub_l[i] - sub_c[i - 1]),
        )
        atr_sum += tr

    highest = max(sub_h)
    lowest = min(sub_l)
    rng = highest - lowest

    if rng == 0 or window < 2:
        return None

    ci = 100 * math.log(atr_sum / rng) / math.log(window)
    return ci, atr_sum, rng


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    window: int = 14,
    trend_threshold: float = 38.2,
    choppy_threshold: float = 61.8,
) -> Optional[ChoppinessSignal]:
    """Choppiness Index 信号"""
    res = compute_choppiness(highs, lows, closes, window=window)
    if res is None:
        return None
    ci, atr_sum, rng = res
    current = closes[-1]

    # 判断趋势方向
    sma = sum(closes[-window:]) / window
    trending_up = current > sma
    trending_down = current < sma

    if ci < trend_threshold:
        trend_strength = "trending"
        if trending_up:
            side = "buy"
            strength = 0.9
        elif trending_down:
            side = "sell"
            strength = 0.9
        else:
            side = "hold"
            strength = 0.5
    elif ci > choppy_threshold:
        trend_strength = "choppy"
        side = "hold"
        strength = 0.2
    else:
        trend_strength = "normal"
        if trending_up:
            side = "buy"
            strength = 0.5
        elif trending_down:
            side = "sell"
            strength = 0.5
        else:
            side = "hold"
            strength = 0.3

    return ChoppinessSignal(
        code=code, side=side,
        current=round(current, 4),
        ci=round(ci, 4),
        atr_sum=round(atr_sum, 4),
        range_high_low=round(rng, 4),
        trend_strength=trend_strength,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[ChoppinessSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes) in universe.items():
        sig = generate_signal(code, highs, lows, closes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[ChoppinessSignal]) -> tuple[list[ChoppinessSignal], list[ChoppinessSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: ChoppinessSignal) -> str:
    return (f"{s.code}: {s.side.upper()} ci={s.ci:.1f} "
            f"trend={s.trend_strength} strength={s.strength:.2f}")


# ═══════════════════════════════════════════════════════
# Phase 5 总览
# ═══════════════════════════════════════════════════════

def phase5_summary() -> dict:
    """Phase 5 全部 44 个策略汇总"""
    return {
        "phase": 5,
        "total_strategies": 44,
        "categories": {
            "oscillator": ["RSI", "Stochastic", "Williams_R", "CCI", "ROC", "MFI", "UO", "StochRSI"],
            "trend": ["MACD", "ADX", "Aroon", "TRIX", "PPO", "DPO", "Vortex"],
            "moving_average": ["SMA_cross", "EMA_cross", "Keltner", "Donchian", "Ichimoku", "ParabolicSAR", "VWAP"],
            "volume": ["OBV", "OBV_Trend", "ADL", "CMF", "EOM", "WAD", "NVI", "PVI"],
            "volatility": ["Bollinger", "ATR", "Keltner_Ch", "Choppiness"],
            "pattern": ["Engulfing", "Doji", "Hammer", "MeanReversion"],
            "momentum": ["Momentum", "ROC", "TSI"],
            "regression": ["LinearReg", "LinearRegSlope", "ZScore"],
        },
        "ship_range": "61-100",
        "completion": "100/100 🎉",
    }