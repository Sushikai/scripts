#!/usr/bin/env python3
"""
test_metrics_stream.py
Ship 35 单元测试 — 指标流
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.metrics_stream import (
    MetricPoint, MetricsStream, to_sse, aggregate,
)


class TestMetricsStream(unittest.TestCase):
    def test_emit_and_recent(self):
        s = MetricsStream()
        s.emit("ic", 0.05)
        s.emit("ic", 0.06)
        s.emit("ic", 0.07)
        recent = s.recent("ic", n=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[-1].value, 0.07)

    def test_latest(self):
        s = MetricsStream()
        s.emit("a", 1.0)
        s.emit("a", 2.0)
        s.emit("b", 3.0)
        self.assertEqual(s.latest("a").value, 2.0)
        self.assertEqual(s.latest("b").value, 3.0)
        self.assertIsNone(s.latest("c"))

    def test_all_latest(self):
        s = MetricsStream()
        s.emit("a", 1.0)
        s.emit("b", 2.0)
        s.emit("a", 3.0)  # a 第二次
        d = s.all_latest()
        self.assertEqual(d["a"].value, 3.0)
        self.assertEqual(d["b"].value, 2.0)

    def test_by_tag(self):
        s = MetricsStream()
        s.emit("latency", 100, tags={"src": "eastmoney"})
        s.emit("latency", 200, tags={"src": "tencent"})
        s.emit("latency", 150, tags={"src": "eastmoney"})
        em = s.by_tag("src", "eastmoney")
        self.assertEqual(len(em), 2)
        self.assertTrue(all(p.tags["src"] == "eastmoney" for p in em))

    def test_maxlen(self):
        s = MetricsStream(maxlen=5)
        for i in range(10):
            s.emit("x", i)
        self.assertEqual(len(s._buffer), 5)
        # 应保留最后 5 个
        self.assertEqual(s.recent("x", n=10)[-1].value, 9)

    def test_clear(self):
        s = MetricsStream()
        s.emit("a", 1.0)
        s.clear()
        self.assertEqual(len(s._buffer), 0)


class TestAggregate(unittest.TestCase):
    def test_no_points(self):
        s = MetricsStream()
        a = aggregate(s, "x", window_seconds=60)
        self.assertEqual(a["count"], 0)

    def test_avg_min_max(self):
        s = MetricsStream()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            s.emit("ic", v)
        a = aggregate(s, "ic", window_seconds=60)
        self.assertEqual(a["count"], 5)
        self.assertEqual(a["avg"], 3.0)
        self.assertEqual(a["min"], 1.0)
        self.assertEqual(a["max"], 5.0)
        self.assertEqual(a["latest"], 5.0)


class TestToSSE(unittest.TestCase):
    def test_basic(self):
        s = MetricsStream()
        s.emit("a", 1.0)
        s.emit("b", 2.0)
        out = to_sse(s)
        self.assertTrue(out.startswith("data: "))
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_with_names(self):
        s = MetricsStream()
        s.emit("a", 1.0)
        s.emit("b", 2.0)
        out = to_sse(s, names=["a"])
        self.assertIn("a", out)
        self.assertNotIn('"name": "b"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
