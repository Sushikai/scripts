#!/usr/bin/env python3
"""
tuixue_v3/strat_era35_adl.py
Ship 91/100 — 量化 era 2026 高级策略 #35

ADL Strategy (Accumulation/Distribution Line)

设计:
CLV = ((close - low) - (high - close)) / (high - low) if h!=l else 0
ADL[i] = ADL[i-1] + CLV[i] * volume[i]

信号:
- ADL 上升趋势 → buy (累积)
- ADL 下降趋势 → sell (派发)
- 用 ADL 与价格比较判断背离

输入: {code: list[(high, low, close, volume)]}
输出: signal 列表

2026-08-03 Ship 91 — 10000 轮迭代 P5 第三十六步
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
class ADLSignal:
    code: str
    side: str
    current: float
    adl: float
    adl_prev: float
    adl_slope: float
    price_slope: float
    divergence: str           # "bull" | "bear" | "none"
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "adl": self.adl,
            "adl_prev": self.adl_prev,
            "adl_slope": self.adl_slope,
            "price_slope": self.price_slope,
            "divergence": self.divergence,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_adl_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> list[float]:
    """ADL 时序"""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n < 1:
        return []
    adl = [0.0]
    for i in range(1, n):
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = volumes[i]
        if h == l:
            clv = 0.0
        else:
            clv = ((c - l) - (h - c)) / (h - l)
        adl.append(adl[-1] + clv * v)
    return adl


def compute_slope(series: list[float], window: int = 10) -> Optional[float]:
    """线性回归斜率"""
    if len(series) < window:
        return None
    sub = series[-window:]
    n = len(sub)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(sub) / n
    num = sum((xs[i] - x_mean) * (sub[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    *,
    slope_window: int = 10,
) -> Optional[ADLSignal]:
    """ADL 信号"""
    adl_series = compute_adl_series(highs, lows, closes, volumes)
    if len(adl_series) < slope_window:
        return None

    adl = adl_series[-1]
    adl_prev = adl_series[-2]
    adl_slope = compute_slope(adl_series, window=slope_window)
    price_slope = compute_slope(closes, window=slope_window)
    if adl_slope is None or price_slope is None:
        return None
    current = closes[-1]

    # 背离
    divergence = "none"
    if adl_slope > 0 and price_slope < 0:
        divergence = "bull"
    elif adl_slope < 0 and price_slope > 0:
        divergence = "bear"

    if adl_slope > 0:
        side = "buy"
        strength = min(1.0, abs(adl_slope) / max(abs(adl), 1.0))
        if divergence == "bull":
            strength = min(1.0, strength + 0.2)
    elif adl_slope < 0:
        side = "sell"
        strength = min(1.0, abs(adl_slope) / max(abs(adl), 1.0))
        if divergence == "bear":
            strength = min(1.0, strength + 0.2)
    else:
        side = "hold"
        strength = 0.3

    return ADLSignal(
        code=code, side=side,
        current=round(current, 4),
        adl=round(adl, 2),
        adl_prev=round(adl_prev, 2),
        adl_slope=round(adl_slope, 4),
        price_slope=round(price_slope, 4),
        divergence=divergence,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[ADLSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes, volumes) in universe.items():
        sig = generate_signal(code, highs, lows, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[ADLSignal]) -> tuple[list[ADLSignal], list[ADLSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: ADLSignal) -> str:
    return (f"{s.code}: {s.side.upper()} adl={s.adl:.0f} "
            f"slope={s.adl_slope:+.2f} div={s.divergence} strength={s.strength:.2f}")