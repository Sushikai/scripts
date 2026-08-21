#!/usr/bin/env python3
"""
tuixue_v3/factor_contrib.py
Ship 41/100 — 因子贡献度分解 (Factor Contribution Decomposition)

设计:
把一个综合 alpha 分数拆解到各因子上:
- 每个因子的 IC × 因子暴露度 = 因子贡献
- 总贡献度 = sum
- 贡献占比 = factor_contrib / total_contrib

输入: {factor_name: (ic, exposure)}, 全 alpha score
输出: FactorContribution dataclass 列表

降级: 缺失 IC → 贡献=0, 缺失 exposure → 1.0

2026-08-03 Ship 41 — 10000 轮迭代 P4 第一步
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class FactorContribution:
    """单因子贡献"""
    factor: str
    ic: float              # 因子 IC
    exposure: float        # 当前暴露 (-1 到 1 或更多)
    contribution: float    # ic * exposure
    pct: float             # 占总贡献 %


@dataclass
class ContributionResult:
    """完整分解结果"""
    total_alpha: float
    total_contrib: float
    contributions: list[FactorContribution]

    @property
    def dominant(self) -> Optional[str]:
        """主导因子"""
        if not self.contributions:
            return None
        return max(self.contributions, key=lambda c: abs(c.contribution)).factor


# ═══════════════════════════════════════════════════════
# 分解
# ═══════════════════════════════════════════════════════

def decompose(
    factor_data: dict[str, tuple[float, float]],
    total_alpha: Optional[float] = None,
) -> ContributionResult:
    """分解因子贡献

    Args:
        factor_data: {factor: (ic, exposure)}
        total_alpha: 组合 alpha 分数 (若 None 则用 Σ contribution)
    """
    contributions = []
    for name, (ic, exposure) in factor_data.items():
        contributions.append(FactorContribution(
            factor=name,
            ic=ic, exposure=exposure,
            contribution=round(ic * exposure, 6),
            pct=0.0,  # 填占比
        ))

    total_contrib = sum(c.contribution for c in contributions)

    # 占比
    if total_contrib != 0:
        for c in contributions:
            c.pct = round(c.contribution / total_contrib, 4)
    else:
        # 全 0 → 等分
        n = len(contributions) or 1
        for c in contributions:
            c.pct = round(1.0 / n, 4)

    if total_alpha is None:
        total_alpha = total_contrib

    return ContributionResult(
        total_alpha=round(total_alpha, 6),
        total_contrib=round(total_contrib, 6),
        contributions=contributions,
    )


def decompose_with_zscore(
    factor_zscores: dict[str, float],
    factor_ics: dict[str, float],
) -> ContributionResult:
    """按 z-score 加权分解 (标准 quant 风格)

    Args:
        factor_zscores: {factor: z_score 暴露}
        factor_ics: {factor: IC}
    """
    data = {}
    for f in factor_zscores:
        z = factor_zscores[f]
        ic = factor_ics.get(f, 0.0)
        data[f] = (ic, z)
    return decompose(data)


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def top_contributors(result: ContributionResult, n: int = 3) -> list[FactorContribution]:
    """贡献最大的 N 个"""
    return sorted(result.contributions,
                  key=lambda c: abs(c.contribution), reverse=True)[:n]


def summarize(result: ContributionResult) -> dict:
    """汇总为 dict"""
    return {
        "total_alpha": result.total_alpha,
        "total_contrib": result.total_contrib,
        "dominant": result.dominant,
        "factors": [
            {
                "factor": c.factor,
                "ic": c.ic,
                "exposure": c.exposure,
                "contribution": c.contribution,
                "pct": c.pct,
            }
            for c in result.contributions
        ],
    }


def decay_aware_contrib(
    factor_ics: dict[str, float],
    factor_exposures: dict[str, float],
    factor_decay: dict[str, float],     # 衰减权重 0-1
) -> ContributionResult:
    """衰减感知贡献: 衰减的因子贡献打折"""
    adj_ics = {}
    for f, ic in factor_ics.items():
        decay = factor_decay.get(f, 0.0)
        adj = ic * (1.0 - decay)
        adj_ics[f] = adj

    data = {}
    for f in adj_ics:
        data[f] = (adj_ics[f], factor_exposures.get(f, 0.0))
    return decompose(data)


# ═══════════════════════════════════════════════════════
# Top / Bottom 因子
# ═══════════════════════════════════════════════════════

def rank_by_ic(factor_ics: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """按 IC 排序"""
    sorted_items = sorted(factor_ics.items(), key=lambda kv: kv[1], reverse=True)
    return sorted_items[:n]


def rank_by_abs_ic(factor_ics: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """按 |IC| 排序 (最强预测力)"""
    sorted_items = sorted(factor_ics.items(), key=lambda kv: abs(kv[1]), reverse=True)
    return sorted_items[:n]
