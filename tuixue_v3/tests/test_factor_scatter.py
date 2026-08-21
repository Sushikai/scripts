#!/usr/bin/env python3
"""
test_factor_scatter.py
Ship 47 单元测试 — 因子散点图
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_scatter import (
    ScatterPoint, RegressionLine, FactorScatter,
    build_scatter, _safe_regression, to_echarts, to_dict,
    build_pair_scatter,
)


class TestRegression(unittest.TestCase):
    def test_perfect_linear(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        r = _safe_regression(x, y)
        self.assertAlmostEqual(r.slope, 2.0, places=4)
        self.assertAlmostEqual(r.intercept, 0.0, places=4)
        self.assertAlmostEqual(r.r2, 1.0, places=4)

    def test_no_correlation(self):
        # 离散
        x = [0.0, 1.0, 0.0, 1.0, 0.5]
        y = [0.5, 0.5, 0.5, 0.5, 0.5]
        r = _safe_regression(x, y)
        self.assertEqual(r.slope, 0.0)
        self.assertAlmostEqual(r.intercept, 0.5)

    def test_const_x(self):
        x = [5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0]
        r = _safe_regression(x, y)
        # x 全相同 → 不能算
        self.assertIsNone(r)

    def test_insufficient(self):
        r = _safe_regression([1.0], [1.0])
        self.assertIsNone(r)


class TestBuild(unittest.TestCase):
    def test_basic(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        s = build_scatter(x, y, x_name="mom", y_name="val")
        self.assertEqual(len(s.points), 5)
        self.assertEqual(s.x_name, "mom")

    def test_with_labels(self):
        x = [1.0, 2.0]
        y = [2.0, 4.0]
        labels = ["000001", "000002"]
        s = build_scatter(x, y, labels=labels)
        self.assertEqual(s.points[0].label, "000001")

    def test_with_groups(self):
        x = [1.0, 2.0, 3.0, 4.0]
        y = [1.0, 2.0, 3.0, 4.0]
        groups = ["A", "B", "A", "B"]
        s = build_scatter(x, y, groups=groups)
        self.assertEqual(s.points[0].group, "A")
        self.assertEqual(s.points[1].group, "B")

    def test_outliers(self):
        # 5 个点, 4 个接近, 1 个远离
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [1.0, 2.0, 3.0, 4.0, 100.0]   # 远离 ≥3σ
        # mu=22, sigma≈43, 100 z≈1.79 (不够 → 调整 outlier_k)
        # 用 outlier_k=1.5 应该能识别
        s = build_scatter(x, y, outlier_k=1.5)
        # 验证至少 1 个被标记为 outlier
        n_outliers = sum(1 for p in s.points if p.is_outlier)
        self.assertGreater(n_outliers, 0)
        self.assertEqual(s.n_outliers, n_outliers)

    def test_empty(self):
        s = build_scatter([], [])
        self.assertEqual(s.points, [])
        self.assertIsNone(s.regression)
        self.assertEqual(s.n_outliers, 0)

    def test_min_length(self):
        # x 比 y 长 → 取最短
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0]
        s = build_scatter(x, y)
        self.assertEqual(len(s.points), 2)


class TestToEcharts(unittest.TestCase):
    def test_basic(self):
        x = [1.0, 2.0, 3.0]
        y = [2.0, 4.0, 6.0]
        s = build_scatter(x, y, groups=["A", "A", "B"])
        ech = to_echarts(s)
        self.assertEqual(ech["x_name"], "x")
        # 2 组
        self.assertEqual(len(ech["series"]), 2)

    def test_no_regression_skipped(self):
        s = FactorScatter(
            x_name="x", y_name="y",
            points=[], regression=None, n_outliers=0,
        )
        ech = to_echarts(s)
        self.assertNotIn("regression", ech)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        x = [1.0, 2.0]
        y = [2.0, 4.0]
        labels = ["a", "b"]
        s = build_scatter(x, y, labels=labels)
        d = to_dict(s)
        self.assertEqual(d["x_name"], "x")
        self.assertEqual(len(d["points"]), 2)
        self.assertEqual(d["points"][0]["label"], "a")


class TestBuildPair(unittest.TestCase):
    def test_basic(self):
        factor_dict = {
            "mom": [1.0, 2.0, 3.0],
            "val": [2.0, 4.0, 6.0],
        }
        s = build_pair_scatter(factor_dict, ("mom", "val"))
        self.assertEqual(s.x_name, "mom")
        self.assertEqual(s.y_name, "val")
        self.assertEqual(len(s.points), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
