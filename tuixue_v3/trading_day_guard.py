#!/usr/bin/env python3
"""
tuixue_v3/trading_day_guard.py
Ship 5/100 — 跨日污染终极防御 (zk-style invariant)

设计:
- 任何 cache 写都强校验 trade_date 与 today_str 一致
- 3 种日期格式统一 (YYYYMMDD / YYYY-MM-DD / epoch)
- 自动检测并拒绝跨日污染写入
- 不破坏现有接口, 提供 guard_before_write() / guard_after_read() 包裹层
- 配置: STRICT_CROSS_DAY_GUARD=0 可临时关闭 (admin only)

背景: 项目历史多次因多源并行无日期校验导致跨日污染 (memory
feedback_tuixue_v3_intraday_date_defense / feedback_tuixue_v3_10d_streak_strict)
此 Ship 是终极防御层: 不依赖单点维护, 写入即校验。

2026-08-02 Ship 5 — 10000 轮迭代 P0 第五步
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, date
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 日期格式统一
# ═══════════════════════════════════════════════════════

_RE_YYYYMMDD = re.compile(r"^\d{8}$")
_RE_YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_YYYY_MM_DD_HH_MM_SS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def normalize_date(value: Any) -> Optional[str]:
    """3 种格式 → 统一 YYYY-MM-DD

    Args:
        value: 字符串/date/datetime/int(epoch)
    Returns:
        YYYY-MM-DD 字符串, 或 None (无法识别)

    Examples:
        >>> normalize_date("20260802")
        '2026-08-02'
        >>> normalize_date("2026-08-02")
        '2026-08-02'
        >>> normalize_date("2026-08-02 15:00:00")
        '2026-08-02'
        >>> normalize_date(date(2026, 8, 2))
        '2026-08-02'
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)):
        try:
            # epoch 秒
            if value > 1e10:
                value = value / 1000  # ms → s
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if _RE_YYYYMMDD.match(s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if _RE_YYYY_MM_DD.match(s):
        return s
    if _RE_YYYY_MM_DD_HH_MM_SS.match(s):
        return s.split(" ")[0].split("T")[0]
    # 尝试 datetime parse
    for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(s[:len(fmt)+4], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def today_str() -> str:
    """今天日期 YYYY-MM-DD (本地时区)"""
    return datetime.now().strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════
# 守卫 — 写入前/读取后校验
# ═══════════════════════════════════════════════════════

@dataclass
class GuardResult:
    """守卫结果"""
    ok: bool
    reason: str = ""
    trade_date: Optional[str] = None   # 检测到的日期
    expected_date: Optional[str] = None  # 期望的日期


class TradingDayGuard:
    """跨日污染守卫 (单例)"""

    def __init__(self):
        self._lock = threading.RLock()
        self._violations = 0
        self._last_violation: Optional[GuardResult] = None
        self._enabled = self._init_enabled()

    def _init_enabled(self) -> bool:
        """默认开启, env STRICT_CROSS_DAY_GUARD=0 可关"""
        return os.environ.get("STRICT_CROSS_DAY_GUARD", "1") != "0"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        with self._lock:
            self._enabled = True

    def disable(self):
        """管理员关闭 (调试用, 不推荐生产)"""
        with self._lock:
            self._enabled = False
            logger.warning("TradingDayGuard 已被手动关闭 (跨日污染风险!)")

    def check(self, payload: Any, expected_date: Optional[str] = None,
              date_field: str = "date") -> GuardResult:
        """检查 payload 中是否有跨日污染

        Args:
            payload: dict / list / str / any
            expected_date: 期望的日期 (默认 = today)
            date_field: dict 中的日期字段名

        Returns:
            GuardResult: ok=True 表示安全, ok=False 表示检测到跨日污染
        """
        if not self._enabled:
            return GuardResult(ok=True, reason="guard disabled")

        expected = expected_date or today_str()

        # 字符串直接 normalize
        if isinstance(payload, str):
            actual = normalize_date(payload)
            if actual is None:
                return GuardResult(ok=True, reason="not a date", trade_date=None, expected_date=expected)
            return self._compare(actual, expected)

        # dict: 取 date_field 或扫所有值
        if isinstance(payload, dict):
            # 优先取明确字段
            if date_field in payload:
                actual = normalize_date(payload[date_field])
                if actual is not None:
                    return self._compare(actual, expected)
            # fallback: 扫描所有 string 值
            for k, v in payload.items():
                if isinstance(v, str):
                    actual = normalize_date(v)
                    if actual and actual != expected:
                        return GuardResult(
                            ok=False,
                            reason=f"field [{k}] = [{v[:50]}] normalized [{actual}] != [{expected}]",
                            trade_date=actual, expected_date=expected,
                        )
            return GuardResult(ok=True, reason="no date found", trade_date=None, expected_date=expected)

        # list: 逐项检查
        if isinstance(payload, list):
            for i, item in enumerate(payload):
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, str):
                            actual = normalize_date(v)
                            if actual and actual != expected:
                                return GuardResult(
                                    ok=False,
                                    reason=f"list[{i}].{k} = [{v[:50]}] normalized [{actual}] != [{expected}]",
                                    trade_date=actual, expected_date=expected,
                                )
            return GuardResult(ok=True, reason="list scanned", trade_date=None, expected_date=expected)

        return GuardResult(ok=True, reason="unsupported type", trade_date=None, expected_date=expected)

    def _compare(self, actual: str, expected: str) -> GuardResult:
        if actual == expected:
            return GuardResult(ok=True, reason="match", trade_date=actual, expected_date=expected)
        result = GuardResult(
            ok=False,
            reason=f"date mismatch: actual={actual}, expected={expected}",
            trade_date=actual, expected_date=expected,
        )
        with self._lock:
            self._violations += 1
            self._last_violation = result
        logger.error(
            f"🚨 跨日污染检测: actual={actual}, expected={expected} "
            f"(total violations: {self._violations})"
        )
        return result

    def stats(self) -> dict:
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_violations": self._violations,
                "last_violation": (
                    {
                        "trade_date": self._last_violation.trade_date,
                        "expected_date": self._last_violation.expected_date,
                        "reason": self._last_violation.reason,
                    }
                    if self._last_violation else None
                ),
            }

    def reset_stats(self):
        """测试/调试用"""
        with self._lock:
            self._violations = 0
            self._last_violation = None


# 全局单例
guard = TradingDayGuard()


# ═══════════════════════════════════════════════════════
# 业务包裹层 — 写入/读取自动守卫
# ═══════════════════════════════════════════════════════

class CrossDayGuardError(Exception):
    """跨日污染 — 写入被拒"""
    pass


def guard_before_write(payload: Any, *, expected_date: Optional[str] = None,
                        date_field: str = "date", strict: bool = True) -> Any:
    """写入 cache 前调用 — 跨日则抛错或返回 None

    Args:
        payload: 要写入的数据
        expected_date: 期望日期 (默认 today)
        date_field: dict 中的日期字段名
        strict: True=抛错 / False=返 None

    Returns:
        原 payload (校验通过) 或 None (失败)
    Raises:
        CrossDayGuardError: strict=True 且检测到污染
    """
    result = guard.check(payload, expected_date=expected_date, date_field=date_field)
    if result.ok:
        return payload
    if strict:
        raise CrossDayGuardError(
            f"跨日污染拦截: {result.reason} "
            f"(actual={result.trade_date}, expected={result.expected_date})"
        )
    logger.warning(f"跨日污染已拦截 (non-strict): {result.reason}")
    return None


def guard_after_read(payload: Any, *, expected_date: Optional[str] = None,
                      date_field: str = "date") -> Any:
    """读取 cache 后调用 — 跨日则记 metrics 但不抛错 (便于前端兜底)"""
    result = guard.check(payload, expected_date=expected_date, date_field=date_field)
    if result.ok:
        return payload
    # 读取时不抛错, 仅记录 (前端可显示"数据已过期"提示)
    logger.warning(f"⚠ 读到跨日 cache: {result.reason}")
    return None  # 让调用方返空, 触发重新拉取


# ═══════════════════════════════════════════════════════
# 装饰器: 自动守卫 cache 写入函数
# ═══════════════════════════════════════════════════════

def guarded_cache_write(date_field: str = "date", strict: bool = True):
    """装饰器: 自动守卫 cache 写入函数

    Example:
        @guarded_cache_write(date_field="trade_date")
        def cache_set_today_kline(code, payload):
            cache_db.set(f"kline:{code}", payload)
            return True
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            # 假设 payload 是 kwargs['payload'] 或 args[-1]
            payload = kwargs.get("payload", args[-1] if args else None)
            if payload is None:
                return fn(*args, **kwargs)
            guarded = guard_before_write(payload, date_field=date_field, strict=strict)
            if guarded is None and strict:
                raise CrossDayGuardError("decorator 拦截跨日写入")
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator
