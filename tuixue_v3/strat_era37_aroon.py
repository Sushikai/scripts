#!/usr/bin/env python3
"""
tuixue_v3/strat_era37_aroon.py
Ship 93/100 — 量化 era 2026 高级策略 #37

Aroon Oscillator Strategy

设计:
Aroon Up = ((n - periods_since_highest_high) / n) * 100
Aroon Down = ((n - periods_since_lowest_low) / n) * 100
Aroon Oscillator = Aroon Up - Aroon Down

信号:
- Aroon Up > 70: 新高密集 → 强势 → buy
- Aroon Down > 70: 新低密集 → 弱势 → sell
- Oscillator > 0: 上升趋势
- Oscillator < 0: 下降趋势

输入: {code: list[(high, low)]}
输出: signal 列表

2026-08-03 Ship 93 — 10000 轮迭代 P5 第三十八步
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
    oscillator: float
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
    highs: list[float],
    lows: list[float],
    window: int = 25,
) -> Optional[tuple[float, float]]:
    """Aroon Up / Aroon Down"""
    if len(highs) < window or len(lows) < window:
        return None

    sub_h = highs[-window:]
    sub_l = lows[-window:]

    highest_idx = sub_h.index(max(sub_h))
    lowest_idx = sub_l.index(min(sub_l))

    periods_since_high = window - 1 - highest_idx
    periods_since_low = window - 1 - lowest_idx

    aroon_up = ((window - periods_since_high) / window) * 100
    aroon_down = ((window - periods_since_low) / window) * 100
    return aroon_up, aroon_down


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    highs: list[float],
    lows: list[float],
    *,
    window: int = 25,
) -> Optional[AroonSignal]:
    """Aroon 信号"""
    res = compute_aroon(highs, lows, window=window)
    if res is None:
        return None

    aroon_up, aroon_down = res
    oscillator = aroon_up - aroon_down
    current = (highs[-1] + lows[-1]) / 2

    if aroon_up > 70 and oscillator > 0:
        side = "buy"
        strength = min(1.0, aroon_up / 100)
    elif aroon_down > 70 and oscillator < 0:
        side = "sell"
        strength = min(1.0, aroon_down / 100)
    elif oscillator > 30:
        side = "buy"
        strength = 0.5
    elif oscillator < -30:
        side = "sell"
        strength = 0.5
    else:
        side = "hold"
        strength = 0.3

    return AroonSignal(
        code=code, side=side,
        current=round(current, 4),
        aroon_up=round(aroon_up, 4),
        aroon_down=round(aroon_down, 4),
        oscillator=round(oscillator, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[AroonSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows) in universe.items():
        sig = generate_signal(code, highs, lows)
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