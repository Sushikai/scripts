#!/usr/bin/env python3
"""
tuixue_v3/factor_correlation.py
Ship 43/100 — 因子相关性矩阵 (Factor Correlation Matrix)

设计:
计算 N 因子两两 Pearson 相关性:
- 输入: {factor_name: list[float]} (returns / exposures)
- 输出: N×N 对称矩阵, 对角线为 1.0

相关性高 → 因子冗余, 应压缩权重
相关性低 → 多样性, 组合收益稳定

降级: 长度不足或常量 → 返回 0.0

2026-08-03 Ship 43 — 10000 轮迭代 P4 第三步
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
class CorrelationMatrix:
    """N×N 相关性矩阵"""
    factors: list[str]
    matrix: list[list[float]]     # matrix[i][j] = corr(i, j)
    n_samples: int

    def get(self, f1: str, f2: str) -> Optional[float]:
        try:
            i = self.factors.index(f1)
            j = self.factors.index(f2)
            return self.matrix[i][j]
        except ValueError:
            return None

    def pair(self, f1: str, f2: str) -> tuple[Optional[float], Optional[float]]:
        return self.get(f1, f2), self.get(f2, f1)


# ═══════════════════════════════════════════════════════
# 相关性
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


def compute_matrix(
    factor_series: dict[str, list[float]],
) -> CorrelationMatrix:
    """计算 N×N 相关性矩阵"""
    factors = list(factor_series.keys())
    n = len(factors)
    matrix = [[0.0] * n for _ in range(n)]

    # 决定 n_samples (用最短序列)
    min_len = min((len(v) for v in factor_series.values()), default=0)

    for i, fa in enumerate(factors):
        for j, fb in enumerate(factors):
            if i == j:
                matrix[i][j] = 1.0
            elif j > i:
                # 截至 j>i 部分对称填
                a = factor_series[fa][:min_len] if min_len > 0 else factor_series[fa]
                b = factor_series[fb][:min_len] if min_len > 0 else factor_series[fb]
                c = _pearson(a, b)
                matrix[i][j] = c
                matrix[j][i] = c

    return CorrelationMatrix(
        factors=factors, matrix=matrix, n_samples=min_len,
    )


# ═══════════════════════════════════════════════════════
# 分析
# ═══════════════════════════════════════════════════════

def find_redundant_pairs(
    matrix: CorrelationMatrix,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """找冗余对 (|corr| > threshold)

    Returns: [(f1, f2, corr), ...]
    """
    out = []
    factors = matrix.factors
    n = len(factors)
    for i in range(n):
        for j in range(i + 1, n):
            c = matrix.matrix[i][j]
            if abs(c) >= threshold:
                out.append((factors[i], factors[j], c))
    return out


def avg_offdiag_correlation(matrix: CorrelationMatrix) -> float:
    """平均非对角相关性"""
    factors = matrix.factors
    n = len(factors)
    if n < 2:
        return 0.0
    s = 0.0
    cnt = 0
    for i in range(n):
        for j in range(i + 1, n):
            s += abs(matrix.matrix[i][j])
            cnt += 1
    return s / cnt if cnt > 0 else 0.0


def most_correlated(matrix: CorrelationMatrix,
                    factor: str) -> Optional[tuple[str, float]]:
    """与某因子最相关的另一个"""
    if factor not in matrix.factors:
        return None
    i = matrix.factors.index(factor)
    n = len(matrix.factors)
    best_j = None
    best_c = -2.0
    for j in range(n):
        if i == j:
            continue
        c = abs(matrix.matrix[i][j])
        if c > best_c:
            best_c = c
            best_j = j
    if best_j is None:
        return None
    return matrix.factors[best_j], matrix.matrix[i][best_j]


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(m: CorrelationMatrix) -> dict:
    return {
        "factors": list(m.factors),
        "matrix": [[round(c, 4) for c in row] for row in m.matrix],
        "n_samples": m.n_samples,
        "avg_offdiag_corr": round(avg_offdiag_correlation(m), 4),
    }


def summary(m: CorrelationMatrix) -> str:
    """人类可读摘要"""
    pairs = find_redundant_pairs(m, threshold=0.7)
    if not pairs:
        return f"因子 {len(m.factors)} 个, 无高度相关 (>0.7)"
    out = [f"因子 {len(m.factors)} 个, 冗余对 {len(pairs)}:"]
    for f1, f2, c in pairs[:5]:
        out.append(f"  {f1} - {f2}: {c:+.3f}")
    return "\n".join(out)
