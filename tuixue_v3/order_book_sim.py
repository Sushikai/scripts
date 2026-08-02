#!/usr/bin/env python3
"""
tuixue_v3/order_book_sim.py
Ship 31/100 — 订单簿模拟 (用于回测 + paper trading)

设计:
给定 bid/ask 5 档盘口, 模拟大单拆分:
- 买单: 从最低 ask 开始吃, 直到吃够数量
- 卖单: 从最高 bid 开始抛, 直到抛够数量
- 计算 VWAP + 滑点 + 冲击成本

输出: FillResult {vwap, filled_shares, total_cost, slippage_bps, levels_consumed}

降级: 盘口不全 → 用 mid price + 默认滑点

2026-08-02 Ship 31 — 10000 轮迭代 P3 第六步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class PriceLevel:
    """一档盘口"""
    price: float
    volume: int            # 股数


@dataclass
class OrderBook:
    """订单簿快照"""
    bids: list[PriceLevel] = field(default_factory=list)  # 降序
    asks: list[PriceLevel] = field(default_factory=list)  # 升序
    last_price: float = 0.0
    timestamp: float = 0.0

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / 2
        if self.last_price > 0:
            return self.last_price
        return 0.0


@dataclass
class FillResult:
    """成交结果"""
    filled_shares: int
    vwap: float
    total_cost: float           # 总金额 (含费前)
    slippage_bps: float         # 相对 mid 的滑点 (bp, 1bp=0.01%)
    levels_consumed: int        # 吃/抛了几档
    unfilled_shares: int        # 未成交
    cost_breakdown: list[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def simulate_fill(book: OrderBook, side: str, shares: int,
                  *, commission: float = 0.0003,
                  default_slippage_bps: float = 10.0) -> FillResult:
    """模拟下单成交

    Args:
        book: 订单簿快照
        side: "buy" / "sell"
        shares: 目标股数
        commission: 单边手续费率
        default_slippage_bps: 无盘口时默认滑点

    Returns:
        FillResult
    """
    if shares <= 0:
        return FillResult(
            filled_shares=0, vwap=0.0, total_cost=0.0,
            slippage_bps=0.0, levels_consumed=0, unfilled_shares=0,
        )

    mid = book.mid
    if mid <= 0:
        return FillResult(
            filled_shares=0, vwap=0.0, total_cost=0.0,
            slippage_bps=0.0, levels_consumed=0, unfilled_shares=shares,
            cost_breakdown=[{"error": "无 mid 价格"}],
        )

    # 选档位
    if side == "buy":
        levels = sorted(book.asks, key=lambda lv: lv.price)  # 升序
    else:
        levels = sorted(book.bids, key=lambda lv: -lv.price)  # 降序

    if not levels:
        # 用 mid + 默认滑点
        slip_bps = default_slippage_bps
        exec_price = mid * (1 + slip_bps / 10000) if side == "buy" else mid * (1 - slip_bps / 10000)
        total_cost = shares * exec_price
        return FillResult(
            filled_shares=shares, vwap=round(exec_price, 4),
            total_cost=round(total_cost, 2),
            slippage_bps=slip_bps, levels_consumed=0,
            unfilled_shares=0,
            cost_breakdown=[{"price": exec_price, "volume": shares, "src": "default"}],
        )

    # 模拟吃档
    remaining = shares
    total_cost = 0.0
    total_filled = 0
    breakdown = []
    levels_consumed = 0

    for lv in levels:
        if remaining <= 0:
            break
        take = min(remaining, lv.volume)
        if take <= 0:
            continue
        cost = take * lv.price
        total_cost += cost
        total_filled += take
        remaining -= take
        levels_consumed += 1
        breakdown.append({"price": lv.price, "volume": take, "src": f"level{levels_consumed}"})

    unfilled = remaining
    if total_filled > 0:
        vwap = total_cost / total_filled
        slip_bps = abs(vwap - mid) / mid * 10000
    else:
        vwap = 0.0
        slip_bps = default_slippage_bps

    return FillResult(
        filled_shares=total_filled,
        vwap=round(vwap, 4),
        total_cost=round(total_cost, 2),
        slippage_bps=round(slip_bps, 2),
        levels_consumed=levels_consumed,
        unfilled_shares=unfilled,
        cost_breakdown=breakdown,
    )


def estimate_slippage(book: OrderBook, side: str, shares: int) -> float:
    """估算滑点 (bp), 不实际成交"""
    r = simulate_fill(book, side, shares)
    return r.slippage_bps


def to_dict(result: FillResult) -> dict:
    return {
        "filled_shares": result.filled_shares,
        "vwap": result.vwap,
        "total_cost": result.total_cost,
        "slippage_bps": result.slippage_bps,
        "levels_consumed": result.levels_consumed,
        "unfilled_shares": result.unfilled_shares,
        "cost_breakdown": result.cost_breakdown,
    }
