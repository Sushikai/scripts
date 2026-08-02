#!/usr/bin/env python3
"""
test_position_sizing.py
Ship 14 单元测试 — Kelly + 波动率倒数加权仓位管理
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.position_sizing import (
    KellyInputs, SizingResult,
    compute_kelly, size_portfolio,
)


class TestComputeKelly(unittest.TestCase):
    def test_basic_60_win_2x_payoff(self):
        """60% 胜率 + 2:1 盈亏比 → raw Kelly = (0.6*2-0.4)/2 = 0.4"""
        ki = KellyInputs(
            win_rate=0.6, avg_win=0.10, avg_loss=0.05,
            vol_n=0.02, target_vol=0.02,
        )
        r = compute_kelly(ki)
        self.assertAlmostEqual(r.raw_kelly, 0.4, places=4)
        self.assertAlmostEqual(r.half_kelly, 0.2, places=4)
        # vol=target → ratio=1 → vol_adjusted = half_kelly = 0.2
        self.assertAlmostEqual(r.vol_adjusted, 0.2, places=4)
        self.assertFalse(r.capped)
        self.assertGreater(r.confidence, 0.5)

    def test_high_vol_reduces_size(self):
        """vol_n=0.04 (2x target) → 仓位砍半"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0.04, target_vol=0.02)
        r = compute_kelly(ki)
        # half_kelly=0.2, ratio=0.5, vol_adjusted=0.1
        self.assertAlmostEqual(r.vol_adjusted, 0.1, places=4)

    def test_low_vol_increases_size(self):
        """vol_n=0.01 (0.5x target) → 仓位翻倍"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0.01, target_vol=0.02)
        r = compute_kelly(ki)
        # half_kelly=0.2, ratio=2.0, vol_adjusted=0.4
        self.assertAlmostEqual(r.vol_adjusted, 0.4, places=4)

    def test_max_position_caps(self):
        """raw Kelly 极大 → 被 max_position 截断"""
        ki = KellyInputs(win_rate=0.95, avg_win=0.50, avg_loss=0.05,
                        vol_n=0.005, target_vol=0.02,
                        max_position_pct=0.30)
        r = compute_kelly(ki)
        # raw = (0.95*10 - 0.05) / 10 = 0.945
        # half = 0.4725, vol adj = 0.4725 * (0.02/0.005)=4x 限到 4x = 1.89
        # → 被 max_position 0.30 截断
        self.assertEqual(r.final, 0.30)
        self.assertTrue(r.capped)

    def test_no_kelly_invalid_win_rate(self):
        ki = KellyInputs(win_rate=1.0, avg_win=0.1, avg_loss=0.05)
        r = compute_kelly(ki)
        self.assertEqual(r.final, 0.0)
        self.assertEqual(r.confidence, 0.0)

    def test_zero_avg_loss(self):
        ki = KellyInputs(win_rate=0.6, avg_win=0.1, avg_loss=0.0)
        r = compute_kelly(ki)
        self.assertEqual(r.final, 0.0)

    def test_negative_kelly_no_position(self):
        """win_rate=0.3, b=0.5 → Kelly 负 → 不开仓"""
        ki = KellyInputs(win_rate=0.3, avg_win=0.05, avg_loss=0.10)
        r = compute_kelly(ki)
        # raw = (0.3*0.5 - 0.7)/0.5 = -1.1
        self.assertLess(r.raw_kelly, 0)
        self.assertEqual(r.final, 0.0)
        self.assertIn("Kelly 负值", r.reason)

    def test_full_kelly_not_half(self):
        """half_kelly=False → 用全 Kelly"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0.02, target_vol=0.02,
                        half_kelly=False)
        r = compute_kelly(ki)
        # raw=0.4, half_kelly 字段 = full = 0.4 (重命名不当, 见 docstring)
        self.assertAlmostEqual(r.half_kelly, 0.4, places=4)
        self.assertAlmostEqual(r.vol_adjusted, 0.4, places=4)

    def test_zero_vol_uses_kelly(self):
        """vol_n=0 → vol_adjusted = half_kelly"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0, target_vol=0.02)
        r = compute_kelly(ki)
        self.assertAlmostEqual(r.vol_adjusted, r.half_kelly, places=4)
        self.assertIn("波动率未知", r.reason)

    def test_vol_clamp_upper(self):
        """极低 vol + 高 Kelly → ratio 被 clamp 到 4x"""
        ki = KellyInputs(win_rate=0.95, avg_win=0.50, avg_loss=0.05,
                        vol_n=0.001, target_vol=0.02,
                        max_position_pct=0.99)  # 高 cap 让 clamp 生效
        r = compute_kelly(ki)
        # ratio = 20, clamped to 4
        # half=0.47, vol_adj=0.47*4=1.88, cap 0.99 → final 0.99
        self.assertEqual(r.final, 0.99)
        self.assertTrue(r.capped)

    def test_confidence_no_vol(self):
        """无 vol 数据 → conf 缺一项"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.1, avg_loss=0.05, vol_n=0)
        r = compute_kelly(ki)
        self.assertAlmostEqual(r.confidence, 0.7, places=4)

    def test_confidence_full(self):
        """全数据 → conf=1"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.1, avg_loss=0.05, vol_n=0.02)
        r = compute_kelly(ki)
        self.assertAlmostEqual(r.confidence, 1.0, places=4)


class TestSizePortfolio(unittest.TestCase):
    def test_basic(self):
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0.02, target_vol=0.02)
        r = compute_kelly(ki)
        out = size_portfolio(100000, n_picks=3, sizing=r)
        self.assertGreater(out["per_position"], 0)
        self.assertEqual(out["n"], 3)
        self.assertLessEqual(out["total_deployed"], 100000)

    def test_zero_n_picks(self):
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05)
        r = compute_kelly(ki)
        out = size_portfolio(100000, n_picks=0, sizing=r)
        self.assertEqual(out["per_position"], 0.0)
        self.assertEqual(out["cash_reserve"], 100000)

    def test_zero_sizing(self):
        ki = KellyInputs(win_rate=0.0, avg_win=0.1, avg_loss=0.05)  # 无胜率
        r = compute_kelly(ki)
        out = size_portfolio(100000, n_picks=5, sizing=r)
        self.assertEqual(out["total_deployed"], 0.0)
        self.assertEqual(out["cash_reserve"], 100000)

    def test_many_picks_caps_per_share(self):
        """N 大 → 单股仓位不能超过 1.5/N"""
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05,
                        vol_n=0.02, target_vol=0.02, max_position_pct=0.50)
        r = compute_kelly(ki)
        out = size_portfolio(100000, n_picks=20, sizing=r)
        # 1/20 * 1.5 = 0.075 per share
        self.assertLessEqual(out["per_position_pct"], 0.075)

    def test_per_position_round(self):
        ki = KellyInputs(win_rate=0.6, avg_win=0.10, avg_loss=0.05)
        r = compute_kelly(ki)
        out = size_portfolio(100000, n_picks=1, sizing=r)
        # 整数金额精度
        self.assertEqual(out["per_position"], round(out["per_position"], 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)