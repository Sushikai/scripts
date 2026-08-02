#!/usr/bin/env python3
"""
test_alert_system.py
Ship 26 单元测试 — 告警系统
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.alert_system import (
    Alert, AlertManager,
    check_single_drop, check_total_dd, check_datasource, check_regime_change,
    SINGLE_DROP_WARN, SINGLE_DROP_BLOCK,
    TOTAL_DD_WARN, TOTAL_DD_BLOCK,
)


class TestAlertManager(unittest.TestCase):
    def test_send_basic(self):
        mgr = AlertManager()
        sent = mgr.send(Alert(
            type="test", severity="info",
            message="hello", dedupe_key="k1",
        ))
        self.assertTrue(sent)
        self.assertEqual(len(mgr.history()), 1)

    def test_quiet_period(self):
        mgr = AlertManager(quiet_seconds=10)
        a = Alert(type="t", severity="info", message="m", dedupe_key="k1")
        self.assertTrue(mgr.send(a))
        self.assertFalse(mgr.send(a))  # 同 key 静默
        self.assertEqual(len(mgr.history()), 2)  # 历史仍记录
        # 清静默 → 再次发送
        mgr.clear_quiet("k1")
        self.assertTrue(mgr.send(a))

    def test_clear_all_quiet(self):
        mgr = AlertManager(quiet_seconds=10)
        a = Alert(type="t", severity="info", message="m", dedupe_key="k1")
        mgr.send(a)
        mgr.clear_quiet()
        self.assertTrue(mgr.send(a))

    def test_no_dedupe_key_always_sends(self):
        mgr = AlertManager()
        for _ in range(5):
            self.assertTrue(mgr.send(Alert(
                type="t", severity="info", message="m",
            )))

    def test_sender_failure(self):
        mgr = AlertManager()
        def bad_sender(a):
            raise RuntimeError("TG fail")
        mgr.set_sender(bad_sender)
        sent = mgr.send(Alert(type="t", severity="info", message="m"))
        self.assertFalse(sent)

    def test_history_order(self):
        mgr = AlertManager()
        mgr.send(Alert("t", "info", "first", dedupe_key="k1"))
        mgr.send(Alert("t", "info", "second", dedupe_key="k2"))
        h = mgr.history()
        self.assertEqual(h[0].message, "second")  # 最新在前
        self.assertEqual(h[1].message, "first")


class TestCheckSingleDrop(unittest.TestCase):
    def test_no_drop(self):
        mgr = AlertManager()
        self.assertIsNone(check_single_drop("A", 10, 11, mgr))

    def test_warning(self):
        mgr = AlertManager()
        a = check_single_drop("A", 10, 9.4, mgr)  # -6%
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "warning")

    def test_block(self):
        mgr = AlertManager()
        a = check_single_drop("A", 10, 9.0, mgr)  # -10%
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "block")

    def test_invalid_cost(self):
        mgr = AlertManager()
        self.assertIsNone(check_single_drop("A", 0, 9, mgr))


class TestCheckTotalDD(unittest.TestCase):
    def test_no_dd(self):
        mgr = AlertManager()
        self.assertIsNone(check_total_dd(110000, 100000, mgr))

    def test_warning(self):
        mgr = AlertManager()
        a = check_total_dd(88000, 100000, mgr)  # -12%
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "warning")

    def test_block(self):
        mgr = AlertManager()
        a = check_total_dd(80000, 100000, mgr)  # -20%
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "block")

    def test_zero_initial(self):
        mgr = AlertManager()
        self.assertIsNone(check_total_dd(100000, 0, mgr))


class TestCheckDatasource(unittest.TestCase):
    def test_no_alert(self):
        mgr = AlertManager()
        self.assertIsNone(check_datasource("eastmoney", 1, mgr))

    def test_warning(self):
        mgr = AlertManager()
        a = check_datasource("eastmoney", 5, mgr)
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "warning")

    def test_block(self):
        mgr = AlertManager()
        a = check_datasource("eastmoney", 15, mgr)
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "block")


class TestCheckRegimeChange(unittest.TestCase):
    def test_no_change(self):
        mgr = AlertManager()
        self.assertIsNone(check_regime_change("bull", "bull", mgr))

    def test_change(self):
        mgr = AlertManager()
        a = check_regime_change("bull", "bear", mgr)
        self.assertIsNotNone(a)
        self.assertEqual(a.severity, "info")
        self.assertIn("bull", a.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
