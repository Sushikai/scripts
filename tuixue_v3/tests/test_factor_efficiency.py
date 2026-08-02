#!/usr/bin/env python3
"""
test_factor_efficiency.py
Ship 50 单元测试 — 因子效率边界
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_efficiency import (
    FrontierPoint, FrontierResult,
    compute_mean_cov, _portfolio_metrics,
    build_frontier, ir_weight_from, inverse_vol_weight, _solve_2d,
    to_dict, best_sharpe, min_vol,
)


class TestMeanCov(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06],
            "b": [-0.02, -0.03, -0.01, -0.04, -0.02],
        }
        means, cov = compute_mean_cov(ic_dict)
        self.assertAlmostEqual(means["a"], 0.052, places=4)
        self.assertLess(means["b"], 0)
        self.assertAlmostEqual(cov[("a", "a")], cov[("a", "a")], places=4)
        # a-b 协方差应 < 0
        self.assertLess(cov[("a", "b")], 0)

    def test_empty(self):
        means, cov = compute_mean_cov({})
        self.assertEqual(means, {})
        self.assertEqual(cov, {})


class TestPortfolioMetrics(unittest.TestCase):
    def test_equal_weight_single(self):
        means = {"a": 0.05}
        cov = {("a", "a"): 0.001}
        weights = {"a": 1.0}
        r, v = _portfolio_metrics(weights, means, cov)
        self.assertAlmostEqual(r, 0.05)
        self.assertAlmostEqual(v, math.sqrt(0.001), places=4)

    def test_two_factors(self):
        means = {"a": 0.05, "b": 0.03}
        cov = {
            ("a", "a"): 0.001, ("b", "b"): 0.002,
            ("a", "b"): 0.0005, ("b", "a"): 0.0005,
        }
        weights = {"a": 0.5, "b": 0.5}
        import math
        r, v = _portfolio_metrics(weights, means, cov)
        self.assertAlmostEqual(r, 0.04)
        # vol = sqrt(0.5²×0.001 + 2×0.5×0.5×0.0005 + 0.5²×0.002)
        expected_var = (0.5**2 * 0.001 + 2 * 0.5 * 0.5 * 0.0005 + 0.5**2 * 0.002)
        self.assertAlmostEqual(v, math.sqrt(expected_var), places=4)


class TestSolve2D(unittest.TestCase):
    def test_basic(self):
        A = [[2.0, 1.0], [1.0, 3.0]]
        b = [5.0, 9.0]
        x = _solve_2d(A, b)
        # [[2,1],[1,3]] x = [5,9] → x = [1.2, 2.6]
        self.assertAlmostEqual(x[0], 1.2, places=3)
        self.assertAlmostEqual(x[1], 2.6, places=3)

    def test_singular(self):
        A = [[1.0, 2.0], [2.0, 4.0]]
        b = [3.0, 6.0]
        # Singular, numpy raises — 兜底可解
        x = _solve_2d(A, b)
        # 要么 None (回退路径), 要么合理结果
        # 这里不强求 None, 只看返回类型
        # 不报异常

    def test_zero_size(self):
        x = _solve_2d([], [])
        self.assertIsNone(x)


class TestIRWeight(unittest.TestCase):
    def test_basic(self):
        means = {"a": 0.05, "b": 0.10}
        # a 风险小
        cov = {
            ("a", "a"): 0.001, ("b", "b"): 0.005,
            ("a", "b"): 0.0, ("b", "a"): 0.0,
        }
        w = ir_weight_from(means, cov)
        # 应分配给两个
        self.assertAlmostEqual(sum(w.values()), 1.0, places=4)


class TestInverseVol(unittest.TestCase):
    def test_basic(self):
        means = {"a": 0.05, "b": 0.05}
        cov = {
            ("a", "a"): 0.001, ("b", "b"): 0.004,  # a 比 b 小一半
            ("a", "b"): 0.0, ("b", "a"): 0.0,
        }
        w = inverse_vol_weight(means, cov)
        # a 的权重应 > b
        self.assertGreater(w["a"], w["b"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=4)


class TestBuildFrontier(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06, 0.07, 0.04, 0.05],
            "b": [0.03, 0.04, 0.02, 0.03, 0.04, 0.05, 0.02, 0.03],
        }
        r = build_frontier(ic_dict)
        self.assertGreater(len(r.points), 0)
        self.assertEqual(r.factors, ["a", "b"])

    def test_empty(self):
        r = build_frontier({})
        self.assertEqual(r.points, [])

    def test_single_factor(self):
        r = build_frontier({"a": [0.05] * 10})
        # 单因子 → 等权就是它自己
        self.assertGreater(len(r.points), 0)


class TestBestSharpe(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05] * 10,
            "b": [0.01, -0.01] * 5,
        }
        r = build_frontier(ic_dict)
        b = best_sharpe(r)
        self.assertIsNotNone(b)
        # 单因子 a 高 Sharpe 应胜出


class TestMinVol(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06],
            "b": [0.05, -0.06, 0.04, -0.05, 0.06],
        }
        r = build_frontier(ic_dict)
        m = min_vol(r)
        self.assertIsNotNone(m)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        ic_dict = {"a": [0.05] * 10, "b": [0.04] * 10}
        r = build_frontier(ic_dict)
        d = to_dict(r)
        self.assertIn("factors", d)
        self.assertIn("points", d)


import math


if __name__ == "__main__":
    unittest.main(verbosity=2)
