#!/usr/bin/env python3
"""
tuixue_v3/strat_era41_wad.py
Ship 97/100 — 量化 era 2026 高级策略 #41

WAD Strategy (Williams Accumulation/Distribution)

设计:
TRH = max(close_prev, high)
TRL = min(close_prev, low)

if close > close_prev:
    AD = close - TRL
elif close < close_prev:
    AD = close - TRH
else:
    AD = 0

WAD = cumsum(AD)

信号:
- WAD 上升趋势 → buy (累积)
- WAD 下降趋势 → sell (派发)
- 与价格背离 → 反转信号

输入: {code: list[(high, low, close)]}
输出: signal 列表

2026-08-03 Ship 97 — 10000 轮迭代 P5 第四十二步
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
class WADSignal:
    code: str
    side: str
    current: float
    wad: float
    wad_prev: float
    wad_slope: float
    price_slope: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "wad": self.wad,
            "wad_prev": self.wad_prev,
            "wad_slope": self.wad_slope,
            "price_slope": self.price_slope,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_wad_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[float]:
    """WAD 时序"""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return []

    wad = [0.0]
    for i in range(1, n):
        prev_close = closes[i - 1]
        trh = max(prev_close, highs[i])
        trl = min(prev_close, lows[i])

        if closes[i] > prev_close:
            ad = closes[i] - trl
        elif closes[i] < prev_close:
            ad = closes[i] - trh
        else:
            ad = 0
        wad.append(wad[-1] + ad)
    return wad


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
    *,
    slope_window: int = 10,
) -> Optional[WADSignal]:
    """WAD 信号"""
    wad_series = compute_wad_series(highs, lows, closes)
    if len(wad_series) < slope_window:
        return None

    wad = wad_series[-1]
    wad_prev = wad_series[-2]
    wad_slope = compute_slope(wad_series, window=slope_window)
    price_slope = compute_slope(closes, window=slope_window)
    if wad_slope is None or price_slope is None:
        return None
    current = closes[-1]

    if wad_slope > 0:
        side = "buy"
        strength = min(1.0, abs(wad_slope) / max(abs(wad) / 10, 1.0))
    elif wad_slope < 0:
        side = "sell"
        strength = min(1.0, abs(wad_slope) / max(abs(wad) / 10, 1.0))
    else:
        side = "hold"
        strength = 0.3

    return WADSignal(
        code=code, side=side,
        current=round(current, 4),
        wad=round(wad, 4),
        wad_prev=round(wad_prev, 4),
        wad_slope=round(wad_slope, 4),
        price_slope=round(price_slope, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[WADSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes) in universe.items():
        sig = generate_signal(code, highs, lows, closes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[WADSignal]) -> tuple[list[WADSignal], list[WADSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: WADSignal) -> str:
    return (f"{s.code}: {s.side.upper()} wad={s.wad:.0f} "
            f"slope={s.wad_slope:+.2f} strength={s.strength:.2f}")