#!/usr/bin/env python3
"""
tuixue_v3/strat_era06_vol_breakout.py
Ship 62/100 — 量化 era 2026 高级策略 #6

Volatility Breakout Strategy (波动率突破策略)

设计:
基于 ATR / 历史波动率构造动态阈值:
- 价格突破 N 倍 ATR 上轨 → 强势买入 (momentum)
- 价格跌破 N 倍 ATR 下轨 → 弱势卖出 (reversal)
- 中性区: hold

输入: {code: list[(high, low, close)]} 或 {code: list[float]} (close-only)
输出: signal 列表 (buy/sell/hold)

降级: 数据不足 → 不开仓

2026-08-03 Ship 62 — 10000 轮迭代 P5 第七步
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
class VBSignal:
    code: str
    side: str                  # "buy" / "sell" / "hold"
    current: float
    atr: float                 # 当前 ATR
    upper_band: float          # 上轨
    lower_band: float          # 下轨
    breakout_pct: float        # 突破幅度 (相对 band)
    window: int
    strength: float            # 0-1

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "atr": self.atr,
            "upper_band": self.upper_band,
            "lower_band": self.lower_band,
            "breakout_pct": self.breakout_pct,
            "window": self.window,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def true_range(high: float, low: float, prev_close: float) -> float:
    """True Range: max(high-low, |high-prev_close|, |low-prev_close|)"""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def compute_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    window: int = 14,
) -> Optional[float]:
    """计算 ATR (Average True Range)"""
    n = min(len(highs), len(lows), len(closes))
    if n < window + 1:
        return None

    trs = []
    for i in range(1, n):
        tr = true_range(highs[i], lows[i], closes[i - 1])
        trs.append(tr)

    if len(trs) < window:
        return None

    recent = trs[-window:]
    return statistics.mean(recent)


def compute_atr_from_closes(
    prices: list[float],
    window: int = 14,
) -> Optional[float]:
    """从 close-only 序列估算 ATR (使用 |delta| 作为 TR 近似)"""
    if len(prices) < window + 1:
        return None

    diffs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    if len(diffs) < window:
        return None
    return statistics.mean(diffs[-window:])


def historical_volatility(prices: list[float], window: int = 20) -> Optional[float]:
    """历史波动率 (returns stdev)"""
    if len(prices) < window + 1:
        return None
    rets = [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(len(prices) - window, len(prices))]
    return statistics.stdev(rets) if len(rets) > 1 else 0.0


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    prices: list[float],
    *,
    window: int = 14,
    atr_mult: float = 1.5,
    exit_atr_mult: float = 0.3,
    use_close_only: bool = True,
) -> Optional[VBSignal]:
    """波动率突破信号

    - close > mid + atr_mult * ATR → buy (向上突破)
    - close < mid - atr_mult * ATR → sell (向下突破)
    - |close - mid| < exit_atr_mult * ATR → 回归中轨, hold
    """
    if len(prices) < window + 1:
        return None

    if use_close_only:
        atr = compute_atr_from_closes(prices, window=window)
    else:
        atr = historical_volatility(prices, window=window)
    if atr is None or atr <= 0:
        return None

    sub = prices[-window:]
    mid = statistics.mean(sub)
    current = prices[-1]

    upper = mid + atr_mult * atr
    lower = mid - atr_mult * atr

    if current > upper:
        side = "buy"
        breakout_pct = (current - upper) / upper
        strength = min(1.0, breakout_pct * 10)
    elif current < lower:
        side = "sell"
        breakout_pct = (lower - current) / lower
        strength = min(1.0, breakout_pct * 10)
    elif abs(current - mid) < exit_atr_mult * atr:
        side = "hold"
        breakout_pct = abs(current - mid) / mid if mid > 0 else 0.0
        strength = 1 - breakout_pct * 10
    else:
        return None   # 中性区, 无信号

    return VBSignal(
        code=code, side=side,
        current=round(current, 4),
        atr=round(atr, 4),
        upper_band=round(upper, 4),
        lower_band=round(lower, 4),
        breakout_pct=round(breakout_pct, 4),
        window=window,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 多窗口
# ═══════════════════════════════════════════════════════

def generate_multi_window(
    code: str,
    prices: list[float],
    *,
    windows: tuple[int, ...] = (10, 20, 60),
    atr_mult: float = 1.5,
) -> Optional[VBSignal]:
    """多窗口波动率突破

    一致 → 强信号; 分歧 → None
    """
    signals = []
    for w in windows:
        if len(prices) < w + 1:
            continue
        sig = generate_signal(code, prices, window=w, atr_mult=atr_mult)
        if sig is not None:
            signals.append(sig)

    if not signals:
        return None

    sides = [s.side for s in signals]
    main = max(set(sides), key=sides.count)
    consistent = sum(1 for x in sides if x == main)
    if consistent < len(signals) // 2 + 1:
        return None

    weights = [1.0 / (i + 1) for i in range(len(signals))]
    total_w = sum(weights)
    avg_window = int(sum(s.window * w for s, w in zip(signals, weights)) / total_w)
    avg_strength = sum(s.strength * w for s, w in zip(signals, weights)) / total_w

    latest = signals[-1]
    return VBSignal(
        code=code,
        side=main,
        current=latest.current,
        atr=latest.atr,
        upper_band=latest.upper_band,
        lower_band=latest.lower_band,
        breakout_pct=latest.breakout_pct,
        window=avg_window,
        strength=round(avg_strength * (consistent / len(signals)), 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
    window: int = 14,
    atr_mult: float = 1.5,
) -> list[VBSignal]:
    """扫全 universe, 找波动率突破信号"""
    signals = []
    for code, prices in universe.items():
        sig = generate_signal(code, prices, window=window, atr_mult=atr_mult)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[VBSignal]) -> tuple[list[VBSignal], list[VBSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: VBSignal) -> str:
    return (f"{s.code}: {s.side.upper()} brk={s.breakout_pct:+.2%} "
            f"atr={s.atr:.3f} strength={s.strength:.2f}")