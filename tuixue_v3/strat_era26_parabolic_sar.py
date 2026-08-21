#!/usr/bin/env python3
"""
tuixue_v3/strat_era26_parabolic_sar.py
Ship 82/100 — 量化 era 2026 高级策略 #26

Parabolic SAR Strategy (抛物线止损反转)

设计:
抛物线 SAR (Stop And Reverse):
- 上升趋势: SAR_t = SAR_{t-1} + AF * (EP - SAR_{t-1})
- AF (加速因子): 0.02 → 0.20, 每次新高加 0.02
- EP (极值点): 上升趋势中最高价

简化版 (从 close-only 估计):
- close > sar → 上升趋势, buy
- close < sar → 下降趋势, sell
- 反转时切换 AF

输入: {code: list[float]} (close-only)
输出: signal 列表

2026-08-03 Ship 82 — 10000 轮迭代 P5 第二十七步
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
class ParabolicSARSignal:
    code: str
    side: str
    current: float
    sar: float
    trend: str                  # "up" / "down"
    af: float                   # 加速因子
    ep: float                   # 极值点
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "sar": self.sar,
            "trend": self.trend,
            "af": self.af,
            "ep": self.ep,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_sar(
    prices: list[float],
    *,
    step: float = 0.02,
    max_step: float = 0.20,
) -> Optional[tuple[float, str, float, float]]:
    """简化版 Parabolic SAR

    Returns: (sar, trend, af, ep) or None
    """
    if len(prices) < 3:
        return None

    # 初始化: 假设上升趋势, SAR = 前两个最低
    sar = min(prices[0], prices[1])
    ep = max(prices[0], prices[1])
    trend = "up"
    af = step

    for i in range(2, len(prices)):
        # 上升趋势中
        if trend == "up":
            sar = sar + af * (ep - sar)
            # SAR 不能高于前两根最低
            sar = min(sar, prices[i - 1], prices[i - 2])

            if prices[i] < sar:
                # 反转 → 下降趋势
                trend = "down"
                sar = ep
                ep = prices[i]
                af = step
            else:
                if prices[i] > ep:
                    ep = prices[i]
                    af = min(max_step, af + step)
        else:
            sar = sar + af * (ep - sar)
            sar = max(sar, prices[i - 1], prices[i - 2])

            if prices[i] > sar:
                trend = "up"
                sar = ep
                ep = prices[i]
                af = step
            else:
                if prices[i] < ep:
                    ep = prices[i]
                    af = min(max_step, af + step)

    return sar, trend, af, ep


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    step: float = 0.02,
    max_step: float = 0.20,
) -> Optional[ParabolicSARSignal]:
    """Parabolic SAR 信号"""
    res = compute_sar(prices, step=step, max_step=max_step)
    if res is None:
        return None

    sar, trend, af, ep = res
    current = prices[-1]

    if current > sar and trend == "up":
        side = "buy"
        # 价格远高于 SAR → 趋势强
        dist = (current - sar) / current if current > 0 else 0
        strength = min(1.0, dist * 10)
    elif current < sar and trend == "down":
        side = "sell"
        dist = (sar - current) / sar if sar > 0 else 0
        strength = min(1.0, dist * 10)
    else:
        side = "hold"
        strength = 0.3

    return ParabolicSARSignal(
        code=code, side=side,
        current=round(current, 4),
        sar=round(sar, 4),
        trend=trend,
        af=round(af, 4),
        ep=round(ep, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[ParabolicSARSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[ParabolicSARSignal]) -> tuple[list[ParabolicSARSignal], list[ParabolicSARSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: ParabolicSARSignal) -> str:
    return (f"{s.code}: {s.side.upper()} trend={s.trend} "
            f"sar={s.sar:.2f} cur={s.current:.2f} af={s.af:.2f} strength={s.strength:.2f}")