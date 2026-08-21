#!/usr/bin/env python3
"""
test_risk_control.py
Ship 13 单元测试 — 风控规则引擎 (仓位/单股/板块/总回撤)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.risk_control import (
    Holding, Portfolio, Violation, RiskResult, RiskConfig,
    compute_metrics, evaluate,
    check_position_limit, check_single_drawdown,
    check_sector_concentration, check_total_drawdown,
)


def make_holding(code, shares, cost, price=0, sector="", name=""):
    return Holding(code=code, shares=shares, cost=cost,
                   price=price, sector=sector, name=name or code)


class TestComputeMetrics(unittest.TestCase):
    def test_empty(self):
        m = compute_metrics(Portfolio())
        self.assertEqual(m["position_pct"], 0.0)
        self.assertEqual(m["total_market"], 0.0)
        self.assertEqual(m["max_drawdown_pct"], 0.0)
        self.assertEqual(m["sector_pct"], {})

    def test_basic(self):
        p = Portfolio(
            holdings=[
                make_holding("A", 100, 10, price=12, sector="新能源"),
                make_holding("B", 200, 5, price=4, sector="新能源"),
                make_holding("C", 50, 20, price=18, sector="半导体"),
            ],
            cash=1000,
            initial_capital=10000,
        )
        m = compute_metrics(p)
        self.assertEqual(m["total_market"], 100 * 12 + 200 * 4 + 50 * 18)  # 2900
        self.assertEqual(m["equity"], 2900 + 1000)
        # sector_pct 按市值占比
        self.assertAlmostEqual(m["sector_pct"]["新能源"], 2000 / 2900 * 100, places=2)
        self.assertAlmostEqual(m["sector_pct"]["半导体"], 900 / 2900 * 100, places=2)
        # 最差: B, 4/5 - 1 = -20%
        self.assertEqual(m["worst_holding"], "B")
        self.assertAlmostEqual(m["max_drawdown_pct"], -20.0, places=2)

    def test_zero_cost_skipped(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 0, price=12)],
            initial_capital=1000,
        )
        m = compute_metrics(p)
        self.assertEqual(m["max_drawdown_pct"], 0.0)

    def test_zero_price_skipped(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=0)],
        )
        m = compute_metrics(p)
        self.assertEqual(m["total_market"], 0.0)

    def test_total_drawdown(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=8)],
            cash=0,
            initial_capital=2000,
        )
        m = compute_metrics(p)
        # equity = 800, initial = 2000, dd = -60%
        self.assertAlmostEqual(m["total_drawdown_pct"], -60.0, places=2)


class TestCheckPositionLimit(unittest.TestCase):
    def test_under(self):
        self.assertIsNone(check_position_limit({"position_pct": 50.0}, max_pct=80.0))

    def test_over_warning(self):
        v = check_position_limit({"position_pct": 85.0}, max_pct=80.0)
        self.assertIsNotNone(v)
        self.assertEqual(v.severity, "warning")

    def test_over_block(self):
        v = check_position_limit({"position_pct": 95.0}, max_pct=80.0)
        self.assertEqual(v.severity, "block")

    def test_at_limit(self):
        """刚好等于上限不报警"""
        self.assertIsNone(check_position_limit({"position_pct": 80.0}, max_pct=80.0))


class TestCheckSingleDrawdown(unittest.TestCase):
    def test_no_loss(self):
        h = [make_holding("A", 100, 10, price=11)]
        self.assertEqual(check_single_drawdown(h, threshold=-15.0), [])

    def test_warning(self):
        h = [make_holding("A", 100, 10, price=8)]  # -20%
        vs = check_single_drawdown(h, threshold=-15.0)
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0].severity, "warning")

    def test_block(self):
        h = [make_holding("A", 100, 10, price=7)]  # -30%
        vs = check_single_drawdown(h, threshold=-15.0)
        self.assertEqual(vs[0].severity, "block")

    def test_zero_cost_skipped(self):
        h = [make_holding("A", 100, 0, price=5)]
        self.assertEqual(check_single_drawdown(h), [])

    def test_multiple_holdings(self):
        h = [
            make_holding("A", 100, 10, price=11),  # +10%
            make_holding("B", 100, 10, price=8),   # -20%
            make_holding("C", 100, 10, price=5),   # -50% (block)
        ]
        vs = check_single_drawdown(h, threshold=-15.0)
        self.assertEqual(len(vs), 2)
        self.assertEqual({v.detail["code"] for v in vs}, {"B", "C"})


class TestCheckSectorConcentration(unittest.TestCase):
    def test_under(self):
        self.assertEqual(check_sector_concentration({"A": 30.0}), [])

    def test_warning(self):
        vs = check_sector_concentration({"A": 50.0})
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0].severity, "warning")

    def test_block(self):
        vs = check_sector_concentration({"A": 60.0})
        self.assertEqual(vs[0].severity, "block")

    def test_multi_sector(self):
        pct = {"新能源": 30.0, "半导体": 50.0, "医药": 20.0}
        vs = check_sector_concentration(pct, threshold=40.0)
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0].detail["sector"], "半导体")


class TestCheckTotalDrawdown(unittest.TestCase):
    def test_no_dd(self):
        self.assertIsNone(check_total_drawdown({"total_drawdown_pct": 0}))

    def test_warning(self):
        v = check_total_drawdown({"total_drawdown_pct": -25.0})
        self.assertEqual(v.severity, "warning")

    def test_block(self):
        v = check_total_drawdown({"total_drawdown_pct": -35.0})
        self.assertEqual(v.severity, "block")

    def test_under_threshold(self):
        self.assertIsNone(check_total_drawdown({"total_drawdown_pct": -10.0}))


class TestEvaluate(unittest.TestCase):
    """主入口 — 4 类规则联动"""

    def test_clean_portfolio(self):
        """轻仓 + 浮盈 + 多板块分散 + 无回撤 → OK"""
        p = Portfolio(
            holdings=[
                make_holding("A", 100, 10, price=11, sector="新能源"),
                make_holding("B", 100, 10, price=11, sector="半导体"),
                make_holding("C", 100, 10, price=11, sector="医药"),
            ],
            cash=9000,
            initial_capital=10000,
        )
        r = evaluate(p)
        self.assertEqual(r.violations, [])
        self.assertEqual(r.summary(), "OK")
        self.assertFalse(r.blocked)

    def test_no_price_skipped(self):
        """没 price → 单股 / 板块 skip, 仓位和总回撤仍算"""
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=0, sector="新能源")],
            cash=1000,
            initial_capital=2000,
        )
        r = evaluate(p)
        # 没 price 时 total_market=0, position_pct=0/1000=0 (但 equity=1000 仍 >0)
        self.assertIn("single_drawdown", r.skipped)
        self.assertIn("sector_concentration", r.skipped)

    def test_no_sector_skipped(self):
        """有 price 但没 sector → 板块 skip"""
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=11, sector="")],
            cash=1000,
        )
        r = evaluate(p)
        self.assertIn("sector_concentration", r.skipped)
        self.assertNotIn("single_drawdown", r.skipped)

    def test_position_over(self):
        p = Portfolio(
            holdings=[
                make_holding("A", 1000, 10, price=10, sector="新能源"),
            ],
            cash=100,
            initial_capital=20000,
        )
        # market=10000, equity=10100, position_pct=99%
        r = evaluate(p)
        self.assertTrue(any(v.rule == "position_limit" for v in r.violations))

    def test_single_dd_block(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=7, sector="新能源")],
            cash=5000,
            initial_capital=10000,
        )
        r = evaluate(p)
        self.assertTrue(any(v.rule == "single_drawdown" for v in r.violations))

    def test_total_dd_block(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=5)],
            cash=0,
            initial_capital=2000,
        )
        # equity=500, dd=-75% block
        r = evaluate(p)
        self.assertTrue(r.blocked)
        self.assertTrue(any(v.rule == "total_drawdown" for v in r.violations))

    def test_sector_concentration(self):
        p = Portfolio(
            holdings=[
                make_holding("A", 1000, 10, price=10, sector="新能源"),
                make_holding("B", 100, 20, price=20, sector="半导体"),
            ],
            cash=100,
            initial_capital=20000,
        )
        # 新能源市值 10000, 半导体 2000, 总市值 12000, 新能源占比 83%
        r = evaluate(p)
        self.assertTrue(any(v.rule == "sector_concentration" for v in r.violations))

    def test_metrics_in_result(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=11, sector="X")],
            cash=500,
            initial_capital=2000,
        )
        r = evaluate(p)
        self.assertIn("position_pct", r.metrics)
        self.assertIn("equity", r.metrics)

    def test_custom_config(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=12, sector="X")],
            cash=0,
            initial_capital=500,
        )
        # market=1200, equity=1200, position_pct=100%
        cfg = RiskConfig(max_position_pct=95.0)
        r = evaluate(p, cfg)
        # 100% > 95% → block
        self.assertTrue(any(v.rule == "position_limit" for v in r.violations))

    def test_blocked_property(self):
        p = Portfolio(
            holdings=[make_holding("A", 100, 10, price=5)],
            initial_capital=2000,
        )
        r = evaluate(p)
        self.assertTrue(r.blocked)

    def test_summary_string(self):
        p = Portfolio(holdings=[], cash=1000)
        self.assertEqual(evaluate(p).summary(), "OK")
        # 注入一个 warning
        p2 = Portfolio(
            holdings=[make_holding("A", 100, 10, price=9, sector="X")],
            cash=0, initial_capital=2000,
        )
        # -10% 不触发 (-15 threshold), 总回撤 -50% 触发
        s = evaluate(p2).summary()
        self.assertIn("BLOCKED", s)  # 总回撤 -50% block

    def test_to_dict_violations(self):
        v = Violation("x", "warning", "msg", {"k": 1})
        d = v.__dict__
        self.assertEqual(d["rule"], "x")
        self.assertEqual(d["detail"]["k"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)