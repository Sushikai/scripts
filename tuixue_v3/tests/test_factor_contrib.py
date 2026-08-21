#!/usr/bin/env python3
"""
test_factor_contrib.py
Ship 41 单元测试 — 因子贡献度分解
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_contrib import (
    FactorContribution, ContributionResult,
    decompose, decompose_with_zscore,
    top_contributors, summarize,
    decay_aware_contrib, rank_by_ic, rank_by_abs_ic,
)


class TestDecompose(unittest.TestCase):
    def test_basic(self):
        data = {
            "mom": (0.1, 1.0),    # contrib = 0.1
            "val": (0.05, -1.0),  # contrib = -0.05
            "vol": (0.2, 0.5),    # contrib = 0.1
        }
        r = decompose(data)
        self.assertAlmostEqual(r.total_contrib, 0.15)

    def test_pct_sums_to_one(self):
        data = {
            "a": (0.1, 1.0),
            "b": (0.05, 1.0),
            "c": (0.2, 1.0),
        }
        r = decompose(data)
        total_pct = sum(c.pct for c in r.contributions)
        self.assertAlmostEqual(total_pct, 1.0, places=4)

    def test_dominant(self):
        r = decompose({
            "a": (0.1, 0.1),    # 0.01
            "b": (0.5, 2.0),    # 1.0
            "c": (0.05, 0.1),   # 0.005
        })
        self.assertEqual(r.dominant, "b")

    def test_total_alpha_override(self):
        r = decompose({"a": (0.1, 1.0)}, total_alpha=0.5)
        self.assertEqual(r.total_alpha, 0.5)
        self.assertAlmostEqual(r.total_contrib, 0.1)

    def test_empty(self):
        r = decompose({})
        self.assertEqual(r.total_contrib, 0.0)
        self.assertIsNone(r.dominant)
        self.assertEqual(r.total_alpha, 0.0)

    def test_all_zero(self):
        r = decompose({
            "a": (0.0, 1.0),
            "b": (0.0, 1.0),
        })
        # 全 0 → 等分
        self.assertAlmostEqual(r.contributions[0].pct, 0.5)


class TestDecomposeZscore(unittest.TestCase):
    def test_basic(self):
        zscores = {"mom": 1.5, "val": -2.0, "vol": 0.5}
        ics = {"mom": 0.1, "val": 0.05, "vol": 0.2}
        r = decompose_with_zscore(zscores, ics)
        # mom: 0.1 * 1.5 = 0.15
        self.assertAlmostEqual(r.contributions[0].contribution, 0.15)
        # val: 0.05 * -2.0 = -0.1
        self.assertAlmostEqual(r.contributions[1].contribution, -0.1)


class TestTopContributors(unittest.TestCase):
    def test_basic(self):
        r = decompose({
            "a": (0.1, 0.1),    # 0.01
            "b": (0.5, 2.0),    # 1.0
            "c": (0.05, 0.1),   # 0.005
            "d": (0.2, -3.0),   # -0.6
        })
        top = top_contributors(r, n=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0].factor, "b")


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        r = decompose({"a": (0.1, 1.0), "b": (0.2, 1.0)})
        d = summarize(r)
        self.assertIn("total_alpha", d)
        self.assertIn("dominant", d)
        self.assertIn("factors", d)
        self.assertEqual(len(d["factors"]), 2)


class TestDecayAware(unittest.TestCase):
    def test_decay_discount(self):
        ics = {"x": 0.1, "y": 0.2}
        exposures = {"x": 1.0, "y": 1.0}
        decay = {"x": 0.5, "y": 0.0}  # x 衰减 50%
        r = decay_aware_contrib(ics, exposures, decay)
        # x: 0.1 * (1 - 0.5) * 1.0 = 0.05
        # y: 0.2 * 1.0 * 1.0 = 0.2
        self.assertAlmostEqual(r.contributions[0].contribution, 0.05)
        self.assertAlmostEqual(r.contributions[1].contribution, 0.2)

    def test_missing_decay_treated_as_zero(self):
        ics = {"x": 0.1}
        exposures = {"x": 1.0}
        decay = {}  # 没有
        r = decay_aware_contrib(ics, exposures, decay)
        self.assertAlmostEqual(r.contributions[0].contribution, 0.1)


class TestRank(unittest.TestCase):
    def test_rank_by_ic(self):
        ics = {"a": 0.05, "b": 0.15, "c": -0.1}
        ranked = rank_by_ic(ics, n=2)
        self.assertEqual(ranked[0][0], "b")
        self.assertEqual(ranked[1][0], "a")

    def test_rank_by_abs_ic(self):
        ics = {"a": 0.05, "b": -0.2, "c": 0.1}
        ranked = rank_by_abs_ic(ics, n=2)
        self.assertEqual(ranked[0][0], "b")
        self.assertEqual(ranked[1][0], "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
