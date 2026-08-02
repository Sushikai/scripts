#!/usr/bin/env python3
"""
tuixue_v3/factor_snr.py
Ship 56/100 — 因子 Signal-Noise Ratio 计算器

设计:
衡量一个因子的 信噪比 (SNR):
- SNR = |mean(IC)| / std(IC)
- 标准化 SNR (除以 √n)
- 和 z-score 类似, 但更稳定

输出:
- snr: 主指标
- z_snr: 标准化 (z-statistic)
- 信噪分档 (优秀/合格/差)

降级: 样本不足 → 0

2026-08-03 Ship 56 — 10000 轮迭代 P5 第一步
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
class SNRResult:
    factor: str
    n: int
    mean: float
    std: float
    snr: float              # mean / std (== IR)
    z_snr: float            # t-stat
    grade: str              # "excellent" / "good" / "fair" / "poor"
    p_value: float          # 假设 mean=0 的 t 双尾 p

    @property
    def is_significant(self) -> bool:
        return self.p_value < 0.05

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "n": self.n,
            "mean": self.mean,
            "std": self.std,
            "snr": self.snr,
            "z_snr": self.z_snr,
            "grade": self.grade,
            "p_value": self.p_value,
            "is_significant": self.is_significant,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _student_t_cdf(t: float, df: int) -> float:
    """简化 student-t CDF 近似

    Returns: P(T <= t) double-tail p-value 用 1 - |t|/2 近似
    """
    # 学生 t 分布的 CDF 精确计算复杂, 用正态近似
    # 大 df (n >= 30) 几乎一样
    from math import erf, sqrt
    if df < 1:
        return 0.5
    # normal approximation
    return 0.5 + 0.5 * erf(t / sqrt(2.0))


def _two_tail_p(t: float, df: int) -> float:
    """双尾 p-value"""
    # 简化: 用正态近似
    from math import erfc, sqrt
    if df < 1:
        return 1.0
    # p = erfc(|t|/sqrt(2))
    return erfc(abs(t) / sqrt(2.0))


def compute_snr(ic_series: list[float], factor_name: str = "?") -> SNRResult:
    """计算 SNR"""
    n = len(ic_series)
    if n < 2:
        return SNRResult(
            factor=factor_name, n=n,
            mean=0.0, std=0.0, snr=0.0,
            z_snr=0.0, grade="poor", p_value=1.0,
        )

    mu = statistics.mean(ic_series)
    sigma = statistics.stdev(ic_series)
    snr = mu / sigma if sigma > 0 else 0.0
    z_snr = mu / sigma * math.sqrt(n) if sigma > 0 else 0.0
    p_value = _two_tail_p(z_snr, n - 1)

    # 等级: |z_snr| >= 3 → excellent, >= 2 → good, >= 1 → fair
    az = abs(z_snr)
    if az >= 3.0:
        grade = "excellent"
    elif az >= 2.0:
        grade = "good"
    elif az >= 1.0:
        grade = "fair"
    else:
        grade = "poor"

    return SNRResult(
        factor=factor_name, n=n,
        mean=round(mu, 4),
        std=round(sigma, 4),
        snr=round(snr, 4),
        z_snr=round(z_snr, 4),
        grade=grade,
        p_value=round(p_value, 6),
    )


# ═══════════════════════════════════════════════════════
# 多因子
# ═══════════════════════════════════════════════════════

def compute_multi(ic_dict: dict[str, list[float]]) -> list[SNRResult]:
    """多因子 SNR"""
    return [compute_snr(v, f) for f, v in ic_dict.items()]


def rank_by_snr(results: list[SNRResult]) -> list[SNRResult]:
    """按 |z_snr| 排序"""
    return sorted(results, key=lambda r: abs(r.z_snr), reverse=True)


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: SNRResult) -> str:
    if r.grade == "excellent":
        sig = "优秀"
    elif r.grade == "good":
        sig = "显著"
    elif r.grade == "fair":
        sig = "弱显著"
    else:
        sig = "不显著"

    return f"{r.factor}: SNR={r.snr:+.3f} z={r.z_snr:+.2f} {sig} (p={r.p_value:.4f})"
