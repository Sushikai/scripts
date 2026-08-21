#!/usr/bin/env python3
"""
tuixue_v3/signal_metrics.py
Ship 19/100 — 信号质量跟踪 (Precision / Recall / IC)

设计:
跟踪一个策略发出信号后, 历史命中率, 用于:
1. 在线评估策略是否仍然有效
2. 因子 IC (Information Coefficient) 衰减检测
3. 策略淘汰依据 (precision < threshold 自动停用)

数据模型:
- Signal: {date, code, factor_name, predicted_score, actual_return}
- 累计到一定数量后, 计算 precision/recall/IC
- 用 rolling window (默认 60 日) 滑动

输出:
- SignalMetrics: 策略级聚合指标
- is_healthy: True if precision >= min_precision AND ic >= min_ic

降级: 样本不足 (< min_samples) → is_healthy=True (不误杀新策略)

2026-08-02 Ship 19 — 10000 轮迭代 P2 第九步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class Signal:
    """单条信号记录"""
    date: str
    code: str
    factor: str                # "sector_rotation" / "event" / ...
    predicted: float           # 预测分 (-1~1)
    actual: float              # 实际收益 (N 日后)
    is_win: bool = False       # actual > threshold
    is_hit: bool = False       # 预测 + 实际命中 (top N)


@dataclass
class SignalMetrics:
    """信号质量聚合"""
    factor: str
    n_samples: int
    precision: float           # TP / (TP+FP) — 预测 top 中实际 top 比例
    recall: float              # TP / (TP+FN) — 实际 top 中被预测的比例
    ic: float                  # 信息系数 = corr(predicted, actual)
    hit_rate: float            # 预测为正时实际 > threshold 比例
    avg_predicted: float
    avg_actual: float
    is_healthy: bool
    reasons: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def compute_metrics(
    signals: list[Signal],
    *,
    min_samples: int = 30,
    min_precision: float = 0.4,
    min_ic: float = 0.02,
    top_quantile: float = 0.3,   # top 30% 算 hit
    win_threshold: float = 0.0,  # actual > 0 算 win
) -> SignalMetrics:
    """计算信号质量指标

    Args:
        signals: 信号记录列表
        min_samples: 最少样本数, 不足返回 healthy=True (兜底)
        min_precision: 健康线, 低于此 unhealthy
        min_ic: IC 健康线
        top_quantile: top N% 算预测/实际命中
        win_threshold: actual > X 算 win

    Returns:
        SignalMetrics
    """
    if not signals:
        return SignalMetrics(
            factor="", n_samples=0,
            precision=0.0, recall=0.0, ic=0.0,
            hit_rate=0.0, avg_predicted=0.0, avg_actual=0.0,
            is_healthy=True,
            reasons=["无信号数据"],
        )

    factor_name = signals[0].factor
    n = len(signals)

    if n < min_samples:
        return SignalMetrics(
            factor=factor_name, n_samples=n,
            precision=0.0, recall=0.0, ic=0.0,
            hit_rate=0.0,
            avg_predicted=round(statistics.mean(s.predicted for s in signals), 4),
            avg_actual=round(statistics.mean(s.actual for s in signals), 4),
            is_healthy=True,
            reasons=[f"样本不足 ({n} < {min_samples}), 默认健康"],
        )

    # 计算 predicted / actual
    pred = [s.predicted for s in signals]
    act = [s.actual for s in signals]

    # IC: 皮尔森相关系数
    ic = _pearson(pred, act)

    # Top N% 命中
    k = max(1, int(n * top_quantile))
    pred_top_idx = set(_topk_indices(pred, k))
    act_top_idx = set(_topk_indices(act, k))
    tp = len(pred_top_idx & act_top_idx)
    fp = len(pred_top_idx - act_top_idx)
    fn = len(act_top_idx - pred_top_idx)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # Hit rate: predicted > 0 时 actual > threshold 的比例
    pos_preds = [s for s in signals if s.predicted > 0]
    if pos_preds:
        hits = sum(1 for s in pos_preds if s.actual > win_threshold)
        hit_rate = hits / len(pos_preds)
    else:
        hit_rate = 0.0

    # 健康判断
    healthy = precision >= min_precision and ic >= min_ic
    reasons = []
    if not healthy:
        if precision < min_precision:
            reasons.append(f"precision {precision:.2%} < {min_precision:.0%}")
        if ic < min_ic:
            reasons.append(f"IC {ic:.3f} < {min_ic}")

    return SignalMetrics(
        factor=factor_name,
        n_samples=n,
        precision=round(precision, 4),
        recall=round(recall, 4),
        ic=round(ic, 4),
        hit_rate=round(hit_rate, 4),
        avg_predicted=round(statistics.mean(pred), 4),
        avg_actual=round(statistics.mean(act), 4),
        is_healthy=healthy,
        reasons=reasons or ["所有指标达标"],
    )


def update_signal_outcomes(
    signals: list[Signal],
    outcomes: dict[tuple[str, str], float],
) -> list[Signal]:
    """根据 outcomes 更新信号的 actual / is_win / is_hit

    Args:
        signals: 待更新信号列表
        outcomes: {(date, code): actual_return}

    Returns:
        更新后的 signals (新列表, 不修改原列表)
    """
    updated = []
    for s in signals:
        key = (s.date, s.code)
        actual = outcomes.get(key, s.actual)
        is_win = actual > 0
        updated.append(Signal(
            date=s.date, code=s.code, factor=s.factor,
            predicted=s.predicted, actual=actual,
            is_win=is_win, is_hit=is_win,  # is_hit 由 is_win 简化代替
        ))
    return updated


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _pearson(x: list[float], y: list[float]) -> float:
    """皮尔森相关系数"""
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


def _topk_indices(arr: list[float], k: int) -> list[int]:
    """返回 top-k 大元素的索引 (从大到小)"""
    indexed = sorted(enumerate(arr), key=lambda x: x[1], reverse=True)
    return [i for i, _ in indexed[:k]]
