#!/usr/bin/env python3
"""
tuixue_v3/strat_era42_nvi.py
Ship 98/100 — 量化 era 2026 高级策略 #42

NVI Strategy (Negative Volume Index)

设计:
if volume < volume_prev:
    NVI[i] = NVI[i-1] * (1 + (close[i] - close[i-1]) / close[i-1])
else:
    NVI[i] = NVI[i-1]

NVI 默认基 100

信号:
- NVI > NVI_MA(255): 资金悄流入 → buy (聪明钱)
- NVI < NVI_MA(255): 资金悄流出 → sell
- 短均线交叉可作辅助

输入: {code: list[(close, volume)]}
输出: signal 列表

2026-08-03 Ship 98 — 10000 轮迭代 P5 第四十三步
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
class NVISignal:
    code: str
    side: str
    current: float
    nvi: float
    nvi_ma: float
    nvi_prev: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "nvi": self.nvi,
            "nvi_ma": self.nvi_ma,
            "nvi_prev": self.nvi_prev,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_nvi_series(
    closes: list[float],
    volumes: list[float],
    base: float = 100.0,
) -> list[float]:
    """NVI 时序"""
    n = min(len(closes), len(volumes))
    if n < 2:
        return []

    nvi = [base]
    for i in range(1, n):
        if volumes[i] < volumes[i - 1]:
            pct = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0
            nvi.append(nvi[-1] * (1 + pct))
        else:
            nvi.append(nvi[-1])
    return nvi


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    volumes: list[float],
    *,
    ma_window: int = 50,
) -> Optional[NVISignal]:
    """NVI 信号"""
    nvi_series = compute_nvi_series(closes, volumes)
    if len(nvi_series) < ma_window:
        return None

    nvi = nvi_series[-1]
    nvi_prev = nvi_series[-2]
    nvi_ma = sum(nvi_series[-ma_window:]) / ma_window
    current = closes[-1]

    if nvi > nvi_ma:
        side = "buy"
        norm = (nvi - nvi_ma) / max(nvi_ma, 1.0)
        strength = min(1.0, norm * 10)
    elif nvi < nvi_ma:
        side = "sell"
        norm = (nvi_ma - nvi) / max(nvi_ma, 1.0)
        strength = min(1.0, norm * 10)
    else:
        side = "hold"
        strength = 0.3

    return NVISignal(
        code=code, side=side,
        current=round(current, 4),
        nvi=round(nvi, 4),
        nvi_ma=round(nvi_ma, 4),
        nvi_prev=round(nvi_prev, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[NVISignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, volumes) in universe.items():
        sig = generate_signal(code, closes, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[NVISignal]) -> tuple[list[NVISignal], list[NVISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: NVISignal) -> str:
    return (f"{s.code}: {s.side.upper()} nvi={s.nvi:.2f} "
            f"ma={s.nvi_ma:.2f} strength={s.strength:.2f}")