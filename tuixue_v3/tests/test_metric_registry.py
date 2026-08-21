#!/usr/bin/env python3
"""
test_metric_registry.py
Ship 40 单元测试 — 监控指标注册中心
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.metric_registry import (
    MetricType, MetricDef, MetricSample,
    MetricRegistry, default_registry,
)


class TestRegister(unittest.TestCase):
    def test_basic(self):
        r = MetricRegistry()
        r.register("x", MetricType.GAUGE, "ms", "test")
        self.assertTrue(r.registered("x"))

    def test_idempotent(self):
        r = MetricRegistry()
        d1 = r.register("x", "gauge", "ms")
        d2 = r.register("x", "counter", "s")  # 应忽略
        self.assertEqual(d1.type, "gauge")
        self.assertEqual(d2.type, "gauge")  # 不变

    def test_unregistered_returns_false(self):
        r = MetricRegistry()
        self.assertFalse(r.registered("nonexistent"))

    def test_all_defs(self):
        r = MetricRegistry()
        r.register("a")
        r.register("b")
        self.assertEqual(len(r.all_defs()), 2)

    def test_by_tag(self):
        r = MetricRegistry()
        r.register("a", tags={"src": "eastmoney"})
        r.register("b", tags={"src": "tencent"})
        r.register("c", tags={"src": "eastmoney"})
        result = r.by_tag("src", "eastmoney")
        self.assertEqual(len(result), 2)
        self.assertIn("a", [d.name for d in result])
        self.assertIn("c", [d.name for d in result])


class TestRecord(unittest.TestCase):
    def test_gauge(self):
        r = MetricRegistry()
        r.register("latency")
        r.record("latency", 100.0)
        r.record("latency", 200.0)
        s = r.get("latency")
        self.assertEqual(s.value, 200.0)
        self.assertEqual(s.type, "gauge")

    def test_unregistered_uses_default(self):
        r = MetricRegistry()
        r.record("foo", 1.0)
        s = r.get("foo")
        self.assertEqual(s.type, "gauge")

    def test_increment(self):
        r = MetricRegistry()
        r.register("hits", MetricType.COUNTER)
        r.increment("hits", 1.0)
        r.increment("hits", 2.0)
        s = r.get("hits")
        self.assertEqual(s.value, 3.0)

    def test_increment_fresh(self):
        r = MetricRegistry()
        r.increment("foo", 5.0)
        s = r.get("foo")
        self.assertEqual(s.value, 5.0)

    def test_snapshot(self):
        r = MetricRegistry()
        r.record("a", 1.0)
        r.record("b", 2.0)
        snap = r.snapshot()
        self.assertEqual(len(snap), 2)

    def test_by_type(self):
        r = MetricRegistry()
        r.register("g1", MetricType.GAUGE)
        r.register("c1", MetricType.COUNTER)
        r.record("g1", 1.0)
        r.record("c1", 5.0)
        gauges = r.by_type(MetricType.GAUGE)
        self.assertEqual(len(gauges), 1)
        counters = r.by_type(MetricType.COUNTER)
        self.assertEqual(len(counters), 1)

    def test_clear_samples(self):
        r = MetricRegistry()
        r.record("a", 1.0)
        r.clear_samples()
        self.assertEqual(len(r.snapshot()), 0)


class TestExport(unittest.TestCase):
    def test_basic(self):
        r = MetricRegistry()
        r.register("latency", MetricType.GAUGE, "ms", "test")
        r.record("latency", 100.0)
        out = r.export()
        self.assertEqual(len(out), 1)
        m = out[0]
        self.assertEqual(m["name"], "latency")
        self.assertEqual(m["unit"], "ms")
        self.assertEqual(m["value"], 100.0)
        self.assertEqual(m["description"], "test")


class TestDefaultRegistry(unittest.TestCase):
    def test_pre_registered(self):
        r = default_registry()
        # 至少应有 latency_eastmoney, requests_total
        self.assertTrue(r.registered("latency_eastmoney"))
        self.assertTrue(r.registered("requests_total"))
        self.assertTrue(r.registered("equity"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
