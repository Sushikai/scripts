#!/usr/bin/env python3
"""
tuixue_v3/risk_budget.py
Ship 30/100 — 风险预算 (Risk Budget Allocation)

设计:
给定总风险预算 (e.g. 总组合 2% 日波目标), 分配给各策略:
- 每个策略分配 VaR / Expected Shortfall 上限
- 策略相关性 → 风险集中度 → 减少分配
- 历史回撤 → 惩罚项

输出: {strategy: budget} + 检查超额 (over-budget) 警告

降级: 无相关性矩阵 → 默认独立 (相关系数=0)

2026-08-02 Ship 30 — 10000 轮迭代 P3 第五步
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class StrategyRisk:
    """单个策略风险参数"""
    name: str
    expected_vol: float            # 日波动率 (e.g. 0.02)
    historical_dd: float = 0.0     # 历史最大回撤 (绝对值, e.g. 0.15)
    n_trades: int = 0


@dataclass
class RiskBudgetResult:
    """风险预算分配"""
    total_budget: float
    per_strategy: dict[str, float]             # name → 预算
    per_strategy_pct: dict[str, float]         # name → 占总预算比例
    max_combined_vol: float                    # 组合后预期波动率
    warnings: list[str]
    over_budget: list[str]                     # 超出原请求的策略


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def allocate_risk_budget(
    total_budget: float,
    strategies: list[StrategyRisk],
    correlation_matrix: Optional[dict[tuple[str, str], float]] = None,
) -> RiskBudgetResult:
    """分配风险预算

    算法 (简化版 Risk Parity):
    1. 每个策略初始预算 = 总预算 × (1/vol_i) / Σ(1/vol_j)
       → 低波动策略分更多
    2. 历史回撤 > 20% → 减半分配
    3. 相关性 > 0.7 → 该对策略共享受限 (×0.7)
    4. 累加 = 总预算 + Σcorrelation_factor

    Args:
        total_budget: 总风险预算 (e.g. 0.02 = 2% 日 VaR)
        strategies: 策略风险参数
        correlation_matrix: {(s1, s2): corr} (可选)

    Returns:
        RiskBudgetResult
    """
    if not strategies:
        return RiskBudgetResult(
            total_budget=total_budget, per_strategy={},
            per_strategy_pct={}, max_combined_vol=0,
            warnings=["无策略"], over_budget=[],
        )

    correlation_matrix = correlation_matrix or {}
    warnings: list[str] = []
    over_budget: list[str] = []

    # 1. 初始权重 = 1/vol (Risk Parity 简化)
    raw_weights = {}
    for s in strategies:
        if s.expected_vol <= 0:
            raw_weights[s.name] = 0.0
            warnings.append(f"{s.name}: vol=0, 跳过")
            continue
        raw_weights[s.name] = 1.0 / s.expected_vol

    # 2. 历史回撤惩罚
    for s in strategies:
        if s.historical_dd >= 0.20:
            raw_weights[s.name] *= 0.5
            warnings.append(f"{s.name}: 历史回撤 {s.historical_dd:.0%} > 20%, 分配减半")
        elif s.historical_dd >= 0.10:
            raw_weights[s.name] *= 0.75

    # 3. 相关性惩罚 (累加对所有高相关的对方)
    for s1 in strategies:
        for s2 in strategies:
            if s1.name >= s2.name:
                continue
            corr = correlation_matrix.get((s1.name, s2.name), 0.0)
            corr = correlation_matrix.get((s2.name, s1.name), corr)
            if corr > 0.7:
                raw_weights[s1.name] *= (1 - 0.3 * corr)
                raw_weights[s2.name] *= (1 - 0.3 * corr)
                warnings.append(f"{s1.name} ↔ {s2.name}: 相关性 {corr:.2f}, 分配 ×{1 - 0.3 * corr:.2f}")

    # 4. 归一化
    total_weight = sum(raw_weights.values())
    if total_weight <= 0:
        return RiskBudgetResult(
            total_budget=total_budget, per_strategy={s.name: 0 for s in strategies},
            per_strategy_pct={s.name: 0 for s in strategies},
            max_combined_vol=0, warnings=warnings + ["总权重为 0"], over_budget=[],
        )

    per_strategy = {n: total_budget * w / total_weight
                    for n, w in raw_weights.items()}
    per_strategy_pct = {n: w / total_weight
                        for n, w in raw_weights.items()}

    # 5. 组合波动率估算 (粗略, 假设各策略仓位相等, 简化为各 vol 加权平均)
    weighted_vol = sum(per_strategy.get(s.name, 0) for s in strategies)
    max_combined = min(weighted_vol, total_budget)

    # 检查超额 (单一策略超过总预算 80%)
    for name, budget in per_strategy.items():
        if budget > total_budget * 0.8:
            over_budget.append(name)
            warnings.append(f"{name}: 分配 {budget:.4f} 超总预算 80%")

    return RiskBudgetResult(
        total_budget=total_budget,
        per_strategy={k: round(v, 6) for k, v in per_strategy.items()},
        per_strategy_pct={k: round(v, 4) for k, v in per_strategy_pct.items()},
        max_combined_vol=round(max_combined, 4),
        warnings=warnings,
        over_budget=over_budget,
    )


def to_dict(result: RiskBudgetResult) -> dict:
    return {
        "total_budget": result.total_budget,
        "per_strategy": result.per_strategy,
        "per_strategy_pct": result.per_strategy_pct,
        "max_combined_vol": result.max_combined_vol,
        "warnings": result.warnings,
        "over_budget": result.over_budget,
    }
