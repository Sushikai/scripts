#!/usr/bin/env python3
"""
tuixue_v3/strat_era40_eom.py
Ship 96/100 — 量化 era 2026 高级策略 #40

EOM Strategy (Ease of Movement)

设计:
DM = (high + low) / 2 - (high_prev + low_prev) / 2
BR = volume / (high - low) (单位: million)
EOM = DM / BR
EOM_ma = SMA(EOM, n)

信号:
- EOM > 0: 价涨量增 → buy
- EOM < 0: 价跌量增 → sell
- EOM 上穿 0 → 强 buy

输入: {code: list[(high, low, volume)]}
输出: signal 列表

2026-08-03 Ship 96 — 10000 轮迭代 P5 第四十一步
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
class EOMSignal:
    code: str
    side: str
    current: float
    eom: float
    eom_ma: float
    eom_prev: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "eom": self.eom,
            "eom_ma": self.eom_ma,
            "eom_prev": self.eom_prev,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_eom_series(
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> list[float]:
    """EOM 时序"""
    n = min(len(highs), len(lows), len(volumes))
    if n < 2:
        return []

    eom = [0.0]  # 第一个数据无法算 DM
    for i in range(1, n):
        dm = (highs[i] + lows[i]) / 2 - (highs[i - 1] + lows[i - 1]) / 2
        hl_range = highs[i] - lows[i]
        if hl_range == 0 or volumes[i] == 0:
            eom.append(0.0)
        else:
            br = volumes[i] / (hl_range * 1000)  # 缩放
            eom.append(dm / br)
    return eom


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    *,
    ma_window: int = 14,
) -> Optional[EOMSignal]:
    """EOM 信号"""
    eom_series = compute_eom_series(highs, lows, volumes)
    if len(eom_series) < ma_window + 1:
        return None

    eom = eom_series[-1]
    eom_prev = eom_series[-2]
    eom_ma = sum(eom_series[-ma_window:]) / ma_window
    current = (highs[-1] + lows[-1]) / 2

    # 上穿 0
    cross_up = eom_prev <= 0 < eom
    cross_dn = eom_prev >= 0 > eom

    if cross_up:
        side = "buy"
        strength = 0.9
    elif cross_dn:
        side = "sell"
        strength = 0.9
    elif eom > eom_ma:
        side = "buy"
        norm = abs(eom) / max(abs(eom_ma), 1e-6)
        strength = min(1.0, 0.4 + 0.4 * min(1.0, norm))
    elif eom < eom_ma:
        side = "sell"
        norm = abs(eom) / max(abs(eom_ma), 1e-6)
        strength = min(1.0, 0.4 + 0.4 * min(1.0, norm))
    else:
        side = "hold"
        strength = 0.3

    return EOMSignal(
        code=code, side=side,
        current=round(current, 4),
        eom=round(eom, 6),
        eom_ma=round(eom_ma, 6),
        eom_prev=round(eom_prev, 6),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[EOMSignal]:
    """扫全 universe"""
    signals = []
    for code, (highs, lows, volumes) in universe.items():
        sig = generate_signal(code, highs, lows, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[EOMSignal]) -> tuple[list[EOMSignal], list[EOMSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: EOMSignal) -> str:
    return (f"{s.code}: {s.side.upper()} eom={s.eom:+.4f} "
            f"ma={s.eom_ma:+.4f} strength={s.strength:.2f}")