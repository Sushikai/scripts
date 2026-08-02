#!/usr/bin/env python3
"""
tuixue_v3/sentiment_dashboard_api.py
Ship 55/100 — 情绪仪表盘 API 输出 (Sentiment Dashboard API Output)

设计:
把多个情绪指标打包给前端:
- gauge: 当前 0-100
- trend: 最近 60 日 sparkline
- zscore: 距均值的偏差
- signals: 各种触发信号列表
- color: 主色 + 趋势色

降级: 任一上游 None → 用 placeholder, 不阻塞整体

2026-08-03 Ship 55 — 10000 轮迭代 P4 第十五步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 仪表盘数据
# ═══════════════════════════════════════════════════════

@dataclass
class SentimentDashboard:
    gauge: float                # 0-100
    label: str                  # 7 档
    trend: list[float]          # sparkline 数据
    zscore: float               # 当前距均 z-score
    signals: list[str]          # 信号列表
    color: str                  # 主色 (hex)
    trend_color: str            # 趋势色
    components: dict            # 各组件得分
    is_extreme: bool
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "gauge": self.gauge,
            "label": self.label,
            "trend": list(self.trend),
            "zscore": self.zscore,
            "signals": list(self.signals),
            "color": self.color,
            "trend_color": self.trend_color,
            "components": dict(self.components),
            "is_extreme": self.is_extreme,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════
# 工具 (从同包模块导入)
# ═══════════════════════════════════════════════════════

def _label(score: float) -> str:
    if score >= 80:
        return "极度贪婪"
    if score >= 65:
        return "贪婪"
    if score >= 55:
        return "乐观"
    if score >= 45:
        return "中性"
    if score >= 35:
        return "悲观"
    if score >= 20:
        return "恐惧"
    return "极度恐惧"


def _color(score: float) -> str:
    if score >= 80:
        return "#006400"
    if score >= 65:
        return "#70c070"
    if score >= 55:
        return "#a0c0a0"
    if score >= 45:
        return "#999999"
    if score >= 35:
        return "#c0a0a0"
    if score >= 20:
        return "#d97070"
    return "#8b0000"


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def build_dashboard(
    current_score: float,
    history: list[float],
    *,
    components: Optional[dict[str, float]] = None,
) -> SentimentDashboard:
    """构造完整仪表盘

    Args:
        current_score: 当前情绪分数 (0-100)
        history: 历史分数时序 (升序, 旧在前)
        components: 各组件分数 (e.g. {"limit_up": 75, "volume": 60})
    """
    import time

    score = max(0.0, min(100.0, current_score))
    label = _label(score)
    color = _color(score)

    # zscore
    if len(history) >= 5:
        mu = statistics.mean(history)
        sigma = statistics.stdev(history)
        z = (score - mu) / sigma if sigma > 0 else 0.0
    else:
        z = 0.0

    # signals
    signals = []
    if score >= 80:
        signals.append("情绪极度贪婪, 警惕反向风险")
    elif score >= 65:
        signals.append("情绪贪婪, 适度谨慎")
    elif score >= 55:
        signals.append("情绪偏乐观, 关注延续")
    elif score <= 20:
        signals.append("情绪极度恐惧, 反向机会")
    elif score <= 35:
        signals.append("情绪恐惧, 关注反弹")
    if z >= 2.0:
        signals.append("情绪高位异常 (2σ)")
    elif z <= -2.0:
        signals.append("情绪低位异常 (-2σ)")
    if len(history) >= 5:
        recent_5 = history[-5:]
        delta = recent_5[-1] - recent_5[0]
        if delta > 10:
            signals.append(f"短期 +{delta:.0f}, 升温")
        elif delta < -10:
            signals.append(f"短期 {delta:.0f}, 降温")

    # 趋势色 (基于最近 delta)
    trend_color = "#999999"
    if len(history) >= 2:
        delta = history[-1] - history[-2]
        if delta > 2:
            trend_color = "#00b050"
        elif delta < -2:
            trend_color = "#c00000"

    return SentimentDashboard(
        gauge=round(score, 2),
        label=label,
        trend=list(history[-60:]),
        zscore=round(z, 4),
        signals=signals,
        color=color,
        trend_color=trend_color,
        components=components or {},
        is_extreme=(score >= 80 or score <= 20),
        timestamp=time.time(),
    )


# ═══════════════════════════════════════════════════════
# 批量 (多视角)
# ═══════════════════════════════════════════════════════

def build_multi(
    views: dict[str, tuple[float, list[float]]],
    *,
    components_per_view: Optional[dict[str, dict]] = None,
) -> dict[str, SentimentDashboard]:
    """多视角 (宽基/沪深/创业板)"""
    out = {}
    for name, (score, history) in views.items():
        comps = (components_per_view or {}).get(name)
        out[name] = build_dashboard(score, history, components=comps)
    return out
