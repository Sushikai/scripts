#!/usr/bin/env python3
"""
test_factor_correlation.py
Ship 43 单元测试 — 因子相关性矩阵
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_correlation import (
    CorrelationMatrix, _pearson,
    compute_matrix, find_redundant_pairs,
    avg_offdiag_correlation, most_correlated,
    to_dict, summary,
)


class TestPearson(unittest.TestCase):
    def test_perfect_pos(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0, places=4)

    def test_perfect_neg(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [3, 2, 1]), -1.0, places=4)

    def test_const(self):
        self.assertEqual(_pearson([5, 5, 5], [1, 2, 3]), 0.0)


class TestMatrix(unittest.TestCase):
    def test_basic(self):
        # 3 因子, 5 个时间点
        series = {
            "mom": [0.1, 0.2, -0.1, 0.3, -0.2],
            "val": [0.05, 0.1, -0.15, 0.25, -0.3],
            "vol": [0.2, 0.4, -0.2, 0.6, -0.4],  # 与 mom 高度相关
        }
        m = compute_matrix(series)
        self.assertEqual(m.factors, ["mom", "val", "vol"])
        self.assertEqual(len(m.matrix), 3)

    def test_diagonal_is_one(self):
        series = {"a": [1, 2, 3], "b": [2, 4, 6]}
        m = compute_matrix(series)
        self.assertAlmostEqual(m.matrix[0][0], 1.0)
        self.assertAlmostEqual(m.matrix[1][1], 1.0)

    def test_symmetric(self):
        series = {"a": [1, 2, 3, 4], "b": [2, 4, 6, 8], "c": [4, 3, 2, 1]}
        m = compute_matrix(series)
        n = len(m.factors)
        for i in range(n):
            for j in range(n):
                self.assertAlmostEqual(m.matrix[i][j], m.matrix[j][i])

    def test_perfect_corr(self):
        series = {
            "a": [1, 2, 3, 4, 5],
            "b": [2, 4, 6, 8, 10],   # 2×a
        }
        m = compute_matrix(series)
        self.assertAlmostEqual(m.matrix[0][1], 1.0, places=4)

    def test_min_length_used(self):
        series = {
            "a": [1, 2, 3, 4, 5, 6, 7],
            "b": [1, 2, 3],  # 短
        }
        m = compute_matrix(series)
        # n_samples = min(7, 3) = 3
        self.assertEqual(m.n_samples, 3)

    def test_empty(self):
        m = compute_matrix({})
        self.assertEqual(m.factors, [])

    def test_get(self):
        series = {"a": [1, 2, 3], "b": [2, 4, 6]}
        m = compute_matrix(series)
        c = m.get("a", "b")
        self.assertAlmostEqual(c, 1.0, places=4)
        self.assertIsNone(m.get("nonexistent", "a"))

    def test_pair(self):
        series = {"a": [1, 2, 3], "b": [2, 4, 6]}
        m = compute_matrix(series)
        ab, ba = m.pair("a", "b")
        self.assertEqual(ab, ba)


class TestRedundantPairs(unittest.TestCase):
    def test_find_high_corr(self):
        series = {
            "a": [1, 2, 3, 4, 5],
            "b": [2, 4, 6, 8, 10],   # r=1
            "c": [5, 4, 3, 2, 1],    # r=-1
        }
        m = compute_matrix(series)
        pairs = find_redundant_pairs(m, threshold=0.7)
        # 3 选 2 = 3 pairs
        self.assertEqual(len(pairs), 3)
        names = {(p[0], p[1]) for p in pairs}
        self.assertIn(("a", "b"), names)
        self.assertIn(("a", "c"), names)
        self.assertIn(("b", "c"), names)

    def test_no_redundant(self):
        series = {
            "a": [1, 0, 1, 0, 1],
            "b": [0, 1, 0, 1, 0],   # 与 a 负相关
        }
        m = compute_matrix(series)
        # |corr|=1 → 也算高
        pairs = find_redundant_pairs(m, threshold=0.5)
        self.assertEqual(len(pairs), 1)


class TestAvgOffdiag(unittest.TestCase):
    def test_basic(self):
        series = {
            "a": [1, 2, 3],
            "b": [2, 4, 6],
            "c": [3, 2, 1],
        }
        m = compute_matrix(series)
        avg = avg_offdiag_correlation(m)
        # a-b=1, b-c=-1, a-c≈-1, average = 1
        self.assertGreater(avg, 0)


class TestMostCorrelated(unittest.TestCase):
    def test_basic(self):
        series = {
            "a": [1, 2, 3, 4, 5],
            "b": [2, 4, 6, 8, 10],   # r=1
            "c": [5, 4, 3, 2, 1],    # r=-1
        }
        m = compute_matrix(series)
        mc = most_correlated(m, "a")
        self.assertIsNotNone(mc)
        # b 和 c 都是 |1|, 取先遍历
        # 我们用 >, 取第一个遇到的 → b
        self.assertEqual(mc[0], "b")
        self.assertAlmostEqual(mc[1], 1.0, places=4)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        series = {"a": [1, 2, 3], "b": [2, 4, 6]}
        m = compute_matrix(series)
        d = to_dict(m)
        self.assertIn("factors", d)
        self.assertIn("matrix", d)
        self.assertIn("avg_offdiag_corr", d)


class TestSummary(unittest.TestCase):
    def test_no_redundant(self):
        series = {"a": [1, 2, 3, 4, 5], "b": [5, 4, 3, 2, 1]}
        m = compute_matrix(series)
        s = summary(m)
        self.assertIn("2 个", s)

    def test_with_redundant(self):
        series = {
            "a": [1, 2, 3, 4, 5],
            "b": [2, 4, 6, 8, 10],
        }
        m = compute_matrix(series)
        s = summary(m)
        self.assertIn("冗余对", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
