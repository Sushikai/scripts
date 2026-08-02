#!/usr/bin/env python3
"""
tuixue_v3/strat_era43_pvi.py
Ship 99/100 — 量化 era 2026 高级策略 #43

PVI Strategy (Positive Volume Index)

设计:
if volume > volume_prev:
    PVI[i] = PVI[i-1] * (1 + (close[i] - close[i-1]) / close[i-1])
else:
    PVI[i] = PVI[i-1]

PVI 默认基 100

PVI 与 NVI 互补: PVI 跟踪放量日散户行为, NVI 跟踪缩量日主力

信号:
- PVI > PVI_MA: 放量上涨 → buy
- PVI < PVI_MA: 放量下跌 → sell

输入: {code: list[(close, volume)]}
输出: signal 列表

2026-08-03 Ship 99 — 10000 轮迭代 P5 第四十四步
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
class PVISignal:
    code: str
    side: str
    current: float
    pvi: float
    pvi_ma: float
    pvi_prev: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "pvi": self.pvi,
            "pvi_ma": self.pvi_ma,
            "pvi_prev": self.pvi_prev,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_pvi_series(
    closes: list[float],
    volumes: list[float],
    base: float = 100.0,
) -> list[float]:
    """PVI 时序"""
    n = min(len(closes), len(volumes))
    if n < 2:
        return []

    pvi = [base]
    for i in range(1, n):
        if volumes[i] > volumes[i - 1]:
            pct = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0
            pvi.append(pvi[-1] * (1 + pct))
        else:
            pvi.append(pvi[-1])
    return pvi


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    volumes: list[float],
    *,
    ma_window: int = 50,
) -> Optional[PVISignal]:
    """PVI 信号"""
    pvi_series = compute_pvi_series(closes, volumes)
    if len(pvi_series) < ma_window:
        return None

    pvi = pvi_series[-1]
    pvi_prev = pvi_series[-2]
    pvi_ma = sum(pvi_series[-ma_window:]) / ma_window
    current = closes[-1]

    if pvi > pvi_ma:
        side = "buy"
        norm = (pvi - pvi_ma) / max(pvi_ma, 1.0)
        strength = min(1.0, norm * 10)
    elif pvi < pvi_ma:
        side = "sell"
        norm = (pvi_ma - pvi) / max(pvi_ma, 1.0)
        strength = min(1.0, norm * 10)
    else:
        side = "hold"
        strength = 0.3

    return PVISignal(
        code=code, side=side,
        current=round(current, 4),
        pvi=round(pvi, 4),
        pvi_ma=round(pvi_ma, 4),
        pvi_prev=round(pvi_prev, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[PVISignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, volumes) in universe.items():
        sig = generate_signal(code, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[PVISignal]) -> tuple[list[PVISignal], list[PVISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: PVISignal) -> str:
    return (f"{s.code}: {s.side.upper()} pvi={s.pvi:.2f} "
            f"ma={s.pvi_ma:.2f} strength={s.strength:.2f}")