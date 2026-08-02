#!/usr/bin/env python3
"""
tuixue_v3/risk_control.py
Ship 13/100 — 风控规则引擎 (仓位 / 回撤 / 板块集中度)

设计:
- 4 类规则: 仓位上限 / 单股回撤熔断 / 板块集中度 / 最大连续亏损
- 规则 → 单调函数 evaluate(portfolio, params) → violations / warnings
- portfolio = {holdings: [{code, shares, cost, sector}], cash, ...}
- 失败模式: 数据不全 → 规则跳过 + 标注 skipped, 永不抛错

规则不全: 还差一个市价快照 fetch (后端调), 单测里 mock portfolio 直接给 price。

2026-08-02 Ship 13 — 10000 轮迭代 P2 第三步
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
    code: str
    shares: int                       # 持股数
    cost: float                       # 成本价
    price: float = 0.0                # 现价 (没拿到时=0)
    sector: str = ""                  # 板块 (没拿到时="")
    name: str = ""                    # 名称 (可选)


@dataclass
class Portfolio:
    holdings: list[Holding] = field(default_factory=list)
    cash: float = 0.0                 # 现金
    total_capital: float = 0.0        # 总资金 (= cash + market value)
    initial_capital: float = 0.0      # 初始本金 (算最大回撤用)


@dataclass
class Violation:
    rule: str                         # 规则名
    severity: str                     # "warning" | "block"
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class RiskResult:
    violations: list[Violation] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 跳过的规则名
    metrics: dict = field(default_factory=dict)        # 评估指标 (前端展示)

    @property
    def blocked(self) -> bool:
        return any(v.severity == "block" for v in self.violations)

    def summary(self) -> str:
        if self.blocked:
            return f"BLOCKED ({len(self.violations)} violations)"
        if self.violations:
            return f"WARN ({len(self.violations)})"
        return "OK"


# ═══════════════════════════════════════════════════════
# 评估指标
# ═══════════════════════════════════════════════════════

def compute_metrics(portfolio: Portfolio) -> dict:
    """评估指标 — 仓位 / 板块占比 / 总市值 / 单股浮亏"""
    holdings = portfolio.holdings
    total_market = sum(h.shares * h.price for h in holdings
                       if h.price > 0)
    total_cost = sum(h.shares * h.cost for h in holdings)
    equity = total_market + portfolio.cash

    # 板块占比
    sector_value: dict[str, float] = {}
    for h in holdings:
        if h.price <= 0:
            continue
        sector_value[h.sector or "(未知)"] = (
            sector_value.get(h.sector or "(未知)", 0.0)
            + h.shares * h.price
        )

    sector_pct = {
        s: (v / total_market * 100 if total_market > 0 else 0.0)
        for s, v in sector_value.items()
    }

    # 最大单股浮亏
    max_drawdown_pct = 0.0
    worst_holding = None
    for h in holdings:
        if h.cost <= 0 or h.price <= 0:
            continue
        dd = (h.price / h.cost - 1) * 100
        if dd < max_drawdown_pct:
            max_drawdown_pct = dd
            worst_holding = h.code

    # 总回撤 (从 initial)
    total_dd_pct = 0.0
    if portfolio.initial_capital > 0:
        total_dd_pct = (equity / portfolio.initial_capital - 1) * 100

    return {
        "total_market": round(total_market, 2),
        "total_cost": round(total_cost, 2),
        "equity": round(equity, 2),
        "position_pct": round(total_market / equity * 100, 2) if equity > 0 else 0.0,
        "sector_pct": {s: round(p, 2) for s, p in sector_pct.items()},
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "worst_holding": worst_holding,
        "total_drawdown_pct": round(total_dd_pct, 2),
    }


# ═══════════════════════════════════════════════════════
# 4 类规则
# ═══════════════════════════════════════════════════════

def check_position_limit(metrics: dict, *, max_pct: float = 80.0) -> Optional[Violation]:
    """仓位上限 — 总仓位超 max_pct 报警"""
    pos = metrics.get("position_pct", 0)
    if pos > max_pct:
        return Violation(
            rule="position_limit",
            severity="warning" if pos < max_pct + 10 else "block",
            message=f"仓位 {pos:.1f}% 超上限 {max_pct:.0f}%",
            detail={"position_pct": pos, "limit": max_pct},
        )
    return None


def check_single_drawdown(holdings: list[Holding], *,
                          threshold: float = -15.0) -> list[Violation]:
    """单股浮亏超 threshold 报警 (-15 = 亏 15%)"""
    out = []
    for h in holdings:
        if h.cost <= 0 or h.price <= 0:
            continue
        dd = (h.price / h.cost - 1) * 100
        if dd <= threshold:
            out.append(Violation(
                rule="single_drawdown",
                severity="block" if dd <= threshold * 1.5 else "warning",
                message=f"{h.code or h.name} 浮亏 {dd:.1f}% 触线 {threshold:.0f}%",
                detail={"code": h.code, "drawdown": dd, "threshold": threshold},
            ))
    return out


def check_sector_concentration(sector_pct: dict[str, float], *,
                                threshold: float = 40.0) -> list[Violation]:
    """单板块占比超 threshold 报警"""
    out = []
    for sec, pct in sector_pct.items():
        if pct > threshold:
            out.append(Violation(
                rule="sector_concentration",
                severity="warning" if pct < threshold + 15 else "block",
                message=f"板块 {sec} 占比 {pct:.1f}% 超 {threshold:.0f}%",
                detail={"sector": sec, "pct": pct, "threshold": threshold},
            ))
    return out


def check_total_drawdown(metrics: dict, *,
                          threshold: float = -20.0) -> Optional[Violation]:
    """总回撤超 threshold 报警"""
    dd = metrics.get("total_drawdown_pct", 0)
    if dd <= threshold:
        return Violation(
            rule="total_drawdown",
            severity="block" if dd <= threshold * 1.5 else "warning",
            message=f"总回撤 {dd:.1f}% 触线 {threshold:.0f}%",
            detail={"drawdown": dd, "threshold": threshold},
        )
    return None


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

@dataclass
class RiskConfig:
    max_position_pct: float = 80.0
    single_drawdown_threshold: float = -15.0
    sector_concentration_threshold: float = 40.0
    total_drawdown_threshold: float = -20.0


def evaluate(portfolio: Portfolio,
             config: Optional[RiskConfig] = None) -> RiskResult:
    """跑 4 类规则, 收集 violations + skipped

    Args:
        portfolio: 组合
        config: 风控参数 (None 用默认)

    Returns:
        RiskResult 含 violations / skipped / metrics
    """
    cfg = config or RiskConfig()
    result = RiskResult()

    # 任何需要 price 的规则, 没拿到 price 视为 skipped
    holdings = portfolio.holdings
    any_price = any(h.price > 0 for h in holdings)
    any_sector = any(h.sector for h in holdings)
    if not any_price:
        result.skipped.extend(["single_drawdown", "sector_concentration"])
    if not any_sector:
        result.skipped.append("sector_concentration")

    metrics = compute_metrics(portfolio)
    result.metrics = metrics

    # 仓位上限 (只用 total_market/equity — equity 含 cash 不需 price)
    v = check_position_limit(metrics, max_pct=cfg.max_position_pct)
    if v:
        result.violations.append(v)

    # 单股浮亏
    if "single_drawdown" not in result.skipped:
        result.violations.extend(
            check_single_drawdown(holdings,
                                  threshold=cfg.single_drawdown_threshold)
        )

    # 板块集中度
    if "sector_concentration" not in result.skipped:
        result.violations.extend(
            check_sector_concentration(metrics.get("sector_pct", {}),
                                       threshold=cfg.sector_concentration_threshold)
        )

    # 总回撤
    v = check_total_drawdown(metrics,
                             threshold=cfg.total_drawdown_threshold)
    if v:
        result.violations.append(v)

    return result