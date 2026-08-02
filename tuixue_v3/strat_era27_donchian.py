#!/usr/bin/env python3
"""
tuixue_v3/strat_era27_donchian.py
Ship 83/100 — 量化 era 2026 高级策略 #27

Donchian Channel Strategy (唐奇安通道)

设计:
- 上轨: N 日最高 (默认 20)
- 下轨: N 日最低 (默认 20)
- 中轨: (上 + 下) / 2

信号:
- close > 上轨 (突破新高) → buy
- close < 下轨 (突破新低) → sell
- 中性: hold

输入: {code: list[float]}
输出: signal 列表

2026-08-03 Ship 83 — 10000 轮迭代 P5 第二十八步
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
class DonchianSignal:
    code: str
    side: str
    current: float
    upper: float               # N 日最高
    lower: float               # N 日最低
    middle: float              # 中轨
    position_pct: float        # 在通道中的位置 (0-1)
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "upper": self.upper,
            "lower": self.lower,
            "middle": self.middle,
            "position_pct": self.position_pct,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_donchian(
    prices: list[float],
    window: int = 20,
) -> Optional[tuple[float, float, float]]:
    """Donchian (upper, lower, middle) (排除当前)"""
    if len(prices) < window + 1:
        return None

    # 排除当前价格, 取前 window 个
    sub = prices[-(window + 1):-1]
    upper = max(sub)
    lower = min(sub)
    middle = (upper + lower) / 2
    return upper, lower, middle


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 20,
) -> Optional[DonchianSignal]:
    """Donchian 信号"""
    res = compute_donchian(prices, window=window)
    if res is None:
        return None

    upper, lower, middle = res
    current = prices[-1]

    if upper > lower:
        position_pct = (current - lower) / (upper - lower)
    else:
        position_pct = 0.5

    if current > upper:
        side = "buy"
        strength = min(1.0, (current - upper) / upper * 10)
    elif current < lower:
        side = "sell"
        strength = min(1.0, (lower - current) / lower * 10)
    else:
        side = "hold"
        # 距中轨越远越 hold (中性)
        dist = abs(position_pct - 0.5)
        strength = 1 - min(1.0, dist * 2)

    return DonchianSignal(
        code=code, side=side,
        current=round(current, 4),
        upper=round(upper, 4),
        lower=round(lower, 4),
        middle=round(middle, 4),
        position_pct=round(position_pct, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[DonchianSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[DonchianSignal]) -> tuple[list[DonchianSignal], list[DonchianSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: DonchianSignal) -> str:
    return (f"{s.code}: {s.side.upper()} range={s.lower:.2f}-{s.upper:.2f} "
            f"cur={s.current:.2f} pos={s.position_pct:.2f} strength={s.strength:.2f}")