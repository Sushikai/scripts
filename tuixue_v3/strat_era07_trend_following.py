#!/usr/bin/env python3
"""
tuixue_v3/strat_era07_trend_following.py
Ship 63/100 — 量化 era 2026 高级策略 #7

Trend Following Strategy (趋势跟踪策略)

设计:
基于均线交叉 + 斜率确认:
- 快线上穿慢线 (golden cross) → buy
- 快线下穿慢线 (death cross) → sell
- 斜率同向 + 顺势 → 加权
- 斜率反向 → 抑制信号

输入: {code: list[float]} 价格时序
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 63 — 10000 轮迭代 P5 第八步
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
class TFSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    fast_ma: float             # 快线
    slow_ma: float             # 慢线
    fast_slope: float          # 快线斜率
    slow_slope: float          # 慢线斜率
    spread_pct: float          # 快慢线差距 (%)
    strength: float            # 0-1

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "fast_ma": self.fast_ma,
            "slow_ma": self.slow_ma,
            "fast_slope": self.fast_slope,
            "slow_slope": self.slow_slope,
            "spread_pct": self.spread_pct,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def simple_ma(prices: list[float], window: int) -> Optional[float]:
    """简单移动平均"""
    if len(prices) < window:
        return None
    return statistics.mean(prices[-window:])


def slope(prices: list[float], window: int = 5) -> Optional[float]:
    """线性回归斜率 (近期 window 个)"""
    if len(prices) < window:
        return None
    sub = prices[-window:]
    n = len(sub)
    xs = list(range(n))
    mx = statistics.mean(xs)
    my = statistics.mean(sub)
    num = sum((x - mx) * (sub[i] - my) for i, x in enumerate(xs))
    dx2 = sum((x - mx) ** 2 for x in xs)
    if dx2 == 0:
        return 0.0
    return num / dx2


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    fast: int = 5,
    slow: int = 20,
    slope_window: int = 5,
    spread_threshold: float = 0.005,
    exit_spread: float = 0.002,
) -> Optional[TFSignal]:
    """趋势跟踪信号

    - fast > slow + spread_threshold*slow AND fast_slope > 0 → buy (golden cross)
    - fast < slow - spread_threshold*slow AND fast_slope < 0 → sell (death cross)
    - |fast-slow|/slow < exit_spread → hold (收敛)
    """
    if len(prices) < slow + slope_window:
        return None

    f = simple_ma(prices, fast)
    s = simple_ma(prices, slow)
    if f is None or s is None or s == 0:
        return None

    fs = slope(prices[-fast:], slope_window)
    ss = slope(prices[-slow:], slope_window)
    if fs is None or ss is None:
        return None

    spread_pct = (f - s) / s
    current = prices[-1]

    if spread_pct > spread_threshold and fs > 0:
        side = "buy"
        strength = min(1.0, spread_pct * 50 + fs * 10)
    elif spread_pct < -spread_threshold and fs < 0:
        side = "sell"
        strength = min(1.0, abs(spread_pct) * 50 + abs(fs) * 10)
    elif abs(spread_pct) < exit_spread:
        side = "hold"
        strength = 1 - abs(spread_pct) / exit_spread
    else:
        return None   # 中性区

    return TFSignal(
        code=code, side=side,
        current=round(current, 4),
        fast_ma=round(f, 4),
        slow_ma=round(s, 4),
        fast_slope=round(fs, 4),
        slow_slope=round(ss, 4),
        spread_pct=round(spread_pct, 4),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 多对均线
# ═══════════════════════════════════════════════════════

def generate_multi_ma(
    code: str,
    prices: list[float],
    *,
    pairs: tuple[tuple[int, int], ...] = ((5, 20), (10, 30), (20, 60)),
) -> Optional[TFSignal]:
    """多对均线交叉信号

    一致 → 强信号; 分歧 → None
    """
    signals = []
    for fast, slow in pairs:
        if len(prices) < slow + 5:
            continue
        sig = generate_signal(code, prices, fast=fast, slow=slow)
        if sig is not None:
            signals.append(sig)

    if not signals:
        return None

    sides = [s.side for s in signals]
    main = max(set(sides), key=sides.count)
    consistent = sum(1 for x in sides if x == main)
    if consistent < len(signals) // 2 + 1:
        return None

    weights = [1.0 / (i + 1) for i in range(len(signals))]
    total_w = sum(weights)
    avg_strength = sum(s.strength * w for s, w in zip(signals, weights)) / total_w

    latest = signals[-1]
    return TFSignal(
        code=code,
        side=main,
        current=latest.current,
        fast_ma=latest.fast_ma,
        slow_ma=latest.slow_ma,
        fast_slope=latest.fast_slope,
        slow_slope=latest.slow_slope,
        spread_pct=latest.spread_pct,
        strength=round(avg_strength * (consistent / len(signals)), 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    fast: int = 5,
    slow: int = 20,
) -> list[TFSignal]:
    """扫全 universe, 找趋势信号"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, fast=fast, slow=slow)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[TFSignal]) -> tuple[list[TFSignal], list[TFSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: TFSignal) -> str:
    return (f"{s.code}: {s.side.upper()} spread={s.spread_pct:+.2%} "
            f"f_slope={s.fast_slope:+.3f} s_slope={s.slow_slope:+.3f} "
            f"strength={s.strength:.2f}")