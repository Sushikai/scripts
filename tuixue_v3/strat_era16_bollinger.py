#!/usr/bin/env python3
"""
tuixue_v3/strat_era16_bollinger.py
Ship 72/100 — 量化 era 2026 高级策略 #16

Bollinger Bands Strategy (布林带策略)

设计:
布林带 (中轨 = N 日均线, 上下轨 = ±kσ):
- close < 下轨 → 超卖, buy (均值回归)
- close > 上轨 → 超买, sell
- 中性区: hold

输入: {code: list[float]} 价格时序
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 72 — 10000 轮迭代 P5 第十七步
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
class BollingerSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    middle: float              # 中轨 (MA)
    upper: float               # 上轨
    lower: float               # 下轨
    bandwidth_pct: float       # 带宽 (upper-lower)/middle
    pct_b: float               # %b = (close-lower)/(upper-lower)
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "middle": self.middle,
            "upper": self.upper,
            "lower": self.lower,
            "bandwidth_pct": self.bandwidth_pct,
            "pct_b": self.pct_b,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_bands(
    prices: list[float],
    window: int = 20,
    num_std: float = 2.0,
) -> Optional[tuple[float, float, float]]:
    """布林带 (middle, upper, lower)"""
    if len(prices) < window:
        return None
    sub = prices[-window:]
    middle = statistics.mean(sub)
    sigma = statistics.stdev(sub)
    upper = middle + num_std * sigma
    lower = middle - num_std * sigma
    return middle, upper, lower


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 20,
    num_std: float = 2.0,
) -> Optional[BollingerSignal]:
    """布林带信号

    - close < lower → buy
    - close > upper → sell
    - 中性 → hold
    """
    res = compute_bands(prices, window=window, num_std=num_std)
    if res is None:
        return None

    middle, upper, lower = res
    current = prices[-1]

    bandwidth_pct = (upper - lower) / middle if middle > 0 else 0.0
    if upper > lower:
        pct_b = (current - lower) / (upper - lower)
    else:
        pct_b = 0.5

    if current < lower:
        side = "buy"
        strength = min(1.0, (lower - current) / lower * 10)
    elif current > upper:
        side = "sell"
        strength = min(1.0, (current - upper) / upper * 10)
    else:
        side = "hold"
        # 距中轨越近越 hold
        dist = abs(current - middle) / middle
        strength = 1 - min(1.0, dist * 5)

    return BollingerSignal(
        code=code, side=side,
        current=round(current, 4),
        middle=round(middle, 4),
        upper=round(upper, 4),
        lower=round(lower, 4),
        bandwidth_pct=round(bandwidth_pct, 4),
        pct_b=round(pct_b, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# Squeeze 检测 (带宽收缩)
# ═══════════════════════════════════════════════════════

def is_squeeze(
    prices: list[float],
    *,
    window: int = 20,
    threshold: float = 0.05,
    lookback: int = 20,
) -> bool:
    """带宽收缩 (squeeze) → 即将突破"""
    if len(prices) < window + lookback:
        return False
    curr = compute_bands(prices[-window - 1:], window=window)
    if curr is None:
        return False
    middle, upper, lower = curr
    bw = (upper - lower) / middle if middle > 0 else 0.0
    return bw < threshold


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 20,
) -> list[BollingerSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[BollingerSignal]) -> tuple[list[BollingerSignal], list[BollingerSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: BollingerSignal) -> str:
    return (f"{s.code}: {s.side.upper()} pct_b={s.pct_b:.2f} "
            f"bw={s.bandwidth_pct:.2%} strength={s.strength:.2f}")