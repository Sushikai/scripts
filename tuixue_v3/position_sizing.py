#!/usr/bin/env python3
"""
tuixue_v3/position_sizing.py
Ship 14/100 — 仓位管理 (Kelly + 波动率倒数加权)

设计:
- Kelly 公式: f* = (p - q) / b, 其中 p=胜率, q=败率, b=盈亏比
  → 给定历史胜率 + 盈亏比, 算理论最优仓位
- 半 Kelly: f* / 2 (实务中更稳, 防参数误差爆仓)
- 波动率倒数加权: 高波动 → 低仓位, 低波动 → 高仓位
  → 满仓波动率目标 2% 日波, 实际 vol_n > target 时降权

组合仓位 = min(半 Kelly, 波动率倒数加权)
再加 max_position 上限 (默认 30%) — 单股仓位不超过总资金 30%。

2026-08-02 Ship 14 — 10000 轮迭代 P2 第四步
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class KellyInputs:
    win_rate: float                # 胜率 (0~1)
    avg_win: float                 # 平均盈利 (如 0.05 = +5%)
    avg_loss: float                # 平均亏损 (绝对值, 如 0.03 = -3%)
    vol_n: float = 0.0             # 该股近 N 日日波动率 (std of pct_change)
    target_vol: float = 0.02       # 目标日波动率 (默认 2%)
    max_position_pct: float = 0.30  # 单股最大仓位
    half_kelly: bool = True         # 默认半 Kelly


@dataclass
class SizingResult:
    raw_kelly: float                # 原始 Kelly 比例
    half_kelly: float               # 半 Kelly 比例
    vol_adjusted: float            # 波动率倒数加权后比例
    final: float                   # 最终建议仓位 (= min(half_kelly, vol_adjusted))
    capped: bool                   # 是否被 max_position_pct 截断
    confidence: float              # 置信度 (0~1, 数据越多越高)
    reason: str                    # 说明


# ═══════════════════════════════════════════════════════
# 核心算法
# ═══════════════════════════════════════════════════════

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_kelly(inputs: KellyInputs) -> SizingResult:
    """Kelly + 波动率倒数加权 → 最终仓位

    Args:
        inputs: KellyInputs

    Returns:
        SizingResult 含 raw/half_kelly/vol_adjusted/final/capped/confidence
    """
    # 1. 原始 Kelly: f* = (p*b - q) / b, b = avg_win/avg_loss
    b = (inputs.avg_win / inputs.avg_loss) if inputs.avg_loss > 0 else 0.0
    if b <= 0 or inputs.win_rate <= 0 or inputs.win_rate >= 1:
        # 参数不全 → 不开仓
        return SizingResult(
            raw_kelly=0.0, half_kelly=0.0, vol_adjusted=0.0,
            final=0.0, capped=False, confidence=0.0,
            reason="参数不全 (win_rate 或 b 异常)",
        )

    p, q = inputs.win_rate, 1 - inputs.win_rate
    raw_kelly = (p * b - q) / b
    # Kelly 可能为负 (期望为负的策略), 此时不持仓
    if raw_kelly <= 0:
        return SizingResult(
            raw_kelly=round(raw_kelly, 4), half_kelly=0.0,
            vol_adjusted=0.0, final=0.0, capped=False,
            confidence=0.5, reason="Kelly 负值, 期望为负不开仓",
        )

    half_kelly = raw_kelly / 2 if inputs.half_kelly else raw_kelly

    # 2. 波动率倒数加权: target_vol / vol_n (vol 越大, 仓位越小)
    if inputs.vol_n <= 0 or inputs.target_vol <= 0:
        vol_adjusted = half_kelly  # 没 vol 数据就用 Kelly
        vol_reason = "波动率未知, 用半 Kelly"
    else:
        ratio = inputs.target_vol / inputs.vol_n
        vol_adjusted = half_kelly * _clamp(ratio, 0.25, 4.0)  # 4x 上限防小 vol 过度
        vol_reason = f"波动率倒数加权 {ratio:.2f}x"

    # 3. 单股上限
    final = min(vol_adjusted, inputs.max_position_pct)
    capped = vol_adjusted > inputs.max_position_pct

    # 4. 置信度: 数据越完整越高
    conf = 0.0
    if 0 < inputs.win_rate < 1:
        conf += 0.4
    if b > 0:
        conf += 0.3
    if inputs.vol_n > 0:
        conf += 0.3

    reason = vol_reason
    if capped:
        reason += f", 截断至 {inputs.max_position_pct:.0%}"

    return SizingResult(
        raw_kelly=round(raw_kelly, 4),
        half_kelly=round(half_kelly, 4),
        vol_adjusted=round(vol_adjusted, 4),
        final=round(final, 4),
        capped=capped,
        confidence=round(conf, 4),
        reason=reason,
    )


def size_portfolio(capital: float, n_picks: int, sizing: SizingResult) -> dict:
    """给 N 只候选股各分配多少资金

    Args:
        capital: 总资金
        n_picks: 候选数
        sizing: SizingResult (用 final)

    Returns:
        {per_position: 金额, total_deployed: 总投入, cash_reserve: 保留现金}
    """
    if n_picks <= 0 or sizing.final <= 0:
        return {"per_position": 0.0, "total_deployed": 0.0,
                "cash_reserve": capital, "n": 0}
    # 单股仓位 = min(sizing.final, 1/n_picks * 1.5) — 多只时降权
    per_share_cap = 1.0 / n_picks * 1.5
    per_position_pct = min(sizing.final, per_share_cap)
    per_position = capital * per_position_pct
    total_deployed = per_position * n_picks
    cash_reserve = max(0.0, capital - total_deployed)
    return {
        "per_position": round(per_position, 2),
        "per_position_pct": round(per_position_pct, 4),
        "total_deployed": round(total_deployed, 2),
        "cash_reserve": round(cash_reserve, 2),
        "n": n_picks,
    }