#!/usr/bin/env python3
"""
tuixue_v3/strat_era05_mean_reversion.py
Ship 61/100 — 量化 era 2026 高级策略 #5

Mean Reversion Strategy (均值回归策略)

设计:
检测偏离均值过远的股票 → 期待回归:
- 当前价 vs 20 日均线
- 当前价 vs 60 日均线
- z-score > 2 → 高估, 卖
- z-score < -2 → 低估, 买

输入: {code: list[float]} 价格时序
输出: signal 列表 (买/卖/持)

降级: 数据不足 (n < 30) → 不开仓

2026-08-03 Ship 61 — 10000 轮迭代 P5 第六步
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
class MRSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    mean: float
    z_score: float
    deviation_pct: float       # 偏离均值 %
    window: int                # 使用的窗口
    strength: float            # 0-1, 信号强度

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "mean": self.mean,
            "z_score": self.z_score,
            "deviation_pct": self.deviation_pct,
            "window": self.window,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def zscore(prices: list[float], window: int) -> Optional[tuple[float, float, float]]:
    """rolling z-score (近 window 个)

    Returns: (current_price, mean, sigma) or None
    """
    if len(prices) < window + 1:
        return None

    sub = prices[-window:]
    mean = statistics.mean(sub)
    sigma = statistics.stdev(sub) if window > 1 else 0.0
    return sub[-1], mean, sigma


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 20,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
) -> Optional[MRSignal]:
    """生成信号

    - z > entry_z → 高估, sell
    - z < -entry_z → 低估, buy
    - |z| < exit_z → 均值回归完成, hold
    """
    res = zscore(prices, window)
    if res is None:
        return None
    current, mean, sigma = res
    if sigma == 0:
        return None

    z = (current - mean) / sigma
    deviation_pct = (current - mean) / mean if mean > 0 else 0.0

    if z > entry_z:
        side = "sell"
        strength = min(1.0, (z - entry_z) / 2)
    elif z < -entry_z:
        side = "buy"
        strength = min(1.0, (-z - entry_z) / 2)
    elif abs(z) < exit_z:
        side = "hold"
        strength = 1 - abs(z) / exit_z
    else:
        return None   # 在中性区无信号

    return MRSignal(
        code=code, side=side,
        current=round(current, 2),
        mean=round(mean, 2),
        z_score=round(z, 4),
        deviation_pct=round(deviation_pct, 4),
        window=window,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 多窗口综合
# ═══════════════════════════════════════════════════════

def generate_multi_window(
    code: str,
    prices: list[float],
    *,
    windows: tuple[int, ...] = (20, 60, 120),
    entry_z: float = 2.0,
) -> Optional[MRSignal]:
    """综合多个窗口, 一致信号强, 分歧弱

    返回合并后的最强信号
    """
    signals = []
    for w in windows:
        sig = generate_signal(code, prices, window=w, entry_z=entry_z)
        if sig is not None:
            signals.append(sig)

    if not signals:
        return None

    # 一致性
    sides = [s.side for s in signals]
    main_side = max(set(sides), key=sides.count)
    consistent = sum(1 for s in sides if s == main_side)
    if consistent < len(signals) // 2 + 1:
        return None   # 分歧, 不开仓

    # 综合 z = 加权平均 (短窗口权重高)
    weights = [1.0 / (i + 1) for i in range(len(signals))]
    total_w = sum(weights)
    z = sum(s.z_score * w for s, w in zip(signals, weights)) / total_w
    avg_window = int(sum(s.window * w for s, w in zip(signals, weights)) / total_w)
    avg_strength = sum(s.strength * w for s, w in zip(signals, weights)) / total_w

    latest = signals[-1]
    return MRSignal(
        code=code,
        side=main_side,
        current=latest.current,
        mean=latest.mean,
        z_score=round(z, 4),
        deviation_pct=latest.deviation_pct,
        window=avg_window,
        strength=round(avg_strength * (consistent / len(signals)), 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 20,
) -> list[MRSignal]:
    """扫全 universe, 找出最强 MR 信号

    Returns: 信号按 strength 排序 (买强 → 卖强)
    """
    signals: list[MRSignal] = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window)
        if sig and sig.side != "hold":
            signals.append(sig)

    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[MRSignal]) -> tuple[list[MRSignal], list[MRSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: MRSignal) -> str:
    return (f"{s.code}: {s.side.upper()} z={s.z_score:+.2f} "
            f"dev={s.deviation_pct:+.1%} strength={s.strength:.2f}")
