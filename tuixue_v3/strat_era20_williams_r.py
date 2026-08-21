#!/usr/bin/env python3
"""
tuixue_v3/strat_era20_williams_r.py
Ship 76/100 — 量化 era 2026 高级策略 #20

Williams %R Strategy (威廉指标)

设计:
Williams %R = (highest_n - close) / (highest_n - lowest_n) * -100
- %R > -20: 超买 → sell
- %R < -80: 超卖 → buy
- 中性: hold

输入: {code: list[(high, low, close)]} 或 {code: list[float]}
输出: signal 列表

2026-08-03 Ship 76 — 10000 轮迭代 P5 第二十一步
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
class WilliamsSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    williams_r: float          # 0 到 -100
    highest: float
    lowest: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "williams_r": self.williams_r,
            "highest": self.highest,
            "lowest": self.lowest,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_williams_r(
    prices: list[float],
    window: int = 14,
) -> Optional[tuple[float, float, float]]:
    """Williams %R (从 close-only 估计)

    Returns: (%R, highest, lowest) or None
    """
    if len(prices) < window:
        return None

    sub = prices[-window:]
    highest = max(sub)
    lowest = min(sub)
    current = prices[-1]

    if highest == lowest:
        return 0.0, highest, lowest

    wr = (highest - current) / (highest - lowest) * -100
    return wr, highest, lowest


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 14,
    overbought: float = -20.0,
    oversold: float = -80.0,
) -> Optional[WilliamsSignal]:
    """Williams %R 信号"""
    res = compute_williams_r(prices, window=window)
    if res is None:
        return None

    wr, highest, lowest = res
    current = prices[-1]

    if wr > overbought:
        side = "sell"
        strength = min(1.0, (wr - overbought) / 20)
    elif wr < oversold:
        side = "buy"
        strength = min(1.0, (oversold - wr) / 20)
    else:
        side = "hold"
        strength = 1 - abs(wr) / 100

    return WilliamsSignal(
        code=code, side=side,
        current=round(current, 4),
        williams_r=round(wr, 2),
        highest=round(highest, 4),
        lowest=round(lowest, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 14,
) -> list[WilliamsSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[WilliamsSignal]) -> tuple[list[WilliamsSignal], list[WilliamsSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: WilliamsSignal) -> str:
    return f"{s.code}: {s.side.upper()} %R={s.williams_r:+.1f} strength={s.strength:.2f}"