#!/usr/bin/env python3
"""
tuixue_v3/factor_decay_chart.py
Ship 54/100 — 因子衰减可视化 (Factor Decay Chart)

设计:
把因子时序上的衰减情况可视化:
- X 轴: 时间窗口
- Y 轴: IC
- 历史 IC vs 当前 IC 对比
- 衰减速率标签

输入: FactorDecayTracker 或 (predicted, actual) 列表
输出: 一组时序数据 + 关键点

降级: 样本不足 → 默认 0 线

2026-08-03 Ship 54 — 10000 轮迭代 P4 第十四步
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
class DecayChartPoint:
    idx: int
    ic: float            # rolling IC 在该点
    cumulative: float    # cumulative IC
    is_alert: bool       # 是否衰减点


@dataclass
class DecayChart:
    factor: str
    points: list[DecayChartPoint]
    current_ic: float
    historical_ic: float
    decay_pct: float
    status: str           # "stable" / "warning" / "decayed"

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "current_ic": self.current_ic,
            "historical_ic": self.historical_ic,
            "decay_pct": self.decay_pct,
            "status": self.status,
            "series": [
                {"idx": p.idx, "ic": p.ic,
                 "cumulative": p.cumulative, "is_alert": p.is_alert}
                for p in self.points
            ],
        }


# ═══════════════════════════════════════════════════════
# 构造
# ═══════════════════════════════════════════════════════

def build_decay_chart(
    factor: str,
    ic_series: list[float],
    *,
    window: int = 20,
    decay_threshold: float = 0.5,
) -> DecayChart:
    """构造衰减图

    Args:
        ic_series: rolling IC 时序
        window: decay 检测窗口
        decay_threshold: 衰减率 > 此值报 decay

    关键点:
    - recent_window (后 window 个) 平均 vs
    - historical_window (前 window 个) 平均
    """
    n = len(ic_series)
    if n < 2 * window:
        return DecayChart(
            factor=factor, points=[],
            current_ic=0.0, historical_ic=0.0,
            decay_pct=0.0, status="stable",
        )

    # 时序 rolling IC 用给定 ic_series (累积)
    cum = 0.0
    points = []
    for i, ic in enumerate(ic_series):
        cum += ic
        is_alert = ic < -decay_threshold * abs(statistics.mean(ic_series)) if ic_series else False
        points.append(DecayChartPoint(
            idx=i, ic=round(ic, 4),
            cumulative=round(cum, 4),
            is_alert=False,   # 非衰减
        ))

    historical = ic_series[:window]
    current = ic_series[-window:]

    hist_avg = statistics.mean(historical)
    curr_avg = statistics.mean(current)

    if abs(hist_avg) > 0.01:
        decay = (hist_avg - curr_avg) / abs(hist_avg)
    else:
        decay = 0.0

    if abs(decay) >= decay_threshold:
        status = "decayed"
    elif abs(decay) >= decay_threshold / 2:
        status = "warning"
    else:
        status = "stable"

    # 标记 alert 点
    threshold = hist_avg * 0.3 if hist_avg > 0 else -0.05
    for p in points:
        p.is_alert = p.ic < threshold

    return DecayChart(
        factor=factor, points=points,
        current_ic=round(curr_avg, 4),
        historical_ic=round(hist_avg, 4),
        decay_pct=round(decay, 4),
        status=status,
    )


# ═══════════════════════════════════════════════════════
# 多窗口对比
# ═══════════════════════════════════════════════════════

def rolling_ic(predicted: list[float], actual: list[float],
               window: int = 20) -> list[float]:
    """滚动 IC 时序"""
    n = min(len(predicted), len(actual))
    out = []
    for i in range(n):
        if i + 1 < window:
            out.append(0.0)
            continue
        sub_p = predicted[max(0, i + 1 - window):i + 1]
        sub_a = actual[max(0, i + 1 - window):i + 1]
        out.append(_pearson(sub_p, sub_a))
    return out


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


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_echarts(c: DecayChart) -> dict:
    """ECharts 双 series"""
    return {
        "factor": c.factor,
        "status": c.status,
        "x_data": list(range(len(c.points))),
        "series": [
            {
                "name": "IC",
                "type": "line",
                "data": [p.ic for p in c.points],
            },
            {
                "name": "Cumulative",
                "type": "line",
                "data": [p.cumulative for p in c.points],
            },
        ],
        "mark_points": [p.idx for p in c.points if p.is_alert],
        "current_ic": c.current_ic,
        "historical_ic": c.historical_ic,
        "decay_pct": c.decay_pct,
    }


def summarize(c: DecayChart) -> str:
    """人类可读"""
    if c.status == "decayed":
        return f"{c.factor}: 衰减 {c.decay_pct:+.1%}, 已弃用"
    if c.status == "warning":
        return f"{c.factor}: 衰减 {c.decay_pct:+.1%}, 警告"
    return f"{c.factor}: 稳定 (current IC={c.current_ic:+.3f}, hist IC={c.historical_ic:+.3f})"
