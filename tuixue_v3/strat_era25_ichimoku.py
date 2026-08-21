#!/usr/bin/env python3
"""
tuixue_v3/strat_era25_ichimoku.py
Ship 81/100 — 量化 era 2026 高级策略 #25

Ichimoku Cloud Strategy (一目均衡表)

设计:
一目均衡表 5 条线:
- 转换线 (Tenkan): (9 日高 + 9 日低) / 2
- 基准线 (Kijun): (26 日高 + 26 日低) / 2
- 先行带 A (Senkou A): (转换 + 基准) / 2, 前移 26
- 先行带 B (Senkou B): (52 日高 + 52 日低) / 2, 前移 26
- 迟行线 (Chikou): 当前 close, 后移 26

信号:
- close > 云上沿 → 强 buy
- close < 云下沿 → 强 sell
- close 在云中 → 中性, hold
- 转换 > 基准 → 短期偏多 (辅助)
- 转换 < 基准 → 短期偏空 (辅助)

输入: {code: list[float]} (close-only 估计)
输出: signal 列表

2026-08-03 Ship 81 — 10000 轮迭代 P5 第二十六步
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
class IchimokuSignal:
    code: str
    side: str
    current: float
    tenkan: float              # 转换线
    kijun: float               # 基准线
    senkou_a: float            # 先行带 A (当前位)
    senkou_b: float            # 先行带 B (当前位)
    cloud_top: float
    cloud_bottom: float
    above_cloud: bool
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "tenkan": self.tenkan,
            "kijun": self.kijun,
            "senkou_a": self.senkou_a,
            "senkou_b": self.senkou_b,
            "cloud_top": self.cloud_top,
            "cloud_bottom": self.cloud_bottom,
            "above_cloud": self.above_cloud,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def midpoint(prices: list[float], window: int) -> Optional[float]:
    """(window 期最高 + window 期最低) / 2"""
    if len(prices) < window:
        return None
    sub = prices[-window:]
    return (max(sub) + min(sub)) / 2


def compute_ichimoku(
    prices: list[float],
    *,
    tenkan_window: int = 9,
    kijun_window: int = 26,
    senkou_b_window: int = 52,
) -> Optional[tuple[float, float, float, float, float, float]]:
    """Ichimoku 5 条线 (当前位)

    Returns: (tenkan, kijun, senkou_a, senkou_b, cloud_top, cloud_bottom)
    """
    if len(prices) < senkou_b_window:
        return None

    t = midpoint(prices, tenkan_window)
    k = midpoint(prices, kijun_window)
    if t is None or k is None:
        return None

    # Senkou A = (T + K) / 2, 26 期前 → 当前位 (即 (26期前的 midpoint))
    if len(prices) >= kijun_window + senkou_b_window:
        # 26 期前的 (T+K)/2
        past_t = midpoint(prices[:-(kijun_window - 26)] if (kijun_window - 26) > 0 else prices, tenkan_window)
        past_k = midpoint(prices[:-(kijun_window - 26)] if (kijun_window - 26) > 0 else prices, kijun_window)
        sa = ((past_t or t) + (past_k or k)) / 2
    else:
        sa = (t + k) / 2

    sb = midpoint(prices, senkou_b_window)
    if sb is None:
        return None

    cloud_top = max(sa, sb)
    cloud_bottom = min(sa, sb)

    return t, k, sa, sb, cloud_top, cloud_bottom


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    tenkan_window: int = 9,
    kijun_window: int = 26,
    senkou_b_window: int = 52,
) -> Optional[IchimokuSignal]:
    """Ichimoku 信号"""
    res = compute_ichimoku(
        prices,
        tenkan_window=tenkan_window,
        kijun_window=kijun_window,
        senkou_b_window=senkou_b_window,
    )
    if res is None:
        return None

    tenkan, kijun, senkou_a, senkou_b, cloud_top, cloud_bottom = res
    current = prices[-1]
    above = current > cloud_top
    below = current < cloud_bottom
    tenkan_kijun_up = tenkan > kijun

    if above and tenkan_kijun_up:
        side = "buy"
        strength = 0.9
    elif above:
        side = "buy"
        strength = 0.6
    elif below and not tenkan_kijun_up:
        side = "sell"
        strength = 0.9
    elif below:
        side = "sell"
        strength = 0.6
    else:
        side = "hold"
        strength = 0.3

    return IchimokuSignal(
        code=code, side=side,
        current=round(current, 4),
        tenkan=round(tenkan, 4),
        kijun=round(kijun, 4),
        senkou_a=round(senkou_a, 4),
        senkou_b=round(senkou_b, 4),
        cloud_top=round(cloud_top, 4),
        cloud_bottom=round(cloud_bottom, 4),
        above_cloud=above,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[IchimokuSignal]:
    """扫全 universe"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[IchimokuSignal]) -> tuple[list[IchimokuSignal], list[IchimokuSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: IchimokuSignal) -> str:
    return (f"{s.code}: {s.side.upper()} T={s.tenkan:.2f} K={s.kijun:.2f} "
            f"cloud={s.cloud_bottom:.2f}-{s.cloud_top:.2f} above={s.above_cloud} strength={s.strength:.2f}")