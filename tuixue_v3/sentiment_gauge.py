#!/usr/bin/env python3
"""
tuixue_v3/sentiment_gauge.py
Ship 38/100 — 情绪仪表盘 (Sentiment Gauge)

设计:
聚合多源情绪数据为 [0, 100] 仪表盘分数:
- 涨停数 / 跌停数 (沪深 +10 / -10)
- 上涨家数 / 下跌家数
- 量能 (今日 vs 5 日均)
- 北向资金净流入
- 龙虎榜净买入

输出:
- gauge_value: 0-100 (50 中性)
- components: 各项细节
- label: 文字标签 (贪婪/中性/恐惧)

降级: 缺失 → 该项计 50 (中性), 不阻塞

2026-08-03 Ship 38 — 10000 轮迭代 P3 第十三步
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class SentimentComponent:
    """单组件"""
    name: str
    value: float          # 原始值
    score: float          # 0-100 归一化
    weight: float
    missing: bool = False


@dataclass
class SentimentGauge:
    """完整仪表盘"""
    gauge_value: float          # 0-100, 最终
    label: str                  # 贪婪/乐观/中性/悲观/恐惧
    components: list[SentimentComponent]
    timestamp: float = 0.0

    @property
    def is_extreme(self) -> bool:
        """极端值 (>= 80 或 <= 20)"""
        return self.gauge_value >= 80 or self.gauge_value <= 20


# ═══════════════════════════════════════════════════════
# 标签
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


# ═══════════════════════════════════════════════════════
# 聚合
# ═══════════════════════════════════════════════════════

def build_gauge(
    *,
    n_limit_up: Optional[int] = None,
    n_limit_down: Optional[int] = None,
    n_advancing: Optional[int] = None,
    n_declining: Optional[int] = None,
    volume_ratio: Optional[float] = None,         # 今日 / 5日均
    north_flow: Optional[float] = None,          # 亿, 北向净流入
    dragon_net_buy: Optional[float] = None,      # 亿, 龙虎榜净买入
    weights: Optional[dict[str, float]] = None,
) -> SentimentGauge:
    """构造情绪仪表盘

    每项 0-100 归一化:
    - limit_up: 0 个 → 30, 30+ → 90
    - limit_down: 0 → 70, 100+ → 10 (反向)
    - advancing_ratio: 比例
    - volume_ratio: 0.5 → 20, 1.5+ → 80
    - north_flow: -50亿 → 20, +100亿 → 90
    - dragon_net_buy: 同 north

    缺失项: score=50 中性
    """
    import time

    default_weights = {
        "limit_up": 0.15,
        "limit_down": 0.15,
        "advance_decline": 0.20,
        "volume_ratio": 0.15,
        "north_flow": 0.20,
        "dragon_net_buy": 0.15,
    }
    if weights:
        default_weights.update(weights)

    components: list[SentimentComponent] = []

    # 1. 涨停数
    if n_limit_up is None:
        components.append(SentimentComponent("limit_up", 0, 50.0, default_weights["limit_up"], True))
    else:
        score = _limit_up_score(n_limit_up)
        components.append(SentimentComponent("limit_up", n_limit_up, score, default_weights["limit_up"]))

    # 2. 跌停数 (反向)
    if n_limit_down is None:
        components.append(SentimentComponent("limit_down", 0, 50.0, default_weights["limit_down"], True))
    else:
        score = _limit_down_score(n_limit_down)
        components.append(SentimentComponent("limit_down", n_limit_down, score, default_weights["limit_down"]))

    # 3. 涨/跌比
    if n_advancing is None or n_declining is None:
        components.append(SentimentComponent("advance_decline", 0, 50.0, default_weights["advance_decline"], True))
    else:
        ratio = n_advancing / max(n_advancing + n_declining, 1)
        score = _ratio_score(ratio)
        components.append(SentimentComponent("advance_decline", ratio, score, default_weights["advance_decline"]))

    # 4. 量比
    if volume_ratio is None:
        components.append(SentimentComponent("volume_ratio", 0, 50.0, default_weights["volume_ratio"], True))
    else:
        score = _volume_ratio_score(volume_ratio)
        components.append(SentimentComponent("volume_ratio", volume_ratio, score, default_weights["volume_ratio"]))

    # 5. 北向
    if north_flow is None:
        components.append(SentimentComponent("north_flow", 0, 50.0, default_weights["north_flow"], True))
    else:
        score = _flow_score(north_flow)
        components.append(SentimentComponent("north_flow", north_flow, score, default_weights["north_flow"]))

    # 6. 龙虎榜
    if dragon_net_buy is None:
        components.append(SentimentComponent("dragon_net_buy", 0, 50.0, default_weights["dragon_net_buy"], True))
    else:
        score = _flow_score(dragon_net_buy)
        components.append(SentimentComponent("dragon_net_buy", dragon_net_buy, score, default_weights["dragon_net_buy"]))

    # 加权
    total_w = sum(c.weight for c in components)
    if total_w == 0:
        gauge = 50.0
    else:
        gauge = sum(c.score * c.weight for c in components) / total_w

    return SentimentGauge(
        gauge_value=round(gauge, 2),
        label=_label(gauge),
        components=components,
        timestamp=time.time(),
    )


# ═══════════════════════════════════════════════════════
# 单项打分
# ═══════════════════════════════════════════════════════

def _limit_up_score(n: int) -> float:
    """涨停数 → 分数"""
    if n <= 0:
        return 20.0
    if n >= 80:
        return 95.0
    # 0 → 20, 80 → 95
    return 20.0 + (min(n, 80) / 80) * 75.0


def _limit_down_score(n: int) -> float:
    """跌停数 → 分数 (反向)"""
    if n <= 0:
        return 80.0
    if n >= 50:
        return 10.0
    # 0 → 80, 50 → 10
    return 80.0 - (min(n, 50) / 50) * 70.0


def _ratio_score(ratio: float) -> float:
    """涨/总 → 分数"""
    # 0 → 20, 0.5 → 50, 1.0 → 80
    if ratio >= 0.5:
        return 50.0 + (ratio - 0.5) * 60.0
    return 20.0 + ratio * 60.0


def _volume_ratio_score(r: float) -> float:
    """量比 → 分数"""
    if r < 0.5:
        return 20.0
    if r > 1.5:
        return 80.0 + min(r - 1.5, 1.0) * 15.0
    # 0.5 → 20, 1.0 → 50, 1.5 → 80
    return 20.0 + (r - 0.5) * 60.0


def _flow_score(billion: float) -> float:
    """资金净流入 → 分数"""
    # -50亿 → 20, 0 → 50, +100亿 → 90
    if billion <= -50:
        return 10.0
    if billion >= 100:
        return 90.0
    # -50 → 10, 0 → 50, 100 → 90
    return 50.0 + (billion / 100) * 40.0


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(g: SentimentGauge) -> dict:
    return {
        "gauge_value": g.gauge_value,
        "label": g.label,
        "is_extreme": g.is_extreme,
        "timestamp": g.timestamp,
        "components": [
            {
                "name": c.name, "value": c.value,
                "score": c.score, "weight": c.weight,
                "missing": c.missing,
            }
            for c in g.components
        ],
    }


def trend_label(prev: Optional[float], curr: float) -> str:
    """对比 prev, 给趋势标签"""
    if prev is None:
        return "首次"
    diff = curr - prev
    if diff > 5:
        return "升温"
    if diff < -5:
        return "降温"
    return "平稳"
