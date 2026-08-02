#!/usr/bin/env python3
"""
tuixue_v3/strategy_tuner.py
Ship 32/100 — 策略权重动态调整

设计:
基于 signal_metrics 自动调整策略权重:
- precision/IC 高 → 加权
- unhealthy → 降权或禁用
- 样本不足 → 不变 (cold start)

输出: {strategy: new_weight}
触发: 周期性 (日/周) 跑一次

降级: 策略无 metrics → 跳过; 健康策略无 metrics → 不变

2026-08-02 Ship 32 — 10000 轮迭代 P3 第七步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .signal_metrics import SignalMetrics

logger = logging.getLogger(__name__)


@dataclass
class StrategyTuningResult:
    """调权结果"""
    new_weights: dict[str, float]
    disabled: list[str]
    boosted: list[str]
    reduced: list[str]
    reasons: dict[str, str]


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def tune_weights(
    current_weights: dict[str, float],
    metrics: dict[str, SignalMetrics],
    *,
    precision_boost: float = 0.3,
    ic_boost: float = 0.2,
    unhealthy_penalty: float = 0.0,   # 直接禁用
    min_samples: int = 30,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
) -> StrategyTuningResult:
    """根据 signal metrics 调权

    Args:
        current_weights: {strategy: weight} (默认各策略相等)
        metrics: {strategy: SignalMetrics}
        precision_boost: precision 每超 0.1 加权幅度
        ic_boost: IC 每超 0.01 加权幅度
        unhealthy_penalty: 0=禁用, >0=惩罚
        min_samples: 低于此样本不调整
        min/max_weight: 权重上下限

    Returns:
        StrategyTuningResult
    """
    new_weights = dict(current_weights)
    disabled: list[str] = []
    boosted: list[str] = []
    reduced: list[str] = []
    reasons: dict[str, str] = {}

    for name, m in metrics.items():
        if m.n_samples < min_samples:
            reasons[name] = f"样本不足 ({m.n_samples} < {min_samples})"
            continue

        if not m.is_healthy:
            if unhealthy_penalty == 0:
                new_weights[name] = 0.0
                disabled.append(name)
                reasons[name] = f"unhealthy: {m.reasons}"
                continue
            else:
                factor = unhealthy_penalty
                new_weights[name] = new_weights.get(name, 0.5) * factor
                reduced.append(name)
                reasons[name] = f"unhealthy × {factor}"

        # 健康 → 基于 precision + ic 加权
        # precision > 0.5 → boost; < 0.4 → reduce
        if m.precision > 0.5:
            factor = 1 + (m.precision - 0.5) * precision_boost * 10
            new_weights[name] = new_weights.get(name, 0.5) * factor
            boosted.append(name)
            reasons[name] = f"precision {m.precision:.0%} → × {factor:.2f}"
        elif m.precision < 0.4 and m.precision > 0:
            factor = max(0.3, 1 - (0.4 - m.precision) * 2)
            new_weights[name] = new_weights.get(name, 0.5) * factor
            reduced.append(name)
            reasons[name] = f"precision {m.precision:.0%} → × {factor:.2f}"

        # IC 调整
        if m.ic > 0.05:
            factor = 1 + (m.ic - 0.05) * ic_boost * 20
            new_weights[name] = new_weights.get(name, 0.5) * factor
        elif m.ic < 0 and m.ic != 0:
            factor = max(0.3, 1 + m.ic * 5)
            new_weights[name] = new_weights.get(name, 0.5) * factor

    # 上下限 + 归一化
    new_weights = {k: max(min_weight, min(max_weight, v))
                    for k, v in new_weights.items()}
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {k: v / total for k, v in new_weights.items()}

    return StrategyTuningResult(
        new_weights=new_weights,
        disabled=disabled,
        boosted=boosted,
        reduced=reduced,
        reasons=reasons,
    )


def to_dict(result: StrategyTuningResult) -> dict:
    return {
        "new_weights": result.new_weights,
        "disabled": result.disabled,
        "boosted": result.boosted,
        "reduced": result.reduced,
        "reasons": result.reasons,
    }
