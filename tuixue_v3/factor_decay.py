#!/usr/bin/env python3
"""
tuixue_v3/factor_decay.py
Ship 34/100 — 因子衰减检测

设计:
跟踪一个因子的 IC 时序, 检测衰减:
- 用 rolling window (近 60 日) IC 对比 历史 (前 60 日) IC
- 衰减率 = (recent_ic - historical_ic) / abs(historical_ic)
- 衰减 > 50% → 警告, > 80% → 弃用

输出:
- DecayResult: factor, current_ic, historical_ic, decay_pct, is_decayed, reason

降级: 样本不足 (< 60) → is_decayed=False (不误杀)

2026-08-02 Ship 34 — 10000 轮迭代 P3 第九步
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
class DecayResult:
    """衰减检测结果"""
    factor: str
    n_samples: int
    current_ic: float          # 近 window IC
    historical_ic: float       # 之前 window IC
    decay_pct: float           # 衰减率 (正=衰减, 负=增强)
    is_warning: bool
    is_decayed: bool           # 弃用
    reasons: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# 跟踪器
# ═══════════════════════════════════════════════════════

class FactorDecayTracker:
    """因子 IC 时序跟踪"""
    def __init__(self, factor_name: str, window: int = 60):
        self.factor_name = factor_name
        self.window = window
        self._predictions: deque = deque(maxlen=window * 2)
        self._actuals: deque = deque(maxlen=window * 2)

    def add(self, predicted: float, actual: float) -> None:
        """添加一对 (predicted, actual)"""
        self._predictions.append(predicted)
        self._actuals.append(actual)

    def detect_decay(self, *,
                     warn_decay: float = 0.5,
                     severe_decay: float = 0.8) -> DecayResult:
        """检测衰减

        Args:
            warn_decay: 衰减 > 此值 → 警告 (0.5 = 50%)
            severe_decay: 衰减 > 此值 → 弃用 (0.8 = 80%)
        """
        n = len(self._predictions)
        if n < self.window:
            return DecayResult(
                factor=self.factor_name, n_samples=n,
                current_ic=0.0, historical_ic=0.0, decay_pct=0.0,
                is_warning=False, is_decayed=False,
                reasons=[f"样本不足 ({n} < {self.window})"],
            )

        # 分两半: historical (前半), current (后半)
        half = n // 2
        pred_list = list(self._predictions)
        act_list = list(self._actuals)

        hist_pred = pred_list[:half]
        hist_act = act_list[:half]
        curr_pred = pred_list[half:]
        curr_act = act_list[half:]

        hist_ic = _pearson(hist_pred, hist_act)
        curr_ic = _pearson(curr_pred, curr_act)

        # 衰减率
        if abs(hist_ic) > 0.01:
            decay = (hist_ic - curr_ic) / abs(hist_ic)
        else:
            # historical IC 接近 0 → 不可比, 用绝对差
            decay = hist_ic - curr_ic

        reasons = []
        is_warning = False
        is_decayed = False

        if decay >= severe_decay:
            is_decayed = True
            reasons.append(f"IC 衰减 {decay:.0%} > {severe_decay:.0%}, 弃用")
        elif decay >= warn_decay:
            is_warning = True
            reasons.append(f"IC 衰减 {decay:.0%} > {warn_decay:.0%}, 警告")

        # 翻转 (sign change): hist > 0, current < 0 → 严格衰减
        if hist_ic > 0.02 and curr_ic < -0.02:
            is_decayed = True
            reasons.append(f"IC 翻转 ({hist_ic:.3f} → {curr_ic:.3f})")

        return DecayResult(
            factor=self.factor_name, n_samples=n,
            current_ic=round(curr_ic, 4),
            historical_ic=round(hist_ic, 4),
            decay_pct=round(decay, 4),
            is_warning=is_warning, is_decayed=is_decayed,
            reasons=reasons or ["IC 稳定"],
        )


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

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


def to_dict(result: DecayResult) -> dict:
    return {
        "factor": result.factor,
        "n_samples": result.n_samples,
        "current_ic": result.current_ic,
        "historical_ic": result.historical_ic,
        "decay_pct": result.decay_pct,
        "is_warning": result.is_warning,
        "is_decayed": result.is_decayed,
        "reasons": result.reasons,
    }
