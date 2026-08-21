#!/usr/bin/env python3
"""
test_strat_era12_black_litterman.py
Ship 68 单元测试 — Black-Litterman 简化版
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era12_black_litterman import (
    BLResult, market_implied_prior, combine_views,
    posterior_weights, optimize, momentum_views, summarize,
)


class TestMarketImpliedPrior(unittest.TestCase):
    def test_basic(self):
        caps = {"a": 100.0, "b": 300.0}
        prior = market_implied_prior(caps, vols={"a": 0.2, "b": 0.3})
        self.assertEqual(len(prior), 2)
        self.assertGreater(prior["b"], prior["a"])

    def test_empty(self):
        self.assertEqual(market_implied_prior({}), {})


class TestCombineViews(unittest.TestCase):
    def test_basic(self):
        prior = {"a": 0.05, "b": 0.10}
        views = {"a": 0.20, "b": 0.05}
        out = combine_views(prior, views, default_confidence=0.5)
        # 后验 = 0.5 * view + 0.5 * prior
        self.assertAlmostEqual(out["a"][0], 0.125)
        self.assertAlmostEqual(out["b"][0], 0.075)

    def test_only_prior(self):
        prior = {"a": 0.05}
        out = combine_views(prior, {}, default_confidence=0.5)
        # a 仅 prior → posterior = prior
        self.assertAlmostEqual(out["a"][0], 0.05)


class TestPosteriorWeights(unittest.TestCase):
    def test_basic(self):
        posterior = {"a": 0.10, "b": 0.20}
        vols = {"a": 0.20, "b": 0.20}
        w = posterior_weights(posterior, vols)
        # 后验比 = 1:2 → 权重 b 应 = 2*a
        self.assertAlmostEqual(w["b"], 2 * w["a"])

    def test_negative(self):
        posterior = {"a": -0.10, "b": -0.20}
        vols = {"a": 0.20, "b": 0.20}
        w = posterior_weights(posterior, vols)
        # 全负 → 平均分配
        self.assertAlmostEqual(w["a"], w["b"])


class TestOptimize(unittest.TestCase):
    def test_basic(self):
        caps = {"a": 100.0, "b": 200.0, "c": 300.0}
        views = {"a": 0.15, "b": 0.05}
        vols = {"a": 0.20, "b": 0.30, "c": 0.25}
        r = optimize(caps, views, vols=vols)
        self.assertEqual(len(r.posterior_returns), 3)
        self.assertAlmostEqual(sum(r.weights.values()), 1.0, places=2)

    def test_no_views(self):
        caps = {"a": 100.0, "b": 200.0}
        r = optimize(caps, {}, vols={"a": 0.2, "b": 0.3})
        self.assertEqual(r.n_views, 0)
        self.assertEqual(r.n_prior, 2)


class TestMomentumViews(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 - i * 0.1 for i in range(100)],
        }
        views = momentum_views(universe, lookback=60)
        self.assertEqual(len(views), 2)
        self.assertGreater(views["a"], 0)
        self.assertLess(views["b"], 0)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        caps = {"a": 100.0, "b": 200.0}
        r = optimize(caps, {"a": 0.15}, vols={"a": 0.2, "b": 0.3})
        s = summarize(r)
        self.assertIn("BL", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        caps = {"a": 100.0}
        r = optimize(caps, {"a": 0.10}, vols={"a": 0.2})
        d = r.to_dict()
        self.assertEqual(d["n_prior"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)