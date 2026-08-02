#!/usr/bin/env python3
"""
tuixue_v3/strat_era30_trix.py
Ship 86/100 — 量化 era 2026 高级策略 #30

TRIX Strategy (三重平滑 EMA 动量)

设计:
TRIX = ((EMA3 - EMA3_prev) / EMA3_prev) * 100
- TRIX > 0: 上升 → buy
- TRIX < 0: 下降 → sell
- TRIX 上穿 0 → 强 buy
- TRIX 下穿 0 → 强 sell

输入: {code: list[float]}
输出: signal 列表

2026-08-03 Ship 86 — 10000 轮迭代 P5 第三十一步
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
class TRIXSignal:
    code: str
    side: str
    current: float
    trix: float                # TRIX 当前值
    trix_prev: float           # TRIX 前值
    signal_line: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "trix": self.trix,
            "trix_prev": self.trix_prev,
            "signal_line": self.signal_line,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def triple_ema_series(
    prices: list[float],
    window: int = 15,
) -> list[float]:
    """三重 EMA 时序"""
    if len(prices) < window:
        return []
    k = 2.0 / (window + 1)
    # 第一重 EMA
    e1 = sum(prices[:window]) / window
    e1_series = [e1]
    for p in prices[window:]:
        e1 = p * k + e1 * (1 - k)
        e1_series.append(e1)

    # 第二重
    if len(e1_series) < window:
        return []
    e2 = sum(e1_series[:window]) / window
    e2_series = [e2]
    for v in e1_series[window:]:
        e2 = v * k + e2 * (1 - k)
        e2_series.append(e2)

    # 第三重
    if len(e2_series) < window:
        return []
    e3 = sum(e2_series[:window]) / window
    e3_series = [e3]
    for v in e2_series[window:]:
        e3 = v * k + e3 * (1 - k)
        e3_series.append(e3)

    return e3_series


def compute_trix_series(
    prices: list[float],
    window: int = 15,
) -> list[float]:
    """TRIX 时序 (rate of change of triple EMA)"""
    e3_series = triple_ema_series(prices, window=window)
    if len(e3_series) < 2:
        return []
    trix = []
    for i in range(1, len(e3_series)):
        if e3_series[i - 1] > 0:
            trix.append((e3_series[i] - e3_series[i - 1]) / e3_series[i - 1] * 100)
        else:
            trix.append(0.0)
    return trix


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 15,
    signal_window: int = 9,
) -> Optional[TRIXSignal]:
    """TRIX 信号"""
    trix_series = compute_trix_series(prices, window=window)
    if len(trix_series) < signal_window + 1:
        return None

    trix = trix_series[-1]
    trix_prev = trix_series[-2]
    signal_line = statistics.mean(trix_series[-signal_window:])
    current = prices[-1]

    # 上穿/下穿 0
    cross_up = trix_prev <= 0 < trix
    cross_dn = trix_prev >= 0 > trix

    if cross_up:
        side = "buy"
        strength = 0.9
    elif cross_dn:
        side = "sell"
        strength = 0.9
    elif trix > signal_line:
        side = "buy"
        strength = 0.5
    elif trix < signal_line:
        side = "sell"
        strength = 0.5
    else:
        side = "hold"
        strength = 0.3

    return TRIXSignal(
        code=code, side=side,
        current=round(current, 4),
        trix=round(trix, 4),
        trix_prev=round(trix_prev, 4),
        signal_line=round(signal_line, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[TRIXSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[TRIXSignal]) -> tuple[list[TRIXSignal], list[TRIXSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: TRIXSignal) -> str:
    return (f"{s.code}: {s.side.upper()} trix={s.trix:+.3f} "
            f"sig={s.signal_line:+.3f} strength={s.strength:.2f}")