#!/usr/bin/env python3
"""
tuixue_v3/strat_era18_kdj.py
Ship 74/100 — 量化 era 2026 高级策略 #18

KDJ Strategy (Stochastic Oscillator)

设计:
KDJ 指标:
- RSV = (close - low_n) / (high_n - low_n) * 100
- K = 2/3 * prev_K + 1/3 * RSV  (类似 EMA)
- D = 2/3 * prev_D + 1/3 * K
- J = 3K - 2D

信号:
- K 上穿 D → buy
- K 下穿 D → sell
- J > 100 超买, J < 0 超卖

输入: {code: list[(high, low, close)]} 或 {code: list[float]} (close-only)
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 74 — 10000 轮迭代 P5 第十九步
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
class KDJSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    k: float
    d: float
    j: float
    rsv: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "k": self.k,
            "d": self.d,
            "j": self.j,
            "rsv": self.rsv,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_rsv_series(
    closes: list[float],
    highs: Optional[list[float]] = None,
    lows: Optional[list[float]] = None,
    window: int = 9,
) -> list[float]:
    """RSV 时序

    若 highs/lows 缺失, 用 close 估计
    """
    out = []
    for i in range(window - 1, len(closes)):
        if highs is not None and lows is not None:
            sub_h = highs[i - window + 1:i + 1]
            sub_l = lows[i - window + 1:i + 1]
        else:
            sub_h = sub_l = closes[i - window + 1:i + 1]
        h = max(sub_h)
        l = min(sub_l)
        c = closes[i]
        if h == l:
            rsv = 50.0
        else:
            rsv = (c - l) / (h - l) * 100.0
        out.append(rsv)
    return out


def compute_kd(
    rsv_series: list[float],
    *,
    k_init: float = 50.0,
    d_init: float = 50.0,
) -> tuple[list[float], list[float], list[float]]:
    """计算 K, D, J 时序"""
    k = k_init
    d = d_init
    k_series = []
    d_series = []
    for rsv in rsv_series:
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
        k_series.append(k)
        d_series.append(d)
    j_series = [3 * k - 2 * d for k, d in zip(k_series, d_series)]
    return k_series, d_series, j_series


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 9,
    overbought: float = 80.0,
    oversold: float = 20.0,
) -> Optional[KDJSignal]:
    """KDJ 信号

    - K 上穿 D → buy (低位更可靠)
    - K 下穿 D → sell (高位更可靠)
    - J > 100 → 超买 → sell
    - J < 0 → 超卖 → buy
    """
    if len(prices) < window:
        return None

    rsv_series = compute_rsv_series(prices, window=window)
    if len(rsv_series) < 2:
        return None

    k_series, d_series, j_series = compute_kd(rsv_series)

    k_curr = k_series[-1]
    d_curr = d_series[-1]
    j_curr = j_series[-1]
    k_prev = k_series[-2]
    d_prev = d_series[-2]
    rsv_curr = rsv_series[-1]

    current = prices[-1]

    cross_up = k_prev <= d_prev and k_curr > d_curr
    cross_dn = k_prev >= d_prev and k_curr < d_curr

    if j_curr > 100:
        side = "sell"
        strength = min(1.0, (j_curr - 100) / 30)
    elif j_curr < 0:
        side = "buy"
        strength = min(1.0, (0 - j_curr) / 30)
    elif cross_up and k_curr < oversold:
        side = "buy"
        strength = 0.7
    elif cross_dn and k_curr > overbought:
        side = "sell"
        strength = 0.7
    elif cross_up:
        side = "buy"
        strength = 0.4
    elif cross_dn:
        side = "sell"
        strength = 0.4
    else:
        side = "hold"
        strength = 0.5

    return KDJSignal(
        code=code, side=side,
        current=round(current, 4),
        k=round(k_curr, 2),
        d=round(d_curr, 2),
        j=round(j_curr, 2),
        rsv=round(rsv_curr, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 9,
) -> list[KDJSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[KDJSignal]) -> tuple[list[KDJSignal], list[KDJSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: KDJSignal) -> str:
    return f"{s.code}: {s.side.upper()} K={s.k:.1f} D={s.d:.1f} J={s.j:.1f} strength={s.strength:.2f}"