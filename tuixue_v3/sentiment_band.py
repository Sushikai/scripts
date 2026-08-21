#!/usr/bin/env python3
"""
tuixue_v3/sentiment_band.py
Ship 46/100 — 情绪分位带 (Sentiment Percentile Band)

设计:
跟踪情绪分数的分位区间:
- p10/p25/p50/p75/p90 五条线
- 当前分位位置
- 历史 vs 当前分位差距 → 异常识别

输入: 时序情绪分数
输出: 分位带 + 当前 percentile rank

降级: 样本不足 → 用 [0,100] 默认分位

2026-08-03 Ship 46 — 10000 轮迭代 P4 第六步
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
class PercentileBand:
    """分位带"""
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    n: int
    current: float
    current_pct: float       # 当前在历史中的百分位
    is_top_band: bool        # >= p90
    is_bottom_band: bool     # <= p10

    def summary(self) -> str:
        return (
            f"P10={self.p10:.0f} P25={self.p25:.0f} "
            f"P50={self.p50:.0f} P75={self.p75:.0f} P90={self.p90:.0f} "
            f"current={self.current:.0f} ({self.current_pct:.0%})"
        )


# ═══════════════════════════════════════════════════════
# 跟踪器
# ═══════════════════════════════════════════════════════

class SentimentBandTracker:
    """情绪分位带跟踪"""
    def __init__(self, window: int = 60):
        self.window = window
        self._buf: deque = deque(maxlen=window)
        self._current: Optional[float] = None

    def add(self, score: float) -> None:
        self._buf.append(score)
        self._current = score

    def set_current(self, score: float) -> None:
        self._current = score

    def compute(self) -> PercentileBand:
        n = len(self._buf)
        if n == 0:
            return PercentileBand(
                p10=10, p25=25, p50=50, p75=75, p90=90,
                n=0,
                current=self._current or 50,
                current_pct=0.5,
                is_top_band=False, is_bottom_band=False,
            )

        sorted_buf = sorted(self._buf)

        def pct(p):
            if n == 1:
                return sorted_buf[0]
            idx = int((p / 100) * (n - 1))
            idx = max(0, min(idx, n - 1))
            return sorted_buf[idx]

        p10 = pct(10)
        p25 = pct(25)
        p50 = pct(50)
        p75 = pct(75)
        p90 = pct(90)

        # 当前在历史百分位 (多少 % ≤ current)
        if self._current is None:
            current_pct = 0.5
        else:
            less = sum(1 for v in sorted_buf if v < self._current)
            eq = sum(1 for v in sorted_buf if v == self._current)
            current_pct = (less + 0.5 * eq) / n

        is_top = self._current is not None and self._current >= p90
        is_bot = self._current is not None and self._current <= p10

        return PercentileBand(
            p10=round(p10, 2),
            p25=round(p25, 2),
            p50=round(p50, 2),
            p75=round(p75, 2),
            p90=round(p90, 2),
            n=n,
            current=self._current if self._current is not None else p50,
            current_pct=round(current_pct, 4),
            is_top_band=is_top,
            is_bottom_band=is_bot,
        )


def compute_band_static(scores: list[float], current: Optional[float] = None) -> PercentileBand:
    """静态计算分位带 (不维护 buffer)"""
    t = SentimentBandTracker(window=len(scores) or 60)
    for s in scores:
        t.add(s)
    if current is not None:
        t.set_current(current)
    return t.compute()


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def to_dict(b: PercentileBand) -> dict:
    return {
        "p10": b.p10, "p25": b.p25, "p50": b.p50,
        "p75": b.p75, "p90": b.p90, "n": b.n,
        "current": b.current, "current_pct": b.current_pct,
        "is_top_band": b.is_top_band,
        "is_bottom_band": b.is_bottom_band,
    }


def signal_from_band(b: PercentileBand) -> str:
    """根据分位位置给信号"""
    if b.is_top_band:
        return "extreme_high"
    if b.is_bottom_band:
        return "extreme_low"
    if b.current_pct >= 0.8:
        return "high"
    if b.current_pct <= 0.2:
        return "low"
    return "normal"


def band_width(b: PercentileBand) -> float:
    """带宽 P90-P10"""
    return b.p90 - b.p10
