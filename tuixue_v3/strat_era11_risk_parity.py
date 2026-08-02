#!/usr/bin/env python3
"""
tuixue_v3/strat_era11_risk_parity.py
Ship 67/100 — 量化 era 2026 高级策略 #11

Risk Parity Strategy (风险平价策略)

设计:
每个资产分配相同风险贡献 (= weight × volatility):
- weight_i = (1/vol_i) / Σ(1/vol_j)
- 适用于 vol 估计可靠的 universe
- 可选 leverage cap / vol targeting

输入: {code: list[float]} 价格时序
输出: weight 字典

降级: 数据不足 → 平均分配

2026-08-03 Ship 67 — 10000 轮迭代 P5 第十二步
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
class RiskParityResult:
    weights: dict[str, float]
    vols: dict[str, float]
    risk_contribs: dict[str, float]      # 各资产对组合风险的贡献
    total_risk: float
    n_assets: int

    def to_dict(self) -> dict:
        return {
            "weights": self.weights,
            "vols": self.vols,
            "risk_contribs": self.risk_contribs,
            "total_risk": self.total_risk,
            "n_assets": self.n_assets,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def asset_vol(prices: list[float], window: int = 20) -> Optional[float]:
    """单资产波动率"""
    if len(prices) < window + 1:
        return None
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - window, len(prices))
            if prices[i - 1] > 0]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


# ═══════════════════════════════════════════════════════
# 风险平价
# ═══════════════════════════════════════════════════════

def risk_parity_weights(
    vols: dict[str, float],
    *,
    min_vol: float = 1e-6,
) -> dict[str, float]:
    """根据 vol 反比分配权重"""
    if not vols:
        return {}

    inv_vols = {code: 1.0 / max(v, min_vol) for code, v in vols.items()}
    total = sum(inv_vols.values())
    if total <= 0:
        # 全部为零, 平均分配
        n = len(vols)
        return {code: 1.0 / n for code in vols}

    return {code: iv / total for code, iv in inv_vols.items()}


def compute_risk_contribs(
    weights: dict[str, float],
    vols: dict[str, float],
) -> dict[str, float]:
    """每个资产对组合风险的贡献 = weight * vol"""
    return {code: weights.get(code, 0) * vols.get(code, 0)
            for code in weights}


# ═══════════════════════════════════════════════════════
# Vol Targeting
# ═══════════════════════════════════════════════════════

def vol_targeting_weights(
    vols: dict[str, float],
    *,
    target_vol: float = 0.10,        # 10% 年化
    max_leverage: float = 2.0,
) -> dict[str, float]:
    """波动率目标权重: 缩放到目标波动率"""
    rp = risk_parity_weights(vols)
    if not rp:
        return {}

    # 组合波动率 = Σ(w_i * vol_i) (简化, 假设零相关)
    port_vol = sum(rp[c] * vols[c] for c in rp)
    if port_vol <= 0:
        return rp

    leverage = target_vol / port_vol
    leverage = min(leverage, max_leverage)

    return {c: w * leverage for c, w in rp.items()}


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def allocate(
    universe: dict[str, list[float]],
    *,
    window: int = 20,
    target_vol: float = 0.10,
    use_vol_targeting: bool = True,
    max_leverage: float = 2.0,
) -> RiskParityResult:
    """主入口: 计算风险平价配置"""
    vols = {}
    for code, prices in universe.items():
        v = asset_vol(prices, window=window)
        if v is not None:
            vols[code] = v

    if not vols:
        return RiskParityResult(
            weights={}, vols={}, risk_contribs={},
            total_risk=0.0, n_assets=0,
        )

    if use_vol_targeting:
        weights = vol_targeting_weights(
            vols, target_vol=target_vol, max_leverage=max_leverage,
        )
    else:
        weights = risk_parity_weights(vols)

    contribs = compute_risk_contribs(weights, vols)
    total_risk = sum(contribs.values())

    return RiskParityResult(
        weights={k: round(v, 4) for k, v in weights.items()},
        vols={k: round(v, 4) for k, v in vols.items()},
        risk_contribs={k: round(v, 4) for k, v in contribs.items()},
        total_risk=round(total_risk, 4),
        n_assets=len(weights),
    )


# ═══════════════════════════════════════════════════════
# Rebalance 检测
# ═══════════════════════════════════════════════════════

def rebalance_needed(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    *,
    threshold: float = 0.05,
) -> tuple[bool, dict[str, float]]:
    """检测是否需要再平衡

    Returns: (needs_rebalance, deltas)
    """
    all_codes = set(current_weights) | set(target_weights)
    deltas = {}
    needs = False

    for code in all_codes:
        cur = current_weights.get(code, 0)
        tgt = target_weights.get(code, 0)
        d = tgt - cur
        deltas[code] = round(d, 4)
        if abs(d) > threshold:
            needs = True

    return needs, deltas


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: RiskParityResult, top_k: int = 10) -> str:
    out = [f"Risk Parity ({r.n_assets} assets, total risk={r.total_risk:.3f}):"]
    items = sorted(r.weights.items(), key=lambda x: -x[1])[:top_k]
    for code, w in items:
        v = r.vols.get(code, 0)
        rc = r.risk_contribs.get(code, 0)
        out.append(f"  {code}: w={w:.2%} vol={v:.3f} rc={rc:.3f}")
    return "\n".join(out)