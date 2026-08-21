#!/usr/bin/env python3
"""
test_strategy_tuner.py
Ship 32 单元测试 — 策略权重动态调整
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strategy_tuner import tune_weights, to_dict
from tuixue_v3.signal_metrics import SignalMetrics


def sm(precision=0.5, ic=0.03, n=50, healthy=True, hit=0.5, reasons=None):
    return SignalMetrics(
        factor="x", n_samples=n,
        precision=precision, recall=0.5, ic=ic,
        hit_rate=hit, avg_predicted=0.5, avg_actual=0.02,
        is_healthy=healthy, reasons=reasons or ["ok"],
    )


class TestTuneWeights(unittest.TestCase):
    def test_empty(self):
        r = tune_weights({}, {})
        self.assertEqual(r.new_weights, {})
        self.assertEqual(r.disabled, [])

    def test_no_change_default(self):
        """无 metrics → 当前权重不变"""
        r = tune_weights({"a": 0.5, "b": 0.5}, {})
        self.assertAlmostEqual(r.new_weights["a"], 0.5)

    def test_insufficient_samples_skip(self):
        """样本 < 30 不调整"""
        m = {"a": sm(n=10)}
        r = tune_weights({"a": 0.5}, m)
        self.assertIn("样本不足", r.reasons.get("a", ""))

    def test_high_precision_boost(self):
        """precision > 0.5 → 加权"""
        m = {"good": sm(precision=0.7, ic=0.05), "bad": sm(precision=0.3)}
        r = tune_weights({"good": 0.5, "bad": 0.5}, m)
        self.assertIn("good", r.boosted)
        self.assertIn("bad", r.reduced)
        # good 分得更多
        self.assertGreater(r.new_weights["good"], r.new_weights["bad"])

    def test_unhealthy_disabled(self):
        m = {"bad": sm(precision=0.2, ic=0.0, healthy=False)}
        r = tune_weights({"bad": 0.5}, m)
        self.assertIn("bad", r.disabled)
        self.assertEqual(r.new_weights["bad"], 0.0)

    def test_high_ic_boost(self):
        """高 IC → 额外加权 (双策略对比)"""
        m1 = {"x": sm(precision=0.5, ic=0.10), "y": sm(precision=0.5, ic=0.03)}
        r1 = tune_weights({"x": 0.5, "y": 0.5}, m1)
        # x 的权重应 > y
        self.assertGreater(r1.new_weights["x"], r1.new_weights["y"])

    def test_normalized(self):
        """权重之和 = 1"""
        m = {
            "a": sm(precision=0.7), "b": sm(precision=0.5),
            "c": sm(precision=0.3),
        }
        r = tune_weights({"a": 0.33, "b": 0.33, "c": 0.33}, m)
        s = sum(r.new_weights.values())
        self.assertAlmostEqual(s, 1.0, places=3)

    def test_min_weight(self):
        """最低权重"""
        m = {"x": sm(precision=0.0), "y": sm(precision=0.5)}
        r = tune_weights({"x": 0.5, "y": 0.5}, m, min_weight=0.1)
        self.assertGreaterEqual(r.new_weights["x"], 0.1)

    def test_max_weight(self):
        """最高权重 (双策略对比)"""
        m = {"x": sm(precision=0.99, ic=0.5), "y": sm(precision=0.3)}
        r = tune_weights({"x": 0.5, "y": 0.5}, m, max_weight=0.8)
        self.assertLessEqual(r.new_weights["x"], 0.8)

    def test_to_dict(self):
        r = tune_weights({"a": 0.5}, {})
        d = to_dict(r)
        self.assertIn("new_weights", d)
        self.assertIn("disabled", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
