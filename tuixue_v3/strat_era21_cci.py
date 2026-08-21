#!/usr/bin/env python3
"""
tuixue_v3/strat_era21_cci.py
Ship 77/100 — 量化 era 2026 高级策略 #21

CCI Strategy (Commodity Channel Index 顺势指标)

设计:
CCI = (TP - SMA(TP, n)) / (0.015 * MAD(TP, n))
- TP = (high + low + close) / 3 (典型价)
- MAD = 平均绝对偏差

信号:
- CCI > 100: 强势 → buy
- CCI < -100: 弱势 → sell
- 中性: hold

输入: {code: list[float]} (close-only 估计 TP=close)
输出: signal 列表

2026-08-03 Ship 77 — 10000 轮迭代 P5 第二十二步
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
class CCISignal:
    code: str
    side: str
    current: float
    cci: float                 # CCI 值
    tp: float                  # 典型价
    sma_tp: float
    mad_tp: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "cci": self.cci,
            "tp": self.tp,
            "sma_tp": self.sma_tp,
            "mad_tp": self.mad_tp,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_cci(
    prices: list[float],
    window: int = 20,
) -> Optional[tuple[float, float, float, float]]:
    """CCI (从 close-only 估计, TP=close)

    Returns: (cci, tp, sma_tp, mad_tp) or None
    """
    if len(prices) < window:
        return None

    sub = prices[-window:]
    sma = statistics.mean(sub)
    mad = statistics.mean([abs(p - sma) for p in sub])
    if mad == 0:
        return None

    tp = prices[-1]
    cci = (tp - sma) / (0.015 * mad)
    return cci, tp, sma, mad


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 20,
    overbought: float = 100.0,
    oversold: float = -100.0,
) -> Optional[CCISignal]:
    """CCI 信号"""
    res = compute_cci(prices, window=window)
    if res is None:
        return None

    cci, tp, sma, mad = res
    current = prices[-1]

    if cci > overbought:
        side = "buy"   # 强势 → buy
        strength = min(1.0, (cci - overbought) / 200)
    elif cci < oversold:
        side = "sell"  # 弱势 → sell
        strength = min(1.0, (oversold - cci) / 200)
    else:
        side = "hold"
        strength = 1 - abs(cci) / 200

    return CCISignal(
        code=code, side=side,
        current=round(current, 4),
        cci=round(cci, 2),
        tp=round(tp, 4),
        sma_tp=round(sma, 4),
        mad_tp=round(mad, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 20,
) -> list[CCISignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[CCISignal]) -> tuple[list[CCISignal], list[CCISignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: CCISignal) -> str:
    return f"{s.code}: {s.side.upper()} cci={s.cci:+.1f} strength={s.strength:.2f}"