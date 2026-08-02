#!/usr/bin/env python3
"""
test_sentiment_sparkline.py
Ship 49 单元测试 — 情绪 Sparkline
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_sparkline import (
    Sparkline, SentimentSparkline,
    build_sparkline_from_list, normalize_to_unit,
    to_dict, to_echarts,
)


class TestTracker(unittest.TestCase):
    def test_basic(self):
        sp = SentimentSparkline(max_n=10)
        for v in [40, 50, 60, 55, 65, 70, 75]:
            sp.add(v)
        s = sp.snapshot()
        self.assertEqual(s.n, 7)
        self.assertEqual(s.current, 75.0)
        self.assertEqual(s.trend, "up")

    def test_down_trend(self):
        sp = SentimentSparkline(max_n=10)
        for v in [80, 70, 60, 50, 40, 30]:
            sp.add(v)
        s = sp.snapshot()
        self.assertEqual(s.trend, "down")

    def test_flat(self):
        sp = SentimentSparkline(max_n=10)
        for v in [50, 51, 49, 50, 50]:
            sp.add(v)
        s = sp.snapshot()
        self.assertEqual(s.trend, "flat")

    def test_min_max(self):
        sp = SentimentSparkline(max_n=10)
        for v in [50, 30, 70, 40, 60]:
            sp.add(v)
        s = sp.snapshot()
        self.assertEqual(s.min_idx, 1)   # 30
        self.assertEqual(s.max_idx, 2)   # 70

    def test_empty(self):
        sp = SentimentSparkline(max_n=10)
        s = sp.snapshot()
        self.assertEqual(s.n, 0)
        self.assertEqual(s.trend, "flat")

    def test_maxlen(self):
        sp = SentimentSparkline(max_n=5)
        for i in range(10):
            sp.add(float(i))
        self.assertEqual(sp.snapshot().n, 5)


class TestBuildFromList(unittest.TestCase):
    def test_basic(self):
        s = build_sparkline_from_list([10, 20, 30, 40], labels=["a", "b", "c", "d"])
        self.assertEqual(s.n, 4)
        self.assertEqual(s.labels, ["a", "b", "c", "d"])
        self.assertEqual(s.current, 40.0)

    def test_no_labels(self):
        s = build_sparkline_from_list([10, 20, 30])
        self.assertEqual(s.labels, ["", "", ""])


class TestNormalize(unittest.TestCase):
    def test_basic(self):
        s = build_sparkline_from_list([10, 20, 30, 40])
        norm = normalize_to_unit(s)
        self.assertEqual(norm.data[0], 0.0)
        self.assertEqual(norm.data[-1], 1.0)
        # 中间点 0.33 / 0.66
        self.assertAlmostEqual(norm.data[1], 1.0 / 3, places=3)

    def test_const(self):
        s = build_sparkline_from_list([5, 5, 5, 5])
        norm = normalize_to_unit(s)
        self.assertEqual(norm.data, [0.5, 0.5, 0.5, 0.5])


class TestToDict(unittest.TestCase):
    def test_basic(self):
        s = build_sparkline_from_list([10, 20, 30])
        d = to_dict(s)
        self.assertIn("data", d)
        self.assertIn("trend", d)
        self.assertEqual(d["current"], 30.0)


class TestToEcharts(unittest.TestCase):
    def test_basic(self):
        s = build_sparkline_from_list([10, 20, 30])
        e = to_echarts(s)
        self.assertEqual(e["type"], "line")
        self.assertEqual(len(e["data"]), 3)
        self.assertEqual(e["current"], 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
