#!/usr/bin/env python3
"""
test_sector_rotation_factors.py
Ship 7 单元测试 — 板块轮动因子包 (5 因子 + 综合分 + transition matrix)
"""
import sys
import unittest
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sector_rotation_factors import (
    SectorFactor, compute_momentum, compute_reversal,
    compute_northbound_ratio, compute_margin_change, compute_etf_subscription,
    composite_score, rank_sectors, normalize_factors,
    compute_transition_matrix, predict_next_top, to_dict_list,
    _DEFAULT_WEIGHTS,
)


class TestMomentumFactor(unittest.TestCase):
    """动量因子: 20 日相对强弱"""

    def test_normal(self):
        prices = pd.Series([100, 101, 102, 103, 104, 105] * 5, dtype=float)  # 30 days
        # lookback=20, iloc[-(20+1)]=iloc[-21] = index 9 = 103, iloc[-1]=105
        # ratio = 105/103 - 1 ≈ 0.0194
        result = compute_momentum(prices, lookback=20)
        self.assertAlmostEqual(result, 105 / 103 - 1, places=4)

    def test_insufficient_data(self):
        prices = pd.Series([100, 101, 102])  # 3 days
        result = compute_momentum(prices, lookback=20)
        self.assertEqual(result, 0.0)

    def test_downtrend_negative(self):
        # 21 天: 前 11 天 100, 后 10 天 50 → 跌幅 -50%
        prices = pd.Series([100] * 11 + [50] * 10, dtype=float)
        result = compute_momentum(prices, lookback=10)
        self.assertLess(result, 0)
        self.assertAlmostEqual(result, 50 / 100 - 1, places=4)


class TestReversalFactor(unittest.TestCase):
    """反转因子: -1 × 5 日涨跌幅"""

    def test_decline_positive(self):
        """下跌 → 正值 (预期反弹)"""
        prices = pd.Series([100] * 5 + [90] * 2, dtype=float)
        result = compute_reversal(prices, lookback=5)
        # iloc[-6]=100, iloc[-1]=90, ret=-0.1, 反转后 = +0.1
        self.assertGreater(result, 0)

    def test_rise_negative(self):
        """上涨 → 负值"""
        prices = pd.Series([100] * 5 + [110] * 2, dtype=float)
        result = compute_reversal(prices, lookback=5)
        self.assertLess(result, 0)

    def test_insufficient_data(self):
        prices = pd.Series([100, 101, 102])
        self.assertEqual(compute_reversal(prices, lookback=5), 0.0)


class TestNorthboundFactor(unittest.TestCase):
    """北向资金因子: 净买 / 流通市值"""

    def test_normal(self):
        # 1 亿 / 100 亿 = 0.01
        result = compute_northbound_ratio(1e8, 1e10)
        self.assertAlmostEqual(result, 0.01, places=4)

    def test_zero_market_cap(self):
        self.assertEqual(compute_northbound_ratio(1e8, 0), 0.0)

    def test_negative_market_cap(self):
        self.assertEqual(compute_northbound_ratio(1e8, -1), 0.0)


class TestMarginFactor(unittest.TestCase):
    """两融余额变化因子"""

    def test_normal(self):
        s = pd.Series([100] * 5 + [110] * 2, dtype=float)
        result = compute_margin_change(s, lookback=5)
        # iloc[-6]=100, iloc[-1]=110, ratio=0.1
        self.assertAlmostEqual(result, 0.1, places=4)

    def test_insufficient(self):
        s = pd.Series([100, 101, 102])
        self.assertEqual(compute_margin_change(s, lookback=5), 0.0)


class TestETFSubscriptionFactor(unittest.TestCase):
    """ETF 申赎比因子"""

    def test_normal(self):
        # log(2) ≈ 0.693
        import math
        result = compute_etf_subscription(200, 100)
        self.assertAlmostEqual(result, math.log(2), places=4)

    def test_zero_redeem(self):
        self.assertEqual(compute_etf_subscription(100, 0), 0.0)

    def test_heavy_redeem(self):
        # log(0.5) ≈ -0.693
        import math
        result = compute_etf_subscription(50, 100)
        self.assertAlmostEqual(result, math.log(0.5), places=4)


class TestCompositeScore(unittest.TestCase):
    """综合分 (5 因子加权)"""

    def test_default_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(_DEFAULT_WEIGHTS.values()), 1.0, places=4)

    def test_custom_weights(self):
        f = SectorFactor(
            sector="x",
            momentum_20d=0.1,
            reversal_5d=0.05,
            northbound_ratio=0.02,
            margin_change_5d=0.03,
            etf_subscription_ratio=0.1,
        )
        weights = {
            "momentum_20d": 0.5,
            "reversal_5d": 0.0,
            "northbound_ratio": 0.5,
            "margin_change_5d": 0.0,
            "etf_subscription_ratio": 0.0,
        }
        score = composite_score(f, weights)
        self.assertAlmostEqual(score, 0.5 * 0.1 + 0.5 * 0.02, places=4)


class TestRankSectors(unittest.TestCase):
    """排序 + 排名"""

    def test_basic(self):
        factors = [
            SectorFactor(sector="A", momentum_20d=0.01, northbound_ratio=0.005),
            SectorFactor(sector="B", momentum_20d=0.05, northbound_ratio=0.020),
            SectorFactor(sector="C", momentum_20d=0.03, northbound_ratio=0.010),
        ]
        ranked = rank_sectors(factors)
        # B 综合分最高, 应排第 1
        self.assertEqual(ranked[0].sector, "B")
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[1].sector, "C")
        self.assertEqual(ranked[1].rank, 2)
        self.assertEqual(ranked[2].sector, "A")
        self.assertEqual(ranked[2].rank, 3)


class TestNormalizeFactors(unittest.TestCase):
    """z-score 标准化"""

    def test_normalized(self):
        factors = [
            SectorFactor(sector="A", momentum_20d=0.01),
            SectorFactor(sector="B", momentum_20d=0.05),
            SectorFactor(sector="C", momentum_20d=0.09),
        ]
        normalized = normalize_factors(factors)
        means = [f.momentum_20d for f in normalized]
        # mean 应 ≈ 0
        self.assertAlmostEqual(sum(means) / len(means), 0.0, places=4)

    def test_empty(self):
        self.assertEqual(normalize_factors([]), [])


class TestTransitionMatrix(unittest.TestCase):
    """板块轮动转换矩阵"""

    def test_compute(self):
        # 5 天, 4 个板块
        np.random.seed(42)
        dates = pd.date_range("2026-07-01", periods=10)
        sectors = ["A", "B", "C", "D"]
        data = np.random.rand(10, 4) + 100
        history = pd.DataFrame(data, index=dates, columns=sectors)

        matrix = compute_transition_matrix(history, top_n=2, lookback=5)
        self.assertFalse(matrix.empty)
        self.assertEqual(matrix.shape, (4, 4))
        # 行和应为 1 (条件概率)
        row_sums = matrix.sum(axis=1)
        for v in row_sums:
            if v > 0:  # 跳过全 0 行
                self.assertAlmostEqual(v, 1.0, places=4)

    def test_insufficient_data(self):
        history = pd.DataFrame([[100, 101]], columns=["A", "B"])
        matrix = compute_transition_matrix(history, top_n=2)
        self.assertTrue(matrix.empty)


class TestPredictNextTop(unittest.TestCase):
    """预测下期 top"""

    def test_predict(self):
        np.random.seed(42)
        dates = pd.date_range("2026-07-01", periods=30)
        sectors = ["A", "B", "C", "D", "E"]
        # E 持续上涨 → 应预测 E 持续 top
        data = np.random.rand(30, 5) + 100
        data[:, 4] = np.linspace(100, 200, 30)  # E 持续涨
        history = pd.DataFrame(data, index=dates, columns=sectors)

        predicted = predict_next_top(history, top_n=2)
        self.assertGreater(len(predicted), 0)
        self.assertLessEqual(len(predicted), 2)


class TestToDictList(unittest.TestCase):
    """SectorFactor → dict 序列化"""

    def test_serialization(self):
        f = SectorFactor(sector="X", momentum_20d=0.01, rank=1)
        d = to_dict_list([f])[0]
        self.assertEqual(d["sector"], "X")
        self.assertEqual(d["momentum_20d"], 0.01)
        self.assertEqual(d["rank"], 1)

    def test_round(self):
        f = SectorFactor(sector="X", momentum_20d=0.123456789)
        d = to_dict_list([f])[0]
        # 四舍五入到 4 位
        self.assertEqual(d["momentum_20d"], 0.1235)


if __name__ == "__main__":
    unittest.main(verbosity=2)
