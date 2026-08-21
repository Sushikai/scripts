#!/usr/bin/env python3
"""
tuixue_v3/portfolio_snapshot.py
Ship 33/100 — 组合快照 (定期存盘 + 历史查询)

设计:
每日/每周保存一份组合快照 (持仓 + 净值 + 元数据):
- date, cash, holdings (code → {shares, cost, price}), equity
- 用于: 历史回放、业绩对比、回测起点

输出:
- Snapshot: 单日快照
- load/save: 存 dict 到内存 (实际接 redis/json 文件)

降级: 缺字段 → 兜底, 不抛异常

2026-08-02 Ship 33 — 10000 轮迭代 P3 第八步
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class PortfolioSnapshot:
    """组合快照"""
    date: str
    timestamp: float
    cash: float
    holdings: dict[str, dict]   # code → {shares, cost, price, sector}
    equity: float
    initial_capital: float
    n_positions: int
    n_total_trades: int = 0
    notes: str = ""


# ═══════════════════════════════════════════════════════
# 快照存储
# ═══════════════════════════════════════════════════════

class SnapshotStore:
    """快照存储 (内存)"""
    def __init__(self, max_history: int = 365):
        self.max_history = max_history
        self._snapshots: dict[str, PortfolioSnapshot] = {}
        self._order: deque = deque(maxlen=max_history)

    def save(self, snap: PortfolioSnapshot) -> None:
        """保存快照 (覆盖同日)"""
        if snap.date in self._snapshots:
            logger.debug("覆盖快照 %s", snap.date)
        self._snapshots[snap.date] = snap
        if snap.date not in self._order:
            self._order.append(snap.date)

    def get(self, date: str) -> Optional[PortfolioSnapshot]:
        """取单日快照"""
        return self._snapshots.get(date)

    def latest(self) -> Optional[PortfolioSnapshot]:
        """最近快照"""
        if not self._order:
            return None
        return self._snapshots[self._order[-1]]

    def history(self, days: int = 30) -> list[PortfolioSnapshot]:
        """最近 N 天快照"""
        recent = list(self._order)[-days:]
        return [self._snapshots[d] for d in recent if d in self._snapshots]

    def equity_curve(self) -> list[tuple[str, float]]:
        """equity 曲线"""
        return [(d, self._snapshots[d].equity) for d in self._order
                if d in self._snapshots]

    def clear(self) -> None:
        """清空"""
        self._snapshots.clear()
        self._order.clear()


# ═══════════════════════════════════════════════════════
# 快照构造
# ═══════════════════════════════════════════════════════

def make_snapshot(
    date: str,
    cash: float,
    holdings: dict,                # code → Holding 或 {shares, cost, price}
    initial_capital: float,
    prices: Optional[dict[str, float]] = None,
    n_total_trades: int = 0,
    notes: str = "",
) -> PortfolioSnapshot:
    """构造快照

    Args:
        date: YYYY-MM-DD
        cash: 现金
        holdings: 当前持仓, dict[code] → Holding-like
        initial_capital: 初始资金
        prices: 最新价 (缺则用 holding.cost 估值)
        n_total_trades: 累计交易笔数
        notes: 备注
    """
    prices = prices or {}
    h_dict = {}
    market_value = 0.0
    for code, h in holdings.items():
        # 兼容 Holding dataclass 和 dict
        if hasattr(h, "shares"):
            shares, cost, price, sector = h.shares, h.cost, h.price, h.sector
        else:
            shares = h.get("shares", 0)
            cost = h.get("cost", 0)
            price = h.get("price", 0)
            sector = h.get("sector", "")
        cur_px = prices.get(code, price)
        market_value += shares * cur_px
        h_dict[code] = {
            "shares": shares, "cost": cost,
            "price": cur_px, "sector": sector,
        }

    equity = cash + market_value
    return PortfolioSnapshot(
        date=date, timestamp=time.time(),
        cash=cash, holdings=h_dict,
        equity=equity, initial_capital=initial_capital,
        n_positions=len(h_dict),
        n_total_trades=n_total_trades,
        notes=notes,
    )


def to_dict(snap: PortfolioSnapshot) -> dict:
    return asdict(snap)


def from_dict(d: dict) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        date=d["date"], timestamp=d["timestamp"],
        cash=d["cash"], holdings=d["holdings"],
        equity=d["equity"], initial_capital=d["initial_capital"],
        n_positions=d["n_positions"],
        n_total_trades=d.get("n_total_trades", 0),
        notes=d.get("notes", ""),
    )


def compute_drawdown_series(snapshots: list[PortfolioSnapshot]) -> list[tuple[str, float]]:
    """回撤序列 (date → dd%)"""
    if not snapshots:
        return []
    peak = snapshots[0].equity
    series = []
    for s in snapshots:
        if s.equity > peak:
            peak = s.equity
        dd = (s.equity - peak) / peak if peak > 0 else 0
        series.append((s.date, round(dd, 4)))
    return series
