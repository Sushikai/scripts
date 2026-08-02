#!/usr/bin/env python3
"""
tuixue_v3/alert_system.py
Ship 26/100 — 告警系统

设计:
触发条件:
- 单股跌幅 > 5% (warning) / 8% (block)
- 总回撤 > 10% (warning) / 15% (block)
- 数据源失败 N 次
- 策略信号 unhealthy
- Regime 切换 (bull → bear 等)
- 异常 (API 超时, 解析失败)

每个告警: severity / type / message / suggested_action / dedupe_key
支持:
- 发送 (mock, 实际接 TG/email)
- 静默期 (同 dedupe_key 24h 内不重发)
- 等级聚合 (block 升级 critical)

降级: 发送失败 → 静默 + log warn, 不抛异常

2026-08-02 Ship 26 — 10000 轮迭代 P3 第一步
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class Alert:
    """单条告警"""
    type: str                       # drawdown / data_source / strategy / regime
    severity: str                   # info / warning / block / critical
    message: str
    detail: dict = field(default_factory=dict)
    suggested_action: str = ""
    dedupe_key: str = ""            # 同 key 静默期内不重发
    timestamp: float = 0.0


# ═══════════════════════════════════════════════════════
# 阈值常量
# ═══════════════════════════════════════════════════════

# 单股跌幅 (cost → current_price)
SINGLE_DROP_WARN = -0.05
SINGLE_DROP_BLOCK = -0.08

# 总回撤 (equity → initial)
TOTAL_DD_WARN = -0.10
TOTAL_DD_BLOCK = -0.15

# 数据源失败
DATASOURCE_FAIL_WARN = 3
DATASOURCE_FAIL_BLOCK = 10

# 静默期
DEFAULT_QUIET_SECONDS = 86400   # 24h


# ═══════════════════════════════════════════════════════
# 告警管理器
# ═══════════════════════════════════════════════════════

class AlertManager:
    def __init__(self, quiet_seconds: float = DEFAULT_QUIET_SECONDS):
        self.quiet_seconds = quiet_seconds
        self._last_sent: dict[str, float] = {}   # dedupe_key → timestamp
        self._history: list[Alert] = []
        self._send_fn: Optional[Callable] = None

    def set_sender(self, fn: Callable) -> None:
        """设置发送函数 (mock 实际接 TG/email)"""
        self._send_fn = fn

    def send(self, alert: Alert) -> bool:
        """发送告警 (检查静默期)

        Returns:
            True if actually sent, False if suppressed
        """
        alert.timestamp = time.time()
        self._history.append(alert)

        if not alert.dedupe_key:
            return self._dispatch(alert)

        # 静默期检查
        last = self._last_sent.get(alert.dedupe_key, 0)
        if time.time() - last < self.quiet_seconds:
            logger.debug("告警静默: %s (last %.0fs ago)",
                         alert.dedupe_key, time.time() - last)
            return False

        sent = self._dispatch(alert)
        if sent:
            self._last_sent[alert.dedupe_key] = time.time()
        return sent

    def _dispatch(self, alert: Alert) -> bool:
        """实际发送"""
        if self._send_fn is None:
            logger.info("[%s/%s] %s — %s",
                        alert.severity.upper(), alert.type,
                        alert.message, alert.suggested_action)
            return True
        try:
            self._send_fn(alert)
            return True
        except Exception as e:
            logger.warning("告警发送失败: %s", e)
            return False

    def history(self, limit: int = 100) -> list[Alert]:
        """历史告警 (最新在前)"""
        return list(reversed(self._history[-limit:]))

    def clear_quiet(self, dedupe_key: Optional[str] = None) -> None:
        """清除静默记录"""
        if dedupe_key:
            self._last_sent.pop(dedupe_key, None)
        else:
            self._last_sent.clear()


# ═══════════════════════════════════════════════════════
# 触发函数
# ═══════════════════════════════════════════════════════

def check_single_drop(code: str, cost: float, current: float,
                      manager: AlertManager) -> Optional[Alert]:
    """单股跌幅告警"""
    if cost <= 0:
        return None
    drop = (current - cost) / cost
    if drop <= SINGLE_DROP_BLOCK:
        a = Alert(
            type="drawdown",
            severity="block",
            message=f"{code} 单股暴跌 {drop:.2%}",
            detail={"code": code, "drop_pct": drop},
            suggested_action="考虑止损 / 重新评估基本面",
            dedupe_key=f"single_drop:{code}",
        )
        manager.send(a)
        return a
    if drop <= SINGLE_DROP_WARN:
        a = Alert(
            type="drawdown",
            severity="warning",
            message=f"{code} 单股跌 {drop:.2%}",
            detail={"code": code, "drop_pct": drop},
            suggested_action="关注后续走势",
            dedupe_key=f"single_drop:{code}",
        )
        manager.send(a)
        return a
    return None


def check_total_dd(equity: float, initial: float,
                   manager: AlertManager) -> Optional[Alert]:
    """总回撤告警"""
    if initial <= 0:
        return None
    dd = (equity - initial) / initial
    if dd <= TOTAL_DD_BLOCK:
        a = Alert(
            type="drawdown",
            severity="block",
            message=f"组合总回撤 {dd:.2%}",
            detail={"equity": equity, "initial": initial, "dd_pct": dd},
            suggested_action="降仓 / 复盘",
            dedupe_key="total_dd",
        )
        manager.send(a)
        return a
    if dd <= TOTAL_DD_WARN:
        a = Alert(
            type="drawdown",
            severity="warning",
            message=f"组合回撤 {dd:.2%}",
            detail={"equity": equity, "initial": initial, "dd_pct": dd},
            suggested_action="关注风险敞口",
            dedupe_key="total_dd",
        )
        manager.send(a)
        return a
    return None


def check_datasource(name: str, fail_count: int,
                     manager: AlertManager) -> Optional[Alert]:
    """数据源失败次数告警"""
    if fail_count >= DATASOURCE_FAIL_BLOCK:
        a = Alert(
            type="data_source",
            severity="block",
            message=f"数据源 {name} 失败 {fail_count} 次",
            detail={"source": name, "fail_count": fail_count},
            suggested_action="切备用源 / 暂停依赖该源的策略",
            dedupe_key=f"ds_fail:{name}",
        )
        manager.send(a)
        return a
    if fail_count >= DATASOURCE_FAIL_WARN:
        a = Alert(
            type="data_source",
            severity="warning",
            message=f"数据源 {name} 失败 {fail_count} 次",
            detail={"source": name, "fail_count": fail_count},
            suggested_action="检查网络 / 切换备用",
            dedupe_key=f"ds_fail:{name}",
        )
        manager.send(a)
        return a
    return None


def check_regime_change(old: str, new: str,
                        manager: AlertManager) -> Optional[Alert]:
    """regime 切换告警"""
    if old == new:
        return None
    a = Alert(
        type="regime",
        severity="info",
        message=f"市场状态 {old} → {new}",
        detail={"old": old, "new": new},
        suggested_action="检查策略权重",
        dedupe_key=f"regime:{old}->{new}",
    )
    manager.send(a)
    return a
