#!/usr/bin/env python3
"""
tuixue_v3/factor_ic_histogram.py
Ship 48/100 — 因子 IC 衰减直方图 (IC Decay Histogram)

设计:
对因子 IC 时序做 bin 统计:
- 等宽分箱 (-0.5, -0.3, -0.1, 0, 0.1, 0.3, 0.5)
- 每个 IC bin 的频次
- 统计指标: 均值, t-stat, hit rate (IC>0 占比)

输出:
- bins: [(start, end, count), ...]
- stats: {mean, t_stat, hit_rate, n}

降级: 样本不足 (< 30) → 标记 sample_too_small

2026-08-03 Ship 48 — 10000 轮迭代 P4 第八步
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class Bin:
    start: float         # 区间起点
    end: float           # 区间终点
    count: int           # 频次
    mid: float           # 中点


@dataclass
class ICHistogramResult:
    factor: str
    n: int
    bins: list[Bin]
    stats: dict          # mean, t_stat, hit_rate, ...

    @property
    def is_valid(self) -> bool:
        return self.n >= 30


# ═══════════════════════════════════════════════════════
# 构造
# ═══════════════════════════════════════════════════════

DEFAULT_BINS = [-0.3, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.2, 0.3]


def build_histogram(
    ic_series: list[float],
    factor_name: str = "?",
    bin_edges: Optional[list[float]] = None,
) -> ICHistogramResult:
    """构造 IC 直方图

    Args:
        ic_series: IC 时序 (例如 60 天 daily IC)
        factor_name: 因子名
        bin_edges: 区间边界, 默认用 DEFAULT_BINS
    """
    n = len(ic_series)
    edges = bin_edges or DEFAULT_BINS

    if n == 0:
        return ICHistogramResult(
            factor=factor_name, n=0, bins=[], stats={},
        )

    # 分箱
    counts: Counter = Counter()
    for ic in ic_series:
        placed = False
        for i in range(len(edges) - 1):
            if edges[i] <= ic < edges[i + 1]:
                counts[i] += 1
                placed = True
                break
        # 大于最大 bin
        if not placed:
            if ic >= edges[-1]:
                counts[len(edges) - 1] += 1
            elif ic < edges[0]:
                counts[-1] += 1   # 用 -1 表示"低于最小",特立维护

    # 转 Bin 列表 (含 edges[0] 之前的小于区间)
    bins = []
    # 处理 "小于最小" 的 bin
    less_count = counts.pop(-1, 0)
    if less_count > 0:
        bins.append(Bin(
            start=-1.0, end=edges[0],
            count=less_count, mid=(edges[0] - 1) / 2,
        ))
    for i in range(len(edges) - 1):
        c = counts.pop(i, 0)
        bins.append(Bin(
            start=edges[i], end=edges[i + 1],
            count=c,
            mid=(edges[i] + edges[i + 1]) / 2,
        ))

    # 处理 "大于最大"
    over_count = counts.pop(len(edges) - 1, 0)
    if over_count > 0:
        bins.append(Bin(
            start=edges[-1], end=1.0,
            count=over_count,
            mid=(edges[-1] + 1) / 2,
        ))

    # 统计
    mu = statistics.mean(ic_series)
    sigma = statistics.stdev(ic_series) if n > 1 else 0.0
    t_stat = mu / sigma * math.sqrt(n) if sigma > 0 else 0.0
    hit_rate = sum(1 for v in ic_series if v > 0) / n

    stats = {
        "mean": round(mu, 4),
        "std": round(sigma, 4),
        "t_stat": round(t_stat, 4),
        "hit_rate": round(hit_rate, 4),
        "min": round(min(ic_series), 4),
        "max": round(max(ic_series), 4),
    }

    return ICHistogramResult(
        factor=factor_name, n=n,
        bins=bins, stats=stats,
    )


# ═══════════════════════════════════════════════════════
# 多因子
# ═══════════════════════════════════════════════════════

def build_multi(
    ic_dict: dict[str, list[float]],
    bin_edges: Optional[list[float]] = None,
) -> list[ICHistogramResult]:
    """对多因子一次性建直方图"""
    out = []
    for factor, series in ic_dict.items():
        out.append(build_histogram(series, factor, bin_edges))
    return out


# ═══════════════════════════════════════════════════════
# 输出 (ECharts)
# ═══════════════════════════════════════════════════════

def to_echarts(h: ICHistogramResult) -> dict:
    """转 ECharts bar 数据"""
    return {
        "factor": h.factor,
        "n": h.n,
        "x_data": [f"[{b.start:.2f},{b.end:.2f})" for b in h.bins],
        "data": [b.count for b in h.bins],
        "stats": h.stats,
        "is_valid": h.is_valid,
    }


def to_dict(h: ICHistogramResult) -> dict:
    return {
        "factor": h.factor,
        "n": h.n,
        "bins": [
            {"start": b.start, "end": b.end,
             "count": b.count, "mid": b.mid}
            for b in h.bins
        ],
        "stats": h.stats,
        "is_valid": h.is_valid,
    }


# ═══════════════════════════════════════════════════════
# 解读
# ═══════════════════════════════════════════════════════

def interpret(h: ICHistogramResult) -> str:
    """自然语言解读"""
    if h.n < 30:
        return f"样本不足 ({h.n} < 30)"

    mu = h.stats.get("mean", 0)
    t = h.stats.get("t_stat", 0)
    hit = h.stats.get("hit_rate", 0)

    if abs(t) >= 2.0:
        sig = "显著"
    elif abs(t) >= 1.0:
        sig = "弱显著"
    else:
        sig = "不显著"

    if mu > 0:
        return f"正向 IC {mu:+.3f}, {sig} (t={t:+.2f}), hit_rate {hit:.0%}"
    return f"负向 IC {mu:+.3f}, {sig} (t={t:+.2f}), hit_rate {hit:.0%}"
