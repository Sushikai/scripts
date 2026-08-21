#!/usr/bin/env python3
"""
test_signal_metrics.py
Ship 19 单元测试 — 信号质量跟踪 (Precision / Recall / IC)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.signal_metrics import (
    Signal, SignalMetrics,
    compute_metrics, update_signal_outcomes,
    _pearson, _topk_indices,
)


class TestPearson(unittest.TestCase):
    def test_perfect_corr(self):
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        self.assertAlmostEqual(_pearson(x, y), 1.0, places=4)

    def test_negative_corr(self):
        x = [1, 2, 3, 4, 5]
        y = [5, 4, 3, 2, 1]
        self.assertAlmostEqual(_pearson(x, y), -1.0, places=4)

    def test_no_corr(self):
        # 噪声: 相关性接近 0
        x = [1, 2, 3, 4, 5]
        y = [2, 1, 4, 3, 5]
        # 这个序列恰好相关性弱
        c = _pearson(x, y)
        self.assertGreater(abs(c), 0.5)

    def test_empty(self):
        self.assertEqual(_pearson([], []), 0.0)


class TestTopKIndices(unittest.TestCase):
    def test_basic(self):
        idx = _topk_indices([3, 1, 4, 1, 5, 9, 2, 6], 3)
        # top 3 values: 9 (idx 5), 6 (idx 7), 5 (idx 4)
        self.assertEqual(set(idx), {5, 7, 4})


class TestComputeMetrics(unittest.TestCase):
    def test_empty_signals(self):
        m = compute_metrics([])
        self.assertEqual(m.n_samples, 0)
        self.assertTrue(m.is_healthy)

    def test_insufficient_samples(self):
        signals = [Signal("2026-08-01", "A", "test", 0.5, 0.1) for _ in range(10)]
        m = compute_metrics(signals)
        self.assertTrue(m.is_healthy)  # 样本不足 → 健康
        self.assertEqual(m.n_samples, 10)

    def test_perfect_signals(self):
        """predicted 完美预测 actual → IC=1, precision=1"""
        signals = []
        for i in range(100):
            signals.append(Signal(f"2026-08-{(i % 30) + 1:02d}",
                                  f"S{i:04d}", "test",
                                  predicted=i / 100.0,
                                  actual=i / 100.0))
        m = compute_metrics(signals)
        self.assertAlmostEqual(m.ic, 1.0, places=3)
        self.assertAlmostEqual(m.precision, 1.0, places=2)
        self.assertTrue(m.is_healthy)

    def test_no_correlation(self):
        """predicted 与 actual 完全无关 → IC≈0"""
        import random
        rng = random.Random(42)
        signals = []
        for i in range(100):
            signals.append(Signal("2026-08-01", f"S{i:04d}", "test",
                                  predicted=rng.uniform(-1, 1),
                                  actual=rng.uniform(-0.05, 0.05)))
        m = compute_metrics(signals)
        # IC 接近 0
        self.assertLess(abs(m.ic), 0.2)
        self.assertFalse(m.is_healthy)  # IC < 0.02 → unhealthy

    def test_negative_ic(self):
        """predicted 与 actual 反向 → IC < 0 → unhealthy"""
        signals = []
        for i in range(100):
            signals.append(Signal("2026-08-01", f"S{i:04d}", "test",
                                  predicted=i / 100.0,
                                  actual=(100 - i) / 100.0))
        m = compute_metrics(signals)
        self.assertLess(m.ic, 0)
        self.assertFalse(m.is_healthy)

    def test_hit_rate(self):
        """predicted > 0 的样本中 actual > 0 的比例"""
        signals = [
            Signal("d", "A", "test", 0.5, 0.03),  # hit
            Signal("d", "B", "test", 0.3, -0.02), # miss
            Signal("d", "C", "test", 0.7, 0.05),  # hit
            Signal("d", "D", "test", -0.5, 0.01), # 不计入 (predicted<=0)
        ] * 10  # 40 条
        m = compute_metrics(signals)
        # 30 条 predicted>0, 20 条 actual>0 → hit_rate = 20/30 ≈ 0.667
        self.assertAlmostEqual(m.hit_rate, 2/3, places=2)

    def test_top_quantile(self):
        """top_quantile 影响 precision/recall"""
        signals = []
        for i in range(100):
            signals.append(Signal("d", f"S{i:04d}", "test",
                                  predicted=i / 100.0,
                                  actual=i / 100.0))  # 完全一致
        m = compute_metrics(signals, top_quantile=0.2)  # top 20%
        # 完全一致时 precision = 1
        self.assertAlmostEqual(m.precision, 1.0, places=2)


class TestUpdateSignalOutcomes(unittest.TestCase):
    def test_basic(self):
        signals = [
            Signal("2026-08-01", "A", "test", 0.5, 0.0),
            Signal("2026-08-01", "B", "test", 0.3, 0.0),
        ]
        outcomes = {("2026-08-01", "A"): 0.05, ("2026-08-01", "B"): -0.02}
        updated = update_signal_outcomes(signals, outcomes)
        self.assertAlmostEqual(updated[0].actual, 0.05)
        self.assertTrue(updated[0].is_win)
        self.assertFalse(updated[1].is_win)

    def test_missing_outcome(self):
        signals = [Signal("2026-08-01", "A", "test", 0.5, 0.0)]
        updated = update_signal_outcomes(signals, {})
        self.assertEqual(updated[0].actual, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
