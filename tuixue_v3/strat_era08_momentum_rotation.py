#!/usr/bin/env python3
"""
tuixue_v3/strat_era08_momentum_rotation.py
Ship 64/100 — 量化 era 2026 高级策略 #8

Momentum Rotation Strategy (动量轮动策略)

设计:
基于多窗口动量 (1m, 3m, 6m, 12m) 综合排序:
- 截面排序: 选 top N 动量最强
- 截面反转: 选 bottom N (反转策略)
- 综合动量 = 加权 (近期高权重)

输入: {code: list[float]} 价格时序 (>= 12m 数据)
输出: ranking 列表

降级: 数据不足 → 排除

2026-08-03 Ship 64 — 10000 轮迭代 P5 第九步
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
class MomentumScore:
    code: str
    ret_1m: float
    ret_3m: float
    ret_6m: float
    ret_12m: float
    composite: float          # 加权综合
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "ret_1m": self.ret_1m,
            "ret_3m": self.ret_3m,
            "ret_6m": self.ret_6m,
            "ret_12m": self.ret_12m,
            "composite": self.composite,
            "rank": self.rank,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def return_pct(prices: list[float], lookback: int) -> Optional[float]:
    """lookback 期收益率"""
    if len(prices) < lookback + 1:
        return None
    past = prices[-(lookback + 1)]
    curr = prices[-1]
    if past <= 0:
        return None
    return (curr - past) / past


def momentum_score(
    code: str,
    prices: list[float],
    *,
    weights: tuple[float, ...] = (0.4, 0.3, 0.2, 0.1),     # 1m, 3m, 6m, 12m
    lookbacks: tuple[int, ...] = (20, 60, 120, 240),
) -> Optional[MomentumScore]:
    """计算动量分"""
    rets = []
    for lb in lookbacks:
        r = return_pct(prices, lb)
        if r is None:
            return None
        rets.append(r)

    if len(weights) != len(rets):
        return None

    composite = sum(r * w for r, w in zip(rets, weights))

    return MomentumScore(
        code=code,
        ret_1m=round(rets[0], 4),
        ret_3m=round(rets[1], 4),
        ret_6m=round(rets[2], 4),
        ret_12m=round(rets[3], 4),
        composite=round(composite, 4),
    )


# ═══════════════════════════════════════════════════════
# 截面排序
# ═══════════════════════════════════════════════════════

def rank_momentum(
    universe: dict[str, list[float]],
    *,
    top_n: Optional[int] = None,
    bottom_n: Optional[int] = None,
    weights: tuple[float, ...] = (0.4, 0.3, 0.2, 0.1),
) -> list[MomentumScore]:
    """截面排序

    top_n: 选最强 N (做多)
    bottom_n: 选最弱 N (做空 / 反转)
    """
    scores = []
    for code, prices in universe.items():
        ms = momentum_score(code, prices, weights=weights)
        if ms is not None:
            scores.append(ms)

    scores.sort(key=lambda s: s.composite, reverse=True)
    for rank, s in enumerate(scores, start=1):
        s.rank = rank

    out = []
    if top_n:
        out.extend(scores[:top_n])
    if bottom_n:
        out.extend(scores[-bottom_n:][::-1])   # 反转: 弱者在前
    if not top_n and not bottom_n:
        out = scores
    return out


# ═══════════════════════════════════════════════════════
# 双策略组合
# ═══════════════════════════════════════════════════════

@dataclass
class DualRanking:
    long_picks: list[MomentumScore]    # 强者
    short_picks: list[MomentumScore]   # 弱者 (反转)
    n_total: int

    def to_dict(self) -> dict:
        return {
            "long": [s.to_dict() for s in self.long_picks],
            "short": [s.to_dict() for s in self.short_picks],
            "n_total": self.n_total,
        }


def dual_rotation(
    universe: dict[str, list[float]],
    *,
    n_each: int = 5,
) -> DualRanking:
    """强者做多 + 弱者做空 (反转)"""
    ranked = rank_momentum(universe)
    return DualRanking(
        long_picks=ranked[:n_each],
        short_picks=list(reversed(ranked[-n_each:])) if len(ranked) >= n_each else [],
        n_total=len(ranked),
    )


# ═══════════════════════════════════════════════════════
# 风险调整动量
# ═══════════════════════════════════════════════════════

def risk_adj_momentum(
    code: str,
    prices: list[float],
    *,
    lookback: int = 60,
    vol_window: int = 20,
) -> Optional[float]:
    """风险调整动量 = 收益 / 波动率"""
    if len(prices) < lookback + vol_window:
        return None

    rets = []
    for i in range(len(prices) - lookback, len(prices)):
        if prices[i - 1] <= 0:
            continue
        rets.append((prices[i] - prices[i - 1]) / prices[i - 1])
    if not rets:
        return None

    total_ret = sum(rets)
    vol = statistics.stdev(rets[-vol_window:]) if len(rets) >= vol_window else statistics.stdev(rets)
    if vol <= 0:
        return None
    return total_ret / vol


def rank_risk_adj(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[tuple[str, float]]:
    """按风险调整动量排序"""
    scores = []
    for code, prices in universe.items():
        s = risk_adj_momentum(code, prices)
        if s is not None:
            scores.append((code, s))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(scores: list[MomentumScore], top_k: int = 10) -> str:
    out = [f"Top {top_k} momentum:"]
    for s in scores[:top_k]:
        out.append(f"  #{s.rank} {s.code}: composite={s.composite:+.3f} "
                   f"(1m={s.ret_1m:+.2%}, 3m={s.ret_3m:+.2%}, 6m={s.ret_6m:+.2%})")
    return "\n".join(out)