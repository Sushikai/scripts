#!/usr/bin/env python3
"""
tuixue_v3/strat_era15_rsi.py
Ship 71/100 — 量化 era 2026 高级策略 #15

RSI Strategy (Relative Strength Index 策略)

设计:
经典 RSI 指标:
- RSI > overbought (默认 70) → 超买, 卖
- RSI < oversold (默认 30) → 超卖, 买
- 中性区: hold

可配置窗口 (默认 14)。

输入: {code: list[float]} 价格时序
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 71 — 10000 轮迭代 P5 第十六步
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
class RSISignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    rsi: float                 # 0-100
    current: float
    avg_gain: float
    avg_loss: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "rsi": self.rsi,
            "current": self.current,
            "avg_gain": self.avg_gain,
            "avg_loss": self.avg_loss,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_rsi(
    prices: list[float],
    window: int = 14,
) -> Optional[tuple[float, float, float]]:
    """计算 RSI

    Returns: (rsi, avg_gain, avg_loss) or None
    """
    if len(prices) < window + 1:
        return None

    diffs = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]

    recent_g = gains[-window:]
    recent_l = losses[-window:]

    avg_g = statistics.mean(recent_g)
    avg_l = statistics.mean(recent_l)

    if avg_l == 0:
        rsi = 100.0
    else:
        rs = avg_g / avg_l
        rsi = 100.0 - (100.0 / (1.0 + rs))

    return rsi, avg_g, avg_l


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> Optional[RSISignal]:
    """RSI 信号

    - rsi > overbought → sell
    - rsi < oversold → buy
    - 中性区 → hold
    """
    res = compute_rsi(prices, window=window)
    if res is None:
        return None

    rsi, avg_g, avg_l = res
    current = prices[-1]

    if rsi > overbought:
        side = "sell"
        strength = min(1.0, (rsi - overbought) / 30)
    elif rsi < oversold:
        side = "buy"
        strength = min(1.0, (oversold - rsi) / 30)
    else:
        side = "hold"
        # 距中位 (50) 越远越弱
        strength = 1 - abs(rsi - 50) / 50

    return RSISignal(
        code=code, side=side,
        rsi=round(rsi, 2),
        current=round(current, 4),
        avg_gain=round(avg_g, 4),
        avg_loss=round(avg_l, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 多窗口
# ═══════════════════════════════════════════════════════

def generate_multi_window(
    code: str,
    prices: list[float],
    *,
    windows: tuple[int, ...] = (7, 14, 21),
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> Optional[RSISignal]:
    """多窗口 RSI 综合"""
    signals = []
    for w in windows:
        sig = generate_signal(
            code, prices,
            window=w, overbought=overbought, oversold=oversold,
        )
        if sig is not None:
            signals.append(sig)

    if not signals:
        return None

    sides = [s.side for s in signals]
    main = max(set(sides), key=sides.count)
    consistent = sum(1 for x in sides if x == main)
    if consistent < len(signals) // 2 + 1:
        return None

    avg_rsi = statistics.mean(s.rsi for s in signals)
    avg_strength = statistics.mean(s.strength for s in signals)

    latest = signals[-1]
    return RSISignal(
        code=code,
        side=main,
        rsi=round(avg_rsi, 2),
        current=latest.current,
        avg_gain=latest.avg_gain,
        avg_loss=latest.avg_loss,
        strength=round(avg_strength * (consistent / len(signals)), 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> list[RSISignal]:
    """扫全 universe, 找 RSI 信号"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(
            code, prices,
            window=window, overbought=overbought, oversold=oversold,
        )
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[RSISignal]) -> tuple[list[RSISignal], list[RSISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: RSISignal) -> str:
    return f"{s.code}: {s.side.upper()} rsi={s.rsi:.1f} strength={s.strength:.2f}"