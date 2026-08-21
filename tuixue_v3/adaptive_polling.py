#!/usr/bin/env python3
"""
tuixue_v3/adaptive_polling.py
Ship 27/100 — 自适应轮询频率

设计:
根据多个信号动态调整轮询间隔 (秒):
- 交易时段: 短间隔 (盘中高频)
- 午休 / 收盘: 长间隔
- 周末/节假日: 最长间隔 (无意义)
- 数据源失败率: 高 → 长间隔 (避峰)
- 最近一次有数据 vs 无数据: 长间隔
- Regime 危机: 高频 (快速反应)

输出: next_interval(seconds) — 下次轮询等待秒数

降级: 无法判断 → 默认 60s

2026-08-02 Ship 27 — 10000 轮迭代 P3 第二步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 时段常量 (A 股)
# ═══════════════════════════════════════════════════════

# 集合竞价 9:15-9:30, 连续竞价 9:30-11:30 + 13:00-15:00
MORNING_OPEN = dtime(9, 30)
MORNING_CLOSE = dtime(11, 30)
AFTERNOON_OPEN = dtime(13, 0)
AFTERNOON_CLOSE = dtime(15, 0)

# 基础间隔 (秒)
INTERVAL_PREMARKET = 60       # 9:00-9:30
INTERVAL_TRADING = 3          # 9:30-11:30 / 13:00-14:00 高频
INTERVAL_LUNCH = 600          # 11:30-13:00 (10 分钟)
INTERVAL_AFTERNOON_LATE = 30  # 14:00-15:00 (尾盘)
INTERVAL_CLOSED = 3600        # 15:00-次日 9:00
INTERVAL_WEEKEND = 14400      # 周末 4 小时


@dataclass
class PollingState:
    """轮询状态"""
    last_success: bool = True
    consecutive_failures: int = 0
    regime: str = "unknown"
    data_source_health: float = 1.0  # 0~1
    in_trading_session: bool = False


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def get_base_interval(now: Optional[datetime] = None) -> int:
    """根据时间返回基础间隔"""
    now = now or datetime.now()
    weekday = now.weekday()  # 0=Mon, 6=Sun

    # 周末
    if weekday >= 5:
        return INTERVAL_WEEKEND

    t = now.time()

    # 盘前 (9:00 之前)
    if t < dtime(9, 0):
        return INTERVAL_CLOSED
    # 9:00-9:30 集合竞价
    if t < MORNING_OPEN:
        return INTERVAL_PREMARKET
    # 9:30-11:30 上午连续
    if t < MORNING_CLOSE:
        return INTERVAL_TRADING
    # 11:30-13:00 午休
    if t < AFTERNOON_OPEN:
        return INTERVAL_LUNCH
    # 13:00-14:00 下午连续
    if t < dtime(14, 0):
        return INTERVAL_TRADING
    # 14:00-15:00 尾盘
    if t < AFTERNOON_CLOSE:
        return INTERVAL_AFTERNOON_LATE
    # 收盘后
    return INTERVAL_CLOSED


def get_next_interval(state: PollingState,
                      now: Optional[datetime] = None) -> int:
    """计算下次轮询间隔

    Args:
        state: 轮询状态
        now: 当前时间 (默认 now)

    Returns:
        间隔秒数 (>= 1)
    """
    base = get_base_interval(now)

    # 失败时: 间隔翻倍 (指数退避), 上限 600s
    if state.consecutive_failures > 0:
        backoff = min(2 ** state.consecutive_failures, 600)
        base = max(base, backoff)

    # 数据源不健康 (<0.5): 间隔 × 2
    if state.data_source_health < 0.5:
        base *= 2

    # Regime 危机: 间隔减半 (最小 1s, 只在交易时段)
    if state.regime == "crisis" and state.in_trading_session:
        base = max(1, base // 2)

    return max(1, int(base))


def is_trading_session(now: Optional[datetime] = None) -> bool:
    """是否在交易时段"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    if MORNING_OPEN <= t < MORNING_CLOSE:
        return True
    if AFTERNOON_OPEN <= t < AFTERNOON_CLOSE:
        return True
    return False
