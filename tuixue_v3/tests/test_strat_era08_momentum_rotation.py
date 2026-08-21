#!/usr/bin/env python3
"""
test_strat_era08_momentum_rotation.py
Ship 64 单元测试 — 动量轮动
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era08_momentum_rotation import (
    MomentumScore, return_pct, momentum_score,
    rank_momentum, dual_rotation, risk_adj_momentum, rank_risk_adj, summarize,
)


class TestReturnPct(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 11.0, 12.0]
        self.assertAlmostEqual(return_pct(prices, 2), 0.2)

    def test_short(self):
        self.assertIsNone(return_pct([10.0, 11.0], 5))


class TestMomentumScore(unittest.TestCase):
    def test_basic(self):
        # 250 个, 涨势
        prices = [10.0 + i * 0.05 for i in range(250)]
        ms = momentum_score("a", prices)
        self.assertIsNotNone(ms)
        self.assertGreater(ms.composite, 0)

    def test_short(self):
        ms = momentum_score("a", [10.0] * 100)
        self.assertIsNone(ms)


class TestRankMomentum(unittest.TestCase):
    def test_top_n(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(250)],   # 涨
            "down": [20.0 - i * 0.05 for i in range(250)],   # 跌
            "flat": [15.0] * 250,                            # 横盘
        }
        ranked = rank_momentum(universe, top_n=2)
        self.assertEqual(len(ranked), 2)
        # 第一名是 up
        self.assertEqual(ranked[0].code, "up")

    def test_bottom_n(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(250)],
            "down": [20.0 - i * 0.05 for i in range(250)],
        }
        ranked = rank_momentum(universe, bottom_n=1)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].code, "down")


class TestDualRotation(unittest.TestCase):
    def test_basic(self):
        universe = {f"s{i}": [10.0 + i * 0.01 + j * 0.001 for j in range(250)]
                   for i in range(10)}
        result = dual_rotation(universe, n_each=3)
        self.assertEqual(len(result.long_picks), 3)
        self.assertEqual(len(result.short_picks), 3)


class TestRiskAdjMomentum(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.05 + (i % 3) * 0.1 for i in range(100)]
        ram = risk_adj_momentum("a", prices)
        self.assertIsNotNone(ram)


class TestRankRiskAdj(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 - i * 0.1 for i in range(100)],
        }
        ranked = rank_risk_adj(universe, top_n=2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0][0], "a")


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(250)],
            "down": [20.0 - i * 0.05 for i in range(250)],
        }
        ranked = rank_momentum(universe, top_n=1)
        s = summarize(ranked, top_k=1)
        self.assertIn("up", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.05 for i in range(250)]
        ms = momentum_score("a", prices)
        d = ms.to_dict()
        self.assertEqual(d["code"], "a")
        self.assertIn("composite", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)