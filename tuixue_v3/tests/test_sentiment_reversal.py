#!/usr/bin/env python3
"""
test_sentiment_reversal.py
Ship 42 单元测试 — 情绪回退检测
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_reversal import (
    ReversalResult, SentimentReversalTracker,
    to_dict, current_zone,
)


class TestTracker(unittest.TestCase):
    def test_insufficient(self):
        t = SentimentReversalTracker(window=20)
        for s in [50, 51, 52]:
            t.add(s)
        r = t.detect()
        self.assertFalse(r.is_reversal)
        self.assertFalse(r.is_warning)

    def test_stable(self):
        """情绪稳定"""
        t = SentimentReversalTracker(window=20)
        for _ in range(40):
            t.add(50.0)
        r = t.detect()
        self.assertFalse(r.is_reversal)
        self.assertFalse(r.is_warning)

    def test_moderate_decline(self):
        """中等下降"""
        t = SentimentReversalTracker(window=20)
        for _ in range(20):
            t.add(70.0)
        for _ in range(20):
            t.add(50.0)
        r = t.detect()
        self.assertTrue(r.is_warning)
        self.assertFalse(r.is_reversal)
        self.assertEqual(r.signal, "moderate_decline")

    def test_sharp_decline(self):
        """急降"""
        t = SentimentReversalTracker(window=20)
        for _ in range(20):
            t.add(70.0)
        for _ in range(20):
            t.add(40.0)  # -30 / 70 = -43%
        r = t.detect()
        self.assertTrue(r.is_reversal)
        self.assertEqual(r.signal, "sharp_decline")

    def test_extreme_greed_drop(self):
        """极度贪婪骤降 → 反向风险"""
        t = SentimentReversalTracker(window=20)
        for _ in range(20):
            t.add(85.0)  # 极度贪婪
        for _ in range(20):
            t.add(60.0)  # -25
        r = t.detect()
        self.assertTrue(r.is_reversal)
        self.assertEqual(r.signal, "extreme_greed_drop")

    def test_extreme_fear_rise(self):
        """极度恐惧反弹 → 反向机会"""
        t = SentimentReversalTracker(window=20)
        for _ in range(20):
            t.add(15.0)  # 极度恐惧
        for _ in range(20):
            t.add(35.0)  # +20
        r = t.detect()
        self.assertTrue(r.is_reversal)
        self.assertEqual(r.signal, "extreme_fear_rise")


class TestToDict(unittest.TestCase):
    def test_basic(self):
        t = SentimentReversalTracker(window=20)
        for _ in range(20):
            t.add(50.0)
        r = t.detect()
        d = to_dict(r)
        self.assertIn("n_samples", d)
        self.assertIn("is_reversal", d)


class TestCurrentZone(unittest.TestCase):
    def test_extreme_greed(self):
        self.assertEqual(current_zone(85), "extreme_greed")

    def test_neutral(self):
        self.assertEqual(current_zone(50), "neutral")

    def test_extreme_fear(self):
        self.assertEqual(current_zone(15), "extreme_fear")


if __name__ == "__main__":
    unittest.main(verbosity=2)
