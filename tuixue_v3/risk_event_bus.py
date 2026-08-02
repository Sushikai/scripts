#!/usr/bin/env python3
"""
tuixue_v3/risk_event_bus.py
Ship 39/100 — 实时风险事件总线 (Risk Event Bus)

设计:
集中管理实时风险事件:
- 数据源故障 → emit "source_down"
- 大盘暴跌 → emit "market_crash"
- 单股跌停 → emit "position_limit_down"
- 单股涨停 → emit "position_limit_up"
- 风控阈值触发 → emit "risk_violation"

事件分类:
- severity: info/warning/error/critical
- source: 模块名
- payload: dict 任意

降级: 失败 subscribe 不阻塞 emit

2026-08-03 Ship 39 — 10000 轮迭代 P3 第十四步
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 严重程度
# ═══════════════════════════════════════════════════════

class Severity:
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

LEVELS = [Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL]
LEVEL_RANK = {l: i for i, l in enumerate(LEVELS)}


# ═══════════════════════════════════════════════════════
# 事件类型
# ═══════════════════════════════════════════════════════

class EventType:
    SOURCE_DOWN = "source_down"
    SOURCE_UP = "source_up"
    MARKET_CRASH = "market_crash"
    MARKET_SPIKE = "market_spike"
    POSITION_LIMIT_DOWN = "position_limit_down"
    POSITION_LIMIT_UP = "position_limit_up"
    POSITION_DRAWDOWN = "position_drawdown"
    RISK_VIOLATION = "risk_violation"
    REGIME_CHANGE = "regime_change"
    FACTOR_DECAY = "factor_decay"
    CUSTOM = "custom"


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class RiskEvent:
    type: str
    severity: str
    source: str
    timestamp: float
    payload: dict = field(default_factory=dict)
    event_id: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "source": self.source,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "event_id": self.event_id,
        }


# ═══════════════════════════════════════════════════════
# 订阅者
# ═══════════════════════════════════════════════════════

SubscriberFn = Callable[[RiskEvent], None]


class RiskEventBus:
    """风险事件总线"""
    def __init__(self, maxlen: int = 1000):
        self.maxlen = maxlen
        self._events: deque = deque(maxlen=maxlen)
        self._subscribers: list[tuple[str, SubscriberFn]] = []
        self._next_id = 0

    def subscribe(self, etype: str, fn: SubscriberFn) -> None:
        """订阅某种事件"""
        self._subscribers.append((etype, fn))

    def emit(self, etype: str, severity: str, source: str,
             payload: Optional[dict] = None) -> RiskEvent:
        """发送事件"""
        self._next_id += 1
        ev = RiskEvent(
            type=etype, severity=severity, source=source,
            timestamp=time.time(),
            payload=payload or {},
            event_id=f"re_{self._next_id}",
        )
        self._events.append(ev)

        # 通知订阅者 (失败不阻塞)
        for sub_etype, fn in self._subscribers:
            if sub_etype == etype or sub_etype == "*":
                try:
                    fn(ev)
                except Exception as e:
                    logger.warning(f"subscriber failed: {e}")

        return ev

    # ─── 查询 ───

    def recent(self, n: int = 50, *, severity_min: Optional[str] = None,
               etype: Optional[str] = None) -> list[RiskEvent]:
        """最近事件 (倒序)"""
        out = []
        for ev in reversed(self._events):
            if severity_min and LEVEL_RANK[ev.severity] < LEVEL_RANK[severity_min]:
                continue
            if etype and ev.type != etype:
                continue
            out.append(ev)
            if len(out) >= n:
                break
        return out

    def count(self, etype: Optional[str] = None,
              since: Optional[float] = None) -> int:
        n = 0
        for ev in self._events:
            if etype and ev.type != etype:
                continue
            if since and ev.timestamp < since:
                continue
            n += 1
        return n

    def last(self, etype: str) -> Optional[RiskEvent]:
        for ev in reversed(self._events):
            if ev.type == etype:
                return ev
        return None

    def last_critical(self) -> Optional[RiskEvent]:
        for ev in reversed(self._events):
            if ev.severity == Severity.CRITICAL:
                return ev
        return None

    def clear(self) -> None:
        self._events.clear()


# ═══════════════════════════════════════════════════════
# 便捷构造
# ═══════════════════════════════════════════════════════

def source_down(bus: RiskEventBus, source: str, msg: str = "") -> RiskEvent:
    return bus.emit(
        EventType.SOURCE_DOWN, Severity.WARNING, source,
        {"message": msg or f"{source} 离线"},
    )


def market_crash(bus: RiskEventBus, change_pct: float) -> RiskEvent:
    sev = Severity.CRITICAL if change_pct <= -5 else Severity.WARNING
    return bus.emit(
        EventType.MARKET_CRASH, sev, "market",
        {"change_pct": change_pct},
    )


def position_limit_down(bus: RiskEventBus, code: str) -> RiskEvent:
    return bus.emit(
        EventType.POSITION_LIMIT_DOWN, Severity.WARNING, "portfolio",
        {"code": code},
    )


def risk_violation(bus: RiskEventBus, rule: str, value: float,
                   threshold: float) -> RiskEvent:
    return bus.emit(
        EventType.RISK_VIOLATION, Severity.ERROR, "risk_engine",
        {"rule": rule, "value": value, "threshold": threshold},
    )
