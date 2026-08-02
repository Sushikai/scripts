#!/usr/bin/env python3
"""
tuixue_v3/strat_era19_obv.py
Ship 75/100 — 量化 era 2026 高级策略 #19

OBV Strategy (On-Balance Volume)

设计:
OBV (能量潮):
- close > prev_close: OBV += volume
- close < prev_close: OBV -= volume
- close == prev_close: OBV 不变

OBV 与价格背离:
- 价格新高 + OBV 不新高 → 顶背离, sell
- 价格新低 + OBV 不新低 → 底背离, buy

输入: {code: list[(close, volume)]}
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 75 — 10000 轮迭代 P5 第二十步
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
class OBVSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    obv: float
    obv_ma: float
    obv_slope: float
    price_slope: float
    divergence: str            # "bullish" / "bearish" / "none"
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "obv": self.obv,
            "obv_ma": self.obv_ma,
            "obv_slope": self.obv_slope,
            "price_slope": self.price_slope,
            "divergence": self.divergence,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_obv(
    closes: list[float],
    volumes: list[float],
) -> list[float]:
    """OBV 时序"""
    if len(closes) < 2:
        return []
    n = min(len(closes), len(volumes))
    obv_series = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv_series.append(obv_series[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_series.append(obv_series[-1] - volumes[i])
        else:
            obv_series.append(obv_series[-1])
    return obv_series


def slope(values: list[float], window: int = 5) -> float:
    """近期斜率"""
    if len(values) < window:
        return 0.0
    sub = values[-window:]
    n = len(sub)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(sub) / n
    num = sum((x - mx) * (sub[i] - my) for i, x in enumerate(xs))
    dx2 = sum((x - mx) ** 2 for x in xs)
    if dx2 == 0:
        return 0.0
    return num / dx2


# ═══════════════════════════════════════════════════════
# 背离检测
# ═══════════════════════════════════════════════════════

def detect_divergence(
    closes: list[float],
    obv_series: list[float],
    *,
    lookback: int = 20,
) -> str:
    """背离检测"""
    if len(closes) < lookback + 1 or len(obv_series) < lookback + 1:
        return "none"

    price_curr = closes[-1]
    price_prev = closes[-(lookback + 1)]
    obv_curr = obv_series[-1]
    obv_prev = obv_series[-(lookback + 1)]

    if price_curr < price_prev and obv_curr > obv_prev:
        return "bullish"   # 底背离
    elif price_curr > price_prev and obv_curr < obv_prev:
        return "bearish"   # 顶背离
    return "none"


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    volumes: list[float],
    *,
    obv_ma_window: int = 20,
    slope_window: int = 5,
    divergence_lookback: int = 20,
) -> Optional[OBVSignal]:
    """OBV 信号"""
    if len(closes) < obv_ma_window + slope_window:
        return None
    if len(volumes) < len(closes):
        return None

    obv_series = compute_obv(closes, volumes)
    if len(obv_series) < obv_ma_window:
        return None

    obv = obv_series[-1]
    obv_ma = sum(obv_series[-obv_ma_window:]) / obv_ma_window
    obv_sl = slope(obv_series, slope_window)
    price_sl = slope(closes, slope_window)
    div = detect_divergence(closes, obv_series, lookback=divergence_lookback)
    current = closes[-1]

    if div == "bullish":
        side = "buy"
        strength = 0.8
    elif div == "bearish":
        side = "sell"
        strength = 0.8
    elif obv > obv_ma and price_sl > 0:
        # OBV 在均线上 + 价格上升 → 趋势确认 buy
        side = "buy"
        strength = 0.5
    elif obv < obv_ma and price_sl < 0:
        side = "sell"
        strength = 0.5
    else:
        side = "hold"
        strength = 0.3

    return OBVSignal(
        code=code, side=side,
        current=round(current, 4),
        obv=round(obv, 2),
        obv_ma=round(obv_ma, 2),
        obv_slope=round(obv_sl, 4),
        price_slope=round(price_sl, 4),
        divergence=div,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[OBVSignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, volumes) in universe.items():
        sig = generate_signal(code, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[OBVSignal]) -> tuple[list[OBVSignal], list[OBVSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: OBVSignal) -> str:
    return (f"{s.code}: {s.side.upper()} div={s.divergence} "
            f"obv_slope={s.obv_slope:+.2f} strength={s.strength:.2f}")