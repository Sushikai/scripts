#!/usr/bin/env python3
"""
tuixue_v3/adaptive_selector.py
Ship 21/100 — 自适应策略选择器 (综合 regime + recovery + signal_metrics)

设计:
- 输入: 多个候选策略 (来自 registry)
- 选择依据:
  1. regime suit (策略元信息): 不适合当前 regime 排除
  2. signal_metrics.health: unhealthy 排除
  3. recovery position_factor: 决定总体仓位
  4. 综合评分 = sum(picks.score × strategy_weight × health_factor × regime_factor)
- 输出: 推荐列表 + 选择理由 + 仓位调整

降级: 无任何策略适用 → 空 picks + risk_factor=0.5 保守

2026-08-02 Ship 21 — 10000 轮迭代 P2 第十一步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .drawdown_recovery import evaluate_recovery, get_factor as recovery_factor
from .market_regime import get_position_factor as regime_factor
from .signal_metrics import SignalMetrics
from .strategy_registry import (
    StrategyContext, StrategyPick, StrategyInfo,
    run_strategy, list_all,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class SelectionResult:
    """自适应选择结果"""
    picks: list[StrategyPick]
    strategies_used: list[str]                # 实际启用的策略名
    strategies_skipped: list[str]             # 跳过的策略及原因
    regime: str
    regime_factor: float                      # regime 仓位系数
    recovery_factor: float                    # recovery 仓位系数
    combined_factor: float                    # 二者相乘
    final_position_pct: float                 # 单股最大仓位
    reasons: list[str]


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def select_adaptive(
    ctx: StrategyContext,
    *,
    strategy_metrics: Optional[dict[str, SignalMetrics]] = None,
    equity_history: Optional[list[float]] = None,
    base_position_pct: float = 0.20,
) -> SelectionResult:
    """自适应选择最优策略

    Args:
        ctx: 策略上下文
        strategy_metrics: {strategy_name: SignalMetrics} 健康度
        equity_history: equity 曲线 (for recovery)
        base_position_pct: 基础单股仓位上限

    Returns:
        SelectionResult
    """
    strategy_metrics = strategy_metrics or {}
    skipped: list[str] = []
    reasons: list[str] = []

    # 1. 候选策略列表 (启用 + 健康)
    candidates_strats: list[StrategyInfo] = []
    for info in list_all(enabled_only=True):
        # 健康度检查
        m = strategy_metrics.get(info.name)
        if m and not m.is_healthy and m.n_samples >= 30:
            skipped.append(f"{info.name}(unhealthy)")
            reasons.append(f"策略 {info.name} unhealthy: {m.reasons}")
            continue
        candidates_strats.append(info)

    if not candidates_strats:
        return SelectionResult(
            picks=[], strategies_used=[], strategies_skipped=skipped,
            regime=ctx.regime, regime_factor=regime_factor(ctx.regime),
            recovery_factor=1.0, combined_factor=regime_factor(ctx.regime),
            final_position_pct=0.0,
            reasons=["无任何策略适用"] + reasons,
        )

    # 2. 执行所有候选策略
    all_picks: dict[str, list[StrategyPick]] = {}
    for info in candidates_strats:
        picks = run_strategy(info.name, ctx)
        all_picks[info.name] = picks

    # 3. 合并 picks: 按 code 聚合 (不同策略推荐同一只 → 累加 score)
    merged: dict[str, dict] = {}  # code → {score, contributors}
    for strat_name, picks in all_picks.items():
        for p in picks:
            if p.code not in merged:
                merged[p.code] = {"score": 0.0, "confidence": 0.0,
                                  "contributors": [], "reasons": []}
            merged[p.code]["score"] += p.score
            merged[p.code]["confidence"] += p.confidence
            merged[p.code]["contributors"].append(strat_name)
            merged[p.code]["reasons"].append(p.reason)

    # 4. 应用 regime + recovery 系数
    rf = regime_factor(ctx.regime)
    rc_state = evaluate_recovery(ctx.portfolio_value or 0,
                                 ctx.initial_capital,
                                 equity_history)
    rcf = rc_state.position_factor
    combined = rf * rcf
    final_position = round(base_position_pct * combined, 4)

    final_picks = []
    for code, m in merged.items():
        score = m["score"] * combined
        confidence = m["confidence"] / max(len(m["contributors"]), 1)
        final_picks.append(StrategyPick(
            code=code, score=round(score, 4),
            confidence=round(confidence, 4),
            reason=" + ".join(m["reasons"][:2]),
        ))

    final_picks.sort(key=lambda p: p.score, reverse=True)
    used_names = [info.name for info in candidates_strats]

    reasons.insert(0, f"regime={ctx.regime}(×{rf:.2f}) × recovery(×{rcf:.2f}) = {combined:.2f}")
    reasons.insert(0, f"候选策略 {len(candidates_strats)} 个, 跳过 {len(skipped)}")

    return SelectionResult(
        picks=final_picks,
        strategies_used=used_names,
        strategies_skipped=skipped,
        regime=ctx.regime,
        regime_factor=rf,
        recovery_factor=rcf,
        combined_factor=combined,
        final_position_pct=final_position,
        reasons=reasons,
    )


def to_dict(result: SelectionResult) -> dict:
    """SelectionResult → JSON dict"""
    return {
        "regime": result.regime,
        "regime_factor": result.regime_factor,
        "recovery_factor": result.recovery_factor,
        "combined_factor": result.combined_factor,
        "final_position_pct": result.final_position_pct,
        "strategies_used": result.strategies_used,
        "strategies_skipped": result.strategies_skipped,
        "reasons": result.reasons,
        "picks": [
            {"code": p.code, "score": p.score, "confidence": p.confidence,
             "reason": p.reason}
            for p in result.picks
        ],
    }
