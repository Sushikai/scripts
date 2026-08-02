#!/usr/bin/env python3
"""
test_adaptive_polling.py
Ship 27 单元测试 — 自适应轮询频率
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.adaptive_polling import (
    PollingState, get_base_interval, get_next_interval,
    is_trading_session,
    INTERVAL_TRADING, INTERVAL_CLOSED, INTERVAL_WEEKEND,
)


def dt(hour, minute, weekday=0):
    """构造一个 datetime (weekday 0=Mon)"""
    return datetime(2026, 8, 3 + weekday, hour, minute)


class TestGetBaseInterval(unittest.TestCase):
    def test_weekend(self):
        # 2026-08-08 是周六 (weekday=5)
        self.assertEqual(get_base_interval(dt(10, 0, weekday=5)), INTERVAL_WEEKEND)
        self.assertEqual(get_base_interval(dt(10, 0, weekday=6)), INTERVAL_WEEKEND)

    def test_before_market(self):
        self.assertEqual(get_base_interval(dt(8, 30)), INTERVAL_CLOSED)

    def test_premarket(self):
        self.assertEqual(get_base_interval(dt(9, 15)), 60)  # INTERVAL_PREMARKET

    def test_morning_trading(self):
        self.assertEqual(get_base_interval(dt(10, 0)), INTERVAL_TRADING)

    def test_lunch(self):
        self.assertEqual(get_base_interval(dt(12, 0)), 600)

    def test_afternoon_early(self):
        self.assertEqual(get_base_interval(dt(13, 30)), INTERVAL_TRADING)

    def test_afternoon_late(self):
        self.assertEqual(get_base_interval(dt(14, 30)), 30)

    def test_after_close(self):
        self.assertEqual(get_base_interval(dt(15, 30)), INTERVAL_CLOSED)


class TestGetNextInterval(unittest.TestCase):
    def test_no_failures(self):
        s = PollingState(consecutive_failures=0, data_source_health=1.0)
        self.assertEqual(get_next_interval(s, dt(10, 0)), INTERVAL_TRADING)

    def test_one_failure(self):
        s = PollingState(consecutive_failures=1, data_source_health=1.0)
        # backoff = 2, base 3 → max(3, 2) = 3
        self.assertEqual(get_next_interval(s, dt(10, 0)), INTERVAL_TRADING)

    def test_many_failures(self):
        s = PollingState(consecutive_failures=5, data_source_health=1.0)
        # backoff = 32, base 3 → max(3, 32) = 32
        self.assertEqual(get_next_interval(s, dt(10, 0)), 32)

    def test_huge_failures_clamped(self):
        s = PollingState(consecutive_failures=20, data_source_health=1.0)
        # backoff = min(2^20, 600) = 600
        self.assertEqual(get_next_interval(s, dt(10, 0)), 600)

    def test_unhealthy_source_doubles(self):
        s = PollingState(consecutive_failures=0, data_source_health=0.3)
        # base 3 × 2 = 6
        self.assertEqual(get_next_interval(s, dt(10, 0)), 6)

    def test_crisis_regime_halves(self):
        s = PollingState(consecutive_failures=0,
                        data_source_health=1.0,
                        regime="crisis", in_trading_session=True)
        # base 3 // 2 = 1
        self.assertEqual(get_next_interval(s, dt(10, 0)), 1)

    def test_crisis_regime_outside_trading(self):
        """非交易时段危机不缩间隔"""
        s = PollingState(consecutive_failures=0,
                        data_source_health=1.0,
                        regime="crisis", in_trading_session=False)
        # base 3600, 不缩
        self.assertEqual(get_next_interval(s, dt(16, 0)), INTERVAL_CLOSED)

    def test_min_one_second(self):
        s = PollingState(consecutive_failures=0,
                        data_source_health=1.0,
                        regime="crisis", in_trading_session=True)
        # 强制最小 1
        self.assertGreaterEqual(get_next_interval(s, dt(10, 0)), 1)


class TestIsTradingSession(unittest.TestCase):
    def test_morning(self):
        self.assertTrue(is_trading_session(dt(10, 0)))
        self.assertTrue(is_trading_session(dt(9, 30)))
        self.assertFalse(is_trading_session(dt(9, 29)))

    def test_lunch(self):
        self.assertFalse(is_trading_session(dt(12, 0)))
        self.assertFalse(is_trading_session(dt(11, 30)))

    def test_afternoon(self):
        self.assertTrue(is_trading_session(dt(13, 0)))
        self.assertTrue(is_trading_session(dt(14, 59)))
        self.assertFalse(is_trading_session(dt(15, 0)))

    def test_weekend(self):
        self.assertFalse(is_trading_session(dt(10, 0, weekday=5)))
        self.assertFalse(is_trading_session(dt(10, 0, weekday=6)))

    def test_evening(self):
        self.assertFalse(is_trading_session(dt(20, 0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
