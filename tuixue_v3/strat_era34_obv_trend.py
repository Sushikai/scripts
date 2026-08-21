#!/usr/bin/env python3
"""
tuixue_v3/strat_era34_obv_trend.py
Ship 90/100 — 量化 era 2026 高级策略 #34

OBV Trend Strategy (OBV 趋势信号)

设计:
OBV[i] = OBV[i-1] + volume[i] if close[i] > close[i-1]
       = OBV[i-1] - volume[i] if close[i] < close[i-1]
       = OBV[i-1] otherwise

OBV_trend = linear_regression_slope(OBV, window)

信号:
- slope > 0: OBV 上升 → buy
- slope < 0: OBV 下降 → sell
- |slope| 越大 strength 越高

输入: {code: list[(close, volume)]}
输出: signal 列表

2026-08-03 Ship 90 — 10000 轮迭代 P5 第三十五步
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
class OBVTrendSignal:
    code: str
    side: str
    current: float
    obv: float
    obv_prev: float
    slope: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "obv": self.obv,
            "obv_prev": self.obv_prev,
            "slope": self.slope,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_obv_series(closes: list[float], volumes: list[float]) -> list[float]:
    """OBV 时序"""
    n = min(len(closes), len(volumes))
    if n < 2:
        return []
    obv = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


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
    closes: list[float],
    volumes: list[float],
    *,
    slope_window: int = 10,
    threshold: float = 0.0,
) -> Optional[OBVTrendSignal]:
    """OBV 趋势信号"""
    obv_series = compute_obv_series(closes, volumes)
    if len(obv_series) < slope_window:
        return None

    obv = obv_series[-1]
    obv_prev = obv_series[-2]
    slope = compute_slope(obv_series, window=slope_window)
    if slope is None:
        return None
    current = closes[-1]

    if slope > threshold:
        side = "buy"
        # 用 abs(slope) / mean_vol 做归一
        avg_vol = statistics.mean(volumes[-slope_window:]) if volumes else 1
        norm = abs(slope) / max(avg_vol, 1.0)
        strength = min(1.0, norm)
    elif slope < -threshold:
        side = "sell"
        avg_vol = statistics.mean(volumes[-slope_window:]) if volumes else 1
        norm = abs(slope) / max(avg_vol, 1.0)
        strength = min(1.0, norm)
    else:
        side = "hold"
        strength = 0.3

    return OBVTrendSignal(
        code=code, side=side,
        current=round(current, 4),
        obv=round(obv, 2),
        obv_prev=round(obv_prev, 2),
        slope=round(slope, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[OBVTrendSignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, volumes) in universe.items():
        sig = generate_signal(code, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[OBVTrendSignal]) -> tuple[list[OBVTrendSignal], list[OBVTrendSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: OBVTrendSignal) -> str:
    return (f"{s.code}: {s.side.upper()} obv={s.obv:.0f} "
            f"slope={s.slope:+.2f} strength={s.strength:.2f}")