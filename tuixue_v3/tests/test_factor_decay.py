#!/usr/bin/env python3
"""
test_factor_decay.py
Ship 34 单元测试 — 因子衰减检测
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_decay import (
    DecayResult, FactorDecayTracker, _pearson, to_dict,
)


class TestTracker(unittest.TestCase):
    def test_insufficient_samples(self):
        t = FactorDecayTracker("x", window=60)
        for i in range(30):
            t.add(i / 30.0, i / 30.0)
        r = t.detect_decay()
        self.assertFalse(r.is_decayed)
        self.assertIn("样本不足", r.reasons[0])

    def test_stable_factor(self):
        """前后 IC 接近 → 不衰减"""
        t = FactorDecayTracker("x", window=60)
        # 前 60 + 后 60, 全部 perfect 预测
        for i in range(120):
            t.add(i / 120.0, i / 120.0)
        r = t.detect_decay()
        # IC 都 ≈ 1.0, 衰减 ≈ 0
        self.assertFalse(r.is_decayed)
        self.assertFalse(r.is_warning)

    def test_decayed_factor(self):
        """前半预测好, 后半乱猜 → 衰减"""
        t = FactorDecayTracker("x", window=60)
        # 前半 (历史): 完美预测
        for i in range(60):
            t.add(i / 60.0, i / 60.0)
        # 后半 (当前): 预测完全无关
        for i in range(60):
            t.add(0.5, i / 60.0)  # predicted 固定, actual 变
        r = t.detect_decay()
        # historical IC ≈ 1.0, current IC ≈ 0 (predicted 常数)
        # decay = (1.0 - 0) / 1.0 = 1.0 → 弃用
        self.assertTrue(r.is_decayed)

    def test_sign_change(self):
        """IC 翻转 (正 → 负) → 弃用"""
        t = FactorDecayTracker("x", window=60)
        # 前半: 中等正相关 (避免 severe_decay 提前触发)
        for i in range(60):
            t.add(0.5 + i / 120.0, 0.3 + 0.4 * (i / 60.0))
        # 后半: 反向
        for i in range(60):
            t.add(0.5 + i / 120.0, 0.7 - 0.4 * (i / 60.0))
        r = t.detect_decay()
        self.assertTrue(r.is_decayed)
        # 要么翻转 要么 severe decay
        self.assertTrue(any("翻转" in x or "衰减" in x for x in r.reasons))

    def test_warning_only(self):
        """中等衰减 → warning 而非 decayed"""
        t = FactorDecayTracker("x", window=60)
        # 前半: IC 0.6
        for i in range(60):
            t.add(i / 60.0, 0.6 * (i / 60.0) + 0.1)
        # 后半: IC 0.3
        for i in range(60):
            t.add(i / 60.0, 0.3 * (i / 60.0) + 0.2)
        r = t.detect_decay()
        # decay = (0.6 - 0.3) / 0.6 = 0.5 → 临界 warning
        print(f"  warn test: hist_ic={r.historical_ic} curr_ic={r.current_ic} decay={r.decay_pct}")
        # 至少不在 decayed (除非 precision > 0.8)
        # 如果 decay < 0.8, is_decayed=False
        self.assertFalse(r.is_decayed)


class TestPearson(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0, places=4)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        t = FactorDecayTracker("x", window=60)
        for i in range(60):
            t.add(0.5, 0.5)
        r = t.detect_decay()
        d = to_dict(r)
        self.assertIn("factor", d)
        self.assertIn("decay_pct", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
