#!/usr/bin/env python3
"""
tuixue_v3/factor_efficiency.py
Ship 50/100 — 因子效率边界 (Efficient Frontier for Factors)

设计:
把因子看做组合:
- mean = IC 序列均值
- cov = 因子 IC 之间协方差
- 求解给定 max_drawdown 约束下的最优权重
- 输出: efficient frontier 边界

简化实现:
- 给定目标 vol, 求最大 IR
- 提供 3 个点: min vol, max vol, max Sharpe

降级: 数据不足 → 等权 1/n

2026-08-03 Ship 50 — 10000 轮迭代 P4 第十步
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
class FrontierPoint:
    weights: dict[str, float]      # 因子权重
    expected_return: float          # mean(IC × weight)
    vol: float                      # 因子组合 vol
    sharpe: float                   # IR (mean / vol)
    description: str


@dataclass
class FrontierResult:
    points: list[FrontierPoint]
    factors: list[str]


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_mean_cov(
    ic_dict: dict[str, list[float]],
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    """算 mean 和 cov"""
    means = {f: statistics.mean(v) if v else 0.0
             for f, v in ic_dict.items()}
    # cov 矩阵 (pairwise)
    factors = list(ic_dict.keys())
    n = len(factors)
    cov: dict[tuple[str, str], float] = {}

    for i, fi in enumerate(factors):
        for j, fj in enumerate(factors):
            xi = ic_dict[fi]
            xj = ic_dict[fj]
            if not xi or not xj:
                cov[(fi, fj)] = 0.0
                continue
            min_len = min(len(xi), len(xj))
            if min_len < 2:
                cov[(fi, fj)] = 0.0
                continue
            mi = means[fi]
            mj = means[fj]
            num = sum((xi[k] - mi) * (xj[k] - mj) for k in range(min_len))
            cov[(fi, fj)] = num / (min_len - 1)

    return means, cov


def _portfolio_metrics(
    weights: dict[str, float],
    means: dict[str, float],
    cov: dict[tuple[str, str], float],
) -> tuple[float, float]:
    """组合 (mean, vol)"""
    exp_return = sum(weights.get(f, 0) * means.get(f, 0) for f in weights)
    var = 0.0
    for fi, wi in weights.items():
        for fj, wj in weights.items():
            var += wi * wj * cov.get((fi, fj), 0.0)
    vol = math.sqrt(max(var, 0.0))
    return exp_return, vol


# ═══════════════════════════════════════════════════════
# 构造 frontier
# ═══════════════════════════════════════════════════════

def build_frontier(
    ic_dict: dict[str, list[float]],
    n_points: int = 5,
) -> FrontierResult:
    """构建效率边界

    简化: 不做真正的二次规划, 用启发式:
    - point 0: equal weight (低 vol, 中等 return)
    - point 1: only best-IC factor
    - point 2: top-2 IR weighted
    - point 3: max return skew
    - point 4: 等权 + risk parity
    """
    factors = list(ic_dict.keys())
    if not factors:
        return FrontierResult(points=[], factors=[])

    means, cov = compute_mean_cov(ic_dict)
    points = []

    # 1. 等权 (低 vol 假设)
    w_equal = {f: 1.0 / len(factors) for f in factors}
    r, v = _portfolio_metrics(w_equal, means, cov)
    sh = r / v if v > 0 else 0.0
    points.append(FrontierPoint(
        weights=w_equal,
        expected_return=round(r, 4), vol=round(v, 4),
        sharpe=round(sh, 4),
        description="等权 (低 vol 起点)",
    ))

    if len(factors) >= 2:
        # 2. 单因子 max IR (找 IR 最大的)
        best_ir_f = max(factors, key=lambda f: means.get(f, 0) ** 2 / max(cov.get((f, f), 0.01), 0.01))
        w_single = {f: 1.0 if f == best_ir_f else 0.0 for f in factors}
        r, v = _portfolio_metrics(w_single, means, cov)
        sh = r / v if v > 0 else 0.0
        points.append(FrontierPoint(
            weights=w_single,
            expected_return=round(r, 4), vol=round(v, 4),
            sharpe=round(sh, 4),
            description=f"单因子 max IR ({best_ir_f})",
        ))

    # 3. IR 加权
    w_ir = ir_weight_from(means, cov)
    if w_ir:
        r, v = _portfolio_metrics(w_ir, means, cov)
        sh = r / v if v > 0 else 0.0
        points.append(FrontierPoint(
            weights=w_ir,
            expected_return=round(r, 4), vol=round(v, 4),
            sharpe=round(sh, 4),
            description="IR 加权 (中点)",
        ))

    # 4. Inverse-vol
    w_iv = inverse_vol_weight(means, cov)
    if w_iv:
        r, v = _portfolio_metrics(w_iv, means, cov)
        sh = r / v if v > 0 else 0.0
        points.append(FrontierPoint(
            weights=w_iv,
            expected_return=round(r, 4), vol=round(v, 4),
            sharpe=round(sh, 4),
            description="逆方差加权",
        ))

    # 5. 极化权重 (单因子高 alpha, others 0)
    if factors:
        best_mean_f = max(factors, key=lambda f: means.get(f, 0))
        w_skew = {f: 1.0 if f == best_mean_f else 0.0 for f in factors}
        r, v = _portfolio_metrics(w_skew, means, cov)
        sh = r / v if v > 0 else 0.0
        points.append(FrontierPoint(
            weights=w_skew,
            expected_return=round(r, 4), vol=round(v, 4),
            sharpe=round(sh, 4),
            description=f"极化 ({best_mean_f})",
        ))

    return FrontierResult(points=points, factors=factors)


def ir_weight_from(
    means: dict[str, float],
    cov: dict[tuple[str, str], float],
) -> dict[str, float]:
    """max IR 解析解: w ∝ Σ⁻¹ μ"""
    factors = list(means.keys())
    n = len(factors)
    if n == 0:
        return {}

    # build 2D cov array
    cov2d = [[cov.get((fi, fj), 0.0) for fj in factors] for fi in factors]
    # mean vector
    mu = [means.get(f, 0.0) for f in factors]

    # Solve cov @ w = mu
    w = _solve_2d(cov2d, mu)
    if not w:
        return {f: 1.0 / n for f in factors}

    total = sum(w)
    if abs(total) > 0.01:
        w = [wi / total for wi in w]
    return {f: w[i] for i, f in enumerate(factors)}


def inverse_vol_weight(
    means: dict[str, float],
    cov: dict[tuple[str, str], float],
) -> dict[str, float]:
    """逆波动率加权"""
    factors = list(means.keys())
    if not factors:
        return {}

    vols = []
    for f in factors:
        v = cov.get((f, f), 0.0)
        vols.append(1.0 / math.sqrt(v) if v > 0.0001 else 0.0)
    total = sum(vols)
    if total == 0:
        return {f: 1.0 / len(factors) for f in factors}

    return {f: vols[i] / total for i, f in enumerate(factors)}


# ═══════════════════════════════════════════════════════
# 简单 2D 求解 (LU 不依赖 numpy)
# ═══════════════════════════════════════════════════════

def _solve_2d(A: list[list[float]], b: list[float]) -> Optional[list[float]]:
    """2D 解 Ax = b (LU 简单版)"""
    n = len(A)
    if n == 0 or n != len(b):
        return None

    # 用 numpy 兜底
    try:
        import numpy as np
        x = np.linalg.solve(np.array(A, dtype=float), np.array(b, dtype=float))
        return list(x)
    except Exception:
        # 高斯消元
        aug = [list(A[i]) + [b[i]] for i in range(n)]
        for i in range(n):
            # 找主元
            max_row = i
            for k in range(i + 1, n):
                if abs(aug[k][i]) > abs(aug[max_row][i]):
                    max_row = k
            aug[i], aug[max_row] = aug[max_row], aug[i]
            if abs(aug[i][i]) < 1e-10:
                return None
            for k in range(i + 1, n):
                factor = aug[k][i] / aug[i][i]
                for j in range(i, n + 1):
                    aug[k][j] -= factor * aug[i][j]
        # 回代
        x = [0.0] * n
        for i in range(n - 1, -1, -1):
            x[i] = aug[i][n]
            for j in range(i + 1, n):
                x[i] -= aug[i][j] * x[j]
            x[i] /= aug[i][i]
        return x


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(r: FrontierResult) -> dict:
    return {
        "factors": r.factors,
        "points": [
            {
                "weights": p.weights,
                "expected_return": p.expected_return,
                "vol": p.vol,
                "sharpe": p.sharpe,
                "description": p.description,
            }
            for p in r.points
        ],
    }


def best_sharpe(r: FrontierResult) -> Optional[FrontierPoint]:
    """最高 Sharpe 的点"""
    if not r.points:
        return None
    return max(r.points, key=lambda p: p.sharpe)


def min_vol(r: FrontierResult) -> Optional[FrontierPoint]:
    """最低 vol"""
    if not r.points:
        return None
    return min(r.points, key=lambda p: p.vol)
