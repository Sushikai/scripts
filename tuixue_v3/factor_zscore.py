#!/usr/bin/env python3
"""
tuixue_v3/factor_zscore.py
Ship 44/100 — 因子 Z-Score 标准化 (Cross-Sectional Normalization)

设计:
横截面标准化 (跨股票对比):
- z = (x - μ) / σ
- winsorize: 限 ±3σ 范围, 防极值噪声

时序标准化 (单股票历史对比):
- rolling zscore
- 标准化后因子可加可减可平均

降级: std=0 → z=0, 不阻塞

2026-08-03 Ship 44 — 10000 轮迭代 P4 第四步
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 横截面
# ═══════════════════════════════════════════════════════

def cross_section_zscore(values: list[float], *,
                         winsorize: float = 3.0) -> list[float]:
    """横截面 Z-Score (n 只股票同截面)"""
    if len(values) < 2:
        return [0.0] * len(values)

    # 1. winsorize (限 |z| ≤ winsorize σ)
    mu_pre = statistics.mean(values)
    sigma_pre = statistics.stdev(values) if len(values) > 1 else 0.0

    if sigma_pre > 0:
        capped = []
        for v in values:
            z = (v - mu_pre) / sigma_pre
            if z > winsorize:
                capped.append(mu_pre + winsorize * sigma_pre)
            elif z < -winsorize:
                capped.append(mu_pre - winsorize * sigma_pre)
            else:
                capped.append(v)
    else:
        capped = list(values)

    # 2. 重新算 mu / σ
    mu = statistics.mean(capped)
    sigma = statistics.stdev(capped) if len(capped) > 1 else 0.0
    if sigma == 0:
        return [0.0] * len(values)

    return [(v - mu) / sigma for v in capped]


# ═══════════════════════════════════════════════════════
# 时序 rolling
# ═══════════════════════════════════════════════════════

class RollingZScore:
    """单序列 rolling Z-Score"""
    def __init__(self, window: int = 60):
        self.window = window
        self._buf: deque = deque(maxlen=window)

    def add(self, value: float) -> Optional[float]:
        """添加一个值, 返回当前 z-score, 样本不足返 None"""
        self._buf.append(value)
        if len(self._buf) < 2:
            return None
        mu = statistics.mean(self._buf)
        sigma = statistics.stdev(self._buf)
        if sigma == 0:
            return 0.0
        return (value - mu) / sigma

    @property
    def n(self) -> int:
        return len(self._buf)

    @property
    def mean(self) -> Optional[float]:
        if not self._buf:
            return None
        return statistics.mean(self._buf)

    @property
    def std(self) -> Optional[float]:
        if len(self._buf) < 2:
            return None
        return statistics.stdev(self._buf)


def time_series_zscore(values: list[float], window: int = 60) -> list[Optional[float]]:
    """对长序列做 rolling Z-Score, 返回等长 list"""
    rz = RollingZScore(window=window)
    out = []
    for v in values:
        out.append(rz.add(v))
    return out


# ═══════════════════════════════════════════════════════
# Rank 标准化
# ═══════════════════════════════════════════════════════

def rank(values: list[float]) -> list[float]:
    """Rank 标准化 (1 到 N, 平均处 0)"""
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda kv: kv[1])
    out = [0.0] * n
    for rank_pos, (orig_idx, _) in enumerate(indexed, start=1):
        out[orig_idx] = rank_pos
    # 中心化: 减去 (n+1)/2
    center = (n + 1) / 2.0
    return [r - center for r in out]


def rank_zscore(values: list[float]) -> list[float]:
    """Rank z-score (近似正态)"""
    ranks = rank(values)
    n = len(ranks)
    if n == 0:
        return []
    # 归一化: 仍用 rank 形式, 范围 [-0.5, 0.5]
    # 保持可解释
    return [r / n for r in ranks]


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def standardize_combo(
    factor_dict: dict[str, list[float]],
    method: str = "zscore",
) -> dict[str, list[float]]:
    """对每列做标准化

    Args:
        method: "zscore", "rank", "rank_zscore"
    """
    out = {}
    for factor, values in factor_dict.items():
        if method == "zscore":
            out[factor] = cross_section_zscore(values)
        elif method == "rank":
            out[factor] = rank(values)
        elif method == "rank_zscore":
            out[factor] = rank_zscore(values)
        else:
            out[factor] = list(values)
    return out


def neutralized_zscore(
    values: list[float],
    industry_indicator: list[int],   # 同长度, 行业标签
) -> list[float]:
    """行业中性化 Z-Score (去行业暴露)"""
    n = len(values)
    if n < 2:
        return [0.0] * n

    # 按行业分组求均值
    industry_means: dict[int, list[float]] = {}
    for v, ind in zip(values, industry_indicator):
        industry_means.setdefault(ind, []).append(v)

    industry_avg = {}
    for ind, vs in industry_means.items():
        industry_avg[ind] = statistics.mean(vs)

    # 残差 = value - industry_avg
    residuals = [v - industry_avg.get(ind, 0) for v, ind in zip(values, industry_indicator)]

    # 残差标准化
    if len(residuals) > 1:
        mu = statistics.mean(residuals)
        sigma = statistics.stdev(residuals)
        if sigma > 0:
            return [(r - mu) / sigma for r in residuals]
    return [0.0] * n


def winsorize(values: list[float], k: float = 3.0) -> list[float]:
    """±kσ 削尾"""
    if len(values) < 2:
        return list(values)
    mu = statistics.mean(values)
    sigma = statistics.stdev(values)
    if sigma == 0:
        return list(values)
    return [
        max(mu - k * sigma, min(mu + k * sigma, v))
        for v in values
    ]
