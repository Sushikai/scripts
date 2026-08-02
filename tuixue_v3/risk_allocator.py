#!/usr/bin/env python3
"""
tuixue_v3/risk_allocator.py
Ship 22/100 — 风险感知仓位分配

设计:
给定:
- 候选 picks (有序, 高分在前)
- 总资金
- 各类约束 (单股上限 / 总仓位 / 板块上限 / 现金预留)

按以下规则分配:
1. 候选按 score 降序遍历
2. 每只分配: min(候选默认仓位, 单股上限, 剩余资金/N)
3. 板块集中度约束: 同板块累计不能超过 板块上限
4. 总仓位不能超过 上限 (留 cash reserve)

输出:
- Allocation: code → amount + shares (按 100 股 round)
- 总投入 + 现金剩余 + 触发约束警告

降级: 资金不足 / 候选全被约束 → 全 0 仓位 + 警告

2026-08-02 Ship 22 — 10000 轮迭代 P2 第十二步
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
class CandidatePick:
    """待分配的候选"""
    code: str
    score: float                       # 0~1
    sector: str = "(unknown)"
    suggested_pct: float = 0.0         # 策略建议的仓位 (0~1)
    price: Optional[float] = None      # 当前价 (None → 按金额分配)


@dataclass
class Allocation:
    """单只分配结果"""
    code: str
    amount: float                      # 投入金额
    shares: int                        # 股数 (按 100 round)
    price: float
    actual_pct: float                  # 实际仓位 (amount/capital)
    sector: str
    reason: str                        # 分配/跳过的原因


@dataclass
class AllocationResult:
    """总分配结果"""
    capital: float
    total_deployed: float
    cash_reserve: float
    allocations: list[Allocation]
    skipped: list[Allocation]
    warnings: list[str]
    sector_allocated: dict[str, float]  # sector → 已分配金额

    def summary(self) -> str:
        return (
            f"投入 {self.total_deployed:.0f}/{self.capital:.0f} "
            f"({self.total_deployed / self.capital:.1%}) "
            f"分配 {len(self.allocations)} 只, "
            f"跳过 {len(self.skipped)} 只, "
            f"现金 {self.cash_reserve:.0f}"
        )


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def allocate(
    capital: float,
    candidates: list[CandidatePick],
    *,
    max_position_pct: float = 0.20,
    max_total_pct: float = 0.80,
    max_sector_pct: float = 0.40,
    cash_reserve_pct: float = 0.10,
    lot_size: int = 100,
) -> AllocationResult:
    """分配资金到候选

    Args:
        capital: 总资金
        candidates: 候选列表 (按 score 降序传入)
        max_position_pct: 单股仓位上限
        max_total_pct: 总仓位上限
        max_sector_pct: 板块上限
        cash_reserve_pct: 现金预留比例
        lot_size: 1 手股数 (A 股默认 100)

    Returns:
        AllocationResult
    """
    if capital <= 0:
        return AllocationResult(
            capital=capital, total_deployed=0, cash_reserve=0,
            allocations=[], skipped=[],
            warnings=["资金为 0"], sector_allocated={},
        )

    warnings: list[str] = []
    allocations: list[Allocation] = []
    skipped: list[Allocation] = []
    sector_alloc: dict[str, float] = {}

    # 可用资金 = capital × (1 - cash_reserve)
    available = capital * (1.0 - cash_reserve_pct)
    # 但也不能超过 max_total
    deploy_budget = min(available, capital * max_total_pct)
    deployed = 0.0

    # 按 score 排序 (高分优先)
    sorted_cands = sorted(candidates, key=lambda c: c.score, reverse=True)
    n = len(sorted_cands)

    for i, cand in enumerate(sorted_cands):
        # 1. 候选默认仓位 (策略给的) vs 单股上限
        target_pct = min(cand.suggested_pct, max_position_pct)
        if target_pct <= 0:
            skipped.append(Allocation(
                code=cand.code, amount=0, shares=0,
                price=cand.price or 0, actual_pct=0,
                sector=cand.sector, reason="候选仓位为 0",
            ))
            continue

        # 2. 剩余资金约束 (剩几只均分)
        remaining_cands = max(1, n - i)
        per_cand_cap_pct = (deploy_budget - deployed) / capital / remaining_cands
        target_pct = min(target_pct, per_cand_cap_pct)

        # 3. 板块约束
        sector_used = sector_alloc.get(cand.sector, 0)
        sector_cap_amt = capital * max_sector_pct
        if sector_used >= sector_cap_amt:
            skipped.append(Allocation(
                code=cand.code, amount=0, shares=0,
                price=cand.price or 0, actual_pct=0,
                sector=cand.sector,
                reason=f"板块 {cand.sector} 已用满 {sector_used:.0f}",
            ))
            warnings.append(f"{cand.code}: 板块 {cand.sector} 集中度满")
            continue
        sector_remaining = sector_cap_amt - sector_used
        sector_cap_pct = sector_remaining / capital
        target_pct = min(target_pct, sector_cap_pct)

        # 4. 转金额
        target_amount = capital * target_pct
        # 不能超过 deploy_budget - deployed
        target_amount = min(target_amount, deploy_budget - deployed)
        # 不能超过 sector_remaining
        target_amount = min(target_amount, sector_remaining)

        if target_amount <= 0:
            skipped.append(Allocation(
                code=cand.code, amount=0, shares=0,
                price=cand.price or 0, actual_pct=0,
                sector=cand.sector, reason="资金/约束用尽",
            ))
            continue

        # 5. 价格 → 股数 (按 lot_size round)
        if cand.price is None or cand.price <= 0:
            # 没价格: 按金额分配 (skip 股数计算)
            amount = target_amount
            shares = 0
            warnings.append(f"{cand.code}: 无价格, 仅按金额记录")
        else:
            shares = int(target_amount / cand.price / lot_size) * lot_size
            if shares == 0:
                skipped.append(Allocation(
                    code=cand.code, amount=0, shares=0,
                    price=cand.price, actual_pct=0,
                    sector=cand.sector,
                    reason=f"价格 {cand.price} 太高买不起 1 手",
                ))
                continue
            amount = shares * cand.price

        # 6. 检查 amount 不超 deploy_budget - deployed
        if amount > deploy_budget - deployed:
            # 重新按剩余资金算 shares
            if cand.price and cand.price > 0:
                shares = int((deploy_budget - deployed) / cand.price / lot_size) * lot_size
                if shares == 0:
                    skipped.append(Allocation(
                        code=cand.code, amount=0, shares=0,
                        price=cand.price, actual_pct=0,
                        sector=cand.sector, reason="资金用尽",
                    ))
                    continue
                amount = shares * cand.price
            else:
                amount = deploy_budget - deployed

        deployed += amount
        sector_alloc[cand.sector] = sector_alloc.get(cand.sector, 0) + amount
        allocations.append(Allocation(
            code=cand.code, amount=round(amount, 2),
            shares=shares, price=cand.price or 0,
            actual_pct=round(amount / capital, 4),
            sector=cand.sector,
            reason=f"score={cand.score:.2f}",
        ))

    cash_reserve = capital - deployed
    # 兜底: 现金预留至少 cash_reserve_pct
    if cash_reserve < capital * cash_reserve_pct:
        warnings.append(f"现金剩余 {cash_reserve:.0f} 低于预留 {cash_reserve_pct:.0%}")

    return AllocationResult(
        capital=capital,
        total_deployed=round(deployed, 2),
        cash_reserve=round(cash_reserve, 2),
        allocations=allocations,
        skipped=skipped,
        warnings=warnings,
        sector_allocated={k: round(v, 2) for k, v in sector_alloc.items()},
    )


def to_dict(result: AllocationResult) -> dict:
    """AllocationResult → JSON"""
    return {
        "capital": result.capital,
        "total_deployed": result.total_deployed,
        "cash_reserve": result.cash_reserve,
        "deploy_pct": round(result.total_deployed / result.capital, 4) if result.capital > 0 else 0,
        "n_allocated": len(result.allocations),
        "n_skipped": len(result.skipped),
        "warnings": result.warnings,
        "sector_allocated": result.sector_allocated,
        "allocations": [
            {"code": a.code, "amount": a.amount, "shares": a.shares,
             "price": a.price, "actual_pct": a.actual_pct,
             "sector": a.sector, "reason": a.reason}
            for a in result.allocations
        ],
        "skipped": [
            {"code": s.code, "sector": s.sector, "reason": s.reason}
            for s in result.skipped
        ],
    }
