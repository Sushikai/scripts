#!/usr/bin/env python3
"""
tuixue_v3/strat_era28_keltner.py
Ship 84/100 — 量化 era 2026 高级策略 #28

Keltner Channel Strategy (凯尔特纳通道)

设计:
- 中轨: EMA(N) (默认 20)
- 上轨: EMA + k * ATR
- 下轨: EMA - k * ATR

信号:
- close > 上轨 → 强势 buy
- close < 下轨 → 弱势 sell
- close 在通道内 → hold

输入: {code: list[float]} (close-only ATR)
输出: signal 列表

2026-08-03 Ship 84 — 10000 轮迭代 P5 第二十九步
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
class KeltnerSignal:
    code: str
    side: str
    current: float
    ema: float                 # 中轨
    atr: float
    upper: float
    lower: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "ema": self.ema,
            "atr": self.atr,
            "upper": self.upper,
            "lower": self.lower,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def ema_value(prices: list[float], window: int) -> Optional[float]:
    """EMA 当前值"""
    if len(prices) < window:
        return None
    k = 2.0 / (window + 1)
    ema_val = sum(prices[:window]) / window
    for p in prices[window:]:
        ema_val = p * k + ema_val * (1 - k)
    return ema_val


def atr_value(prices: list[float], window: int = 14) -> Optional[float]:
    """ATR (close-only)"""
    if len(prices) < window + 1:
        return None
    diffs = [abs(prices[i] - prices[i - 1]) for i in range(len(prices) - window, len(prices))]
    return statistics.mean(diffs)


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    ema_window: int = 20,
    atr_window: int = 14,
    atr_mult: float = 2.0,
) -> Optional[KeltnerSignal]:
    """Keltner 信号"""
    ema = ema_value(prices, ema_window)
    atr = atr_value(prices, atr_window)
    if ema is None or atr is None:
        return None

    upper = ema + atr_mult * atr
    lower = ema - atr_mult * atr
    current = prices[-1]

    if current > upper:
        side = "buy"
        strength = min(1.0, (current - upper) / upper * 20)
    elif current < lower:
        side = "sell"
        strength = min(1.0, (lower - current) / lower * 20)
    else:
        side = "hold"
        strength = 0.5

    return KeltnerSignal(
        code=code, side=side,
        current=round(current, 4),
        ema=round(ema, 4),
        atr=round(atr, 4),
        upper=round(upper, 4),
        lower=round(lower, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[KeltnerSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[KeltnerSignal]) -> tuple[list[KeltnerSignal], list[KeltnerSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: KeltnerSignal) -> str:
    return (f"{s.code}: {s.side.upper()} ema={s.ema:.2f} "
            f"range={s.lower:.2f}-{s.upper:.2f} cur={s.current:.2f} strength={s.strength:.2f}")