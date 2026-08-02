#!/usr/bin/env python3
"""
test_health_monitor.py
Ship 28 单元测试 — 系统健康监控
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.health_monitor import (
    SourceHealth, HealthSnapshot, HealthTracker, to_dict,
)


class TestSourceHealth(unittest.TestCase):
    def test_no_data(self):
        h = SourceHealth(name="x")
        self.assertEqual(h.total, 0)
        self.assertEqual(h.success_rate, 1.0)

    def test_all_success(self):
        h = SourceHealth(name="x", success_count=10, fail_count=0)
        self.assertEqual(h.success_rate, 1.0)

    def test_partial_success(self):
        h = SourceHealth(name="x", success_count=7, fail_count=3)
        self.assertAlmostEqual(h.success_rate, 0.7, places=2)

    def test_health_score(self):
        h = SourceHealth(name="x", success_count=10, fail_count=0,
                        avg_latency_ms=100)
        self.assertGreater(h.health_score, 0.9)

    def test_health_score_high_latency(self):
        h = SourceHealth(name="x", success_count=10, fail_count=0,
                        avg_latency_ms=10000)
        # latency penalty: 1 - 10000/5000 = -1, clamp 0 → 仅 sr*0.7 = 0.7
        self.assertAlmostEqual(h.health_score, 0.7, places=2)


class TestHealthTracker(unittest.TestCase):
    def test_record_source_call(self):
        t = HealthTracker()
        t.record_source_call("eastmoney", True, 100)
        t.record_source_call("eastmoney", True, 200)
        t.record_source_call("eastmoney", False, 500, "timeout")
        s = t.snapshot()
        self.assertIn("eastmoney", s.sources)
        self.assertEqual(s.sources["eastmoney"].success_count, 2)
        self.assertEqual(s.sources["eastmoney"].fail_count, 1)

    def test_ema_latency(self):
        t = HealthTracker()
        t.record_source_call("x", True, 100)
        t.record_source_call("x", True, 200)
        # avg = 0.8*100 + 0.2*200 = 120
        self.assertAlmostEqual(t.sources["x"].avg_latency_ms, 120, places=1)

    def test_api_p95(self):
        t = HealthTracker()
        for i in range(20):
            t.record_api_call(float(i * 10))  # 0~190ms
        s = t.snapshot()
        # p95 ≈ 190 * 0.95 ≈ 180
        self.assertGreater(s.api_p95_ms, 100)

    def test_cache_hit_rate(self):
        t = HealthTracker()
        for _ in range(8):
            t.record_cache(True)
        for _ in range(2):
            t.record_cache(False)
        s = t.snapshot()
        self.assertAlmostEqual(s.cache_hit_rate, 0.8, places=2)

    def test_error_rate(self):
        t = HealthTracker()
        for _ in range(8):
            t.record_api_call(100, success=True)
        for _ in range(2):
            t.record_api_call(100, success=False)
        s = t.snapshot()
        self.assertAlmostEqual(s.error_rate, 0.2, places=2)

    def test_overall_score(self):
        t = HealthTracker()
        # 全部成功 → 高分
        for _ in range(10):
            t.record_source_call("x", True, 50)
        for _ in range(10):
            t.record_api_call(50)
        s = t.snapshot()
        self.assertGreater(s.overall_score, 80)

    def test_critical_when_low(self):
        t = HealthTracker()
        for _ in range(5):
            t.record_source_call("x", False, 100, "fail")
        s = t.snapshot()
        self.assertLess(s.overall_score, 50)
        self.assertTrue(s.is_critical())

    def test_warning_when_mid(self):
        t = HealthTracker()
        # 半成功 → 中等分
        for _ in range(5):
            t.record_source_call("x", True, 100)
        for _ in range(5):
            t.record_source_call("x", False, 100, "fail")
        s = t.snapshot()
        # overall = 0.5 * 100 = 50, 临界 warning
        self.assertTrue(s.is_warning() or s.is_critical())

    def test_issues_listed(self):
        t = HealthTracker()
        for _ in range(10):
            t.record_source_call("bad", False, 100, "fail")
        s = t.snapshot()
        self.assertGreater(len(s.issues), 0)
        self.assertTrue(any("bad" in i for i in s.issues))

    def test_empty_snapshot(self):
        t = HealthTracker()
        s = t.snapshot()
        # 无数据 → overall = 100
        self.assertEqual(s.overall_score, 100)
        self.assertEqual(s.api_p95_ms, None)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        t = HealthTracker()
        t.record_source_call("em", True, 100)
        s = t.snapshot()
        d = to_dict(s)
        self.assertIn("overall_score", d)
        self.assertIn("sources", d)
        self.assertIn("em", d["sources"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
