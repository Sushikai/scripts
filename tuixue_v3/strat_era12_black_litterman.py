#!/usr/bin/env python3
"""
tuixue_v3/strat_era12_black_litterman.py
Ship 68/100 — 量化 era 2026 高级策略 #12

Black-Litterman Lite Strategy

设计:
Black-Litterman 模型精简版:
- 先验: 市场均衡收益 (reverse optimization)
- 主观观点: 用户提供的 view (alpha, confidence)
- 后验: 混合权重 = (τΣ)^-1 μ_prior + P^T Ω^-1 q
       μ_posterior ∝ inverse-variance weighted blend

为简化, 直接采用 view 强度加权混合:
- μ_posterior_i = w * μ_view_i + (1-w) * μ_prior_i

输入: prior_returns (dict), views (dict {code: alpha}), confidence (dict)
输出: posterior returns + 推荐权重

降级: views 为空 → 仅用 prior

2026-08-03 Ship 68 — 10000 轮迭代 P5 第十三步
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
class BLResult:
    posterior_returns: dict[str, float]
    weights: dict[str, float]            # 建议权重 (按 posterior 排序)
    confidence_blend: dict[str, float]   # 每个资产的 confidence blend 比例
    n_views: int
    n_prior: int

    def to_dict(self) -> dict:
        return {
            "posterior_returns": self.posterior_returns,
            "weights": self.weights,
            "confidence_blend": self.confidence_blend,
            "n_views": self.n_views,
            "n_prior": self.n_prior,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def market_implied_prior(
    market_caps: dict[str, float],
    risk_aversion: float = 2.5,
    vols: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """市场隐含均衡收益 = risk_aversion * cap_weight * vol^2"""
    total_cap = sum(market_caps.values())
    if total_cap <= 0:
        return {}

    priors = {}
    for code, cap in market_caps.items():
        w = cap / total_cap
        v = vols.get(code, 0.20) if vols else 0.20
        priors[code] = risk_aversion * w * (v ** 2)

    return priors


def combine_views(
    prior: dict[str, float],
    views: dict[str, float],
    confidences: Optional[dict[str, float]] = None,
    *,
    default_confidence: float = 0.5,
) -> dict[str, tuple[float, float]]:
    """混合 prior 与 view

    Returns: {code: (posterior, blend_w)}

    若 code 不在 views 中, posterior = prior (无 view 干预)
    若 code 在 views 中, posterior = c * view + (1-c) * prior
    """
    out = {}
    all_codes = set(prior) | set(views)

    for code in all_codes:
        p = prior.get(code, 0.0)
        if code in views:
            v = views[code]
            c = confidences.get(code, default_confidence) if confidences else default_confidence
            c = max(0.0, min(1.0, c))
            posterior = c * v + (1 - c) * p
            blend_w = c
        else:
            # 无 view, posterior = prior
            posterior = p
            blend_w = 0.0
        out[code] = (posterior, blend_w)

    return out


# ═══════════════════════════════════════════════════════
# 权重 (最大化 posterior / vol)
# ═══════════════════════════════════════════════════════

def posterior_weights(
    posterior: dict[str, float],
    vols: dict[str, float],
    *,
    min_weight: float = 0.0,
) -> dict[str, float]:
    """最大化 risk-adj return: weight ∝ posterior / vol"""
    if not posterior:
        return {}

    scores = {}
    for code, ret in posterior.items():
        v = vols.get(code, 0.20)
        if v <= 0:
            v = 0.20
        scores[code] = max(ret / v, 0.0)

    total = sum(scores.values())
    if total <= 0:
        # 全部 ≤ 0, 平均分配
        n = len(posterior)
        return {c: 1.0 / n for c in posterior}

    weights = {c: s / total for c, s in scores.items()}

    # 应用最小权重
    if min_weight > 0:
        diff = len(posterior) * min_weight
        if diff < 1.0:
            for c in weights:
                weights[c] = weights[c] * (1 - diff) + min_weight

    return weights


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def optimize(
    market_caps: dict[str, float],
    views: dict[str, float],
    confidences: Optional[dict[str, float]] = None,
    *,
    vols: Optional[dict[str, float]] = None,
    risk_aversion: float = 2.5,
    default_confidence: float = 0.5,
    min_weight: float = 0.0,
) -> BLResult:
    """主入口

    - market_caps: 各资产市值 (用于 prior)
    - views: 用户观点 {code: alpha}
    - confidences: 各观点置信度
    - vols: 各资产波动率
    """
    prior = market_implied_prior(market_caps, risk_aversion=risk_aversion, vols=vols)
    combined = combine_views(
        prior, views, confidences,
        default_confidence=default_confidence,
    )

    posterior = {c: round(p, 6) for c, (p, _) in combined.items()}
    blend = {c: round(b, 4) for c, (_, b) in combined.items()}

    eff_vols = vols if vols else {c: 0.20 for c in posterior}
    weights = posterior_weights(posterior, eff_vols, min_weight=min_weight)

    return BLResult(
        posterior_returns=posterior,
        weights={k: round(v, 4) for k, v in weights.items()},
        confidence_blend=blend,
        n_views=len(views),
        n_prior=len(prior),
    )


# ═══════════════════════════════════════════════════════
# View 生成器 (基于动量)
# ═══════════════════════════════════════════════════════

def momentum_views(
    universe: dict[str, list[float]],
    *,
    lookback: int = 60,
) -> dict[str, float]:
    """基于动量生成 view (return)"""
    views = {}
    for code, prices in universe.items():
        if len(prices) < lookback + 1:
            continue
        past = prices[-(lookback + 1)]
        curr = prices[-1]
        if past <= 0:
            continue
        ret = (curr - past) / past
        views[code] = ret
    return views


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: BLResult, top_k: int = 10) -> str:
    out = [f"BL (views={r.n_views}, prior={r.n_prior}):"]
    items = sorted(r.weights.items(), key=lambda x: -x[1])[:top_k]
    for code, w in items:
        ret = r.posterior_returns.get(code, 0)
        c = r.confidence_blend.get(code, 0)
        out.append(f"  {code}: w={w:.2%} post={ret:+.3f} conf={c:.0%}")
    return "\n".join(out)