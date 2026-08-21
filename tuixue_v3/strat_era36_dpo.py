#!/usr/bin/env python3
"""
tuixue_v3/strat_era36_dpo.py
Ship 92/100 — 量化 era 2026 高级策略 #36

DPO Strategy (Detrended Price Oscillator)

设计:
DPO = close - SMA(close, n, shift=n/2+1)
去除长期趋势，专注短期周期

信号:
- DPO > 0: 价格在均线之上 → buy
- DPO < 0: 价格在均线之下 → sell
- 上穿 0 → 强 buy
- 下穿 0 → 强 sell

输入: {code: list[float]}
输出: signal 列表

2026-08-03 Ship 92 — 10000 轮迭代 P5 第三十七步
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
class DPOSignal:
    code: str
    side: str
    current: float
    dpo: float
    dpo_prev: float
    sma: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "dpo": self.dpo,
            "dpo_prev": self.dpo_prev,
            "sma": self.sma,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_dpo_series(
    prices: list[float],
    window: int = 20,
) -> list[float]:
    """DPO 时序"""
    if len(prices) < window + window // 2 + 1:
        return []

    sma_full = []
    for i in range(window - 1, len(prices)):
        sma_full.append(sum(prices[i - window + 1:i + 1]) / window)

    # DPO[i] = prices[i - shift] - sma[i]
    shift = window // 2 + 1
    dpo = []
    for i in range(len(sma_full)):
        price_idx = i - shift
        if price_idx < 0 or price_idx >= len(prices):
            continue
        dpo.append(prices[price_idx] - sma_full[i])
    return dpo


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 20,
) -> Optional[DPOSignal]:
    """DPO 信号"""
    dpo_series = compute_dpo_series(prices, window=window)
    if len(dpo_series) < 2:
        return None

    dpo = dpo_series[-1]
    dpo_prev = dpo_series[-2]
    current = prices[-1]

    # 当前 SMA (用作参考)
    sma_now = sum(prices[-window:]) / window if len(prices) >= window else current

    # 交叉检测
    cross_up = dpo_prev <= 0 < dpo
    cross_dn = dpo_prev >= 0 > dpo

    if cross_up:
        side = "buy"
        strength = 0.9
    elif cross_dn:
        side = "sell"
        strength = 0.9
    elif dpo > 0:
        side = "buy"
        norm = abs(dpo) / max(sma_now, 1.0)
        strength = min(1.0, norm * 20)
    elif dpo < 0:
        side = "sell"
        norm = abs(dpo) / max(sma_now, 1.0)
        strength = min(1.0, norm * 20)
    else:
        side = "hold"
        strength = 0.3

    return DPOSignal(
        code=code, side=side,
        current=round(current, 4),
        dpo=round(dpo, 4),
        dpo_prev=round(dpo_prev, 4),
        sma=round(sma_now, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[DPOSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[DPOSignal]) -> tuple[list[DPOSignal], list[DPOSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: DPOSignal) -> str:
    return (f"{s.code}: {s.side.upper()} dpo={s.dpo:+.3f} "
            f"sma={s.sma:.2f} strength={s.strength:.2f}")