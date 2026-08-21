#!/usr/bin/env python3
"""
tuixue_v3/strat_era14_dual_momentum.py
Ship 70/100 — 量化 era 2026 高级策略 #14

Dual Momentum Strategy (双重动量策略)

设计:
- 绝对动量: 资产自身近期收益 > 0
- 相对动量: 资产收益 > 同类平均 (cross-sectional)
- 双重过滤: 同时满足才买入, 否则卖出 / 持有现金

输入: {code: list[float]} 价格时序
输出: ranking + 双重过滤后的 buys/sells

降级: 数据不足 → 排除

2026-08-03 Ship 70 — 10000 轮迭代 P5 第十五步
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
class DualMomentumSignal:
    code: str
    side: str                  # "buy" / "sell" / "cash"
    abs_return: float          # 绝对动量
    rel_return: float          # 相对动量 (相对截面均值)
    combined: float            # 综合
    abs_pass: bool             # 绝对动量是否达标
    rel_pass: bool             # 相对动量是否达标
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "abs_return": self.abs_return,
            "rel_return": self.rel_return,
            "combined": self.combined,
            "abs_pass": self.abs_pass,
            "rel_pass": self.rel_pass,
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def period_return(prices: list[float], lookback: int) -> Optional[float]:
    """lookback 期收益率"""
    if len(prices) < lookback + 1:
        return None
    past = prices[-(lookback + 1)]
    curr = prices[-1]
    if past <= 0:
        return None
    return (curr - past) / past


# ═══════════════════════════════════════════════════════
# 截面分析
# ═══════════════════════════════════════════════════════

def cross_section_returns(
    universe: dict[str, list[float]],
    lookback: int = 60,
) -> dict[str, float]:
    """所有资产的 lookback 收益"""
    out = {}
    for code, prices in universe.items():
        r = period_return(prices, lookback)
        if r is not None:
            out[code] = r
    return out


# ═══════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    lookback: int = 60,
    abs_threshold: float = 0.0,
    rel_threshold: float = 0.0,
) -> Optional[DualMomentumSignal]:
    """单资产双重动量

    - abs_pass: return > abs_threshold
    - rel_pass: 相对截面均值 > rel_threshold
    """
    abs_ret = period_return(prices, lookback)
    if abs_ret is None:
        return None

    return DualMomentumSignal(
        code=code,
        side="cash",        # 默认 cash, 由 dual_screen 决定
        abs_return=round(abs_ret, 4),
        rel_return=0.0,
        combined=round(abs_ret, 4),
        abs_pass=abs_ret > abs_threshold,
        rel_pass=False,
        reason="init",
    )


def dual_screen(
    universe: dict[str, list[float]],
    *,
    lookback: int = 60,
    abs_threshold: float = 0.0,
    rel_threshold: float = 0.0,
) -> list[DualMomentumSignal]:
    """全 universe 双重动量筛选

    Returns: 满足双重过滤的 signals (相对超额 + 绝对正收益)
    """
    rets = cross_section_returns(universe, lookback)
    if not rets:
        return []

    # 截面均值
    mean_ret = statistics.mean(rets.values())

    out = []
    for code, abs_ret in rets.items():
        rel_ret = abs_ret - mean_ret
        abs_pass = abs_ret > abs_threshold
        rel_pass = rel_ret > rel_threshold

        if abs_pass and rel_pass:
            side = "buy"
            reason = f"双重达标 (abs={abs_ret:+.2%}, rel={rel_ret:+.2%})"
        elif not abs_pass and rel_pass:
            side = "cash"
            reason = f"相对强但绝对负 (abs={abs_ret:+.2%})"
        elif abs_pass and not rel_pass:
            side = "cash"
            reason = f"绝对正但相对弱 (rel={rel_ret:+.2%})"
        else:
            side = "sell"
            reason = f"双重不达标 (abs={abs_ret:+.2%}, rel={rel_ret:+.2%})"

        out.append(DualMomentumSignal(
            code=code,
            side=side,
            abs_return=round(abs_ret, 4),
            rel_return=round(rel_ret, 4),
            combined=round(abs_ret + rel_ret, 4),
            abs_pass=abs_pass,
            rel_pass=rel_pass,
            reason=reason,
        ))

    return out


# ═══════════════════════════════════════════════════════
# 排序
# ═══════════════════════════════════════════════════════

def rank_by_combined(
    signals: list[DualMomentumSignal],
) -> list[DualMomentumSignal]:
    """按 combined 排序 (强 → 弱)"""
    return sorted(signals, key=lambda s: s.combined, reverse=True)


def filter_buys(signals: list[DualMomentumSignal]) -> list[DualMomentumSignal]:
    """过滤 buy 信号"""
    return [s for s in signals if s.side == "buy"]


def filter_cash(signals: list[DualMomentumSignal]) -> list[DualMomentumSignal]:
    """过滤 cash (持币)"""
    return [s for s in signals if s.side == "cash"]


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(signals: list[DualMomentumSignal], top_k: int = 10) -> str:
    out = [f"Dual Momentum (n={len(signals)}):"]
    buys = filter_buys(signals)
    sells = [s for s in signals if s.side == "sell"]
    out.append(f"  buys: {len(buys)}, sells/cash: {len(sells)}")
    for s in signals[:top_k]:
        out.append(f"  {s.code}: {s.side.upper()} abs={s.abs_return:+.2%} rel={s.rel_return:+.2%} ({s.reason})")
    return "\n".join(out)