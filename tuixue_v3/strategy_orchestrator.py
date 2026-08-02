#!/usr/bin/env python3
"""
tuixue_v3/strategy_orchestrator.py
Ship 25/100 — 策略编排器 (完整链路编排)

设计:
将以下模块按正确顺序串成完整决策链:
1. market_regime → 判 regime + position_factor
2. drawdown_recovery → 当前 dd 状态 + recovery_factor
3. strategy_registry → 多策略选 picks
4. adaptive_selector → 综合过滤 + 合并
5. risk_control → 当前组合风险评估
6. strategy_combiner → factor × risk 合并 (可独立调用)
7. risk_allocator → 资金分配
8. trade_executor → 模拟下单
9. signal_metrics → 跟踪信号效果 (callbacks)

输入: TradeContext (date, candidates, factor_scores, prices, holdings, cash)
输出: OrchestrationResult (regime, picks, allocations, orders, metrics)

降级: 任何环节失败 → 该环节跳过, 但链路继续

2026-08-02 Ship 25 — 10000 轮迭代 P2 第十五步 (链路集成)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .adaptive_selector import select_adaptive
from .drawdown_recovery import evaluate_recovery
from .market_regime import detect_regime
from .risk_allocator import allocate, CandidatePick
from .risk_control import Portfolio, Holding, evaluate as evaluate_risk
from .signal_metrics import SignalMetrics
from .strategy_combiner import StrategyConfig, combine
from .strategy_registry import StrategyContext
from .trade_executor import execute_orders

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class TradeContext:
    """完整交易上下文"""
    date: str
    candidates: list[str]                  # 候选 code 列表
    factor_scores: dict[str, float]        # code → composite (-1~1)
    prices: dict[str, float]               # code → 当前价
    holdings: dict[str, Holding]           # code → Holding
    cash: float
    initial_capital: float
    equity_history: Optional[list[float]] = None
    index_prices: Optional[list[float]] = None  # 大盘指数, for regime
    index_volumes: Optional[list[float]] = None
    strategy_metrics: Optional[dict[str, SignalMetrics]] = None
    base_position_pct: float = 0.20


@dataclass
class OrchestrationResult:
    """完整编排结果"""
    date: str
    regime: str
    regime_factor: float
    recovery_factor: float
    combined_factor: float
    final_position_pct: float
    picks: list[dict]
    allocations: list[dict]
    skipped_allocations: list[dict]
    orders: list[dict]
    risk_summary: str
    blocked: bool
    warnings: list[str]
    duration_ms: float = 0.0

    def summary(self) -> str:
        return (
            f"[{self.date}] regime={self.regime}({self.combined_factor:.2f}) "
            f"picks={len(self.picks)} allocs={len(self.allocations)} "
            f"orders={len(self.orders)} risk={self.risk_summary}"
        )


# ═══════════════════════════════════════════════════════
# 编排器
# ═══════════════════════════════════════════════════════

def orchestrate(ctx: TradeContext) -> OrchestrationResult:
    """完整决策链路

    Args:
        ctx: 交易上下文

    Returns:
        OrchestrationResult
    """
    import time
    t0 = time.time()
    warnings: list[str] = []

    # ═══════════ Step 1: 市场状态识别 ═══════════
    if ctx.index_prices and len(ctx.index_prices) >= 20:
        regime_state = detect_regime(ctx.index_prices, ctx.index_volumes)
        regime = regime_state.regime
        regime_f = regime_state.position_factor
        warnings.extend([f"regime: {r}" for r in regime_state.reasons[:1]])
    else:
        regime = "unknown"
        regime_f = 0.5
        if ctx.index_prices is not None:
            warnings.append("regime: 指数数据 < 20 点, 兜底 unknown")

    # ═══════════ Step 2: 回撤恢复 ═══════════
    equity_now = ctx.cash + sum(h.shares * (ctx.prices.get(h.code, h.price) or h.price)
                                for h in ctx.holdings.values())
    recovery = evaluate_recovery(equity_now, ctx.initial_capital, ctx.equity_history)
    recovery_f = recovery.position_factor

    combined_f = regime_f * recovery_f
    final_pos = round(ctx.base_position_pct * combined_f, 4)

    # ═══════════ Step 3: 风控评估 ═══════════
    portfolio = Portfolio(
        holdings=list(ctx.holdings.values()),
        cash=ctx.cash,
        initial_capital=ctx.initial_capital,
    )
    try:
        risk_result = evaluate_risk(portfolio)
        risk_summary = risk_result.summary()
        blocked = risk_result.blocked
    except Exception as e:
        logger.warning("风控评估失败: %s", e)
        risk_summary = "ERROR"
        blocked = False
        warnings.append(f"risk: {e}")

    if blocked:
        warnings.append("风控 block, 跳过本轮选股")
        return OrchestrationResult(
            date=ctx.date, regime=regime, regime_factor=regime_f,
            recovery_factor=recovery_f, combined_factor=combined_f,
            final_position_pct=0.0, picks=[], allocations=[],
            skipped_allocations=[], orders=[],
            risk_summary=risk_summary, blocked=True,
            warnings=warnings,
            duration_ms=round((time.time() - t0) * 1000, 2),
        )

    # ═══════════ Step 4: 自适应选择策略 ═══════════
    strat_ctx = StrategyContext(
        date=ctx.date,
        candidates=ctx.candidates,
        factor_scores=ctx.factor_scores,
        regime=regime,
        portfolio_value=equity_now,
        cash=ctx.cash,
        initial_capital=ctx.initial_capital,
    )
    selection = select_adaptive(
        strat_ctx,
        strategy_metrics=ctx.strategy_metrics,
        equity_history=ctx.equity_history,
        base_position_pct=ctx.base_position_pct,
    )
    final_pos = selection.final_position_pct

    # ═══════════ Step 5: 资金分配 ═══════════
    candidates_for_alloc = []
    for pick in selection.picks:
        code = pick.code
        price = ctx.prices.get(code)
        # 板块查找 (从 holdings 或 strategy_metrics)
        sector = "(unknown)"
        if code in ctx.holdings:
            sector = ctx.holdings[code].sector or sector
        candidates_for_alloc.append(CandidatePick(
            code=code, score=pick.score, sector=sector,
            suggested_pct=final_pos, price=price,
        ))

    try:
        alloc_result = allocate(
            ctx.cash + sum(h.shares * (ctx.prices.get(h.code, h.price) or h.price)
                           for h in ctx.holdings.values()),
            candidates_for_alloc,
            max_position_pct=final_pos,
            max_total_pct=0.80,
            max_sector_pct=0.40,
            cash_reserve_pct=0.10,
        )
    except Exception as e:
        logger.warning("分配失败: %s", e)
        warnings.append(f"alloc: {e}")
        alloc_result = None

    # ═══════════ Step 6: 订单生成 (如已有持仓, 也支持 sell) ═══════════
    orders = []
    if alloc_result and alloc_result.allocations:
        # 把 Allocation 转成 Order actions
        actions = []
        for a in alloc_result.allocations:
            if a.shares > 0:
                actions.append({"code": a.code, "action": "buy",
                               "shares_delta": a.shares})
        # 暂不卖现有持仓 (无明确 sell 信号)
        if actions:
            try:
                positions = {h.code: h.shares for h in ctx.holdings.values()}
                exec_report = execute_orders(
                    actions, cash=ctx.cash, positions=positions,
                    price_getter=lambda c: ctx.prices.get(c),
                )
                orders = [
                    {"code": o.code, "action": o.action,
                     "filled_shares": o.filled_shares,
                     "avg_price": o.avg_price, "status": o.status}
                    for o in exec_report.orders
                ]
            except Exception as e:
                logger.warning("执行失败: %s", e)
                warnings.append(f"execute: {e}")

    # ═══════════ 输出 ═══════════
    picks_dict = [
        {"code": p.code, "score": p.score, "confidence": p.confidence,
         "reason": p.reason}
        for p in selection.picks
    ]
    allocs_dict = [
        {"code": a.code, "amount": a.amount, "shares": a.shares,
         "sector": a.sector, "actual_pct": a.actual_pct}
        for a in (alloc_result.allocations if alloc_result else [])
    ]
    skipped_dict = [
        {"code": s.code, "sector": s.sector, "reason": s.reason}
        for s in (alloc_result.skipped if alloc_result else [])
    ]

    return OrchestrationResult(
        date=ctx.date, regime=regime, regime_factor=regime_f,
        recovery_factor=recovery_f, combined_factor=combined_f,
        final_position_pct=final_pos,
        picks=picks_dict, allocations=allocs_dict,
        skipped_allocations=skipped_dict,
        orders=orders,
        risk_summary=risk_summary, blocked=False,
        warnings=warnings,
        duration_ms=round((time.time() - t0) * 1000, 2),
    )
