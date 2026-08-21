#!/usr/bin/env python3
"""
tuixue_v3/strat_era04_pairs.py
Ship 60/100 — 量化 era 2026 高级策略 #4

Pairs Trading Strategy (配对交易策略)

设计:
找高度协整的 2 只股票 (pairs), 当价差偏离均值 ±kσ 时开仓:
- z > +kσ: 卖 A, 买 B
- z < -kσ: 卖 B, 买 A
- |z| < 阈值时平仓

输入:
- price_dict: {code: list[float]}
- 设定的 pairs (或自动 discover)

输出: pairs 信号 (entry, exit, side)

降级: 数据不足 → 不开仓

2026-08-03 Ship 60 — 10000 轮迭代 P5 第五步
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
class PairSignal:
    code_a: str
    code_b: str
    side: str                  # "short_a_long_b" / "long_a_short_b" / "close"
    z_score: float
    spread: float
    mean_spread: float
    sigma_spread: float
    reason: str


@dataclass
class PairStats:
    code_a: str
    code_b: str
    beta: float                # 回归系数
    intercept: float
    z_score: float             # 当前 z
    mean_spread: float
    sigma_spread: float
    correlation: float
    n: int

    def to_dict(self) -> dict:
        return {
            "code_a": self.code_a,
            "code_b": self.code_b,
            "beta": self.beta,
            "intercept": self.intercept,
            "z_score": self.z_score,
            "mean_spread": self.mean_spread,
            "sigma_spread": self.sigma_spread,
            "correlation": self.correlation,
            "n": self.n,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _pearson(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    x = x[:n]
    y = y[:n]
    mx = statistics.mean(x)
    my = statistics.mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _ols(x: list[float], y: list[float]) -> tuple[float, float]:
    """OLS 回归 y = a + b*x, 返回 (b, a)"""
    n = min(len(x), len(y))
    if n < 2:
        return 0.0, 0.0
    x = x[:n]
    y = y[:n]
    mx = statistics.mean(x)
    my = statistics.mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx2 = sum((xi - mx) ** 2 for xi in x)
    if dx2 == 0:
        return 0.0, my
    b = num / dx2
    a = my - b * mx
    return b, a


# ═══════════════════════════════════════════════════════
# Pair 计算
# ═══════════════════════════════════════════════════════

def compute_pair_stats(
    code_a: str,
    prices_a: list[float],
    code_b: str,
    prices_b: list[float],
    window: int = 60,
) -> PairStats:
    """计算 pair 当前统计"""
    n = min(len(prices_a), len(prices_b))
    if n < 2:
        return PairStats(
            code_a=code_a, code_b=code_b,
            beta=0.0, intercept=0.0,
            z_score=0.0, mean_spread=0.0,
            sigma_spread=0.0, correlation=0.0,
            n=n,
        )

    pa = prices_a[-min(window, n):]
    pb = prices_b[-min(window, n):]

    # 回归 pb = α + β * pa
    beta, intercept = _ols(pa, pb)

    # 计算 spread = pb - (α + β * pa)
    spreads = [pb[i] - (intercept + beta * pa[i]) for i in range(len(pa))]
    mean_spread = statistics.mean(spreads)
    sigma_spread = statistics.stdev(spreads) if len(spreads) > 1 else 0.0

    current_spread = spreads[-1]
    z = (current_spread - mean_spread) / sigma_spread if sigma_spread > 0 else 0.0

    corr = _pearson(pa, pb)

    return PairStats(
        code_a=code_a, code_b=code_b,
        beta=round(beta, 4), intercept=round(intercept, 4),
        z_score=round(z, 4),
        mean_spread=round(mean_spread, 4),
        sigma_spread=round(sigma_spread, 4),
        correlation=round(corr, 4),
        n=len(pa),
    )


# ═══════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════

def generate_signal(
    pair: PairStats,
    *,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
) -> Optional[PairSignal]:
    """根据 z_score 生成开/平信号

    - z > entry_z → 卖 a 买 b
    - z < -entry_z → 买 a 卖 b
    - |z| < exit_z → 平仓 (close)
    """
    if pair.n < 20 or pair.sigma_spread <= 0:
        return None

    z = pair.z_score
    if z > entry_z:
        return PairSignal(
            code_a=pair.code_a, code_b=pair.code_b,
            side="short_a_long_b",
            z_score=z,
            spread=pair.mean_spread + z * pair.sigma_spread,
            mean_spread=pair.mean_spread,
            sigma_spread=pair.sigma_spread,
            reason=f"z={z:.2f} > entry={entry_z}",
        )
    elif z < -entry_z:
        return PairSignal(
            code_a=pair.code_a, code_b=pair.code_b,
            side="long_a_short_b",
            z_score=z,
            spread=pair.mean_spread + z * pair.sigma_spread,
            mean_spread=pair.mean_spread,
            sigma_spread=pair.sigma_spread,
            reason=f"z={z:.2f} < {-entry_z}",
        )
    elif abs(z) < exit_z:
        return PairSignal(
            code_a=pair.code_a, code_b=pair.code_b,
            side="close",
            z_score=z,
            spread=pair.mean_spread + z * pair.sigma_spread,
            mean_spread=pair.mean_spread,
            sigma_spread=pair.sigma_spread,
            reason=f"z={z:.2f} 在 ±{exit_z} 内, 平仓",
        )
    return None


# ═══════════════════════════════════════════════════════
# 自动发现
# ═══════════════════════════════════════════════════════

def discover_pair(
    universe: dict[str, list[float]],
    *,
    window: int = 60,
    min_correlation: float = 0.7,
) -> list[PairStats]:
    """寻找高度相关的对

    简化: 两两遍历, 返回相关性 > min_correlation 的 pair
    """
    codes = list(universe.keys())
    pairs = []

    for i, ca in enumerate(codes):
        for j, cb in enumerate(codes):
            if j <= i:
                continue
            stats = compute_pair_stats(
                ca, universe[ca],
                cb, universe[cb],
                window=window,
            )
            if abs(stats.correlation) >= min_correlation:
                pairs.append(stats)

    # 按相关性高低
    pairs.sort(key=lambda p: abs(p.correlation), reverse=True)
    return pairs


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(s: PairSignal) -> dict:
    return {
        "code_a": s.code_a, "code_b": s.code_b,
        "side": s.side,
        "z_score": s.z_score,
        "spread": s.spread,
        "reason": s.reason,
    }
