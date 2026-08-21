#!/usr/bin/env python3
"""
tuixue_v3/strat_era10_sentiment_allocation.py
Ship 66/100 — 量化 era 2026 高级策略 #10

Sentiment-driven Allocation Strategy (情绪驱动配置)

设计:
基于市场情绪评分动态调整仓位:
- 极度恐慌 (score < 20): 加仓 (逆向)
- 恐慌 (20-40): 中等仓位, 防御
- 中性 (40-60): 标准仓位
- 贪婪 (60-80): 减仓, 防泡沫
- 极度贪婪 (> 80): 大幅减仓

输入: sentiment_score (0-100), portfolio_value
输出: 仓位调整建议 (action, target_exposure, reason)

2026-08-03 Ship 66 — 10000 轮迭代 P5 第十一步
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
class AllocationAdvice:
    score: float               # 输入情绪 (0-100)
    action: str                # "buy" / "sell" / "hold" / "trim" / "add"
    target_exposure: float     # 目标仓位 (0-1)
    delta_exposure: float      # 相对当前仓位的变化
    reason: str

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "action": self.action,
            "target_exposure": self.target_exposure,
            "delta_exposure": self.delta_exposure,
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════
# 阈值
# ═══════════════════════════════════════════════════════

THRESHOLDS = [
    (10, "add", 1.0),         # score < 10 极度恐慌, 满仓逆向
    (25, "hold", 0.9),        # score < 25 恐慌
    (45, "hold", 0.7),        # score < 45 中性偏低
    (55, "hold", 0.7),        # score < 55 中性偏高
    (75, "trim", 0.5),        # score < 75 贪婪
    (90, "sell", 0.3),        # score < 90 极度贪婪
]


def get_target_exposure(score: float) -> tuple[str, float]:
    """根据情绪得分查 target_exposure

    按阈值从低到高, 第一个 score < threshold 即匹配
    """
    for threshold, action, exposure in THRESHOLDS:
        if score < threshold:
            return action, exposure
    return "sell", 0.2    # score >= 90 极度贪婪, 默认


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def advise(
    score: float,
    *,
    current_exposure: float = 0.7,
) -> AllocationAdvice:
    """根据情绪分数给出建议

    current_exposure: 当前仓位 (默认 0.7)
    """
    score = max(0.0, min(100.0, score))

    action, target = get_target_exposure(score)
    delta = target - current_exposure

    if action == "hold" and abs(delta) < 0.05:
        reason = f"中性区 (score={score:.1f}), 维持当前仓位 {current_exposure:.0%}"
    elif action == "add":
        reason = f"情绪极度恐慌 (score={score:.1f}), 逆向加仓至 {target:.0%}"
    elif action == "trim":
        reason = f"情绪贪婪 (score={score:.1f}), 减仓至 {target:.0%}"
    elif action == "sell":
        reason = f"情绪极度贪婪 (score={score:.1f}), 防御减仓至 {target:.0%}"
    elif action == "buy":
        reason = f"情绪恐慌 (score={score:.1f}), 谨慎买入"
    else:
        reason = f"score={score:.1f}, target={target:.0%}"

    return AllocationAdvice(
        score=round(score, 2),
        action=action,
        target_exposure=round(target, 4),
        delta_exposure=round(delta, 4),
        reason=reason,
    )


# ═══════════════════════════════════════════════════════
# 时序监控
# ═══════════════════════════════════════════════════════

@dataclass
class SentimentTrend:
    current: float
    ma_5: float
    ma_20: float
    delta_5: float         # 5 期变化
    delta_20: float        # 20 期变化
    direction: str         # "warming" / "cooling" / "stable"

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "ma_5": self.ma_5,
            "ma_20": self.ma_20,
            "delta_5": self.delta_5,
            "delta_20": self.delta_20,
            "direction": self.direction,
        }


def trend(scores: list[float]) -> Optional[SentimentTrend]:
    """情绪趋势"""
    if len(scores) < 20:
        return None

    curr = scores[-1]
    ma_5 = statistics.mean(scores[-5:])
    ma_20 = statistics.mean(scores[-20:])
    delta_5 = curr - scores[-5] if len(scores) >= 5 else 0.0
    delta_20 = curr - scores[-20] if len(scores) >= 20 else 0.0

    if delta_20 > 5:
        direction = "warming"
    elif delta_20 < -5:
        direction = "cooling"
    else:
        direction = "stable"

    return SentimentTrend(
        current=round(curr, 2),
        ma_5=round(ma_5, 2),
        ma_20=round(ma_20, 2),
        delta_5=round(delta_5, 2),
        delta_20=round(delta_20, 2),
        direction=direction,
    )


# ═══════════════════════════════════════════════════════
# 综合 (current + trend)
# ═══════════════════════════════════════════════════════

def comprehensive_advice(
    scores: list[float],
    *,
    current_exposure: float = 0.7,
) -> Optional[tuple[AllocationAdvice, SentimentTrend]]:
    """综合建议: 当前 + 趋势"""
    if not scores:
        return None

    advice = advise(scores[-1], current_exposure=current_exposure)
    tr = trend(scores)
    if tr is None:
        return None

    # 趋势叠加: warming + greedy → 更激进减仓
    if tr.direction == "warming" and advice.target_exposure > 0.7:
        advice.target_exposure = max(0.3, advice.target_exposure - 0.1)
        advice.delta_exposure = round(advice.target_exposure - current_exposure, 4)
        advice.reason += " | 情绪升温, 加码减仓"

    # 趋势叠加: cooling + fearful → 更激进加仓
    if tr.direction == "cooling" and advice.target_exposure < 0.7:
        advice.target_exposure = min(1.0, advice.target_exposure + 0.1)
        advice.delta_exposure = round(advice.target_exposure - current_exposure, 4)
        advice.reason += " | 情绪降温, 逆向加仓"

    return advice, tr


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(a: AllocationAdvice) -> str:
    return (f"score={a.score:.1f} → {a.action.upper()} target={a.target_exposure:.0%} "
            f"delta={a.delta_exposure:+.0%} ({a.reason})")