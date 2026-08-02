#!/usr/bin/env python3
"""
tuixue_v3/strat_era31_ppo.py
Ship 87/100 — 量化 era 2026 高级策略 #31

PPO Strategy (Percentage Price Oscillator)

设计:
PPO = (EMA_fast - EMA_slow) / EMA_slow * 100
Signal = EMA(PPO, signal_window)
Histogram = (PPO - Signal) * 2

信号:
- PPO > Signal → buy (动能强)
- PPO < Signal → sell
- 上穿 0 → 强 buy
- 下穿 0 → 强 sell

输入: {code: list[float]}
输出: signal 列表

2026-08-03 Ship 87 — 10000 轮迭代 P5 第三十二步
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
class PPOSignal:
    code: str
    side: str
    current: float
    ppo: float                 # PPO 当前值
    signal: float              # 信号线
    histogram: float
    ppo_prev: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "ppo": self.ppo,
            "signal": self.signal,
            "histogram": self.histogram,
            "ppo_prev": self.ppo_prev,
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


def ema_series(prices: list[float], window: int) -> list[float]:
    """EMA 时序"""
    if len(prices) < window:
        return []
    k = 2.0 / (window + 1)
    e = sum(prices[:window]) / window
    series = [e]
    for p in prices[window:]:
        e = p * k + e * (1 - k)
        series.append(e)
    return series


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal_window: int = 9,
) -> Optional[PPOSignal]:
    """PPO 信号"""
    if len(prices) < slow + signal_window:
        return None

    ema_fast_series = ema_series(prices, fast)
    ema_slow_series = ema_series(prices, slow)
    if not ema_fast_series or not ema_slow_series:
        return None

    n = min(len(ema_fast_series), len(ema_slow_series))
    ppo_series = [(ema_fast_series[i] - ema_slow_series[i]) / ema_slow_series[i] * 100
                  for i in range(-n, 0)]

    if len(ppo_series) < signal_window + 1:
        return None

    ppo = ppo_series[-1]
    ppo_prev = ppo_series[-2]
    signal_line = ema_value(ppo_series, signal_window)
    if signal_line is None:
        return None

    histogram = (ppo - signal_line) * 2
    current = prices[-1]

    # 交叉检测
    cross_up = ppo_prev <= signal_line < ppo
    cross_dn = ppo_prev >= signal_line > ppo

    if cross_up and ppo > 0:
        side = "buy"
        strength = 0.9
    elif cross_dn and ppo < 0:
        side = "sell"
        strength = 0.9
    elif histogram > 0:
        side = "buy"
        strength = 0.5
    elif histogram < 0:
        side = "sell"
        strength = 0.5
    else:
        side = "hold"
        strength = 0.3

    return PPOSignal(
        code=code, side=side,
        current=round(current, 4),
        ppo=round(ppo, 4),
        signal=round(signal_line, 4),
        histogram=round(histogram, 4),
        ppo_prev=round(ppo_prev, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[PPOSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[PPOSignal]) -> tuple[list[PPOSignal], list[PPOSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: PPOSignal) -> str:
    return (f"{s.code}: {s.side.upper()} ppo={s.ppo:+.2f} "
            f"signal={s.signal:+.2f} hist={s.histogram:+.2f} strength={s.strength:.2f}")