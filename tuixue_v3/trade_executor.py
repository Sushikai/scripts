#!/usr/bin/env python3
"""
tuixue_v3/trade_executor.py
Ship 24/100 — 模拟盘执行层 (paper trading executor)

设计:
接收 rebalance actions / allocator 结果, 模拟下单 + 滑点 + 部分成交:
1. 按 action 顺序执行 buy / sell
2. 每笔记录: 订单 id, 时间, 价格 (含滑点), 成交量, 手续费
3. 部分成交: 大单分批 (按流动性)
4. 拒单: 资金不足 / 持仓不足

输出:
- ExecutionReport: 全部订单 + 成功/失败 + 总成本
- 更新后的 cash + positions

降级: 无价格 → 拒单 + 警告; 资金不足 → 部分成交

2026-08-02 Ship 24 — 10000 轮迭代 P2 第十四步
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class Order:
    """单笔订单"""
    order_id: str
    code: str
    action: str                # "buy" / "sell"
    target_shares: int
    filled_shares: int = 0
    avg_price: float = 0.0
    status: str = "pending"    # pending/filled/partial/rejected
    timestamp: float = 0.0
    error: str = ""


@dataclass
class ExecutionReport:
    """执行报告"""
    orders: list[Order]
    total_buy_amount: float
    total_sell_amount: float
    total_commission: float
    n_filled: int
    n_partial: int
    n_rejected: int
    cash_before: float
    cash_after: float
    initial_capital: float


# ═══════════════════════════════════════════════════════
# 价格查询抽象
# ═══════════════════════════════════════════════════════

PriceGetter = object  # Callable[[str], float]


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def execute_orders(
    actions: list[dict],
    *,
    cash: float,
    positions: dict[str, int],   # code → shares
    price_getter,                # (code) → price or None
    commission: float = 0.0003,
    slippage: float = 0.001,
    lot_size: int = 100,
) -> ExecutionReport:
    """执行订单列表

    Args:
        actions: [{code, action ("buy"/"sell"), shares_delta}]
        cash: 起始现金
        positions: 起始持仓
        price_getter: 价格查询
        commission/slippage: 成本
        lot_size: 1 手股数

    Returns:
        ExecutionReport
    """
    initial_cash = cash
    orders: list[Order] = []
    total_buy = 0.0
    total_sell = 0.0
    total_commission = 0.0
    n_filled = n_partial = n_rejected = 0

    for a in actions:
        code = a["code"]
        action = a["action"]
        target = abs(a.get("shares_delta", 0))
        if target == 0:
            continue

        order_id = uuid.uuid4().hex[:12]
        price = price_getter(code) if callable(price_getter) else None

        if price is None or price <= 0:
            order = Order(
                order_id=order_id, code=code, action=action,
                target_shares=target, status="rejected",
                timestamp=time.time(), error="无价格",
            )
            orders.append(order)
            n_rejected += 1
            continue

        # 价格含滑点
        exec_price = price * (1 + slippage) if action == "buy" else price * (1 - slippage)

        # 校验
        if action == "buy":
            cost = target * exec_price * (1 + commission)
            if cost > cash:
                # 部分成交: 买得起多少
                affordable = int(cash / exec_price / (1 + commission) / lot_size) * lot_size
                if affordable == 0:
                    order = Order(
                        order_id=order_id, code=code, action=action,
                        target_shares=target, status="rejected",
                        timestamp=time.time(), error="资金不足",
                    )
                    orders.append(order)
                    n_rejected += 1
                    continue
                filled = affordable
                cost = filled * exec_price * (1 + commission)
                order = Order(
                    order_id=order_id, code=code, action=action,
                    target_shares=target, filled_shares=filled,
                    avg_price=round(exec_price, 4),
                    status="partial", timestamp=time.time(),
                    error=f"目标 {target} 资金只够 {filled}",
                )
                orders.append(order)
                n_partial += 1
            else:
                filled = target
                order = Order(
                    order_id=order_id, code=code, action=action,
                    target_shares=target, filled_shares=filled,
                    avg_price=round(exec_price, 4),
                    status="filled", timestamp=time.time(),
                )
                orders.append(order)
                n_filled += 1
            # 更新
            actual_amount = filled * exec_price
            commission_amt = filled * exec_price * commission
            cash -= (actual_amount + commission_amt)
            positions[code] = positions.get(code, 0) + filled
            total_buy += actual_amount
            total_commission += commission_amt
        else:  # sell
            cur_shares = positions.get(code, 0)
            if cur_shares == 0:
                order = Order(
                    order_id=order_id, code=code, action=action,
                    target_shares=target, status="rejected",
                    timestamp=time.time(), error="无持仓",
                )
                orders.append(order)
                n_rejected += 1
                continue
            filled = min(target, cur_shares)
            filled = (filled // lot_size) * lot_size
            if filled == 0:
                order = Order(
                    order_id=order_id, code=code, action=action,
                    target_shares=target, status="rejected",
                    timestamp=time.time(), error="持仓不足 1 手",
                )
                orders.append(order)
                n_rejected += 1
                continue
            proceeds = filled * exec_price * (1 - commission)
            commission_amt = filled * exec_price * commission
            cash += proceeds
            positions[code] = cur_shares - filled
            if positions[code] == 0:
                del positions[code]
            total_sell += filled * exec_price
            total_commission += commission_amt
            status = "filled" if filled == target else "partial"
            if status == "partial":
                n_partial += 1
            else:
                n_filled += 1
            order = Order(
                order_id=order_id, code=code, action=action,
                target_shares=target, filled_shares=filled,
                avg_price=round(exec_price, 4),
                status=status, timestamp=time.time(),
            )
            orders.append(order)

    return ExecutionReport(
        orders=orders,
        total_buy_amount=round(total_buy, 2),
        total_sell_amount=round(total_sell, 2),
        total_commission=round(total_commission, 2),
        n_filled=n_filled,
        n_partial=n_partial,
        n_rejected=n_rejected,
        cash_before=round(initial_cash, 2),
        cash_after=round(cash, 2),
        initial_capital=round(initial_cash, 2),
    )


def to_dict(report: ExecutionReport) -> dict:
    return {
        "cash_before": report.cash_before,
        "cash_after": report.cash_after,
        "total_buy_amount": report.total_buy_amount,
        "total_sell_amount": report.total_sell_amount,
        "total_commission": report.total_commission,
        "n_filled": report.n_filled,
        "n_partial": report.n_partial,
        "n_rejected": report.n_rejected,
        "orders": [
            {"order_id": o.order_id, "code": o.code, "action": o.action,
             "target_shares": o.target_shares, "filled_shares": o.filled_shares,
             "avg_price": o.avg_price, "status": o.status, "error": o.error}
            for o in report.orders
        ],
    }
