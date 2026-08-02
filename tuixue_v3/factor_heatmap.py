#!/usr/bin/env python3
"""
tuixue_v3/factor_heatmap.py
Ship 37/100 — 因子热力图 (IC 时序 + Z-score 矩阵)

设计:
把多因子的 IC 时序聚合成一个热力图数据:
- X 轴: 日期
- Y 轴: 因子名
- 颜色值: IC (or Z-score 归一化)

降级: 数据缺失 → NaN 占位, 前端用透明色渲染

2026-08-03 Ship 37 — 10000 轮迭代 P3 第十二步
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
class HeatmapCell:
    """一个格子"""
    factor: str
    date: str
    ic: float                # NaN 表示缺失
    z_score: float = 0.0    # 跨因子归一化


@dataclass
class FactorHeatmap:
    """整张热力图"""
    factors: list[str]
    dates: list[str]
    cells: list[HeatmapCell]
    vmin: float = -1.0
    vmax: float = 1.0

    def get(self, factor: str, date: str) -> Optional[HeatmapCell]:
        for c in self.cells:
            if c.factor == factor and c.date == date:
                return c
        return None

    def factor_series(self, factor: str) -> list[HeatmapCell]:
        return [c for c in self.cells if c.factor == factor]


# ═══════════════════════════════════════════════════════
# 构造
# ═══════════════════════════════════════════════════════

def build_heatmap(
    ic_series: dict[str, list[tuple[str, float]]],
) -> FactorHeatmap:
    """从 IC 时序构造热力图

    Args:
        ic_series: {factor_name: [(date, ic), ...]}
    """
    all_dates: set[str] = set()
    for series in ic_series.values():
        for d, _ in series:
            all_dates.add(d)
    dates = sorted(all_dates)

    factors = list(ic_series.keys())

    # 收集所有 IC, 算 mean / std
    ic_values = []
    for series in ic_series.values():
        for _, ic in series:
            if not math.isnan(ic):
                ic_values.append(ic)

    if len(ic_values) >= 2:
        mu = statistics.mean(ic_values)
        sigma = statistics.stdev(ic_values)
        if sigma == 0:
            sigma = 1.0
    else:
        mu = 0.0
        sigma = 1.0

    cells = []
    for factor in factors:
        for date in dates:
            ic = _lookup(ic_series.get(factor, []), date)
            z = (ic - mu) / sigma if not math.isnan(ic) and sigma > 0 else 0.0
            cells.append(HeatmapCell(
                factor=factor, date=date,
                ic=ic, z_score=round(z, 4),
            ))

    # vmin / vmax 用于色阶
    real_ics = [c.ic for c in cells if not math.isnan(c.ic)]
    if real_ics:
        vmin = min(real_ics)
        vmax = max(real_ics)
    else:
        vmin, vmax = -1.0, 1.0

    return FactorHeatmap(
        factors=factors, dates=dates, cells=cells,
        vmin=round(vmin, 4), vmax=round(vmax, 4),
    )


def _lookup(series: list[tuple[str, float]], date: str) -> float:
    """查 IC, 缺失返 NaN"""
    for d, ic in series:
        if d == date:
            return ic
    return float("nan")


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_echarts(heatmap: FactorHeatmap) -> dict:
    """转 ECharts heatmap series 数据"""
    data = []
    for c in heatmap.cells:
        if math.isnan(c.ic):
            continue
        y = heatmap.factors.index(c.factor) if c.factor in heatmap.factors else 0
        x = heatmap.dates.index(c.date) if c.date in heatmap.dates else 0
        data.append([x, y, round(c.ic, 4)])
    return {
        "x_data": heatmap.dates,
        "y_data": heatmap.factors,
        "series": [{
            "type": "heatmap",
            "data": data,
            "vmin": heatmap.vmin,
            "vmax": heatmap.vmax,
        }],
    }


def summarize(heatmap: FactorHeatmap) -> dict:
    """每个因子的均值/标准差"""
    out = {}
    for factor in heatmap.factors:
        ics = [c.ic for c in heatmap.factor_series(factor) if not math.isnan(c.ic)]
        if ics:
            out[factor] = {
                "n": len(ics),
                "mean": round(statistics.mean(ics), 4),
                "std": round(statistics.stdev(ics), 4) if len(ics) >= 2 else 0.0,
                "min": round(min(ics), 4),
                "max": round(max(ics), 4),
            }
        else:
            out[factor] = {"n": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return out
