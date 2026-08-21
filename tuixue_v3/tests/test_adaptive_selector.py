#!/usr/bin/env python3
"""
test_adaptive_selector.py
Ship 21 单元测试 — 自适应策略选择器
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.adaptive_selector import select_adaptive, to_dict
from tuixue_v3.signal_metrics import SignalMetrics
from tuixue_v3.strategy_registry import (
    StrategyContext, StrategyPick, register, clear,
)


class TestSelectAdaptive(unittest.TestCase):
    def setUp(self):
        clear()
        # 注册两个测试策略
        @register("alpha", description="alpha strategy")
        def alpha(ctx):
            return [StrategyPick(c, ctx.factor_scores.get(c, 0.0)) for c in ctx.candidates]

        @register("beta", description="beta strategy")
        def beta(ctx):
            return [StrategyPick(c, ctx.factor_scores.get(c, 0.0) * 0.5) for c in ctx.candidates]

    def test_basic(self):
        ctx = StrategyContext(
            date="2026-08-01", candidates=["A", "B"],
            factor_scores={"A": 0.8, "B": 0.5},
            regime="bull",
            portfolio_value=110000, initial_capital=100000,
        )
        r = select_adaptive(ctx)
        # alpha + beta 都推荐 A (0.8) + B (0.25)
        # merged: A = 0.8 + 0.4 = 1.2, B = 0.5 + 0.25 = 0.75
        # × combined_factor (regime bull=1.0 × recovery 1.0) = 1.0
        # A 最高分
        self.assertEqual(r.picks[0].code, "A")
        self.assertEqual(r.regime_factor, 1.0)
        self.assertGreater(r.combined_factor, 0.5)

    def test_no_strategies(self):
        clear()
        ctx = StrategyContext("d", ["A"], {"A": 0.5})
        r = select_adaptive(ctx)
        self.assertEqual(r.picks, [])
        self.assertIn("无任何策略适用", r.reasons[0])

    def test_unhealthy_strategy_skipped(self):
        unhealthy_metrics = {
            "alpha": SignalMetrics(
                factor="alpha", n_samples=50,
                precision=0.2, recall=0.2, ic=0.0,
                hit_rate=0.3, avg_predicted=0.5, avg_actual=0.01,
                is_healthy=False, reasons=["precision 20%"],
            ),
        }
        ctx = StrategyContext("d", ["A"], {"A": 0.8}, regime="bull")
        r = select_adaptive(ctx, strategy_metrics=unhealthy_metrics)
        # alpha 被跳过, 只剩 beta
        self.assertNotIn("alpha", r.strategies_used)
        self.assertIn("beta", r.strategies_used)

    def test_healthy_strategy_kept(self):
        healthy_metrics = {
            "alpha": SignalMetrics(
                factor="alpha", n_samples=50,
                precision=0.6, recall=0.6, ic=0.05,
                hit_rate=0.7, avg_predicted=0.5, avg_actual=0.03,
                is_healthy=True,
            ),
        }
        ctx = StrategyContext("d", ["A"], {"A": 0.8}, regime="bull")
        r = select_adaptive(ctx, strategy_metrics=healthy_metrics)
        self.assertIn("alpha", r.strategies_used)

    def test_insufficient_samples_kept(self):
        """样本 < 30 → 默认 healthy → 保留"""
        insufficient = {
            "alpha": SignalMetrics(
                factor="alpha", n_samples=10,
                precision=0.0, recall=0.0, ic=0.0,
                hit_rate=0.0, avg_predicted=0.5, avg_actual=0.01,
                is_healthy=True, reasons=["样本不足"],
            ),
        }
        ctx = StrategyContext("d", ["A"], {"A": 0.8})
        r = select_adaptive(ctx, strategy_metrics=insufficient)
        self.assertIn("alpha", r.strategies_used)

    def test_regime_factor_crisis(self):
        ctx = StrategyContext("d", ["A"], {"A": 0.8},
                              regime="crisis",
                              portfolio_value=100000, initial_capital=100000)
        r = select_adaptive(ctx)
        self.assertEqual(r.regime_factor, 0.1)
        # combined 接近 0.1
        self.assertAlmostEqual(r.combined_factor, 0.1, places=2)

    def test_recovery_factor_block(self):
        ctx = StrategyContext("d", ["A"], {"A": 0.8},
                              regime="bull",
                              portfolio_value=75000, initial_capital=100000)
        r = select_adaptive(ctx)
        # dd=-25% → tier 5 → recovery_factor = 0.1
        self.assertEqual(r.recovery_factor, 0.1)

    def test_combined_factor(self):
        """regime × recovery 复合"""
        ctx = StrategyContext("d", ["A"], {"A": 0.8},
                              regime="range",  # 0.6
                              portfolio_value=85000, initial_capital=100000)  # -15% tier 3 (0.5)
        r = select_adaptive(ctx)
        # combined = 0.6 × 0.5 = 0.3
        self.assertAlmostEqual(r.combined_factor, 0.3, places=2)

    def test_final_position_capped(self):
        ctx = StrategyContext("d", ["A"], {"A": 0.8},
                              regime="bull",
                              portfolio_value=100000, initial_capital=100000)
        r = select_adaptive(ctx, base_position_pct=0.20)
        # combined=1.0 → final_position = 0.20
        self.assertAlmostEqual(r.final_position_pct, 0.20, places=4)

    def test_picks_sorted_desc(self):
        ctx = StrategyContext("d", ["A", "B", "C"],
                              factor_scores={"A": 0.3, "B": 0.9, "C": 0.6},
                              regime="bull")
        r = select_adaptive(ctx)
        scores = [p.score for p in r.picks]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestToDict(unittest.TestCase):
    def test_basic(self):
        clear()
        @register("test")
        def t(ctx): return [StrategyPick("A", 0.5)]
        ctx = StrategyContext("d", ["A"], {"A": 0.5})
        r = select_adaptive(ctx)
        d = to_dict(r)
        self.assertIn("regime", d)
        self.assertIn("picks", d)
        self.assertEqual(d["picks"][0]["code"], "A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
