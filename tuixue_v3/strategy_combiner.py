#!/usr/bin/env python3
"""
tuixue_v3/strategy_combiner.py
Ship 15/100 — 多策略综合打分 (factor × strategy_weight + 风控过滤)

输入:
- FactorScore 列表 (来自 ship 11 factor_pipeline)
- Portfolio (当前持仓, 用于算风控)
- StrategyConfig (策略权重 + 阈值)

输出:
- 候选股按综合分排序, 含风控标记 (block/warn/skip)
- 综合分 = factor.composite × strategy_weight × risk_modifier
- risk_modifier 由 risk_control.Violation 决定:
  * block    → 0 (强制出局)
  * warning  → 0.5 (打折)
  * normal  → 1.0

降级: 任何上游失败 → risk_modifier = 0.5, 综合分打 5 折但不报错。

2026-08-02 Ship 15 — 10000 轮迭代 P2 第五步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .factor_pipeline import FactorScore, rank_scores
from .risk_control import Portfolio, RiskConfig, evaluate as evaluate_risk

logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """策略配置"""
    factor_weight: float = 0.7            # 因子权重
    risk_weight: float = 0.3               # 风控权重 (低分 = 高风险)
    min_factor_score: float = -0.3         # 因子分低于此直接出局
    max_recommendations: int = 20          # 最多推荐 N 只
    exclude_codes: tuple[str, ...] = ()    # 黑名单 (ST/退市风险等)


@dataclass
class StockPick:
    """单只股票的最终推荐"""
    code: str
    composite: float
    factor_score: FactorScore
    risk_severity: str                     # "block" | "warning" | "ok" | "skip"
    risk_messages: list[str] = field(default_factory=list)
    final_score: float = 0.0               # composite × risk_modifier
    rank: int = 0


# ═══════════════════════════════════════════════════════
# 风控 → modifier 映射
# ═══════════════════════════════════════════════════════

_RISK_MODIFIER = {
    "block": 0.0,
    "warning": 0.5,
    "ok": 1.0,
    "skip": 1.0,  # 无风控数据 → 信任因子
}


def _evaluate_stock_risk(code: str, portfolio: Portfolio,
                          cfg: RiskConfig) -> tuple[str, list[str]]:
    """评估单只股票对组合的风险贡献

    简化: 模拟把 code 加到组合, 看总规则触发情况。
    Returns:
        (severity, messages)
    """
    # 没找到 code 相关 holding, 模拟加入
    test_portfolio = _simulate_add(portfolio, code)
    result = evaluate_risk(test_portfolio, cfg)

    if result.blocked:
        msgs = [v.message for v in result.violations
                if "板块" in v.message or "仓位" in v.message]
        return "block", msgs
    if result.violations:
        msgs = [v.message for v in result.violations]
        return "warning", msgs
    return "ok", []


def _simulate_add(portfolio: Portfolio, code: str) -> Portfolio:
    """模拟把 code 加入组合 — 防仓位 / 板块 100% 误报

    stub 主仓 1000 元 + 稀释仓 9000 元 (跨 9 个板块, 每板块 1000 元) →
    market=10000, cash=20000 → equity=30000, position_pct=33% < 80% OK
    板块占比 = (候选 1000 / 10000) = 10% < 40% OK
    """
    from .risk_control import Holding
    stub_holdings = list(portfolio.holdings) + [
        Holding(code=code, shares=100, cost=10, price=10, sector="(候选)"),
    ]
    existing_sectors = {h.sector for h in portfolio.holdings if h.sector}
    if not existing_sectors:
        # 9 个稀释板块各 1000 元
        for i in range(9):
            stub_holdings.append(
                Holding(code=f"_stub_{i}", shares=100, cost=10, price=10,
                        sector=f"(稀释{i})")
            )
    # market = 10000 (候选 + 9 stub), cash 留 20000 → equity 30000
    # position_pct = 10000/30000 = 33% < 80% OK
    # 任何单板块 = 1000/10000 = 10% < 40% OK
    new = Portfolio(
        holdings=stub_holdings,
        cash=max(portfolio.cash, 20000),
        total_capital=portfolio.total_capital or 30000,
        initial_capital=portfolio.initial_capital or 30000,
    )
    return new


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def combine(
    factors: list[FactorScore],
    portfolio: Optional[Portfolio] = None,
    cfg: Optional[StrategyConfig] = None,
    risk_cfg: Optional[RiskConfig] = None,
) -> list[StockPick]:
    """多因子综合 + 风控过滤 → 推荐列表

    Args:
        factors: FactorScore 列表 (已 composite_score 算好)
        portfolio: 当前持仓, 用来评估风控
        cfg: 策略配置
        risk_cfg: 风控配置

    Returns:
        按 final_score 降序的 StockPick 列表
    """
    cfg = cfg or StrategyConfig()
    risk_cfg = risk_cfg or RiskConfig()
    portfolio = portfolio or Portfolio()
    exclude = set(cfg.exclude_codes)

    picks: list[StockPick] = []
    for f in factors:
        # 黑名单
        if f.code in exclude:
            continue
        # 因子硬阈值
        if f.composite < cfg.min_factor_score:
            continue
        # 风控
        try:
            severity, msgs = _evaluate_stock_risk(f.code, portfolio, risk_cfg)
        except Exception as e:
            logger.debug("风控评估 %s 失败: %s", f.code, e)
            severity, msgs = "skip", []
        modifier = _RISK_MODIFIER.get(severity, 1.0)
        # 综合分 = factor × risk_modifier (组合分 = cfg 加权)
        raw_score = (cfg.factor_weight * f.composite
                     + cfg.risk_weight * (modifier - 0.5))
        # final = raw × modifier (block 直接 0)
        final = raw_score * modifier if severity != "block" else 0.0
        picks.append(StockPick(
            code=f.code, composite=f.composite,
            factor_score=f, risk_severity=severity, risk_messages=msgs,
            final_score=round(final, 4),
        ))
    # 排序 + rank
    picks.sort(key=lambda p: p.final_score, reverse=True)
    for i, p in enumerate(picks, 1):
        p.rank = i
    return picks[:cfg.max_recommendations]


def to_dict_list(picks: list[StockPick]) -> list[dict]:
    """StockPick → JSON dict"""
    out = []
    for p in picks:
        out.append({
            "code": p.code,
            "rank": p.rank,
            "composite": p.composite,
            "final_score": p.final_score,
            "risk_severity": p.risk_severity,
            "risk_messages": p.risk_messages,
            "factor": {
                "sector_rotation": p.factor_score.sector_rotation,
                "event": p.factor_score.event,
                "sentiment": p.factor_score.sentiment,
                "momentum": p.factor_score.momentum,
                "volatility": p.factor_score.volatility,
                "confidence": p.factor_score.confidence,
            },
        })
    return out