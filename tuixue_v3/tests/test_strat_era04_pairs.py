#!/usr/bin/env python3
"""
test_strat_era04_pairs.py
Ship 60 单元测试 — 配对交易
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era04_pairs import (
    PairSignal, PairStats,
    _pearson, _ols, compute_pair_stats,
    generate_signal, discover_pair,
)


class TestPearson(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0)


class TestOLS(unittest.TestCase):
    def test_basic(self):
        # y = 2x + 1
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [3.0, 5.0, 7.0, 9.0, 11.0]
        b, a = _ols(x, y)
        self.assertAlmostEqual(b, 2.0, places=4)
        self.assertAlmostEqual(a, 1.0, places=4)


class TestComputePairStats(unittest.TestCase):
    def test_basic(self):
        # 高度相关的两根序列
        pa = [float(i) for i in range(60)]
        pb = [2 * pi + 1 for pi in pa]
        s = compute_pair_stats("a", pa, "b", pb)
        self.assertAlmostEqual(s.beta, 2.0, places=3)
        self.assertAlmostEqual(s.intercept, 1.0, places=3)
        self.assertGreater(s.correlation, 0.99)
        # z_score ≈ 0 (完美线性)
        self.assertAlmostEqual(s.z_score, 0.0, places=3)

    def test_short_data(self):
        s = compute_pair_stats("a", [1.0], "b", [2.0])
        self.assertEqual(s.n, 1)
        self.assertEqual(s.beta, 0.0)


class TestGenerateSignal(unittest.TestCase):
    def test_short_a(self):
        # 高 z_score → 卖 a 买 b
        s = PairStats("a", "b", 1.0, 0.0, z_score=2.5,
                      mean_spread=0.0, sigma_spread=1.0,
                      correlation=0.9, n=60)
        sig = generate_signal(s)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "short_a_long_b")

    def test_long_a(self):
        s = PairStats("a", "b", 1.0, 0.0, z_score=-2.5,
                      mean_spread=0.0, sigma_spread=1.0,
                      correlation=0.9, n=60)
        sig = generate_signal(s)
        self.assertEqual(sig.side, "long_a_short_b")

    def test_close(self):
        s = PairStats("a", "b", 1.0, 0.0, z_score=0.3,
                      mean_spread=0.0, sigma_spread=1.0,
                      correlation=0.9, n=60)
        sig = generate_signal(s, exit_z=0.5)
        self.assertEqual(sig.side, "close")

    def test_no_signal(self):
        # 在 entry/exit 之间
        s = PairStats("a", "b", 1.0, 0.0, z_score=1.0,
                      mean_spread=0.0, sigma_spread=1.0,
                      correlation=0.9, n=60)
        sig = generate_signal(s, entry_z=2.0, exit_z=0.5)
        self.assertIsNone(sig)

    def test_insufficient(self):
        s = PairStats("a", "b", 1.0, 0.0, z_score=2.5,
                      mean_spread=0.0, sigma_spread=1.0,
                      correlation=0.9, n=10)
        self.assertIsNone(generate_signal(s))


class TestDiscoverPair(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [float(i) for i in range(60)],
            "b": [2.0 * i + 1 for i in range(60)],   # 相关
            "c": [3.0 * i * ((i % 5) / 5) for i in range(60)],   # 不相关
        }
        pairs = discover_pair(universe, window=30)
        # 至少包含 ab 对
        found_ab = any(p.code_a == "a" and p.code_b == "b" for p in pairs)
        self.assertTrue(found_ab)


if __name__ == "__main__":
    unittest.main(verbosity=2)
