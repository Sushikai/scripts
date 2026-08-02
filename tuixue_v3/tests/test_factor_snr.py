#!/usr/bin/env python3
"""
test_factor_snr.py
Ship 56 单元测试 — 因子 SNR
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_snr import (
    SNRResult, compute_snr, compute_multi, rank_by_snr,
    summarize,
)


class TestCompute(unittest.TestCase):
    def test_strong(self):
        # 强 IC: mean=0.05, std=0.01 → SNR=5, z=22
        ic = [0.05 + 0.01 * ((-1) ** i) for i in range(30)]
        r = compute_snr(ic, "test")
        self.assertGreater(r.snr, 3.0)
        self.assertEqual(r.grade, "excellent")

    def test_zero(self):
        ic = [0.0] * 30
        r = compute_snr(ic, "test")
        # sigma=0 → SNR=0
        self.assertEqual(r.snr, 0.0)

    def test_noisy(self):
        ic = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005, 0.015, -0.015] * 4
        r = compute_snr(ic, "test")
        # SNR 接近 0
        self.assertLess(abs(r.snr), 0.3)

    def test_p_value_known(self):
        # 强 IC: p_value 应很小
        ic = [0.05] * 100
        ic[0] = 0.05
        # 标准差 0 → SNR 0
        # 改为有波动但 IC 强
        ic = [0.04, 0.05, 0.06, 0.05, 0.04, 0.05, 0.06, 0.05] * 12 + [0.05]
        r = compute_snr(ic, "test")
        self.assertLess(r.p_value, 0.001)

    def test_insufficient(self):
        r = compute_snr([0.05], "x")
        self.assertEqual(r.grade, "poor")
        self.assertEqual(r.snr, 0.0)


class TestComputeMulti(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06] * 8,
            "b": [0.01, -0.01, 0.02, -0.02, 0.005] * 8,
        }
        results = compute_multi(ic_dict)
        self.assertEqual(len(results), 2)
        self.assertEqual({r.factor for r in results}, {"a", "b"})


class TestRankBySNR(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06] * 8,
            "b": [0.01, -0.01, 0.02, -0.02, 0.005] * 8,
            "c": [0.10, 0.11, 0.09, 0.10, 0.11] * 8,
        }
        results = compute_multi(ic_dict)
        ranked = rank_by_snr(results)
        # c 应最强
        self.assertEqual(ranked[0].factor, "c")


class TestSignificance(unittest.TestCase):
    def test_excellent(self):
        ic = [0.05] * 60
        # 加小波动
        ic = [0.045 + 0.005 * ((-1) ** i) for i in range(60)]
        r = compute_snr(ic)
        self.assertEqual(r.grade, "excellent")
        self.assertTrue(r.is_significant)

    def test_poor(self):
        ic = [0.005 * ((-1) ** i) for i in range(30)]
        r = compute_snr(ic)
        # 弱 IC
        self.assertEqual(r.grade, "poor")


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        ic = [0.05] * 30
        ic = [v + 0.005 * ((-1) ** i) for i, v in enumerate(ic)]
        r = compute_snr(ic, "mom")
        s = summarize(r)
        self.assertIn("mom", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
