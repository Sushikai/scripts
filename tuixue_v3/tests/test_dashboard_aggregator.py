#!/usr/bin/env python3
"""
test_dashboard_aggregator.py
Ship 36 单元测试 — Dashboard 数据聚合器
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.dashboard_aggregator import (
    DashboardData, aggregate_dashboard, to_dict, to_summary,
)


class TestAggregateDashboard(unittest.TestCase):
    def test_no_args(self):
        """无参数: 全用默认值 (initial=0 避免 equity=0 触发 -100%)"""
        d = aggregate_dashboard(initial_capital=0)
        self.assertEqual(d.regime, "unknown")
        self.assertEqual(d.equity, 0.0)
        self.assertEqual(d.cash, 0.0)
        self.assertEqual(d.n_positions, 0)
        self.assertEqual(d.total_pnl_pct, 0.0)
        self.assertEqual(d.drawdown, 0.0)
        self.assertEqual(d.n_picks, 0)
        self.assertEqual(d.top_picks, [])
        self.assertEqual(d.n_alerts, 0)
        self.assertEqual(d.health_score, 100.0)
        self.assertEqual(d.issues, [])
        self.assertEqual(d.metrics, {})
        self.assertGreater(d.timestamp, 0)

    def test_profit(self):
        """盈利状态"""
        d = aggregate_dashboard(
            equity=120000, cash=30000,
            initial_capital=100000,
        )
        # (120000 - 100000) / 100000 = 0.2
        self.assertAlmostEqual(d.total_pnl_pct, 0.2)
        # 盈利 → drawdown=0
        self.assertAlmostEqual(d.drawdown, 0.0)

    def test_loss(self):
        """亏损状态"""
        d = aggregate_dashboard(
            equity=80000, cash=20000,
            initial_capital=100000,
        )
        # (80000 - 100000) / 100000 = -0.2
        self.assertAlmostEqual(d.total_pnl_pct, -0.2)
        # drawdown = -min(0, -0.2) = 0.2
        self.assertAlmostEqual(d.drawdown, 0.2)

    def test_top_picks_limit(self):
        """top_picks 限 5"""
        picks = [{"code": f"00000{i}"} for i in range(20)]
        d = aggregate_dashboard(picks=picks)
        self.assertEqual(len(d.top_picks), 5)
        self.assertEqual(d.n_picks, 20)

    def test_with_all_args(self):
        """全参数"""
        d = aggregate_dashboard(
            regime="bull",
            regime_factor=1.2,
            recovery_factor=0.8,
            equity=110000, cash=25000,
            initial_capital=100000,
            n_positions=5,
            picks=[{"code": "000001"}, {"code": "000002"}],
            alerts=[{"msg": "alert1"}, {"msg": "alert2"}, {"msg": "alert3"}],
            health_score=85.5,
            issues=["src slow"],
            metrics={"ic": 0.05, "wr": 0.6},
        )
        self.assertEqual(d.regime, "bull")
        self.assertAlmostEqual(d.regime_factor, 1.2)
        self.assertAlmostEqual(d.recovery_factor, 0.8)
        self.assertEqual(d.n_positions, 5)
        self.assertEqual(d.n_picks, 2)
        self.assertEqual(d.n_alerts, 3)
        self.assertAlmostEqual(d.health_score, 85.5)
        self.assertEqual(d.issues, ["src slow"])
        self.assertEqual(d.metrics["ic"], 0.05)
        # top_picks = 2 (少于 5)
        self.assertEqual(len(d.top_picks), 2)

    def test_zero_initial_capital(self):
        """initial=0 不应崩溃"""
        d = aggregate_dashboard(equity=100000, initial_capital=0)
        # 避免除 0, pnl=0
        self.assertEqual(d.total_pnl_pct, 0.0)

    def test_negative_initial_capital(self):
        """initial<0 也不崩"""
        d = aggregate_dashboard(equity=100000, initial_capital=-1)
        self.assertEqual(d.total_pnl_pct, 0.0)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        d = aggregate_dashboard(regime="bull", equity=120000)
        out = to_dict(d)
        self.assertIn("regime", out)
        self.assertIn("equity", out)
        self.assertIn("total_pnl_pct", out)
        self.assertEqual(out["regime"], "bull")
        self.assertEqual(out["equity"], 120000)


class TestToSummary(unittest.TestCase):
    def test_basic(self):
        d = aggregate_dashboard(
            regime="bull", equity=110000,
            picks=[{"c": "1"}] * 3,
            alerts=[{"a": "1"}] * 2,
            health_score=95,
            initial_capital=100000,
        )
        s = to_summary(d)
        # 关键字段都在
        self.assertIn("bull", s)
        self.assertIn("equity=110000", s)
        self.assertIn("pnl=+10.00%", s)
        self.assertIn("picks=3", s)
        self.assertIn("alerts=2", s)
        self.assertIn("health=95", s)

    def test_loss_summary(self):
        d = aggregate_dashboard(
            regime="bear",
            equity=90000,
            initial_capital=100000,
            health_score=60,
        )
        s = to_summary(d)
        self.assertIn("bear", s)
        self.assertIn("pnl=-10.00%", s)
        self.assertIn("dd=10.00%", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
