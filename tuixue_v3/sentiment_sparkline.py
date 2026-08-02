#!/usr/bin/env python3
"""
tuixue_v3/sentiment_sparkline.py
Ship 49/100 — 情绪 Sparkline 数据

设计:
生成小尺寸趋势图数据:
- N 个等间隔点
- 当前点高亮
- 极值点标记
- 趋势 (上升/下降/平稳)

输出: 紧凑字典, 适合 UI sparkline 渲染

降级: 样本不足 → 全 0

2026-08-03 Ship 49 — 10000 轮迭代 P4 第九步
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
class Sparkline:
    data: list[float]            # 数值序列
    labels: list[str]            # 时间标签
    current: float               # 当前值
    current_idx: int             # 当前索引
    delta: float                 # 当前 - 起始
    delta_pct: float             # 百分比
    min_idx: int                 # 最低点索引
    max_idx: int                 # 最高点索引
    trend: str                   # "up" / "down" / "flat"
    n: int

    def summary(self) -> str:
        return (
            f"current={self.current:.1f} "
            f"Δ={self.delta:+.1f} ({self.delta_pct:+.1%}) "
            f"trend={self.trend}"
        )


# ═══════════════════════════════════════════════════════
# 跟踪器
# ═══════════════════════════════════════════════════════

class SentimentSparkline:
    """情绪 sparkline 累积器"""
    def __init__(self, max_n: int = 60):
        self.max_n = max_n
        self._values: deque = deque(maxlen=max_n)
        self._labels: deque = deque(maxlen=max_n)

    def add(self, value: float, label: str = "") -> None:
        self._values.append(float(value))
        self._labels.append(label)

    def snapshot(self) -> Sparkline:
        n = len(self._values)
        if n == 0:
            return Sparkline(
                data=[], labels=[],
                current=0.0, current_idx=0,
                delta=0.0, delta_pct=0.0,
                min_idx=0, max_idx=0,
                trend="flat", n=0,
            )

        data = list(self._values)
        labels = list(self._labels)

        current = data[-1]
        start = data[0]
        delta = current - start
        delta_pct = delta / abs(start) if abs(start) > 0.01 else 0.0

        # 极值点
        min_val = min(data)
        max_val = max(data)
        min_idx = data.index(min_val)
        max_idx = data.index(max_val)

        # 趋势 (基于首尾 + 中点斜率)
        if n >= 3:
            mid_idx = n // 2
            mid = data[mid_idx]
            # 三点斜率
            slope1 = (mid - start) / max(mid_idx, 1)
            slope2 = (current - mid) / max(n - 1 - mid_idx, 1)
            avg_slope = (slope1 + slope2) / 2.0
            if avg_slope > 0.05:
                trend = "up"
            elif avg_slope < -0.05:
                trend = "down"
            else:
                trend = "flat"
        else:
            trend = "flat"

        return Sparkline(
            data=data, labels=labels,
            current=round(current, 2),
            current_idx=n - 1,
            delta=round(delta, 2),
            delta_pct=round(delta_pct, 4),
            min_idx=min_idx,
            max_idx=max_idx,
            trend=trend,
            n=n,
        )


# ═══════════════════════════════════════════════════════
# 静态
# ═══════════════════════════════════════════════════════

def build_sparkline_from_list(
    values: list[float],
    labels: Optional[list[str]] = None,
) -> Sparkline:
    """从 list 直接构造"""
    sp = SentimentSparkline(max_n=len(values) or 60)
    labels = labels or [""] * len(values)
    for v, lb in zip(values, labels):
        sp.add(v, lb)
    return sp.snapshot()


def normalize_to_unit(s: Sparkline) -> Sparkline:
    """归一化到 [0, 1] (适合 ECharts area)"""
    if not s.data:
        return s
    lo = min(s.data)
    hi = max(s.data)
    rng = hi - lo
    if rng == 0:
        norm = [0.5] * len(s.data)
    else:
        norm = [(v - lo) / rng for v in s.data]
    return Sparkline(
        data=[round(v, 4) for v in norm],
        labels=s.labels,
        current=norm[s.current_idx] if s.current_idx < len(norm) else 0.5,
        current_idx=s.current_idx,
        delta=norm[s.current_idx] - (norm[0] if norm else 0),
        delta_pct=s.delta_pct,
        min_idx=s.min_idx,
        max_idx=s.max_idx,
        trend=s.trend,
        n=s.n,
    )


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(s: Sparkline) -> dict:
    return {
        "data": list(s.data),
        "labels": list(s.labels),
        "current": s.current,
        "current_idx": s.current_idx,
        "delta": s.delta,
        "delta_pct": s.delta_pct,
        "min_idx": s.min_idx,
        "max_idx": s.max_idx,
        "trend": s.trend,
        "n": s.n,
    }


def to_echarts(s: Sparkline) -> dict:
    """ECharts line series 数据"""
    return {
        "type": "line",
        "data": list(s.data),
        "showSymbol": False,
        "smooth": True,
        "current": s.current,
        "delta": s.delta,
        "trend": s.trend,
    }
