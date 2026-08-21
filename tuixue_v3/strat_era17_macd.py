#!/usr/bin/env python3
"""
tuixue_v3/strat_era17_macd.py
Ship 73/100 — 量化 era 2026 高级策略 #17

MACD Strategy (Moving Average Convergence Divergence)

设计:
- DIF = EMA(short) - EMA(long)
- DEA = EMA(DIF, signal)
- Histogram = (DIF - DEA) * 2

信号:
- DIF 上穿 DEA → buy
- DIF 下穿 DEA → sell
- Histogram 扩张 → 趋势增强

输入: {code: list[float]} 价格时序
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 73 — 10000 轮迭代 P5 第十八步
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
class MACDSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    dif: float                 # 快线 - 慢线
    dea: float                 # 信号线
    hist: float                # 柱状图
    hist_prev: float           # 上一期柱状图
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "dif": self.dif,
            "dea": self.dea,
            "hist": self.hist,
            "hist_prev": self.hist_prev,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def ema(prices: list[float], window: int) -> Optional[float]:
    """指数移动平均"""
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
    ema_vals = [sum(prices[:window]) / window]
    for p in prices[window:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    return ema_vals


# ═══════════════════════════════════════════════════════
# MACD 计算
# ═══════════════════════════════════════════════════════

def compute_macd(
    prices: list[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[tuple[float, float, float]]:
    """MACD (dif, dea, hist)"""
    if len(prices) < slow + signal:
        return None

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None

    dif = ema_fast - ema_slow

    # DEA = EMA(DIF, signal)
    # 需要先计算 dif 时序
    ema_fast_series = ema_series(prices, fast)
    ema_slow_series = ema_series(prices, slow)
    if not ema_fast_series or not ema_slow_series:
        return None

    n = min(len(ema_fast_series), len(ema_slow_series))
    dif_series = [ema_fast_series[i] - ema_slow_series[i]
                  for i in range(-n, 0)]

    dea = ema(dif_series, signal) if len(dif_series) >= signal else None
    if dea is None:
        return None

    hist = (dif - dea) * 2
    return dif, dea, hist


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
) -> Optional[MACDSignal]:
    """MACD 信号"""
    res = compute_macd(prices, fast=fast, slow=slow, signal=signal_window)
    if res is None:
        return None

    dif, dea, hist = res
    current = prices[-1]

    # 上一期 hist
    prev_prices = prices[:-1]
    prev_res = compute_macd(prev_prices, fast=fast, slow=slow, signal=signal_window) if len(prev_prices) >= slow + signal_window else None
    hist_prev = prev_res[2] if prev_res else hist

    if dif > dea and hist > hist_prev:
        # 金叉 + 柱扩张 → 强 buy
        side = "buy"
        strength = min(1.0, abs(hist) * 5 + 0.3)
    elif dif < dea and hist < hist_prev:
        # 死叉 + 柱收缩 → 强 sell
        side = "sell"
        strength = min(1.0, abs(hist) * 5 + 0.3)
    elif dif > dea:
        side = "buy"
        strength = min(1.0, abs(hist) * 3 + 0.1)
    elif dif < dea:
        side = "sell"
        strength = min(1.0, abs(hist) * 3 + 0.1)
    else:
        side = "hold"
        strength = 0.5

    return MACDSignal(
        code=code, side=side,
        current=round(current, 4),
        dif=round(dif, 4),
        dea=round(dea, 4),
        hist=round(hist, 4),
        hist_prev=round(hist_prev, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[MACDSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[MACDSignal]) -> tuple[list[MACDSignal], list[MACDSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: MACDSignal) -> str:
    return (f"{s.code}: {s.side.upper()} dif={s.dif:+.3f} dea={s.dea:+.3f} "
            f"hist={s.hist:+.3f} strength={s.strength:.2f}")