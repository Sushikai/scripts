#!/usr/bin/env python3
"""
tuixue_v3/market_regime.py
Ship 17/100 — 市场状态识别 (Bull / Bear / Range / Crisis)

设计:
- 输入: 指数日线 close + 量 + 振幅 (近 20/60 日)
- 输出: Regime 标签 + confidence + 推荐仓位系数

Regime 判别规则 (简化版, 用于过滤策略信号):
- Bull: 20 日均线 > 60 日均线 + 近 20 日涨幅 > 5% + 量价齐升
- Bear: 20 日均线 < 60 日均线 + 近 20 日跌幅 > 5%
- Range: |20-60 日均线差| < 2% + 近 20 日振幅 < 8%
- Crisis: 近 5 日跌幅 > 8% 或 近 1 日跌幅 > 4%

仓位系数:
- Bull: 1.0 (满仓)
- Range: 0.6 (半仓)
- Bear: 0.3 (轻仓)
- Crisis: 0.1 (防守)

降级: 输入数据不全 → regime = "unknown", position_factor = 0.5 (保守)

2026-08-02 Ship 17 — 10000 轮迭代 P2 第七步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# Regime 类型 + 仓位系数
# ═══════════════════════════════════════════════════════

REGIMES = ("bull", "range", "bear", "crisis", "unknown")

# 各 regime 对应仓位系数 (乘到最终仓位)
POSITION_FACTOR = {
    "bull": 1.0,
    "range": 0.6,
    "bear": 0.3,
    "crisis": 0.1,
    "unknown": 0.5,  # 保守
}

# 各 regime 描述
REGIME_DESC = {
    "bull": "上升趋势: 均线多头 + 量价齐升",
    "range": "震荡行情: 区间窄幅波动",
    "bear": "下跌趋势: 均线空头 + 持续阴跌",
    "crisis": "危机模式: 急跌 + 高波动",
    "unknown": "数据不足, 保守运行",
}


@dataclass
class RegimeState:
    """市场状态"""
    regime: str                  # bull/range/bear/crisis/unknown
    confidence: float            # 0~1
    position_factor: float       # 建议仓位系数
    reasons: list[str]           # 判别理由
    metrics: dict                # 原始指标


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _sma(prices: list[float], n: int) -> Optional[float]:
    """简单移动平均 (None if 数据不足)"""
    if len(prices) < n or n <= 0:
        return None
    return sum(prices[-n:]) / n


def _pct_change(prices: list[float], n: int) -> Optional[float]:
    """N 日涨跌幅"""
    if len(prices) < n + 1 or n <= 0 or prices[-(n + 1)] <= 0:
        return None
    return (prices[-1] - prices[-(n + 1)]) / prices[-(n + 1)]


def _amplitude(prices: list[float], n: int) -> Optional[float]:
    """近 N 日振幅 (max-min)/min"""
    if len(prices) < n or n <= 0:
        return None
    window = prices[-n:]
    lo, hi = min(window), max(window)
    if lo <= 0:
        return None
    return (hi - lo) / lo


def _volume_trend(volumes: list[float], n: int = 5) -> Optional[float]:
    """量比 (近 N 日均量 / 前 N 日均量 - 1)"""
    if len(volumes) < 2 * n or n <= 0:
        return None
    recent = sum(volumes[-n:]) / n
    prior = sum(volumes[-2 * n:-n]) / n
    if prior <= 0:
        return None
    return recent / prior - 1


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def detect_regime(
    prices: list[float],
    volumes: Optional[list[float]] = None,
) -> RegimeState:
    """判别市场状态

    Args:
        prices: 收盘价序列 (升序, 至少 20 个)
        volumes: 成交量序列 (可选, 与 prices 等长)

    Returns:
        RegimeState
    """
    reasons: list[str] = []
    metrics: dict = {}

    if len(prices) < 20:
        return RegimeState(
            regime="unknown", confidence=0.0,
            position_factor=POSITION_FACTOR["unknown"],
            reasons=[f"价格数据不足 (有 {len(prices)} 点, 需 ≥20)"],
            metrics={"n": len(prices)},
        )

    ma20 = _sma(prices, 20)
    ma60 = _sma(prices, 60) if len(prices) >= 60 else None
    chg20 = _pct_change(prices, 20)
    chg5 = _pct_change(prices, 5)
    chg1 = _pct_change(prices, 1)
    amp20 = _amplitude(prices, 20)
    vol_trend = _volume_trend(volumes, 5) if volumes else None

    metrics.update({
        "ma20": round(ma20, 4) if ma20 else None,
        "ma60": round(ma60, 4) if ma60 else None,
        "chg20_pct": round(chg20 * 100, 2) if chg20 else None,
        "chg5_pct": round(chg5 * 100, 2) if chg5 else None,
        "chg1_pct": round(chg1 * 100, 2) if chg1 else None,
        "amp20_pct": round(amp20 * 100, 2) if amp20 else None,
        "volume_trend": round(vol_trend * 100, 2) if vol_trend else None,
        "n": len(prices),
    })

    # 危机优先 (急跌检测)
    if chg5 is not None and chg5 < -0.08:
        return RegimeState(
            regime="crisis", confidence=0.95,
            position_factor=POSITION_FACTOR["crisis"],
            reasons=[f"近 5 日跌幅 {chg5:.2%} 超 8% 危机线"],
            metrics=metrics,
        )
    if chg1 is not None and chg1 < -0.04:
        return RegimeState(
            regime="crisis", confidence=0.9,
            position_factor=POSITION_FACTOR["crisis"],
            reasons=[f"近 1 日暴跌 {chg1:.2%} 超 4%"],
            metrics=metrics,
        )

    # 牛熊判别
    bull_score = 0.0
    bear_score = 0.0

    if ma60 is not None:
        # 均线多头
        if ma20 > ma60:
            bull_score += 0.4
            reasons.append(f"20日均线 ({ma20:.2f}) > 60日 ({ma60:.2f})")
        elif ma20 < ma60:
            bear_score += 0.4
            reasons.append(f"20日均线 ({ma20:.2f}) < 60日 ({ma60:.2f})")
    else:
        # 数据不足 60 日 → 用 20 日单均线 + chg20
        if chg20 is not None:
            if chg20 > 0.05:
                bull_score += 0.2
            elif chg20 < -0.05:
                bear_score += 0.2

    if chg20 is not None:
        if chg20 > 0.05:
            bull_score += 0.3
            reasons.append(f"近 20 日涨幅 {chg20:.2%}")
        elif chg20 < -0.05:
            bear_score += 0.3
            reasons.append(f"近 20 日跌幅 {chg20:.2%}")

    if vol_trend is not None:
        # 量价齐升 / 量缩价跌
        if vol_trend > 0.2 and chg20 is not None and chg20 > 0:
            bull_score += 0.3
            reasons.append(f"量增 {vol_trend:.1%} + 价升")
        elif vol_trend < -0.2 and chg20 is not None and chg20 < 0:
            bear_score += 0.3
            reasons.append(f"量缩 {vol_trend:.1%} + 价跌")

    # 振幅判别 (窄幅 = range)
    range_score = 0.0
    if amp20 is not None:
        if amp20 < 0.08:
            range_score += 0.5
            reasons.append(f"20 日振幅 {amp20:.2%} < 8% (窄幅)")
        elif amp20 > 0.20:
            # 高振幅 → 不算 range
            pass

    # 决策
    scores = {"bull": bull_score, "bear": bear_score, "range": range_score}
    best = max(scores, key=scores.get)
    conf = min(1.0, scores[best])

    # 兜底: 如果 max score < 0.2 → unknown
    if scores[best] < 0.2:
        return RegimeState(
            regime="unknown", confidence=round(1 - conf, 4),
            position_factor=POSITION_FACTOR["unknown"],
            reasons=["判别信号不足, 标记为 unknown"] + reasons,
            metrics=metrics,
        )

    return RegimeState(
        regime=best, confidence=round(conf, 4),
        position_factor=POSITION_FACTOR[best],
        reasons=reasons,
        metrics=metrics,
    )


def get_position_factor(regime: str) -> float:
    """regime → 仓位系数 (unknown 默认 0.5 保守)"""
    return POSITION_FACTOR.get(regime, 0.5)


def describe(regime: str) -> str:
    """regime → 文字描述"""
    return REGIME_DESC.get(regime, "未知状态")
