#!/usr/bin/env python3
"""
tuixue_v3/sentiment_factor_fusion.py
Ship 51/100 — 情绪 × 因子融合分数

设计:
把情绪分位带 + 因子 IC 综合成 alpha 调整系数:
- sentiment_factor: 当前情绪分位 (0-1)
- factor_ic: 因子 IC (0-1, 归一化)
- 综合: adj_alpha = base_alpha × fusion_coefficient

fusion_coefficient ∈ [0.6, 1.4]:
- 极度贪婪 + 强因子 → boost
- 极度恐惧 + 弱因子 → suppress
- 中性 + 中性 → 1.0

降级: 缺数据 → fusion = 1.0

2026-08-03 Ship 51 — 10000 轮迭代 P4 第十一步
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class FusionResult:
    """融合结果"""
    base_alpha: float
    sentiment_pct: float
    factor_ic_norm: float
    fusion: float
    adjusted_alpha: float
    regime: str

    def to_dict(self) -> dict:
        return {
            "base_alpha": self.base_alpha,
            "sentiment_pct": self.sentiment_pct,
            "factor_ic_norm": self.factor_ic_norm,
            "fusion": self.fusion,
            "adjusted_alpha": self.adjusted_alpha,
            "regime": self.regime,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _ic_to_unit(ic: float) -> float:
    """-1..1 → 0..1"""
    return (ic + 1) / 2.0


# ═══════════════════════════════════════════════════════
# 融合
# ═══════════════════════════════════════════════════════

def fuse(
    base_alpha: float,
    sentiment_pct: float,
    factor_ic: float,
    *,
    min_fusion: float = 0.6,
    max_fusion: float = 1.4,
) -> FusionResult:
    """计算融合

    Args:
        base_alpha: 基础 alpha (e.g. 1.0)
        sentiment_pct: 0-1 (0=极恐惧, 0.5=中性, 1=极贪婪)
        factor_ic: 因子 IC (in [-1, 1])
    """
    ic_norm = _ic_to_unit(factor_ic)
    sign = 1 if factor_ic >= 0 else -1
    ic_strength = abs(factor_ic)

    # 调整方向:
    # 正 IC + fear → 加成 (反向: 越恐惧越该 buy)
    # 正 IC + greed → 抑制 (反向: 越贪婪越该 sell)
    # 负 IC → 反过来
    if sign > 0:
        adjustment = -(sentiment_pct - 0.5) * ic_strength * 0.8
    else:
        adjustment = (sentiment_pct - 0.5) * ic_strength * 0.8

    fusion = 1.0 + adjustment
    fusion = max(min_fusion, min(max_fusion, fusion))

    if fusion > 1.05:
        regime = "boost"
    elif fusion < 0.95:
        regime = "suppress"
    else:
        regime = "neutral"

    return FusionResult(
        base_alpha=base_alpha,
        sentiment_pct=round(sentiment_pct, 4),
        factor_ic_norm=round(ic_norm, 4),
        fusion=round(fusion, 4),
        adjusted_alpha=round(base_alpha * fusion, 4),
        regime=regime,
    )


def fuse_multi(
    sentiment_pct: float,
    ics: dict[str, float],
    factor_weights: Optional[dict[str, float]] = None,
) -> float:
    """多因子加权融合 → 单一调整系数"""
    if not ics:
        return 1.0
    if factor_weights is None:
        n = len(ics)
        factor_weights = {f: 1.0 / n for f in ics}

    # 加权 IC
    weighted_ic = sum(factor_weights.get(f, 0) * ic for f, ic in ics.items())
    return fuse(1.0, sentiment_pct, weighted_ic).fusion


# ═══════════════════════════════════════════════════════
# 反向因子 (有时情绪和 IC 反着来)
# ═══════════════════════════════════════════════════════

def reverse_fuse(
    base_alpha: float,
    sentiment_pct: float,
    factor_ic: float,
) -> FusionResult:
    """反向融合: 情绪强时反向加仓"""
    # 与 fuse() 相反方向
    return fuse(base_alpha, 1.0 - sentiment_pct, factor_ic)
