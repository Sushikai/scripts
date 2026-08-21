#!/usr/bin/env python3
"""
tuixue_v3/strat_era39_mfi.py
Ship 95/100 — 量化 era 2026 高级策略 #39

MFI Strategy (Money Flow Index)

设计:
TP = (high + low + close) / 3
MF = TP × volume
+MF = sum(MF, n) when TP > TP_prev
-MF = sum(MF, n) when TP < TP_prev
MFR = +MF / -MF
MFI = 100 - 100 / (1 + MFR)

信号:
- MFI > 80: 超买 → sell
- MFI < 20: 超卖 → buy
- 50 中轴

输入: {code: list[(high, low, close, volume)]}
输出: signal 列表

2026-08-03 Ship 95 — 10000 轮迭代 P5 第四十步
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
class MFISignal:
    code: str
    side: str
    current: float
    mfi: float
    positive_mf: float
    negative_mf: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "mfi": self.mfi,
            "positive_mf": self.positive_mf,
            "negative_mf": self.negative_mf,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_mfi(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    window: int = 14,
) -> Optional[float]:
    """MFI"""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n < window + 1:
        return None

    tp_series = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]

    pos = 0.0
    neg = 0.0
    for i in range(n - window, n):
        tp = tp_series[i]
        tp_prev = tp_series[i - 1]
        mf = tp * volumes[i]
        if tp > tp_prev:
            pos += mf
        elif tp < tp_prev:
            neg += mf

    if neg == 0:
        return 100.0
    mfr = pos / neg
    return 100 - 100 / (1 + mfr)


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
    window: int = 14,
    overbought: float = 80.0,
    oversold: float = 20.0,
) -> Optional[MFISignal]:
    """MFI 信号"""
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n < window + 1:
        return None

    mfi = compute_mfi(highs, lows, closes, volumes, window=window)
    if mfi is None:
        return None
    current = closes[-1]

    # 估算 +MF / -MF
    tp_series = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
    pos = 0.0
    neg = 0.0
    for i in range(n - window, n):
        tp = tp_series[i]
        tp_prev = tp_series[i - 1]
        mf = tp * volumes[i]
        if tp > tp_prev:
            pos += mf
        elif tp < tp_prev:
            neg += mf

    if mfi > overbought:
        side = "sell"
        strength = min(1.0, (mfi - overbought) / 20)
    elif mfi < oversold:
        side = "buy"
        strength = min(1.0, (oversold - mfi) / 20)
    else:
        side = "hold"
        strength = 1 - abs(mfi - 50) / 30

    return MFISignal(
        code=code, side=side,
        current=round(current, 4),
        mfi=round(mfi, 4),
        positive_mf=round(pos, 2),
        negative_mf=round(neg, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[MFISignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, closes, volumes) in universe.items():
        sig = generate_signal(code, highs, lows, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[MFISignal]) -> tuple[list[MFISignal], list[MFISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: MFISignal) -> str:
    return (f"{s.code}: {s.side.upper()} mfi={s.mfi:.1f} "
            f"+MF={s.positive_mf:.0f} -MF={s.negative_mf:.0f} strength={s.strength:.2f}")