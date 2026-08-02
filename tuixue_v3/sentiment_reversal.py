#!/usr/bin/env python3
"""
tuixue_v3/sentiment_reversal.py
Ship 42/100 — 情绪回退检测 (Sentiment Reversal Detector)

设计:
跟踪情绪分数时序, 检测回退:
- recent_window 平均 vs historical_window 平均
- 显著下降 → 警告
- 急转 (短时间内骤降) → 报警

回退信号:
- 反向风险 (大跌前情绪冲顶)
- 反向机会 (大跌后情绪触底)

降级: 样本不足 → 不报警 (不误杀)

2026-08-03 Ship 42 — 10000 轮迭代 P4 第二步
"""
from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class ReversalResult:
    """回退检测结果"""
    n_samples: int
    historical_avg: float
    recent_avg: float
    delta: float             # recent - historical
    pct_change: float        # 百分比变化
    is_reversal: bool
    is_warning: bool
    signal: str               # "extreme_greed_drop", "extreme_fear_rise" 等
    description: str


# ═══════════════════════════════════════════════════════
# 跟踪器
# ═══════════════════════════════════════════════════════

class SentimentReversalTracker:
    """情绪回退跟踪"""
    def __init__(self, window: int = 20):
        self.window = window
        self._scores: deque = deque(maxlen=window * 2)
        self._timestamps: deque = deque(maxlen=window * 2)

    def add(self, score: float) -> None:
        """添加一个情绪分数 (0-100)"""
        import time
        self._scores.append(score)
        self._timestamps.append(time.time())

    def detect(self, *,
               warn_pct: float = 0.20,         # 20% 变化 → 警告
               reversal_pct: float = 0.30,     # 30% → 回退报警
               extreme_high: float = 75.0,     # 极度贪婪区
               extreme_low: float = 25.0,      # 极度恐惧区
               ) -> ReversalResult:
        """检测回退

        划分:
        - historical: 前 half
        - recent: 后 half

        反向信号:
        - extreme_high → 大跌前贪婪 (担心)
        - extreme_low → 大涨前恐惧 (机会)
        """
        n = len(self._scores)
        if n < self.window // 2:
            return ReversalResult(
                n_samples=n,
                historical_avg=0.0, recent_avg=0.0,
                delta=0.0, pct_change=0.0,
                is_reversal=False, is_warning=False,
                signal="", description="样本不足",
            )

        half = n // 2
        scores = list(self._scores)
        hist = scores[:half]
        curr = scores[half:]

        hist_avg = statistics.mean(hist)
        curr_avg = statistics.mean(curr)
        delta = curr_avg - hist_avg

        # 百分比变化
        if abs(hist_avg) > 0.01:
            pct = delta / hist_avg
        else:
            pct = 0.0

        is_warning = abs(pct) >= warn_pct
        is_reversal = abs(pct) >= reversal_pct

        signal = ""
        description = "情绪平稳"

        if hist_avg >= extreme_high and delta < -10:
            signal = "extreme_greed_drop"
            description = "情绪从极度贪婪骤降, 反向风险"
            is_reversal = True
            is_warning = True
        elif hist_avg <= extreme_low and delta > 10:
            signal = "extreme_fear_rise"
            description = "情绪从极度恐惧反弹, 反向机会"
            is_reversal = True
            is_warning = True
        elif pct <= -reversal_pct:
            signal = "sharp_decline"
            description = f"情绪急降 {-pct:.0%}, 警惕"
            is_reversal = True
        elif pct >= reversal_pct:
            signal = "sharp_recovery"
            description = f"情绪急升 {pct:.0%}, 关注"
            is_reversal = True
        elif pct <= -warn_pct:
            signal = "moderate_decline"
            description = f"情绪降温 {-pct:.0%}"
            is_warning = True
        elif pct >= warn_pct:
            signal = "moderate_rise"
            description = f"情绪回升 {pct:.0%}"
            is_warning = True
        else:
            description = "情绪平稳"

        return ReversalResult(
            n_samples=n,
            historical_avg=round(hist_avg, 2),
            recent_avg=round(curr_avg, 2),
            delta=round(delta, 2),
            pct_change=round(pct, 4),
            is_reversal=is_reversal,
            is_warning=is_warning,
            signal=signal,
            description=description,
        )


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def to_dict(r: ReversalResult) -> dict:
    return {
        "n_samples": r.n_samples,
        "historical_avg": r.historical_avg,
        "recent_avg": r.recent_avg,
        "delta": r.delta,
        "pct_change": r.pct_change,
        "is_reversal": r.is_reversal,
        "is_warning": r.is_warning,
        "signal": r.signal,
        "description": r.description,
    }


def current_zone(score: float) -> str:
    """当前情绪区间"""
    if score >= 80:
        return "extreme_greed"
    if score >= 65:
        return "greed"
    if score >= 55:
        return "mild_greed"
    if score >= 45:
        return "neutral"
    if score >= 35:
        return "mild_fear"
    if score >= 20:
        return "fear"
    return "extreme_fear"
