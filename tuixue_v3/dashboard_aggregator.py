#!/usr/bin/env python3
"""
tuixue_v3/dashboard_aggregator.py
Ship 36/100 — Dashboard 数据聚合器

设计:
把分散的指标 (regime/health/positions/portfolio) 聚合为一个
DashboardData 快照, 供前端一次性 fetch:
- regime: bull/bear/range/crisis
- portfolio: equity / positions / pnl today
- signals: top picks
- alerts: 最近 N 条告警
- health: 系统健康分
- metrics: 关键 KPI

降级: 任何上游 None → 字段默认, 不阻塞

2026-08-02 Ship 36 — 10000 轮迭代 P3 第十一步
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class DashboardData:
    """Dashboard 完整快照"""
    timestamp: float
    regime: str = "unknown"
    regime_factor: float = 1.0
    recovery_factor: float = 1.0
    equity: float = 0.0
    cash: float = 0.0
    n_positions: int = 0
    total_pnl_pct: float = 0.0
    drawdown: float = 0.0
    n_picks: int = 0
    top_picks: list = field(default_factory=list)
    n_alerts: int = 0
    health_score: float = 100.0
    issues: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# 聚合器
# ═══════════════════════════════════════════════════════

def aggregate_dashboard(
    *,
    regime: Optional[str] = None,
    regime_factor: float = 1.0,
    recovery_factor: float = 1.0,
    equity: float = 0.0,
    cash: float = 0.0,
    initial_capital: float = 100000.0,
    n_positions: int = 0,
    picks: Optional[list] = None,
    alerts: Optional[list] = None,
    health_score: float = 100.0,
    issues: Optional[list] = None,
    metrics: Optional[dict] = None,
) -> DashboardData:
    """聚合 dashboard 数据

    所有参数可选, 缺省用默认值
    """
    picks = picks or []
    alerts = alerts or []
    issues = issues or []
    metrics = metrics or {}

    # 总收益 = (equity - initial) / initial
    total_pnl = (equity - initial_capital) / initial_capital if initial_capital > 0 else 0.0

    # 当前回撤 (用 equity vs initial_capital 简化)
    drawdown = -min(0, total_pnl)

    # top picks (前 5)
    top_picks = picks[:5] if isinstance(picks, list) else []

    return DashboardData(
        timestamp=time.time(),
        regime=regime or "unknown",
        regime_factor=regime_factor,
        recovery_factor=recovery_factor,
        equity=equity,
        cash=cash,
        n_positions=n_positions,
        total_pnl_pct=round(total_pnl, 4),
        drawdown=round(drawdown, 4),
        n_picks=len(picks) if isinstance(picks, list) else 0,
        top_picks=top_picks,
        n_alerts=len(alerts) if isinstance(alerts, list) else 0,
        health_score=health_score,
        issues=list(issues),
        metrics=dict(metrics),
    )


def to_dict(d: DashboardData) -> dict:
    return asdict(d)


def to_summary(d: DashboardData) -> str:
    """一行总结"""
    return (
        f"[{d.regime}] equity={d.equity:.0f} pnl={d.total_pnl_pct:+.2%} "
        f"dd={d.drawdown:.2%} picks={d.n_picks} alerts={d.n_alerts} "
        f"health={d.health_score:.0f}"
    )
