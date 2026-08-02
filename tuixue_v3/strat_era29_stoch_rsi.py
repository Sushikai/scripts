#!/usr/bin/env python3
"""
tuixue_v3/strat_era29_stoch_rsi.py
Ship 85/100 — 量化 era 2026 高级策略 #29

Stochastic RSI Strategy (随机 RSI)

设计:
StochRSI = (RSI - min_RSI) / (max_RSI - min_RSI)
- StochRSI > 0.8: 超买 → sell
- StochRSI < 0.2: 超卖 → buy
- 中性: hold

输入: {code: list[float]}
输出: signal 列表

2026-08-03 Ship 85 — 10000 轮迭代 P5 第三十步
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
class StochRSISignal:
    code: str
    side: str
    current: float
    rsi: float
    stoch_rsi: float           # 0-1
    rsi_min: float
    rsi_max: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "rsi": self.rsi,
            "stoch_rsi": self.stoch_rsi,
            "rsi_min": self.rsi_min,
            "rsi_max": self.rsi_max,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_rsi_series(
    prices: list[float],
    window: int = 14,
) -> list[float]:
    """RSI 时序"""
    if len(prices) < window + 1:
        return []
    rsi_series = []
    for i in range(window, len(prices)):
        sub = prices[i - window:i + 1]
        diffs = [sub[j] - sub[j - 1] for j in range(1, len(sub))]
        gains = sum(max(d, 0) for d in diffs) / window
        losses = sum(max(-d, 0) for d in diffs) / window
        if losses == 0:
            rsi = 100.0
        else:
            rs = gains / losses
            rsi = 100.0 - 100.0 / (1.0 + rs)
        rsi_series.append(rsi)
    return rsi_series


def compute_stoch_rsi(
    prices: list[float],
    *,
    rsi_window: int = 14,
    stoch_window: int = 14,
) -> Optional[tuple[float, float, float, float]]:
    """Stochastic RSI

    Returns: (rsi, stoch_rsi, rsi_min, rsi_max)
    """
    if len(prices) < rsi_window + stoch_window + 1:
        return None

    rsi_series = compute_rsi_series(prices, window=rsi_window)
    if len(rsi_series) < stoch_window:
        return None

    recent = rsi_series[-stoch_window:]
    rsi_min = min(recent)
    rsi_max = max(recent)
    rsi = rsi_series[-1]

    if rsi_max == rsi_min:
        stoch_rsi = 0.5
    else:
        stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min)

    return rsi, stoch_rsi, rsi_min, rsi_max


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    rsi_window: int = 14,
    stoch_window: int = 14,
    overbought: float = 0.8,
    oversold: float = 0.2,
) -> Optional[StochRSISignal]:
    """StochRSI 信号"""
    res = compute_stoch_rsi(
        prices, rsi_window=rsi_window, stoch_window=stoch_window,
    )
    if res is None:
        return None

    rsi, stoch_rsi, rsi_min, rsi_max = res
    current = prices[-1]

    if stoch_rsi > overbought:
        side = "sell"
        strength = min(1.0, (stoch_rsi - overbought) / 0.2)
    elif stoch_rsi < oversold:
        side = "buy"
        strength = min(1.0, (oversold - stoch_rsi) / 0.2)
    else:
        side = "hold"
        strength = 1 - abs(stoch_rsi - 0.5) * 2

    return StochRSISignal(
        code=code, side=side,
        current=round(current, 4),
        rsi=round(rsi, 2),
        stoch_rsi=round(stoch_rsi, 4),
        rsi_min=round(rsi_min, 2),
        rsi_max=round(rsi_max, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[StochRSISignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[StochRSISignal]) -> tuple[list[StochRSISignal], list[StochRSISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: StochRSISignal) -> str:
    return f"{s.code}: {s.side.upper()} rsi={s.rsi:.1f} stoch_rsi={s.stoch_rsi:.2f} strength={s.strength:.2f}"