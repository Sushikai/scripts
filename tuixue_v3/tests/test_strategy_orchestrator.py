#!/usr/bin/env python3
"""
test_strategy_orchestrator.py
Ship 25 单元测试 — 策略编排器 (完整链路)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strategy_orchestrator import orchestrate, TradeContext
from tuixue_v3.risk_control import Holding
from tuixue_v3.strategy_registry import clear, register, StrategyPick


class TestOrchestrate(unittest.TestCase):
    def setUp(self):
        clear()
        @register("mock_strategy", description="mock")
        def mock(ctx):
            return [StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0))
                    for c in ctx.candidates]

    def test_basic_flow(self):
        """完整链路跑通"""
        # 大盘指数 (60 日横盘 → range)
        idx_prices = [3000 + (i % 5 - 2) * 5 for i in range(60)]
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A", "B", "C"],
            factor_scores={"A": 0.8, "B": 0.5, "C": 0.3},
            prices={"A": 10.0, "B": 20.0, "C": 30.0},
            holdings={},
            cash=100000,
            initial_capital=100000,
            index_prices=idx_prices,
            index_volumes=[1e6] * 60,
        )
        r = orchestrate(ctx)
        print(r.summary())
        self.assertEqual(r.date, "2026-08-01")
        self.assertIn(r.regime, ("range", "bull", "bear", "unknown"))
        self.assertGreater(len(r.picks), 0)

    def test_blocked_no_orders(self):
        """block 时不选股"""
        # 满仓 + 巨亏 → block
        h = Holding(code="X", shares=10000, cost=10, price=2, sector="新能源")
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A"],
            factor_scores={"A": 0.5},
            prices={"A": 10.0, "X": 2.0},
            holdings={"X": h},
            cash=0,
            initial_capital=200000,  # 初始 200k, 现在 20000 → -90% block
            index_prices=[3000] * 60,
        )
        r = orchestrate(ctx)
        self.assertTrue(r.blocked)
        self.assertEqual(r.allocations, [])
        self.assertEqual(r.orders, [])

    def test_no_index_prices(self):
        """无指数 → regime unknown"""
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A"],
            factor_scores={"A": 0.8},
            prices={"A": 10.0},
            holdings={},
            cash=100000,
            initial_capital=100000,
            index_prices=None,
        )
        r = orchestrate(ctx)
        self.assertEqual(r.regime, "unknown")

    def test_recovery_factor_applied(self):
        """回撤 → combined_factor 降低"""
        idx = [3000 + i for i in range(60)]
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A"],
            factor_scores={"A": 0.8},
            prices={"A": 10.0},
            holdings={},
            cash=75000,  # -25% drawdown
            initial_capital=100000,
            index_prices=idx,
        )
        r = orchestrate(ctx)
        # recovery factor = 0.1 (tier 5)
        self.assertEqual(r.recovery_factor, 0.1)

    def test_summary_string(self):
        idx = [3000] * 60
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A"],
            factor_scores={"A": 0.8},
            prices={"A": 10.0},
            holdings={},
            cash=100000,
            initial_capital=100000,
            index_prices=idx,
        )
        r = orchestrate(ctx)
        s = r.summary()
        self.assertIn("regime=", s)
        self.assertIn("picks=", s)

    def test_duration_recorded(self):
        idx = [3000] * 60
        ctx = TradeContext(
            date="2026-08-01",
            candidates=["A"],
            factor_scores={"A": 0.8},
            prices={"A": 10.0},
            holdings={},
            cash=100000,
            initial_capital=100000,
            index_prices=idx,
        )
        r = orchestrate(ctx)
        self.assertGreaterEqual(r.duration_ms, 0)

    def test_empty_candidates(self):
        idx = [3000] * 60
        ctx = TradeContext(
            date="2026-08-01",
            candidates=[],
            factor_scores={},
            prices={},
            holdings={},
            cash=100000,
            initial_capital=100000,
            index_prices=idx,
        )
        r = orchestrate(ctx)
        self.assertEqual(r.picks, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
