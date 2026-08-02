#!/usr/bin/env python3
"""
tuixue_v3/factor_weight_opt.py
Ship 45/100 — 多因子权重优化 (Factor Weight Optimization)

设计:
基于历史 IC 和 IR 优化因子权重:
- IR (Information Ratio) = IC_mean / IC_std
- 高 IR 因子 → 高权重
- 相关性罚分: 相关性高的因子, 权重之和约束

策略:
- 输入: {factor_name: list[float]} (IC 时序)
- 输出: {factor_name: weight} (sum=1.0)

降级: 数据不足 → 等权 1/n

2026-08-03 Ship 45 — 10000 轮迭代 P4 第五步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class FactorIR:
    """单因子 IR"""
    factor: str
    ic_mean: float
    ic_std: float
    ir: float
    n: int


@dataclass
class WeightOptResult:
    """优化结果"""
    factor_irs: list[FactorIR]
    weights: dict[str, float]
    method: str


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _ir_from_series(ic_series: list[float]) -> FactorIR:
    """从 IC 时序算 IR"""
    if not ic_series:
        return FactorIR("?", 0.0, 0.0, 0.0, 0)
    n = len(ic_series)
    mu = statistics.mean(ic_series)
    if n < 2:
        return FactorIR("?", mu, 0.0, 0.0, n)
    sigma = statistics.stdev(ic_series)
    ir = mu / sigma if sigma > 0 else 0.0
    return FactorIR("?", mu, sigma, ir, n)


def compute_irs(ic_dict: dict[str, list[float]]) -> list[FactorIR]:
    """对所有因子算 IR"""
    out = []
    for name, series in ic_dict.items():
        f = _ir_from_series(series)
        f.factor = name
        out.append(f)
    return out


# ═══════════════════════════════════════════════════════
# 权重策略
# ═══════════════════════════════════════════════════════

def equal_weight(factors: list[str]) -> dict[str, float]:
    """等权"""
    if not factors:
        return {}
    n = len(factors)
    return {f: 1.0 / n for f in factors}


def ir_weighted(irs: list[FactorIR], *,
                abs_weight: bool = False,
                floor: float = 0.0) -> dict[str, float]:
    """按 |IR| 加权 (最大化信息比)"""
    if not irs:
        return {}

    weights_raw = []
    for f in irs:
        ir = abs(f.ir) if abs_weight else f.ir
        # 负 IR → 不分配
        w = max(ir, 0.0) if not abs_weight else abs(ir)
        w = max(w, floor)
        weights_raw.append(w)

    total = sum(weights_raw)
    if total == 0:
        # 全 0 或全负 → 等权
        return equal_weight([f.factor for f in irs])

    return {f.factor: w / total
            for f, w in zip(irs, weights_raw)}


def ic_ir_blend(irs: list[FactorIR], *,
                alpha: float = 0.5) -> dict[str, float]:
    """IC × IR 加权 (alpha 控制侧重)"""
    if not irs:
        return {}

    raw = []
    for f in irs:
        s = alpha * f.ic_mean + (1 - alpha) * f.ir
        # 负 → 0
        raw.append(max(s, 0.0))

    total = sum(raw)
    if total == 0:
        return equal_weight([f.factor for f in irs])

    return {f.factor: w / total for f, w in zip(irs, raw)}


def diversification_aware(
    irs: list[FactorIR],
    correlation_matrix: Optional[list[list[float]]] = None,
    penalty: float = 0.3,
) -> dict[str, float]:
    """多样性感知加权: 相关性高的因子降权

    AdjWeight = IR * (1 - penalty * sum_corr(other))
    """
    if not irs:
        return {}

    n = len(irs)
    has_corr = correlation_matrix is not None and len(correlation_matrix) == n

    if has_corr:
        adj_ir = []
        for i, f in enumerate(irs):
            corr_sum = 0.0
            for j, other in enumerate(irs):
                if i == j:
                    continue
                corr_sum += abs(correlation_matrix[i][j])
            corr_avg = corr_sum / max(n - 1, 1)
            adj = f.ir * (1 - penalty * corr_avg)
            adj_ir.append(max(adj, 0.0))
    else:
        adj_ir = [max(f.ir, 0.0) for f in irs]

    total = sum(adj_ir)
    if total == 0:
        return equal_weight([f.factor for f in irs])

    return {f.factor: w / total for f, w in zip(irs, adj_ir)}


# ═══════════════════════════════════════════════════════
# 优化
# ═══════════════════════════════════════════════════════

def optimize(
    ic_dict: dict[str, list[float]],
    *,
    method: str = "ir",
    correlation_matrix: Optional[list[list[float]]] = None,
    penalty: float = 0.3,
    alpha: float = 0.5,
) -> WeightOptResult:
    """主入口: 因子权重优化

    Args:
        ic_dict: {factor_name: list[float]} IC 时序
        method: "equal" / "ir" / "ic_ir" / "diversification"
    """
    irs = compute_irs(ic_dict)
    if not irs:
        return WeightOptResult([], {}, method)

    if method == "equal":
        w = equal_weight([f.factor for f in irs])
    elif method == "ir":
        w = ir_weighted(irs, abs_weight=True)
    elif method == "ic_ir":
        w = ic_ir_blend(irs, alpha=alpha)
    elif method == "diversification":
        w = diversification_aware(irs, correlation_matrix, penalty)
    else:
        w = equal_weight([f.factor for f in irs])

    return WeightOptResult(
        factor_irs=irs,
        weights=w,
        method=method,
    )


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(r: WeightOptResult) -> dict:
    return {
        "method": r.method,
        "weights": r.weights,
        "factor_irs": [
            {
                "factor": f.factor,
                "ic_mean": round(f.ic_mean, 4),
                "ic_std": round(f.ic_std, 4),
                "ir": round(f.ir, 4),
                "n": f.n,
            }
            for f in r.factor_irs
        ],
    }


def summarize(r: WeightOptResult) -> str:
    out = [f"Method: {r.method}"]
    for f in r.factor_irs:
        w = r.weights.get(f.factor, 0)
        out.append(f"  {f.factor}: IR={f.ir:+.3f} w={w:.2%}")
    return "\n".join(out)


def validate_weights(weights: dict[str, float]) -> bool:
    """sum ≈ 1, all ≥ 0"""
    if not weights:
        return False
    if any(w < 0 for w in weights.values()):
        return False
    s = sum(weights.values())
    return abs(s - 1.0) < 0.001
