#!/usr/bin/env python3
"""
test_strat_era10_sentiment_allocation.py
Ship 66 单元测试 — 情绪驱动配置
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era10_sentiment_allocation import (
    AllocationAdvice, get_target_exposure, THRESHOLDS,
    advise, trend, comprehensive_advice, summarize,
)


class TestGetTargetExposure(unittest.TestCase):
    def test_low(self):
        action, exp = get_target_exposure(5)
        self.assertEqual(action, "add")
        self.assertEqual(exp, 1.0)

    def test_high(self):
        action, exp = get_target_exposure(95)
        self.assertEqual(action, "sell")

    def test_mid(self):
        action, exp = get_target_exposure(50)
        self.assertEqual(action, "hold")


class TestAdvise(unittest.TestCase):
    def test_extreme_fear(self):
        a = advise(5)
        self.assertEqual(a.action, "add")
        self.assertGreater(a.target_exposure, 0.9)

    def test_extreme_greed(self):
        a = advise(95)
        self.assertEqual(a.action, "sell")
        self.assertLess(a.target_exposure, 0.4)

    def test_neutral(self):
        a = advise(50)
        self.assertEqual(a.action, "hold")

    def test_clip(self):
        # 边界
        a1 = advise(-5)
        self.assertEqual(a1.action, "add")
        a2 = advise(150)
        self.assertEqual(a2.action, "sell")


class TestTrend(unittest.TestCase):
    def test_warming(self):
        scores = [30.0] * 15 + [60.0] * 5
        t = trend(scores)
        self.assertEqual(t.direction, "warming")

    def test_cooling(self):
        scores = [70.0] * 15 + [40.0] * 5
        t = trend(scores)
        self.assertEqual(t.direction, "cooling")

    def test_stable(self):
        scores = [50.0] * 25
        t = trend(scores)
        self.assertEqual(t.direction, "stable")

    def test_short(self):
        t = trend([50.0] * 10)
        self.assertIsNone(t)


class TestComprehensive(unittest.TestCase):
    def test_basic(self):
        scores = [30.0] * 15 + [60.0] * 5
        result = comprehensive_advice(scores, current_exposure=0.7)
        self.assertIsNotNone(result)
        advice, tr = result
        self.assertEqual(tr.direction, "warming")

    def test_empty(self):
        result = comprehensive_advice([])
        self.assertIsNone(result)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        a = advise(50)
        s = summarize(a)
        self.assertIn("score", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        a = advise(50)
        d = a.to_dict()
        self.assertEqual(d["score"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)