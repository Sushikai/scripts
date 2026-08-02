#!/usr/bin/env python3
"""
tuixue_v3/strat_era33_vwap.py
Ship 89/100 — 量化 era 2026 高级策略 #33

VWAP Strategy (Volume Weighted Average Price)

设计:
VWAP = Σ(typical_price × volume) / Σ(volume)
typical_price = (high + low + close) / 3

信号:
- close > VWAP: 价格高于均价 → buy (强势)
- close < VWAP: 价格低于均价 → sell (弱势)
- 偏离 VWAP 越大 strength 越高

输入: {code: list[(high, low, close, volume)]}
输出: signal 列表

2026-08-03 Ship 89 — 10000 轮迭代 P5 第三十四步
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
class VWAPSignal:
    code: str
    side: str
    current: float
    vwap: float
    deviation_pct: float
    typical_price: float
    total_volume: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "vwap": self.vwap,
            "deviation_pct": self.deviation_pct,
            "typical_price": self.typical_price,
            "total_volume": self.total_volume,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    window: int = 20,
) -> Optional[float]:
    """VWAP"""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n < window:
        return None

    tp_vol_total = 0.0
    v_total = 0.0
    for i in range(n - window, n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tp_vol_total += tp * volumes[i]
        v_total += volumes[i]

    if v_total == 0:
        return None
    return tp_vol_total / v_total


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
    window: int = 20,
    buy_threshold_pct: float = 0.5,
    sell_threshold_pct: float = -0.5,
) -> Optional[VWAPSignal]:
    """VWAP 信号"""
    vwap = compute_vwap(highs, lows, closes, volumes, window=window)
    if vwap is None:
        return None

    current = closes[-1]
    deviation_pct = (current - vwap) / vwap * 100

    # 估算 typical_price 和 total_volume
    n = min(len(highs), len(lows), len(closes), len(volumes))
    tp = 0.0
    total_vol = 0.0
    for i in range(max(0, n - window), n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        total_vol += volumes[i]
    typical_price = (highs[-1] + lows[-1] + closes[-1]) / 3

    if deviation_pct > buy_threshold_pct:
        side = "buy"
        strength = min(1.0, deviation_pct / 5.0)
    elif deviation_pct < sell_threshold_pct:
        side = "sell"
        strength = min(1.0, -deviation_pct / 5.0)
    else:
        side = "hold"
        strength = 1 - abs(deviation_pct) / max(abs(buy_threshold_pct), abs(sell_threshold_pct))

    return VWAPSignal(
        code=code, side=side,
        current=round(current, 4),
        vwap=round(vwap, 4),
        deviation_pct=round(deviation_pct, 4),
        typical_price=round(typical_price, 4),
        total_volume=round(total_vol, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[VWAPSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes, volumes) in universe.items():
        sig = generate_signal(code, highs, lows, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[VWAPSignal]) -> tuple[list[VWAPSignal], list[VWAPSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: VWAPSignal) -> str:
    return (f"{s.code}: {s.side.upper()} vwap={s.vwap:.2f} "
            f"dev={s.deviation_pct:+.2f}% strength={s.strength:.2f}")