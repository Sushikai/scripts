#!/usr/bin/env python3
"""
tuixue_v3/factor_scatter.py
Ship 47/100 — 因子散点图数据 (Factor Scatter Data)

设计:
生成 2 维因子散点图数据:
- X 因子, Y 因子, 数值点
- 标签 (代码或名称)
- 颜色组 (行业 / 风格)

额外:
- 回归线 (slope/intercept/r²)
- 异常点 (1.5σ 外)

降级: 数据缺失 → 返回空

2026-08-03 Ship 47 — 10000 轮迭代 P4 第七步
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
class ScatterPoint:
    x: float
    y: float
    label: str = ""
    group: str = ""
    is_outlier: bool = False


@dataclass
class RegressionLine:
    slope: float
    intercept: float
    r2: float


@dataclass
class FactorScatter:
    x_name: str
    y_name: str
    points: list[ScatterPoint]
    regression: Optional[RegressionLine] = None
    n_outliers: int = 0


# ═══════════════════════════════════════════════════════
# 构造
# ═══════════════════════════════════════════════════════

def build_scatter(
    x_values: list[float],
    y_values: list[float],
    labels: Optional[list[str]] = None,
    groups: Optional[list[str]] = None,
    x_name: str = "x",
    y_name: str = "y",
    outlier_k: float = 2.5,
) -> FactorScatter:
    """构造散点图 + 回归线 + 异常点

    Args:
        x_values, y_values: 横纵坐标
        labels: 标签 (代码等)
        groups: 行业/风格分组
    """
    n = min(len(x_values), len(y_values))
    if n == 0:
        return FactorScatter(
            x_name=x_name, y_name=y_name,
            points=[], regression=None, n_outliers=0,
        )

    labels = labels or [""] * n
    groups = groups or [""] * n

    points = [ScatterPoint(
        x=x_values[i], y=y_values[i],
        label=labels[i] if i < len(labels) else "",
        group=groups[i] if i < len(groups) else "",
    ) for i in range(n)]

    # 回归 (不含异常点循环)
    regression = _safe_regression(x_values[:n], y_values[:n])

    # 异常点: 用 |y_dev| > k * sigma_y (剔除 y 远离群体的点)
    n_outliers = 0
    if n >= 3:
        y_mean = statistics.mean(y_values[:n])
        y_sigma = statistics.stdev(y_values[:n])
        if y_sigma > 0:
            for i in range(n):
                z = abs(y_values[i] - y_mean) / y_sigma
                if z > outlier_k:
                    points[i].is_outlier = True
                    n_outliers += 1

    return FactorScatter(
        x_name=x_name, y_name=y_name,
        points=points, regression=regression, n_outliers=n_outliers,
    )


def _safe_regression(x: list[float], y: list[float]) -> Optional[RegressionLine]:
    """简单 OLS 回归"""
    n = min(len(x), len(y))
    if n < 2:
        return None
    mx = statistics.mean(x)
    my = statistics.mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x[:n], y[:n]))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x[:n]))
    if dx == 0:
        return None
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y[:n]))
    if dy == 0:
        return RegressionLine(slope=0.0, intercept=my, r2=0.0)
    slope = num / (dx ** 2) if dx ** 2 > 0 else 0.0
    intercept = my - slope * mx
    # R²
    ss_res = sum((y[i] - (slope * x[i] + intercept)) ** 2 for i in range(n))
    ss_tot = sum((yi - my) ** 2 for yi in y[:n])
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return RegressionLine(
        slope=round(slope, 6),
        intercept=round(intercept, 4),
        r2=round(r2, 4),
    )


# ═══════════════════════════════════════════════════════
# 输出 (ECharts)
# ═══════════════════════════════════════════════════════

def to_echarts(s: FactorScatter) -> dict:
    """转 ECharts scatter 数据"""
    series_dict: dict[str, tuple[list, list]] = {}
    for p in s.points:
        g = p.group or "default"
        if g not in series_dict:
            series_dict[g] = ([], [])
        x_list, y_list = series_dict[g]
        x_list.append(p.x)
        y_list.append(p.y)

    series = []
    for g, (xs, ys) in series_dict.items():
        series.append({
            "name": g,
            "type": "scatter",
            "data": list(zip(xs, ys)),
        })

    out = {
        "x_name": s.x_name,
        "y_name": s.y_name,
        "series": series,
        "n_points": len(s.points),
        "n_outliers": s.n_outliers,
    }

    if s.regression:
        out["regression"] = {
            "slope": s.regression.slope,
            "intercept": s.regression.intercept,
            "r2": s.regression.r2,
        }
    return out


def to_dict(s: FactorScatter) -> dict:
    return {
        "x_name": s.x_name,
        "y_name": s.y_name,
        "points": [
            {
                "x": p.x, "y": p.y,
                "label": p.label, "group": p.group,
                "is_outlier": p.is_outlier,
            }
            for p in s.points
        ],
        "regression": {
            "slope": s.regression.slope,
            "intercept": s.regression.intercept,
            "r2": s.regression.r2,
        } if s.regression else None,
        "n_outliers": s.n_outliers,
    }


# ═══════════════════════════════════════════════════════
# 双因子组合分析
# ═══════════════════════════════════════════════════════

def build_pair_scatter(
    factor_dict: dict[str, list[float]],
    pair: tuple[str, str],
    labels: Optional[list[str]] = None,
) -> FactorScatter:
    """两个因子散点"""
    x_name, y_name = pair
    xs = factor_dict.get(x_name, [])
    ys = factor_dict.get(y_name, [])
    return build_scatter(xs, ys, labels=labels, x_name=x_name, y_name=y_name)
