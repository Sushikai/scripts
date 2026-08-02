#!/usr/bin/env python3
"""
tuixue_v3/strat_era13_turtle.py
Ship 69/100 — 量化 era 2026 高级策略 #13

Turtle Trading Strategy (海龟交易策略)

设计:
经典海龟交易规则:
- 入场: 突破 N 日新高 (entry_window, 默认 20)
- 离场: 突破 N 日新低 (exit_window, 默认 10)
- 仓位: 基于 ATR (vol_unit = account / (risk_per_trade * ATR))
- 止损: 2 ATR

输入: {code: list[float]} 价格时序
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 69 — 10000 轮迭代 P5 第十四步
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class TurtleSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    n_high: float              # N 日新高
    n_low: float               # N 日新低
    atr: float                 # ATR
    units: float               # 建议仓位单位
    stop_loss: float           # 止损价
    reason: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "n_high": self.n_high,
            "n_low": self.n_low,
            "atr": self.atr,
            "units": self.units,
            "stop_loss": self.stop_loss,
            "reason": self.reason,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def n_day_high(prices: list[float], window: int) -> Optional[float]:
    """N 日最高价 (排除当前)"""
    if len(prices) < window + 1:
        return None
    return max(prices[-(window + 1):-1])


def n_day_low(prices: list[float], window: int) -> Optional[float]:
    """N 日最低价 (排除当前)"""
    if len(prices) < window + 1:
        return None
    return min(prices[-(window + 1):-1])


def atr_from_prices(prices: list[float], window: int = 20) -> Optional[float]:
    """从价格序列估算 ATR"""
    if len(prices) < window + 1:
        return None
    diffs = [abs(prices[i] - prices[i - 1]) for i in range(len(prices) - window, len(prices))]
    return statistics.mean(diffs)


# ═══════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    entry_window: int = 20,
    exit_window: int = 10,
    atr_window: int = 20,
    risk_per_trade: float = 0.01,
    account_size: float = 1.0,
) -> Optional[TurtleSignal]:
    """海龟信号

    - close > entry_window 高点 → buy
    - close < exit_window 低点 → sell
    - 中性区 → hold
    """
    if len(prices) < max(entry_window, exit_window, atr_window) + 1:
        return None

    nh = n_day_high(prices, entry_window)
    nl = n_day_low(prices, exit_window)
    atr = atr_from_prices(prices, window=atr_window)

    if nh is None or nl is None or atr is None or atr <= 0:
        return None

    current = prices[-1]

    # 仓位计算
    risk_amt = account_size * risk_per_trade
    units = risk_amt / atr if atr > 0 else 0.0

    if current > nh:
        side = "buy"
        stop = current - 2 * atr
        reason = f"突破 {entry_window} 日新高 ({nh:.2f})"
    elif current < nl:
        side = "sell"
        stop = current + 2 * atr
        reason = f"跌破 {exit_window} 日新低 ({nl:.2f})"
    else:
        side = "hold"
        stop = current - 2 * atr if atr > 0 else current
        reason = f"中性区 ({nl:.2f} - {nh:.2f})"

    return TurtleSignal(
        code=code, side=side,
        current=round(current, 4),
        n_high=round(nh, 4),
        n_low=round(nl, 4),
        atr=round(atr, 4),
        units=round(units, 4),
        stop_loss=round(stop, 4),
        reason=reason,
    )


# ═══════════════════════════════════════════════════════
# 系统1 / 系统2 双系统
# ═══════════════════════════════════════════════════════

def generate_dual_system(
    code: str,
    prices: list[float],
    **kwargs,
) -> dict[str, Optional[TurtleSignal]]:
    """海龟双系统 (短 + 长)"""
    s1 = generate_signal(
        code, prices,
        entry_window=kwargs.get("s1_entry", 20),
        exit_window=kwargs.get("s1_exit", 10),
    )
    s2 = generate_signal(
        code, prices,
        entry_window=kwargs.get("s2_entry", 55),
        exit_window=kwargs.get("s2_exit", 20),
    )
    return {"s1": s1, "s2": s2}


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    entry_window: int = 20,
    exit_window: int = 10,
) -> list[TurtleSignal]:
    """扫全 universe, 找海龟信号"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(
            code, prices,
            entry_window=entry_window, exit_window=exit_window,
        )
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: abs(s.current - s.n_high) / s.n_high, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[TurtleSignal]) -> tuple[list[TurtleSignal], list[TurtleSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: TurtleSignal) -> str:
    return (f"{s.code}: {s.side.upper()} cur={s.current:.2f} "
            f"nh={s.n_high:.2f} nl={s.n_low:.2f} atr={s.atr:.3f} "
            f"stop={s.stop_loss:.2f} ({s.reason})")