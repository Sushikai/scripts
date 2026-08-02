#!/usr/bin/env python3
"""
tuixue_v3/strat_era24_vwap.py
Ship 80/100 — 量化 era 2026 高级策略 #24

VWAP Strategy (成交量加权平均价)

设计:
VWAP = Σ(price * volume) / Σ(volume)

信号:
- close > VWAP * (1 + threshold): 强势 → buy
- close < VWAP * (1 - threshold): 弱势 → sell
- 中性区: hold

输入: {code: list[(close, volume)]}
输出: signal 列表

2026-08-03 Ship 80 — 10000 轮迭代 P5 第二十五步
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
    deviation_pct: float      # (current - vwap) / vwap
    upper_band: float
    lower_band: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "vwap": self.vwap,
            "deviation_pct": self.deviation_pct,
            "upper_band": self.upper_band,
            "lower_band": self.lower_band,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_vwap(
    closes: list[float],
    volumes: list[float],
) -> Optional[float]:
    """VWAP"""
    if len(closes) < 2 or len(volumes) < 2:
        return None
    n = min(len(closes), len(volumes))
    total_pv = sum(closes[i] * volumes[i] for i in range(n))
    total_v = sum(volumes[:n])
    if total_v == 0:
        return None
    return total_pv / total_v


def rolling_vwap(
    closes: list[float],
    volumes: list[float],
    window: int = 20,
) -> Optional[float]:
    """rolling VWAP (最近 window 期)"""
    if len(closes) < window:
        return None
    n = min(len(closes), len(volumes))
    sub_c = closes[max(0, n - window):n]
    sub_v = volumes[max(0, n - window):n]
    total_pv = sum(sub_c[i] * sub_v[i] for i in range(len(sub_c)))
    total_v = sum(sub_v)
    if total_v == 0:
        return None
    return total_pv / total_v


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    volumes: list[float],
    *,
    window: int = 20,
    threshold: float = 0.01,
) -> Optional[VWAPSignal]:
    """VWAP 信号"""
    vwap = rolling_vwap(closes, volumes, window=window)
    if vwap is None or vwap <= 0:
        return None

    current = closes[-1]
    deviation_pct = (current - vwap) / vwap
    upper = vwap * (1 + threshold)
    lower = vwap * (1 - threshold)

    if current > upper:
        side = "buy"
        strength = min(1.0, abs(deviation_pct) * 50)
    elif current < lower:
        side = "sell"
        strength = min(1.0, abs(deviation_pct) * 50)
    else:
        side = "hold"
        strength = 1 - abs(deviation_pct) / threshold

    return VWAPSignal(
        code=code, side=side,
        current=round(current, 4),
        vwap=round(vwap, 4),
        deviation_pct=round(deviation_pct, 4),
        upper_band=round(upper, 4),
        lower_band=round(lower, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[VWAPSignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, volumes) in universe.items():
        sig = generate_signal(code, closes, volumes)
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
            f"cur={s.current:.2f} dev={s.deviation_pct:+.2%} strength={s.strength:.2f}")