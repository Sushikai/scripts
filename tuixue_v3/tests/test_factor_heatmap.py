#!/usr/bin/env python3
"""
test_factor_heatmap.py
Ship 37 单元测试 — 因子热力图
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_heatmap import (
    HeatmapCell, FactorHeatmap,
    build_heatmap, to_echarts, summarize,
)


class TestBuild(unittest.TestCase):
    def test_basic(self):
        ic = {
            "mom": [("2026-01-01", 0.1), ("2026-01-02", 0.2)],
            "val": [("2026-01-01", 0.05), ("2026-01-02", 0.15)],
        }
        h = build_heatmap(ic)
        self.assertEqual(len(h.factors), 2)
        self.assertEqual(len(h.dates), 2)
        self.assertEqual(len(h.cells), 4)

    def test_missing_dates(self):
        """某因子某天缺数据 → NaN"""
        ic = {
            "mom": [("2026-01-01", 0.1), ("2026-01-03", 0.3)],
            "val": [("2026-01-02", 0.05)],
        }
        h = build_heatmap(ic)
        # 1 mom 缺 02 → NaN
        cell = h.get("mom", "2026-01-02")
        self.assertTrue(math.isnan(cell.ic))
        # 2 val 缺 01 和 03 → NaN
        cell = h.get("val", "2026-01-01")
        self.assertTrue(math.isnan(cell.ic))

    def test_empty(self):
        h = build_heatmap({})
        self.assertEqual(h.factors, [])
        self.assertEqual(h.dates, [])
        self.assertEqual(h.cells, [])
        self.assertEqual(h.vmin, -1.0)
        self.assertEqual(h.vmax, 1.0)

    def test_zscore_normalization(self):
        """Z-score 跨因子归一化"""
        ic = {
            "a": [("d1", 0.1), ("d2", 0.2)],
            "b": [("d1", -0.1), ("d2", -0.2)],
        }
        h = build_heatmap(ic)
        # 应该有正有负的 z_score
        z_vals = [c.z_score for c in h.cells]
        self.assertTrue(any(z > 0 for z in z_vals))
        self.assertTrue(any(z < 0 for z in z_vals))

    def test_vmin_vmax(self):
        ic = {
            "x": [("d1", 0.05), ("d2", 0.15)],
        }
        h = build_heatmap(ic)
        self.assertAlmostEqual(h.vmin, 0.05)
        self.assertAlmostEqual(h.vmax, 0.15)


class TestFactorSeries(unittest.TestCase):
    def test_basic(self):
        ic = {
            "mom": [("d1", 0.1), ("d2", 0.2)],
            "val": [("d1", 0.05)],
        }
        h = build_heatmap(ic)
        s = h.factor_series("mom")
        self.assertEqual(len(s), 2)
        self.assertTrue(all(c.factor == "mom" for c in s))


class TestToEcharts(unittest.TestCase):
    def test_basic(self):
        ic = {
            "a": [("d1", 0.1), ("d2", 0.2)],
        }
        h = build_heatmap(ic)
        out = to_echarts(h)
        self.assertIn("x_data", out)
        self.assertIn("y_data", out)
        self.assertIn("series", out)
        self.assertEqual(len(out["series"][0]["data"]), 2)

    def test_skips_nan(self):
        ic = {"a": [("d1", 0.1), ("d2", float("nan"))]}
        h = build_heatmap(ic)
        out = to_echarts(h)
        # NaN 应被跳过
        # 注意 build_heatmap 收 IC 时不会过滤 NaN, get 才能拿到 NaN
        # to_echarts 跳过 NaN
        non_nan_cells = [c for c in h.cells if not math.isnan(c.ic)]
        self.assertEqual(len(out["series"][0]["data"]), len(non_nan_cells))


class TestSummarize(unittest.TestCase):
    def test_per_factor_stats(self):
        ic = {
            "a": [("d1", 0.1), ("d2", 0.3), ("d3", 0.5)],
            "b": [("d1", -0.2)],
        }
        h = build_heatmap(ic)
        out = summarize(h)
        self.assertEqual(out["a"]["n"], 3)
        self.assertAlmostEqual(out["a"]["mean"], 0.3)
        self.assertEqual(out["b"]["n"], 1)
        self.assertAlmostEqual(out["b"]["std"], 0.0)  # 单点 std=0

    def test_empty_factor(self):
        ic = {"a": [("d1", 0.1)], "b": []}
        h = build_heatmap(ic)
        # b 没数据, 但 b 仍出现在 factors
        # build 不会自动删, series 会空
        out = summarize(h)
        self.assertEqual(out["b"]["n"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
