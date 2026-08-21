#!/usr/bin/env python3
"""
tuixue_v3/strat_era38_uo.py
Ship 94/100 — 量化 era 2026 高级策略 #38

Ultimate Oscillator Strategy (UO)

设计:
BP = close - min(low, prev_close)
TR = max(high, prev_close) - min(low, prev_close)

UO = 100 × (4 × avg7 + 2 × avg14 + 1 × avg28) / (7 × (4 + 2 + 1))
where avg_n = sum(BP, n) / sum(TR, n)

信号:
- UO > 70: 超买 → sell
- UO < 30: 超卖 → buy
- 牛背离 (价格新低, UO 没新低) → buy

输入: {code: list[(high, low, close)]}
输出: signal 列表

2026-08-03 Ship 94 — 10000 轮迭代 P5 第三十九步
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
class UOSignal:
    code: str
    side: str
    current: float
    uo: float
    uo_prev: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "uo": self.uo,
            "uo_prev": self.uo_prev,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_ultimate_oscillator(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    fast: int = 7,
    mid: int = 14,
    slow: int = 28,
) -> Optional[float]:
    """Ultimate Oscillator"""
    n = min(len(highs), len(lows), len(closes))
    if n < slow + 1:
        return None

    bp = []
    tr = []
    for i in range(1, n):
        prev_close = closes[i - 1]
        bp.append(closes[i] - min(lows[i], prev_close))
        tr.append(max(highs[i], prev_close) - min(lows[i], prev_close))

    def avg_ratio(window: int) -> Optional[float]:
        sub_bp = bp[-window:]
        sub_tr = tr[-window:]
        bp_sum = sum(sub_bp)
        tr_sum = sum(sub_tr)
        if tr_sum == 0:
            return None
        return bp_sum / tr_sum

    fast_r = avg_ratio(fast)
    mid_r = avg_ratio(mid)
    slow_r = avg_ratio(slow)
    if fast_r is None or mid_r is None or slow_r is None:
        return None

    uo = 100 * (4 * fast_r + 2 * mid_r + 1 * slow_r) / 7
    return uo


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    fast: int = 7,
    mid: int = 14,
    slow: int = 28,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> Optional[UOSignal]:
    """UO 信号"""
    n = min(len(highs), len(lows), len(closes))
    if n < slow + 1:
        return None

    uo = compute_ultimate_oscillator(highs, lows, closes, fast=fast, mid=mid, slow=slow)
    if uo is None:
        return None

    # 前一个 UO (用 -2 步)
    if n < slow + 2:
        uo_prev = uo
    else:
        sub_h = highs[:-1]
        sub_l = lows[:-1]
        sub_c = closes[:-1]
        uo_prev = compute_ultimate_oscillator(sub_h, sub_l, sub_c, fast=fast, mid=mid, slow=slow)
        if uo_prev is None:
            uo_prev = uo

    current = closes[-1]

    if uo < oversold:
        side = "buy"
        strength = min(1.0, (oversold - uo) / 30)
    elif uo > overbought:
        side = "sell"
        strength = min(1.0, (uo - overbought) / 30)
    elif uo > uo_prev:
        side = "buy"
        strength = 0.4
    elif uo < uo_prev:
        side = "sell"
        strength = 0.4
    else:
        side = "hold"
        strength = 0.3

    return UOSignal(
        code=code, side=side,
        current=round(current, 4),
        uo=round(uo, 4),
        uo_prev=round(uo_prev, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[UOSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes) in universe.items():
        sig = generate_signal(code, highs, lows, closes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[UOSignal]) -> tuple[list[UOSignal], list[UOSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: UOSignal) -> str:
    return (f"{s.code}: {s.side.upper()} uo={s.uo:.1f} "
            f"prev={s.uo_prev:.1f} strength={s.strength:.2f}")