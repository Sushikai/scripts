#!/usr/bin/env python3
"""
tuixue_v3/portfolio_rebalancer.py
Ship 23/100 — 组合再平衡

设计:
定期 (周/月) 检查当前持仓 vs 目标权重, 偏离过大时触发再平衡:
1. 计算每只当前权重 (市值/总市值)
2. 目标权重 = 用户/策略给的目标
3. 偏离 |current - target| > threshold → 触发
4. 生成调仓指令 (买/卖) + 期望手续费

降级: 目标权重不全 → 用 current 权重 (不动)

2026-08-02 Ship 23 — 10000 轮迭代 P2 第十三步
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
class Holding:
    """当前持仓"""
    code: str
    shares: int
    price: float                # 当前价
    sector: str = "(unknown)"


@dataclass
class TargetWeight:
    """目标权重"""
    code: str
    weight: float               # 0~1
    sector: str = "(unknown)"


@dataclass
class RebalanceAction:
    """单只调仓指令"""
    code: str
    action: str                 # "buy" / "sell" / "hold"
    shares_delta: int           # 正=买, 负=卖
    amount_delta: float         # 正=入金, 负=出金
    current_weight: float
    target_weight: float
    reason: str


@dataclass
class RebalanceResult:
    """再平衡结果"""
    total_value: float
    actions: list[RebalanceAction]
    total_buy: float
    total_sell: float
    turnover: float             # (buy+sell)/2 / total
    expected_commission: float  # 假设万三
    needs_rebalance: bool       # 是否触发再平衡


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def compute_rebalance(
    holdings: list[Holding],
    targets: list[TargetWeight],
    *,
    threshold: float = 0.05,    # 偏离 5% 触发
    commission: float = 0.0003,
    lot_size: int = 100,
) -> RebalanceResult:
    """计算再平衡指令

    Args:
        holdings: 当前持仓
        targets: 目标权重
        threshold: 偏离阈值 (绝对值, 0.05 = 5%)
        commission: 单边手续费率
        lot_size: 1 手股数

    Returns:
        RebalanceResult
    """
    total_value = sum(h.shares * h.price for h in holdings)
    if total_value <= 0:
        return RebalanceResult(
            total_value=0, actions=[], total_buy=0, total_sell=0,
            turnover=0, expected_commission=0, needs_rebalance=False,
        )

    # 当前权重 + 目标权重 dict
    current_w = {h.code: (h.shares * h.price) / total_value for h in holdings}
    target_w = {t.code: t.weight for t in targets}
    target_sector = {t.code: t.sector for t in targets}

    # 合并 code 集合
    all_codes = set(current_w) | set(target_w)

    actions: list[RebalanceAction] = []
    total_buy = 0.0
    total_sell = 0.0
    needs_rebalance = False

    for code in all_codes:
        cur = current_w.get(code, 0.0)
        tgt = target_w.get(code, 0.0)
        delta_w = tgt - cur

        if abs(delta_w) < threshold:
            actions.append(RebalanceAction(
                code=code, action="hold", shares_delta=0, amount_delta=0,
                current_weight=cur, target_weight=tgt,
                reason=f"偏离 {delta_w:+.2%} < {threshold:.0%}",
            ))
            continue

        needs_rebalance = True
        # 目标金额 vs 当前金额
        target_amount = total_value * tgt
        current_amount = total_value * cur
        delta_amount = target_amount - current_amount  # 正=买

        # 找价格 (优先 holdings, 再 targets 隐含)
        price = next((h.price for h in holdings if h.code == code), 0)
        if price <= 0:
            actions.append(RebalanceAction(
                code=code, action="hold", shares_delta=0, amount_delta=0,
                current_weight=cur, target_weight=tgt,
                reason=f"无价格, 跳过 (delta={delta_amount:.0f})",
            ))
            continue

        # 股数 (按 lot_size round)
        if delta_amount > 0:
            shares = int(delta_amount / price / lot_size) * lot_size
            if shares == 0:
                actions.append(RebalanceAction(
                    code=code, action="hold", shares_delta=0, amount_delta=0,
                    current_weight=cur, target_weight=tgt,
                    reason=f"目标 {delta_amount:.0f} 买不起 1 手",
                ))
                continue
            actual_amount = shares * price
            actions.append(RebalanceAction(
                code=code, action="buy", shares_delta=shares,
                amount_delta=actual_amount,
                current_weight=cur, target_weight=tgt,
                reason=f"偏离 {delta_w:+.2%} → 补仓 {shares} 股",
            ))
            total_buy += actual_amount
        else:
            # delta_amount < 0 → 卖; shares 用绝对值
            abs_shares = int(-delta_amount / price / lot_size) * lot_size
            cur_shares = next((h.shares for h in holdings if h.code == code), 0)
            sell_shares = min(abs_shares, cur_shares)
            sell_shares = (sell_shares // lot_size) * lot_size
            if sell_shares == 0:
                actions.append(RebalanceAction(
                    code=code, action="hold", shares_delta=0, amount_delta=0,
                    current_weight=cur, target_weight=tgt,
                    reason=f"目标卖出 {shares} 股, 当前持股不足",
                ))
                continue
            actual_amount = sell_shares * price
            actions.append(RebalanceAction(
                code=code, action="sell", shares_delta=-sell_shares,
                amount_delta=-actual_amount,
                current_weight=cur, target_weight=tgt,
                reason=f"偏离 {delta_w:+.2%} → 减仓 {sell_shares} 股",
            ))
            total_sell += actual_amount

    turnover = (total_buy + total_sell) / 2 / total_value if total_value > 0 else 0
    expected_commission = (total_buy + total_sell) * commission

    return RebalanceResult(
        total_value=round(total_value, 2),
        actions=actions,
        total_buy=round(total_buy, 2),
        total_sell=round(total_sell, 2),
        turnover=round(turnover, 4),
        expected_commission=round(expected_commission, 2),
        needs_rebalance=needs_rebalance,
    )


def to_dict(result: RebalanceResult) -> dict:
    return {
        "total_value": result.total_value,
        "needs_rebalance": result.needs_rebalance,
        "total_buy": result.total_buy,
        "total_sell": result.total_sell,
        "turnover": result.turnover,
        "expected_commission": result.expected_commission,
        "actions": [
            {"code": a.code, "action": a.action, "shares_delta": a.shares_delta,
             "amount_delta": a.amount_delta, "current_weight": a.current_weight,
             "target_weight": a.target_weight, "reason": a.reason}
            for a in result.actions
        ],
    }
