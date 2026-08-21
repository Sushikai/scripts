#!/usr/bin/env python3
"""
test_factor_decay_chart.py
Ship 54 单元测试 — 因子衰减可视化
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_decay_chart import (
    DecayChartPoint, DecayChart,
    build_decay_chart, rolling_ic, _pearson,
    to_echarts, summarize,
)


class TestRollingIC(unittest.TestCase):
    def test_basic(self):
        # 用确定的数验证
        p = list(range(20))
        a = [x * 2 for x in p]
        ics = rolling_ic(p, a, window=10)
        self.assertEqual(len(ics), 20)
        # 后 10 个应接近 1.0
        self.assertAlmostEqual(ics[-1], 1.0, places=4)

    def test_insufficient_window(self):
        ics = rolling_ic([1, 2, 3], [2, 4, 6], window=10)
        # 样本不足, 前端返回 0
        self.assertEqual(len(ics), 3)


class TestPearson(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0)


class TestBuildChart(unittest.TestCase):
    def test_basic(self):
        # 60 个数据, 前 30 IC 高, 后 30 衰减
        ic_series = [0.10] * 30 + [0.02] * 30
        c = build_decay_chart("mom", ic_series, window=20)
        self.assertEqual(c.factor, "mom")
        self.assertEqual(len(c.points), 60)

    def test_status_decayed(self):
        ic_series = [0.10] * 30 + [0.01] * 30  # 后 30 大幅下降
        c = build_decay_chart("mom", ic_series, window=20)
        self.assertEqual(c.status, "decayed")

    def test_status_stable(self):
        ic_series = [0.10] * 60  # 稳定
        c = build_decay_chart("mom", ic_series, window=20)
        self.assertEqual(c.status, "stable")

    def test_insufficient(self):
        c = build_decay_chart("mom", [0.1] * 10, window=20)
        self.assertEqual(len(c.points), 0)

    def test_decay_pct_positive(self):
        ic_series = [0.10] * 30 + [0.02] * 30
        c = build_decay_chart("mom", ic_series, window=20)
        self.assertGreater(c.decay_pct, 0)


class TestToEcharts(unittest.TestCase):
    def test_basic(self):
        ic_series = [0.10] * 30 + [0.02] * 30
        c = build_decay_chart("mom", ic_series)
        out = to_echarts(c)
        self.assertIn("series", out)
        self.assertEqual(len(out["series"]), 2)


class TestSummarize(unittest.TestCase):
    def test_decayed(self):
        ic_series = [0.10] * 30 + [0.01] * 30
        c = build_decay_chart("mom", ic_series)
        s = summarize(c)
        self.assertIn("已弃用", s)

    def test_stable(self):
        ic_series = [0.10] * 60
        c = build_decay_chart("mom", ic_series)
        s = summarize(c)
        self.assertIn("稳定", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
