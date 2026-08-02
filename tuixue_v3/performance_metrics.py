#!/usr/bin/env python3
"""
tuixue_v3/performance_metrics.py
Ship 29/100 — 业绩归因 (Factor Attribution + Brinson-like)

设计:
给定历史 trades (含 factor_score), 拆解 PnL 来源:
1. 因子贡献: 各 factor (momentum/sentiment/event 等) 与 PnL 的相关性
2. 选股贡献: 选 vs 不选 (持仓 vs 指数)
3. 时机贡献: 实际收益 vs buy-and-hold
4. 行业贡献: 各板块 PnL 占比

输入: list[AttributedTrade]
输出: AttributionResult (每项贡献值 + 占比)

降级: 样本 < 30 → 大部分归因不计算

2026-08-02 Ship 29 — 10000 轮迭代 P3 第四步
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class AttributedTrade:
    """单笔可归因交易"""
    code: str
    sector: str
    factor_composite: float
    factor_components: dict[str, float] = field(default_factory=dict)  # factor_name → score
    ret: float = 0.0
    weight: float = 0.0
    date: str = ""


@dataclass
class AttributionResult:
    """归因结果"""
    n_trades: int
    total_pnl_pct: float
    factor_contributions: dict[str, float]      # factor_name → 相关性 (IC)
    sector_contributions: dict[str, float]      # sector → PnL 占比
    timing_contribution: float                  # 时机 (实际 - buy-hold 估算)
    selection_contribution: float               # 选股 (top vs bottom 差异)
    factor_breakdown: dict[str, dict] = field(default_factory=dict)
    is_meaningful: bool = False                 # 样本是否足够


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def compute_attribution(trades: list[AttributedTrade],
                        *,
                        min_samples: int = 30,
                        top_quantile: float = 0.25) -> AttributionResult:
    """归因分析

    Args:
        trades: 历史交易列表
        min_samples: 最少样本, 不足返回空归因
        top_quantile: top N% 视为选股命中
    """
    n = len(trades)
    if n < min_samples:
        return AttributionResult(
            n_trades=n,
            total_pnl_pct=sum(t.ret * t.weight for t in trades),
            factor_contributions={},
            sector_contributions={},
            timing_contribution=0.0,
            selection_contribution=0.0,
            is_meaningful=False,
        )

    # 总 PnL (按权重加权)
    total_pnl = sum(t.ret * t.weight for t in trades)
    total_weight = sum(t.weight for t in trades) or 1.0
    avg_pnl = total_pnl / total_weight

    # 1. 因子 IC: 每个 factor 的 score 与 ret 的相关性
    factor_ics = {}
    factor_breakdown = {}
    for factor_name in trades[0].factor_components:
        scores = [t.factor_components.get(factor_name) for t in trades]
        rets = [t.ret for t in trades]
        # 过滤 None
        valid = [(s, r) for s, r in zip(scores, rets) if s is not None]
        if len(valid) >= min_samples // 2:
            xs = [v[0] for v in valid]
            ys = [v[1] for v in valid]
            ic = _pearson(xs, ys)
            factor_ics[factor_name] = round(ic, 4)
            # Top/Bottom 分桶收益
            k = max(2, int(len(valid) * top_quantile))
            sorted_v = sorted(valid, key=lambda v: v[0])
            bottom = sorted_v[:k]
            top = sorted_v[-k:]
            top_avg = statistics.mean(v[1] for v in top)
            bottom_avg = statistics.mean(v[1] for v in bottom)
            factor_breakdown[factor_name] = {
                "top_avg_ret": round(top_avg, 4),
                "bottom_avg_ret": round(bottom_avg, 4),
                "spread": round(top_avg - bottom_avg, 4),
            }

    # composite 的 IC
    composite_scores = [t.factor_composite for t in trades]
    composite_ret = [t.ret for t in trades]
    composite_ic = _pearson(composite_scores, composite_ret)
    factor_ics["_composite"] = round(composite_ic, 4)

    # 2. 板块贡献
    sector_pnl = defaultdict(float)
    sector_weight = defaultdict(float)
    for t in trades:
        sector_pnl[t.sector] += t.ret * t.weight
        sector_weight[t.sector] += t.weight
    sector_contrib = {}
    for sec, pnl in sector_pnl.items():
        pct = (pnl / total_weight) if total_weight > 0 else 0
        sector_contrib[sec] = round(pct, 4)

    # 3. 选股贡献: top 25% 平均 - bottom 25% 平均 (按 composite 排序)
    sorted_trades = sorted(trades, key=lambda t: t.factor_composite)
    k = max(2, int(n * top_quantile))
    bottom_avg = statistics.mean(t.ret for t in sorted_trades[:k])
    top_avg = statistics.mean(t.ret for t in sorted_trades[-k:])
    selection_contrib = top_avg - bottom_avg

    # 4. 时机贡献: 实际平均 - buy-and-hold 估算
    # buy-hold 估算 = 所有 ret 的中位数 (代表随机持仓)
    bh_estimate = statistics.median(t.ret for t in trades)
    timing_contrib = avg_pnl - bh_estimate

    return AttributionResult(
        n_trades=n,
        total_pnl_pct=round(avg_pnl, 4),
        factor_contributions=factor_ics,
        sector_contributions=sector_contrib,
        timing_contribution=round(timing_contrib, 4),
        selection_contribution=round(selection_contrib, 4),
        factor_breakdown=factor_breakdown,
        is_meaningful=True,
    )


def to_dict(result: AttributionResult) -> dict:
    return {
        "n_trades": result.n_trades,
        "total_pnl_pct": result.total_pnl_pct,
        "is_meaningful": result.is_meaningful,
        "factor_contributions": result.factor_contributions,
        "sector_contributions": result.sector_contributions,
        "timing_contribution": result.timing_contribution,
        "selection_contribution": result.selection_contribution,
        "factor_breakdown": result.factor_breakdown,
    }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx = statistics.mean(x)
    my = statistics.mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)
