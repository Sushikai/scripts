#!/usr/bin/env python3
"""
test_sentiment_band.py
Ship 46 单元测试 — 情绪分位带
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_band import (
    PercentileBand, SentimentBandTracker, compute_band_static,
    to_dict, signal_from_band, band_width,
)


class TestCompute(unittest.TestCase):
    def test_basic(self):
        t = SentimentBandTracker(window=10)
        for s in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            t.add(s)
        b = t.compute()
        self.assertEqual(b.n, 10)
        # p10 接近 19 (10% of index = 1)
        self.assertGreaterEqual(b.p10, 10)
        self.assertLess(b.p10, 30)

    def test_p50(self):
        t = SentimentBandTracker(window=10)
        for s in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            t.add(s)
        b = t.compute()
        # 中位
        self.assertGreaterEqual(b.p50, 40)
        self.assertLessEqual(b.p50, 60)

    def test_current_top_band(self):
        t = SentimentBandTracker(window=10)
        for s in [10, 20, 30, 40, 50, 60, 70, 80, 80, 80]:
            t.add(s)
        t.set_current(95)  # 远超 p90 (80)
        b = t.compute()
        self.assertTrue(b.is_top_band)

    def test_current_bottom_band(self):
        t = SentimentBandTracker(window=10)
        for s in [50, 60, 70, 80, 90, 90, 90, 90, 90, 90]:
            t.add(s)
        t.set_current(10)
        b = t.compute()
        self.assertTrue(b.is_bottom_band)

    def test_empty(self):
        t = SentimentBandTracker(window=10)
        b = t.compute()
        self.assertEqual(b.n, 0)
        # 默认值
        self.assertEqual(b.p10, 10)


class TestComputeStatic(unittest.TestCase):
    def test_basic(self):
        scores = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        b = compute_band_static(scores, current=50)
        self.assertEqual(b.n, 10)

    def test_no_current(self):
        scores = [10, 20, 30, 40, 50]
        b = compute_band_static(scores)
        # current 缺失 → 默认 50
        self.assertEqual(b.current, 50)
        # p50 是真正的中位 30
        self.assertEqual(b.p50, 30)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        t = SentimentBandTracker(window=10)
        for s in [10, 20, 30]:
            t.add(s)
        b = t.compute()
        d = to_dict(b)
        self.assertIn("p10", d)
        self.assertIn("current_pct", d)


class TestSignal(unittest.TestCase):
    def test_extreme_high(self):
        from tuixue_v3.sentiment_band import PercentileBand
        b = PercentileBand(
            p10=10, p25=25, p50=50, p75=75, p90=90, n=10,
            current=95, current_pct=0.95,
            is_top_band=True, is_bottom_band=False,
        )
        self.assertEqual(signal_from_band(b), "extreme_high")

    def test_extreme_low(self):
        from tuixue_v3.sentiment_band import PercentileBand
        b = PercentileBand(
            p10=10, p25=25, p50=50, p75=75, p90=90, n=10,
            current=5, current_pct=0.05,
            is_top_band=False, is_bottom_band=True,
        )
        self.assertEqual(signal_from_band(b), "extreme_low")


class TestBandWidth(unittest.TestCase):
    def test_basic(self):
        from tuixue_v3.sentiment_band import PercentileBand
        b = PercentileBand(
            p10=10, p25=25, p50=50, p75=75, p90=90, n=10,
            current=50, current_pct=0.5,
            is_top_band=False, is_bottom_band=False,
        )
        self.assertEqual(band_width(b), 80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
