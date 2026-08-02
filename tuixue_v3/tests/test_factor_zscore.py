#!/usr/bin/env python3
"""
test_factor_zscore.py
Ship 44 单元测试 — 因子 Z-Score
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_zscore import (
    cross_section_zscore, RollingZScore, time_series_zscore,
    rank, rank_zscore, standardize_combo,
    neutralized_zscore, winsorize,
)


class TestCrossSection(unittest.TestCase):
    def test_basic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        zs = cross_section_zscore(values)
        self.assertEqual(len(zs), 5)
        # 中位 (3) → 0
        self.assertAlmostEqual(zs[2], 0.0, places=4)
        # 平均 0
        self.assertAlmostEqual(sum(zs) / len(zs), 0.0, places=4)
        # std ≈ 1
        import statistics
        self.assertAlmostEqual(statistics.stdev(zs), 1.0, places=3)

    def test_winsorize_outlier(self):
        # 4 个正常 + 1 个 100 的离群值
        values = [1.0, 2.0, 3.0, 4.0, 100.0]
        zs = cross_section_zscore(values, winsorize=3.0)
        # 极端值会被限到 mean+3*sigma
        self.assertLess(zs[4], 3.5)

    def test_single_value(self):
        zs = cross_section_zscore([5.0])
        self.assertEqual(zs, [0.0])

    def test_const(self):
        zs = cross_section_zscore([5.0, 5.0, 5.0])
        # sigma=0 → 全 0
        self.assertEqual(zs, [0.0, 0.0, 0.0])


class TestRolling(unittest.TestCase):
    def test_basic(self):
        rz = RollingZScore(window=10)
        zs = []
        for i in range(10):
            v = float(i)
            z = rz.add(v)
            zs.append(z)
        # 最后一个 z: max value → +1.5 (z-score of max in window=10)
        self.assertGreater(zs[-1], 1.0)

    def test_insufficient_samples(self):
        rz = RollingZScore(window=10)
        self.assertIsNone(rz.add(1.0))

    def test_maxlen(self):
        rz = RollingZScore(window=5)
        for i in range(10):
            rz.add(float(i))
        self.assertEqual(rz.n, 5)

    def test_n_mean_std(self):
        rz = RollingZScore(window=10)
        rz.add(1.0)
        rz.add(2.0)
        self.assertEqual(rz.n, 2)
        self.assertAlmostEqual(rz.mean, 1.5)

    def test_time_series(self):
        values = [float(i) for i in range(20)]
        out = time_series_zscore(values, window=10)
        self.assertEqual(len(out), 20)
        # 第一个 None
        self.assertIsNone(out[0])


class TestRank(unittest.TestCase):
    def test_basic(self):
        ranks = rank([3.0, 1.0, 2.0])
        # 原始 [3, 1, 2] → 排序后 index (0, 1, 2)→ values (1, 2, 3)
        # 第 2 个最小 (1) 排名 1
        # 第 3 个 (2) 排名 2
        # 第 1 个 (3) 排名 3
        # ranks[0] = 3 (3 是最大值)
        # ranks[1] = 1 (1 是最小值)
        # ranks[2] = 2 (2 是中间)
        # 中心化 (n+1)/2 = 2
        # ranks[0]-2 = 1, ranks[1]-2 = -1, ranks[2]-2 = 0
        self.assertEqual(ranks[0], 1.0)
        self.assertEqual(ranks[1], -1.0)
        self.assertEqual(ranks[2], 0.0)

    def test_rank_zscore(self):
        r = rank_zscore([10.0, 20.0, 30.0])
        # rank 中心化后 [-1, 0, 1], /n=[-1/3, 0, 1/3]
        self.assertAlmostEqual(r[0], -1.0 / 3)
        self.assertAlmostEqual(r[1], 0.0)
        self.assertAlmostEqual(r[2], 1.0 / 3)


class TestStandardizeCombo(unittest.TestCase):
    def test_multi_factor(self):
        factor_dict = {
            "a": [1.0, 2.0, 3.0],
            "b": [10.0, 20.0, 30.0],
        }
        out = standardize_combo(factor_dict, method="zscore")
        self.assertEqual(set(out.keys()), {"a", "b"})
        # 都已 zscore 化, mean ≈ 0
        import statistics
        self.assertAlmostEqual(statistics.mean(out["a"]), 0.0, places=4)


class TestNeutralized(unittest.TestCase):
    def test_basic(self):
        # 2 个行业, 每行业 2 只
        values = [1.0, 2.0, 100.0, 200.0]  # 行业 0 整体小
        industry_indicator = [0, 0, 1, 1]
        out = neutralized_zscore(values, industry_indicator)
        # 行业 0 mean=1.5, residual=[-0.5, 0.5]
        # 行业 1 mean=150, residual=[-50, 50]
        # 全部 residual mean=0, std 较大, 整体非 [-1, 1]
        # 由于中性化, 不同行业内 z 应能比较
        # 第一个 (1.0-1.5)/sigma_small 整体会很小
        # 这里只验 zscore mean ≈ 0
        import statistics
        self.assertAlmostEqual(statistics.mean(out), 0.0, places=2)
        # 残差矩阵性质: 残差和应近似 0
        self.assertAlmostEqual(sum(out), 0.0, places=2)


class TestWinsorize(unittest.TestCase):
    def test_basic(self):
        # 100 显著是离群, μ=22, σ≈40, 2σ=80
        # 100 > 22+80=102? 实际 100, 上限 102, 所以不会被砍
        # 改为更显著的: 500
        values = [1, 2, 3, 4, 500]
        out = winsorize(values, k=1.5)
        # 500 应被限制到 μ+1.5σ = 102 + 1.5*222 = 435 左右
        self.assertLess(out[-1], 500)
        self.assertGreater(out[-1], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
