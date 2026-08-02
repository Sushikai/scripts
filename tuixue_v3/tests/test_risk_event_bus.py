#!/usr/bin/env python3
"""
test_risk_event_bus.py
Ship 39 单元测试 — 风险事件总线
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.risk_event_bus import (
    Severity, EventType, RiskEvent, RiskEventBus,
    source_down, market_crash, position_limit_down, risk_violation,
)


class TestSeverity(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(Severity.INFO, "info")
        self.assertIn(Severity.WARNING, ["info", "warning"])
        self.assertEqual(Severity.CRITICAL, "critical")


class TestEmit(unittest.TestCase):
    def test_basic(self):
        bus = RiskEventBus()
        ev = bus.emit("test_type", "info", "test_src", {"k": 1})
        self.assertEqual(ev.type, "test_type")
        self.assertEqual(ev.payload["k"], 1)
        self.assertTrue(ev.event_id.startswith("re_"))

    def test_increments_id(self):
        bus = RiskEventBus()
        ev1 = bus.emit("a", "info", "x")
        ev2 = bus.emit("a", "info", "x")
        self.assertNotEqual(ev1.event_id, ev2.event_id)

    def test_maxlen(self):
        bus = RiskEventBus(maxlen=10)
        for i in range(20):
            bus.emit("a", "info", "x")
        self.assertEqual(len(bus._events), 10)


class TestSubscribe(unittest.TestCase):
    def test_basic(self):
        bus = RiskEventBus()
        received = []

        bus.subscribe("source_down", lambda ev: received.append(ev))
        bus.emit("source_down", "warning", "eastmoney")
        bus.emit("market_crash", "critical", "market")
        # 只订阅 source_down, market_crash 不应触发
        self.assertEqual(len(received), 1)

    def test_wildcard(self):
        bus = RiskEventBus()
        received = []
        bus.subscribe("*", lambda ev: received.append(ev))
        bus.emit("a", "info", "x")
        bus.emit("b", "warning", "y")
        self.assertEqual(len(received), 2)

    def test_failed_subscriber_does_not_block(self):
        bus = RiskEventBus()

        def bad(ev):
            raise RuntimeError("oops")

        bus.subscribe("*", bad)
        # 不应抛
        ev = bus.emit("a", "info", "x")
        self.assertIsNotNone(ev)


class TestQuery(unittest.TestCase):
    def test_recent(self):
        bus = RiskEventBus()
        for i in range(20):
            bus.emit("a", "info", "x")
        recent = bus.recent(5)
        self.assertEqual(len(recent), 5)

    def test_recent_severity_min(self):
        bus = RiskEventBus()
        bus.emit("a", "info", "x")
        bus.emit("a", "warning", "x")
        bus.emit("a", "critical", "x")
        # severity_min=error → 只 critical
        recent = bus.recent(10, severity_min=Severity.ERROR)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].severity, "critical")

    def test_recent_etype_filter(self):
        bus = RiskEventBus()
        bus.emit("a", "info", "x")
        bus.emit("b", "info", "x")
        recent = bus.recent(10, etype="a")
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].type, "a")

    def test_count(self):
        bus = RiskEventBus()
        bus.emit("a", "info", "x")
        bus.emit("a", "info", "x")
        bus.emit("b", "info", "x")
        self.assertEqual(bus.count(), 3)
        self.assertEqual(bus.count(etype="a"), 2)

    def test_last(self):
        bus = RiskEventBus()
        self.assertIsNone(bus.last("missing"))
        bus.emit("a", "info", "x")
        bus.emit("b", "warning", "x")
        ev = bus.last("a")
        self.assertIsNotNone(ev)

    def test_last_critical(self):
        bus = RiskEventBus()
        bus.emit("a", "info", "x")
        bus.emit("b", "critical", "x")
        ev = bus.last_critical()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.severity, "critical")

    def test_clear(self):
        bus = RiskEventBus()
        bus.emit("a", "info", "x")
        bus.clear()
        self.assertEqual(len(bus._events), 0)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        ev = RiskEvent(
            type="a", severity="info", source="x",
            timestamp=1234.5, payload={"k": 1},
            event_id="re_42",
        )
        d = ev.to_dict()
        self.assertEqual(d["type"], "a")
        self.assertEqual(d["event_id"], "re_42")


class TestConvenience(unittest.TestCase):
    def test_source_down(self):
        bus = RiskEventBus()
        ev = source_down(bus, "eastmoney")
        self.assertEqual(ev.type, EventType.SOURCE_DOWN)
        self.assertEqual(ev.source, "eastmoney")
        self.assertEqual(ev.payload["message"], "eastmoney 离线")

    def test_market_crash_5pct(self):
        bus = RiskEventBus()
        ev = market_crash(bus, -6.0)
        self.assertEqual(ev.severity, Severity.CRITICAL)

    def test_market_crash_2pct(self):
        bus = RiskEventBus()
        ev = market_crash(bus, -2.0)
        self.assertEqual(ev.severity, Severity.WARNING)

    def test_position_limit_down(self):
        bus = RiskEventBus()
        ev = position_limit_down(bus, "600519")
        self.assertEqual(ev.type, EventType.POSITION_LIMIT_DOWN)
        self.assertEqual(ev.payload["code"], "600519")

    def test_risk_violation(self):
        bus = RiskEventBus()
        ev = risk_violation(bus, "max_dd", -0.15, -0.10)
        self.assertEqual(ev.type, EventType.RISK_VIOLATION)
        self.assertEqual(ev.payload["threshold"], -0.10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
