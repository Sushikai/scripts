#!/usr/bin/env python3
"""
tuixue_v3/strat_era23_aroon.py
Ship 79/100 — 量化 era 2026 高级策略 #23

Aroon Strategy (阿隆指标)

设计:
Aroon Up = (n - periods_since_highest) / n * 100
Aroon Down = (n - periods_since_lowest) / n * 100
- Aroon Up > 70, Aroon Down < 30 → 强上升 → buy
- Aroon Down > 70, Aroon Up < 30 → 强下降 → sell
- Aroon Up ≈ Aroon Down → 横盘 → hold

输入: {code: list[(high, low)]} 或 {code: list[float]}
输出: signal 列表

2026-08-03 Ship 79 — 10000 轮迭代 P5 第二十四步
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
class AroonSignal:
    code: str
    side: str
    current: float
    aroon_up: float
    aroon_down: float
    oscillator: float          # aroon_up - aroon_down
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "aroon_up": self.aroon_up,
            "aroon_down": self.aroon_down,
            "oscillator": self.oscillator,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_aroon(
    prices: list[float],
    window: int = 25,
) -> Optional[tuple[float, float]]:
    """Aroon Up, Aroon Down

    用 close-only 估计, high=close, low=close (无 high/low)
    """
    if len(prices) < window:
        return None

    sub = prices[-window:]
    highest_idx = sub.index(max(sub))
    lowest_idx = sub.index(min(sub))

    periods_since_high = (window - 1) - highest_idx
    periods_since_low = (window - 1) - lowest_idx

    aroon_up = (window - periods_since_high) / window * 100
    aroon_down = (window - periods_since_low) / window * 100

    return aroon_up, aroon_down


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 25,
    up_threshold: float = 70.0,
    down_threshold: float = 30.0,
) -> Optional[AroonSignal]:
    """Aroon 信号"""
    res = compute_aroon(prices, window=window)
    if res is None:
        return None

    aroon_up, aroon_down = res
    current = prices[-1]
    osc = aroon_up - aroon_down

    if aroon_up > up_threshold and aroon_down < down_threshold:
        side = "buy"
        strength = min(1.0, aroon_up / 100)
    elif aroon_down > up_threshold and aroon_up < down_threshold:
        side = "sell"
        strength = min(1.0, aroon_down / 100)
    else:
        side = "hold"
        strength = 1 - abs(osc) / 200

    return AroonSignal(
        code=code, side=side,
        current=round(current, 4),
        aroon_up=round(aroon_up, 2),
        aroon_down=round(aroon_down, 2),
        oscillator=round(osc, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[AroonSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[AroonSignal]) -> tuple[list[AroonSignal], list[AroonSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: AroonSignal) -> str:
    return (f"{s.code}: {s.side.upper()} up={s.aroon_up:.1f} "
            f"down={s.aroon_down:.1f} osc={s.oscillator:+.1f} strength={s.strength:.2f}")