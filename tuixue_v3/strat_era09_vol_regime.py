#!/usr/bin/env python3
"""
tuixue_v3/strat_era09_vol_regime.py
Ship 65/100 — 量化 era 2026 高级策略 #9

Volatility Regime Strategy (波动率体制策略)

设计:
检测当前波动率体制:
- low_vol: 平稳期, 适合趋势 / 动量
- high_vol: 剧烈期, 适合均值回归 / 反转
- crisis: 极端, 防御为主

依据历史波动率的分位数判定。
不同 regime 应用不同策略权重。

输入: {code: list[float]} 价格时序
输出: regime + 建议策略

2026-08-03 Ship 65 — 10000 轮迭代 P5 第十步
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
class VolRegime:
    code: str
    regime: str                # "low_vol" / "normal" / "high_vol" / "crisis"
    current_vol: float         # 当前波动率
    historical_pct: float      # 当前 vol 在历史分位数 (0-1)
    vol_z: float               # vol 的 z-score
    recommended: str           # "trend" / "reversion" / "defensive" / "neutral"
    size_factor: float         # 仓位调整因子 (0-1)
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "regime": self.regime,
            "current_vol": self.current_vol,
            "historical_pct": self.historical_pct,
            "vol_z": self.vol_z,
            "recommended": self.recommended,
            "size_factor": self.size_factor,
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def realized_vol(prices: list[float], window: int = 20) -> Optional[float]:
    """实现波动率 (年化)"""
    if len(prices) < window + 1:
        return None
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - window, len(prices))
            if prices[i - 1] > 0]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def vol_series(prices: list[float], window: int = 20) -> list[float]:
    """波动率时序 (rolling)"""
    out = []
    for i in range(window, len(prices)):
        sub = prices[i - window:i + 1]
        rets = [(sub[j] - sub[j - 1]) / sub[j - 1]
                for j in range(1, len(sub)) if sub[j - 1] > 0]
        if len(rets) >= 2:
            out.append(statistics.stdev(rets))
    return out


# ═══════════════════════════════════════════════════════
# Regime 判定
# ═══════════════════════════════════════════════════════

def classify_regime(vol: float, vol_history: list[float]) -> tuple[str, float, float]:
    """根据历史 vol 判定当前 regime

    Returns: (regime, percentile, z_score)
    """
    if not vol_history:
        return "normal", 0.5, 0.0

    n = len(vol_history)
    sorted_v = sorted(vol_history)
    rank = sum(1 for v in sorted_v if v <= vol)
    pct = rank / n if n > 0 else 0.5

    mu = statistics.mean(vol_history)
    sigma = statistics.stdev(vol_history) if n > 1 else 0.0
    z = (vol - mu) / sigma if sigma > 0 else 0.0

    if pct >= 0.95:
        regime = "crisis"
    elif pct >= 0.75:
        regime = "high_vol"
    elif pct <= 0.25:
        regime = "low_vol"
    else:
        regime = "normal"

    return regime, pct, z


# ═══════════════════════════════════════════════════════
# 推荐策略
# ═══════════════════════════════════════════════════════

REGIME_STRATEGY = {
    "low_vol": ("trend", 1.0, "低波动期: 适合趋势/动量策略"),
    "normal": ("neutral", 1.0, "正常波动: 均衡策略"),
    "high_vol": ("reversion", 0.8, "高波动期: 适合均值回归, 仓位略降"),
    "crisis": ("defensive", 0.3, "危机: 防御为主, 大幅减仓"),
}


def recommend(regime: str) -> tuple[str, float, str]:
    """根据 regime 推荐策略"""
    return REGIME_STRATEGY.get(regime, ("neutral", 1.0, "未知"))


# ═══════════════════════════════════════════════════════
# 主信号
# ═══════════════════════════════════════════════════════

def detect(
    code: str,
    prices: list[float],
    *,
    short_window: int = 20,
    history_window: int = 60,
) -> Optional[VolRegime]:
    """检测 vol regime"""
    if len(prices) < history_window + short_window + 1:
        return None

    history_vol = vol_series(prices[:-short_window], window=short_window)
    if not history_vol:
        return None

    curr_vol = realized_vol(prices, window=short_window)
    if curr_vol is None:
        return None

    regime, pct, z = classify_regime(curr_vol, history_vol)
    rec, size, reason = recommend(regime)

    return VolRegime(
        code=code,
        regime=regime,
        current_vol=round(curr_vol, 6),
        historical_pct=round(pct, 4),
        vol_z=round(z, 4),
        recommended=rec,
        size_factor=size,
        reason=f"{reason} (vol_z={z:+.2f}, pct={pct:.0%})",
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def detect_universe(
    universe: dict[str, list[float]],
    *,
    short_window: int = 20,
    history_window: int = 60,
) -> dict[str, VolRegime]:
    """扫全 universe, 返回各 code 的 regime"""
    out = {}
    for code, prices in universe.items():
        r = detect(code, prices, short_window=short_window, history_window=history_window)
        if r is not None:
            out[code] = r
    return out


def aggregate_regime(regimes: dict[str, VolRegime]) -> dict[str, int]:
    """汇总各 regime 数量"""
    counts = {"low_vol": 0, "normal": 0, "high_vol": 0, "crisis": 0}
    for r in regimes.values():
        counts[r.regime] = counts.get(r.regime, 0) + 1
    return counts


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: VolRegime) -> str:
    return (f"{r.code}: regime={r.regime} vol={r.current_vol:.4f} "
            f"pct={r.historical_pct:.0%} rec={r.recommended} size={r.size_factor:.0%}")