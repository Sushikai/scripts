#!/usr/bin/env python3
"""
test_strategy_registry.py
Ship 20 单元测试 — 策略注册表
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strategy_registry import (
    StrategyContext, StrategyPick, StrategyInfo,
    register, get, list_all, enable, disable, clear,
    run_strategy, run_strategies,
)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        clear()  # 每个测试前清空

    def test_register_and_get(self):
        @register("test_strat", description="test strategy")
        def my_strat(ctx):
            return [StrategyPick(code="A", score=0.5)]
        info = get("test_strat")
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "test_strat")
        self.assertTrue(info.enabled)

    def test_get_missing(self):
        self.assertIsNone(get("nonexistent"))

    def test_list_all(self):
        @register("s1")
        def s1(ctx): return []
        @register("s2")
        def s2(ctx): return []
        self.assertEqual(len(list_all()), 2)

    def test_list_enabled_only(self):
        @register("s1")
        def s1(ctx): return []
        @register("s2")
        def s2(ctx): return []
        disable("s1")
        enabled = list_all(enabled_only=True)
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].name, "s2")

    def test_enable_disable(self):
        @register("s1")
        def s1(ctx): return []
        disable("s1")
        self.assertFalse(get("s1").enabled)
        enable("s1")
        self.assertTrue(get("s1").enabled)
        # 不存在的
        self.assertFalse(disable("nope"))

    def test_clear(self):
        @register("s1")
        def s1(ctx): return []
        clear()
        self.assertEqual(len(list_all()), 0)


class TestRunStrategy(unittest.TestCase):
    def setUp(self):
        clear()

    def test_basic_run(self):
        @register("test1", description="test")
        def test1(ctx):
            return [StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0))
                    for c in ctx.candidates]
        ctx = StrategyContext(
            date="2026-08-01", candidates=["A", "B"],
            factor_scores={"A": 0.8, "B": 0.5},
        )
        picks = run_strategy("test1", ctx)
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks[0].code, "A")  # 分数高在前

    def test_missing_strategy(self):
        ctx = StrategyContext("d", [], {})
        self.assertEqual(run_strategy("nonexistent", ctx), [])

    def test_disabled_strategy(self):
        @register("dis")
        def dis(ctx):
            return [StrategyPick("A", 0.5)]
        disable("dis")
        ctx = StrategyContext("d", ["A"], {"A": 0.5})
        self.assertEqual(run_strategy("dis", ctx), [])

    def test_strategy_exception(self):
        @register("broken")
        def broken(ctx):
            raise ValueError("intentional fail")
        ctx = StrategyContext("d", ["A"], {"A": 0.5})
        self.assertEqual(run_strategy("broken", ctx), [])

    def test_regime_suit_downgrade(self):
        """不适合的 regime → 降权 ×0.5"""
        @register("bull_only", regime_suit=("bull",))
        def bull_only(ctx):
            return [StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0))
                    for c in ctx.candidates]
        # bear regime → 降权
        ctx = StrategyContext(
            date="d", candidates=["A"],
            factor_scores={"A": 0.8}, regime="bear",
        )
        picks = run_strategy("bull_only", ctx)
        self.assertEqual(len(picks), 1)
        self.assertAlmostEqual(picks[0].score, 0.4)  # 0.8 × 0.5

    def test_min_factor_score_filter(self):
        @register("filtered", min_factor_score=0.3)
        def filtered(ctx):
            return [StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0))
                    for c in ctx.candidates]
        ctx = StrategyContext(
            date="d", candidates=["A", "B", "C"],
            factor_scores={"A": 0.8, "B": 0.2, "C": 0.5},
        )
        picks = run_strategy("filtered", ctx)
        codes = {p.code for p in picks}
        self.assertIn("A", codes)
        self.assertIn("C", codes)
        self.assertNotIn("B", codes)

    def test_max_recommendations(self):
        @register("capped", max_recommendations=2)
        def capped(ctx):
            return [StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0))
                    for c in ctx.candidates]
        ctx = StrategyContext(
            date="d", candidates=[f"S{i}" for i in range(5)],
            factor_scores={f"S{i}": i / 10.0 for i in range(5)},
        )
        picks = run_strategy("capped", ctx)
        self.assertEqual(len(picks), 2)

    def test_run_strategies_batch(self):
        @register("s1")
        def s1(ctx): return [StrategyPick("A", 0.5)]
        @register("s2")
        def s2(ctx): return [StrategyPick("B", 0.7)]
        ctx = StrategyContext("d", ["A", "B"], {"A": 0.5, "B": 0.7})
        out = run_strategies(["s1", "s2"], ctx)
        self.assertEqual(len(out), 2)
        self.assertIn("s1", out)
        self.assertIn("s2", out)


class TestBuiltins(unittest.TestCase):
    def setUp(self):
        clear()

    def test_top_factor_builtin(self):
        from tuixue_v3.strategy_registry import _top_factor_strategy
        ctx = StrategyContext(
            date="d", candidates=["A", "B", "C"],
            factor_scores={"A": 0.3, "B": 0.9, "C": 0.6},
        )
        picks = _top_factor_strategy(ctx)
        # 函数本身不排序, 由 run_strategy 排序; 但 picks 应包含所有候选
        codes = {p.code for p in picks}
        self.assertEqual(codes, {"A", "B", "C"})
        # B 分最高
        b_pick = next(p for p in picks if p.code == "B")
        self.assertAlmostEqual(b_pick.score, 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
