#!/usr/bin/env python3
"""
tuixue_v3/strat_era01_adaptive.py
Ship 57/100 — 量化 era 2026 高级策略 #1

Adaptive Regime-Aware Strategy (自适应 regime 感知策略)

设计:
根据当前市场 regime 选择权重:
- bull regime: 高 beta + 高动量
- bear regime: 低 beta + 反向
- crisis regime: 全 cash + 防御
- range: 均值回归

输入:
- regime (str): bull/bear/range/crisis
- regime_factor (float): 0.5 - 1.5
- factor_weights (dict): 各因子权重
- candidates (list): 候选股票

输出: 排序后的 pick 列表 (含权重)

降级: 缺数据 → 零分配

2026-08-03 Ship 57 — 10000 轮迭代 P5 第二步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# Regime 偏好
# ═══════════════════════════════════════════════════════

REGIME_PREFERENCE = {
    "bull":   {"mom": 1.5, "val": 0.5, "vol": 0.7, "size": 0.5},
    "bear":   {"mom": 0.5, "val": 1.2, "vol": 0.5, "size": 1.5},
    "range":  {"mom": 0.5, "val": 1.5, "vol": 1.2, "size": 1.0},
    "crisis": {"mom": 0.0, "val": 0.5, "vol": 0.3, "size": 1.8},
    "unknown": {"mom": 1.0, "val": 1.0, "vol": 1.0, "size": 1.0},
}


@dataclass
class AdaptivePick:
    code: str
    name: str
    raw_score: float
    adaptive_score: float
    weight: float
    preferred: str


@dataclass
class AdaptiveResult:
    regime: str
    regime_factor: float
    picks: list[AdaptivePick]
    total_weight: float

    def top_n(self, n: int = 5) -> list[AdaptivePick]:
        return self.picks[:n]


# ═══════════════════════════════════════════════════════
# 计算
# ═══════════════════════════════════════════════════════

def adaptive_score(
    raw_score: float,
    factor_exposures: dict[str, float],
    regime: str,
    regime_factor: float = 1.0,
) -> float:
    """根据 regime 调整分数

    Args:
        raw_score: 因子 raw score
        factor_exposures: 因子暴露 {factor_name: exposure}
        regime: 当前 regime
        regime_factor: regime 强度 0.5 - 1.5
    """
    pref = REGIME_PREFERENCE.get(regime, REGIME_PREFERENCE["unknown"])

    # 加权
    multiplier = 1.0
    for factor, exposure in factor_exposures.items():
        bias = pref.get(factor, 1.0)
        multiplier *= bias ** exposure if isinstance(exposure, (int, float)) else 1.0

    # regime 强度缩放
    final = raw_score * multiplier * regime_factor
    return final


def select_picks(
    regime: str,
    candidates: list[dict],
    regime_factor: float = 1.0,
    *,
    max_picks: int = 10,
) -> AdaptiveResult:
    """从 candidates 选 picks

    Args:
        candidates: [{code, name, raw_score, factor_exposures}, ...]
    """
    pref = REGIME_PREFERENCE.get(regime, REGIME_PREFERENCE["unknown"])

    scored = []
    for c in candidates:
        raw = c.get("raw_score", 0.0)
        exposures = c.get("factor_exposures", {})

        # 自适应分
        multiplier = 1.0
        for factor, exposure in exposures.items():
            bias = pref.get(factor, 1.0)
            try:
                multiplier *= bias ** float(exposure)
            except Exception:
                continue

        adaptive = raw * multiplier * regime_factor

        scored.append(AdaptivePick(
            code=c.get("code", ""),
            name=c.get("name", ""),
            raw_score=raw,
            adaptive_score=round(adaptive, 4),
            weight=0.0,   # 后面归一化
            preferred=regime,
        ))

    # 排序
    scored.sort(key=lambda p: p.adaptive_score, reverse=True)
    top = scored[:max_picks]

    # 归一化权重 (softmax 简化: 直接归一化到 1)
    if top:
        total_abs = sum(abs(p.adaptive_score) for p in top) or 1.0
        for p in top:
            p.weight = round(abs(p.adaptive_score) / total_abs, 4)

    return AdaptiveResult(
        regime=regime,
        regime_factor=regime_factor,
        picks=scored,
        total_weight=sum(p.weight for p in top),
    )


# ═══════════════════════════════════════════════════════
# Regime 动态切换
# ═══════════════════════════════════════════════════════

def best_regime_for_score(scores_by_regime: dict[str, float]) -> str:
    """返回分数最高的 regime"""
    if not scores_by_regime:
        return "unknown"
    return max(scores_by_regime.items(), key=lambda kv: kv[1])[0]


def regime_suitability(
    factor_exposures: dict[str, float],
    regime: str,
) -> float:
    """某只股票在某 regime 的适合度 [0, 1]"""
    pref = REGIME_PREFERENCE.get(regime, REGIME_PREFERENCE["unknown"])
    suitability = 0.0
    total_abs = 0.0
    for factor, exposure in factor_exposures.items():
        bias = pref.get(factor, 1.0)
        suitability += float(exposure) * bias
        total_abs += abs(float(exposure))
    if total_abs == 0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + suitability / total_abs * 0.5))


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def to_dict(r: AdaptiveResult) -> dict:
    return {
        "regime": r.regime,
        "regime_factor": r.regime_factor,
        "total_weight": r.total_weight,
        "picks": [
            {
                "code": p.code, "name": p.name,
                "raw_score": p.raw_score,
                "adaptive_score": p.adaptive_score,
                "weight": p.weight,
                "preferred": p.preferred,
            }
            for p in r.picks
        ],
    }
