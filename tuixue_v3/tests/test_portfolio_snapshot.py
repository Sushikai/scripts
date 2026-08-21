#!/usr/bin/env python3
"""
test_portfolio_snapshot.py
Ship 33 单元测试 — 组合快照
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.portfolio_snapshot import (
    PortfolioSnapshot, SnapshotStore,
    make_snapshot, to_dict, from_dict, compute_drawdown_series,
)


def h(code, shares, cost, price, sector=""):
    return {"shares": shares, "cost": cost, "price": price, "sector": sector}


class TestMakeSnapshot(unittest.TestCase):
    def test_basic(self):
        snap = make_snapshot(
            date="2026-08-01", cash=50000,
            holdings={"A": h("A", 100, 10, 12, "新能源")},
            initial_capital=100000,
        )
        self.assertEqual(snap.equity, 50000 + 100 * 12)  # 51200
        self.assertEqual(snap.n_positions, 1)

    def test_empty_holdings(self):
        snap = make_snapshot("d", 100000, {}, 100000)
        self.assertEqual(snap.equity, 100000)
        self.assertEqual(snap.n_positions, 0)

    def test_with_prices_override(self):
        """prices override holdings price"""
        snap = make_snapshot(
            "d", 50000,
            holdings={"A": h("A", 100, 10, 12)},
            initial_capital=100000,
            prices={"A": 15.0},
        )
        # equity = 50000 + 100*15 = 51500
        self.assertEqual(snap.equity, 51500)


class TestSnapshotStore(unittest.TestCase):
    def test_save_and_get(self):
        store = SnapshotStore()
        snap = make_snapshot("2026-08-01", 50000, {}, 100000)
        store.save(snap)
        self.assertEqual(store.get("2026-08-01"), snap)

    def test_latest(self):
        store = SnapshotStore()
        for d in ["2026-08-01", "2026-08-02", "2026-08-03"]:
            store.save(make_snapshot(d, 50000, {}, 100000))
        self.assertEqual(store.latest().date, "2026-08-03")

    def test_history(self):
        store = SnapshotStore()
        for i in range(10):
            d = f"2026-08-{i + 1:02d}"
            store.save(make_snapshot(d, 50000, {}, 100000))
        h = store.history(days=3)
        self.assertEqual(len(h), 3)
        self.assertEqual(h[-1].date, "2026-08-10")

    def test_max_history(self):
        store = SnapshotStore(max_history=5)
        for i in range(10):
            d = f"2026-08-{i + 1:02d}"
            store.save(make_snapshot(d, 50000, {}, 100000))
        self.assertEqual(len(store._order), 5)

    def test_overwrite_same_date(self):
        store = SnapshotStore()
        s1 = make_snapshot("d", 100, {}, 100)
        s2 = make_snapshot("d", 200, {}, 100)
        store.save(s1)
        store.save(s2)
        self.assertEqual(store.get("d").cash, 200)

    def test_equity_curve(self):
        store = SnapshotStore()
        for d, eq in [("d1", 100), ("d2", 110), ("d3", 105)]:
            store.save(make_snapshot(d, eq, {}, 100))
        curve = store.equity_curve()
        self.assertEqual(len(curve), 3)
        self.assertEqual(curve[1][1], 110)

    def test_clear(self):
        store = SnapshotStore()
        store.save(make_snapshot("d", 100, {}, 100))
        store.clear()
        self.assertIsNone(store.get("d"))


class TestToFromDict(unittest.TestCase):
    def test_roundtrip(self):
        snap = make_snapshot("d", 100, {"A": h("A", 100, 10, 12, "X")}, 1000)
        d = to_dict(snap)
        snap2 = from_dict(d)
        self.assertEqual(snap2.cash, 100)
        self.assertEqual(snap2.holdings["A"]["shares"], 100)


class TestDrawdownSeries(unittest.TestCase):
    def test_basic(self):
        # 直接构造快照, 用 cash 字段控制 equity (不用 make_snapshot)
        from tuixue_v3.portfolio_snapshot import PortfolioSnapshot
        snaps = [
            PortfolioSnapshot(date="d1", timestamp=0, cash=100, holdings={},
                              equity=100, initial_capital=100, n_positions=0),
            PortfolioSnapshot(date="d2", timestamp=0, cash=120, holdings={},
                              equity=120, initial_capital=100, n_positions=0),
            PortfolioSnapshot(date="d3", timestamp=0, cash=90, holdings={},
                              equity=90, initial_capital=100, n_positions=0),
        ]
        series = compute_drawdown_series(snaps)
        # d1: peak=100, dd=0; d2: peak=120, dd=0; d3: peak=120, dd=(90-120)/120=-25%
        self.assertEqual(series[0][1], 0)
        self.assertEqual(series[1][1], 0)
        self.assertEqual(series[2][1], -0.25)

    def test_empty(self):
        self.assertEqual(compute_drawdown_series([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
