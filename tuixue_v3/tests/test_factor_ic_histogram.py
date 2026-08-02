#!/usr/bin/env python3
"""
test_factor_ic_histogram.py
Ship 48 单元测试 — 因子 IC 直方图
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_ic_histogram import (
    Bin, ICHistogramResult, DEFAULT_BINS,
    build_histogram, build_multi,
    to_echarts, to_dict, interpret,
)


class TestBuild(unittest.TestCase):
    def test_basic(self):
        ic = [0.05, 0.06, 0.04, 0.07, -0.02, 0.03, 0.05, 0.06, 0.04, 0.05]
        h = build_histogram(ic, "mom")
        self.assertEqual(h.n, 10)
        self.assertEqual(h.factor, "mom")
        # 总 bin 计数应等于 n
        total = sum(b.count for b in h.bins)
        self.assertEqual(total, 10)

    def test_stats(self):
        ic = [0.05] * 30 + [-0.02, -0.03, -0.04]
        h = build_histogram(ic, "mom")
        self.assertEqual(h.n, 33)
        # 均值偏正
        self.assertGreater(h.stats["mean"], 0)
        # hit_rate ≈ 30/33 (前 30 个都 > 0)
        self.assertGreater(h.stats["hit_rate"], 0.5)
        # t-stat 应 > 2
        self.assertGreater(h.stats["t_stat"], 2)

    def test_outside_bins(self):
        ic = [0.0, 0.0, 0.0, 0.5, 0.5]  # 后两个大于默认 bins 上限 0.3
        h = build_histogram(ic)
        total = sum(b.count for b in h.bins)
        self.assertEqual(total, 5)

    def test_below_bins(self):
        ic = [-0.5] * 5  # 小于 -0.3
        h = build_histogram(ic)
        total = sum(b.count for b in h.bins)
        self.assertEqual(total, 5)

    def test_empty(self):
        h = build_histogram([])
        self.assertEqual(h.n, 0)
        self.assertEqual(h.bins, [])
        self.assertFalse(h.is_valid)

    def test_is_valid_threshold(self):
        h = build_histogram([0.05] * 30)
        self.assertTrue(h.is_valid)
        h = build_histogram([0.05] * 20)
        self.assertFalse(h.is_valid)


class TestBuildMulti(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05] * 30 + [0.04, 0.03, 0.06],
            "b": [-0.02] * 30 + [-0.03, -0.01, -0.04],
        }
        results = build_multi(ic_dict)
        self.assertEqual(len(results), 2)
        self.assertEqual({r.factor for r in results}, {"a", "b"})


class TestToEcharts(unittest.TestCase):
    def test_basic(self):
        h = build_histogram([0.05] * 30, "test")
        out = to_echarts(h)
        self.assertEqual(out["factor"], "test")
        self.assertEqual(out["n"], 30)
        self.assertIn("x_data", out)
        self.assertIn("data", out)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        h = build_histogram([0.05] * 30, "test")
        d = to_dict(h)
        self.assertIn("bins", d)
        self.assertIn("stats", d)


class TestInterpret(unittest.TestCase):
    def test_significant_positive(self):
        h = build_histogram([0.05] * 30 + [0.06, 0.04, 0.05])
        s = interpret(h)
        self.assertIn("正向", s)
        self.assertIn("显著", s)

    def test_insufficient(self):
        h = build_histogram([0.05] * 10)
        s = interpret(h)
        self.assertIn("样本不足", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
