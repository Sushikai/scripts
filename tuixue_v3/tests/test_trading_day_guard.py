#!/usr/bin/env python3
"""
test_trading_day_guard.py
Ship 5 单元测试 — 跨日污染终极防御
"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.trading_day_guard import (
    TradingDayGuard, normalize_date, today_str,
    guard_before_write, guard_after_read, CrossDayGuardError,
    guarded_cache_write, guard,
)


class TestNormalizeDate(unittest.TestCase):
    """3 种日期格式统一"""

    def test_yyyymmdd(self):
        self.assertEqual(normalize_date("20260802"), "2026-08-02")

    def test_yyyy_mm_dd(self):
        self.assertEqual(normalize_date("2026-08-02"), "2026-08-02")

    def test_yyyy_mm_dd_hh_mm_ss(self):
        self.assertEqual(normalize_date("2026-08-02 15:00:00"), "2026-08-02")
        self.assertEqual(normalize_date("2026-08-02T15:00:00"), "2026-08-02")

    def test_date_object(self):
        self.assertEqual(normalize_date(date(2026, 8, 2)), "2026-08-02")

    def test_epoch_seconds(self):
        # 2026-08-02 00:00:00 epoch = 1785600000
        self.assertEqual(normalize_date(1785600000), "2026-08-02")

    def test_epoch_ms(self):
        self.assertEqual(normalize_date(1785600000000), "2026-08-02")

    def test_none(self):
        self.assertIsNone(normalize_date(None))

    def test_garbage(self):
        self.assertIsNone(normalize_date("not a date"))
        self.assertIsNone(normalize_date(""))
        self.assertIsNone(normalize_date("123"))

    def test_alternate_formats(self):
        self.assertEqual(normalize_date("2026/08/02"), "2026-08-02")
        self.assertEqual(normalize_date("2026.08.02"), "2026-08-02")

    def test_whitespace_tolerance(self):
        self.assertEqual(normalize_date("  2026-08-02  "), "2026-08-02")


class TestTodayStr(unittest.TestCase):
    """today_str 返回本地日期"""

    def test_format(self):
        self.assertRegex(today_str(), r"^\d{4}-\d{2}-\d{2}$")


class TestGuardBasic(unittest.TestCase):
    """Guard 基础校验"""

    def setUp(self):
        self.g = TradingDayGuard()
        self.g.reset_stats()

    def test_safe_dict(self):
        result = self.g.check({"date": "2026-08-02", "price": 100})
        self.assertTrue(result.ok)

    def test_cross_day_dict(self):
        result = self.g.check({"date": "2026-08-01", "price": 100},
                              expected_date="2026-08-02")
        self.assertFalse(result.ok)
        self.assertEqual(result.trade_date, "2026-08-01")
        self.assertEqual(result.expected_date, "2026-08-02")

    def test_yyyymmdd_format_safe(self):
        result = self.g.check({"date": "20260802"}, expected_date="2026-08-02")
        self.assertTrue(result.ok)

    def test_yyyymmdd_format_cross_day(self):
        result = self.g.check({"date": "20260801"}, expected_date="2026-08-02")
        self.assertFalse(result.ok)

    def test_datetime_format_safe(self):
        result = self.g.check({"date": "2026-08-02 15:00:00"},
                              expected_date="2026-08-02")
        self.assertTrue(result.ok)

    def test_datetime_format_cross_day(self):
        result = self.g.check({"date": "2026-08-01 23:59:59"},
                              expected_date="2026-08-02")
        self.assertFalse(result.ok)

    def test_scan_other_fields(self):
        """dict 无 date 字段时扫描所有 string 值"""
        result = self.g.check({"trade_date": "2026-08-01"}, expected_date="2026-08-02")
        self.assertFalse(result.ok)

    def test_no_date_in_payload(self):
        result = self.g.check({"price": 100, "name": "abc"}, expected_date="2026-08-02")
        self.assertTrue(result.ok)  # 无日期 = 安全

    def test_list_with_cross_day(self):
        result = self.g.check([
            {"date": "2026-08-02", "price": 100},
            {"date": "2026-08-01", "price": 99},
        ], expected_date="2026-08-02")
        self.assertFalse(result.ok)
        self.assertIn("list[1]", result.reason)

    def test_list_all_safe(self):
        result = self.g.check([
            {"date": "2026-08-02", "price": 100},
            {"date": "2026-08-02", "price": 99},
        ], expected_date="2026-08-02")
        self.assertTrue(result.ok)


class TestGuardStats(unittest.TestCase):
    """Stats 累计"""

    def setUp(self):
        self.g = TradingDayGuard()
        self.g.reset_stats()

    def test_violations_counted(self):
        self.g.check({"date": "2026-08-01"}, expected_date="2026-08-02")
        self.g.check({"date": "2026-07-31"}, expected_date="2026-08-02")
        stats = self.g.stats()
        self.assertEqual(stats["total_violations"], 2)
        self.assertEqual(stats["last_violation"]["trade_date"], "2026-07-31")

    def test_reset(self):
        self.g.check({"date": "2026-08-01"}, expected_date="2026-08-02")
        self.g.reset_stats()
        stats = self.g.stats()
        self.assertEqual(stats["total_violations"], 0)
        self.assertIsNone(stats["last_violation"])


class TestGuardToggle(unittest.TestCase):
    """启用/禁用"""

    def test_disabled_skips_check(self):
        g = TradingDayGuard()
        g.disable()
        # 跨日污染但不抛错
        result = g.check({"date": "2026-08-01"}, expected_date="2026-08-02")
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "guard disabled")
        g.enable()


class TestGuardBeforeWrite(unittest.TestCase):
    """写入守卫 — 严格抛错 vs 静默返 None"""

    def test_safe_passes(self):
        payload = {"date": today_str(), "price": 100}
        result = guard_before_write(payload)
        self.assertEqual(result, payload)

    def test_cross_day_strict_raises(self):
        payload = {"date": "2026-08-01", "price": 100}
        with patch("tuixue_v3.trading_day_guard.today_str", return_value="2026-08-02"):
            with self.assertRaises(CrossDayGuardError):
                guard_before_write(payload, strict=True)

    def test_cross_day_non_strict_returns_none(self):
        payload = {"date": "2026-08-01", "price": 100}
        with patch("tuixue_v3.trading_day_guard.today_str", return_value="2026-08-02"):
            result = guard_before_write(payload, strict=False)
            self.assertIsNone(result)


class TestGuardAfterRead(unittest.TestCase):
    """读取守卫 — 不抛错, 仅记录"""

    def test_safe_returns_payload(self):
        payload = {"date": today_str(), "price": 100}
        result = guard_after_read(payload)
        self.assertEqual(result, payload)

    def test_cross_day_returns_none(self):
        payload = {"date": "2026-08-01", "price": 100}
        with patch("tuixue_v3.trading_day_guard.today_str", return_value="2026-08-02"):
            result = guard_after_read(payload)
            self.assertIsNone(result)


class TestDecorator(unittest.TestCase):
    """guarded_cache_write 装饰器"""

    def test_safe_calls_inner(self):
        called = []

        @guarded_cache_write(date_field="trade_date")
        def writer(payload):
            called.append(payload)
            return True

        # 跨日污染应抛错
        with patch("tuixue_v3.trading_day_guard.today_str", return_value="2026-08-02"):
            with self.assertRaises(CrossDayGuardError):
                writer({"trade_date": "2026-08-01"})

        # 当天 safe
        with patch("tuixue_v3.trading_day_guard.today_str", return_value="2026-08-02"):
            writer({"trade_date": "2026-08-02"})

        self.assertEqual(len(called), 1)


class TestGlobalGuard(unittest.TestCase):
    """全局 guard 单例"""

    def test_singleton(self):
        from tuixue_v3.trading_day_guard import guard as g1
        from tuixue_v3.trading_day_guard import guard as g2
        self.assertIs(g1, g2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
