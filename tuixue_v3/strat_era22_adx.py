#!/usr/bin/env python3
"""
tuixue_v3/strat_era22_adx.py
Ship 78/100 — 量化 era 2026 高级策略 #22

ADX Strategy (Average Directional Index)

设计:
ADX 衡量趋势强度 (不区分方向):
- ADX > 25: 强趋势 (方向由 +DI/-DI 决定)
- ADX < 20: 弱趋势 / 盘整

+DI > -DI → up 趋势 → buy
+DI < -DI → down 趋势 → sell

输入: {code: list[(high, low, close)]}
输出: signal 列表 (buy/sell/hold)

2026-08-03 Ship 78 — 10000 轮迭代 P5 第二十三步
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
class ADXSignal:
    code: str
    side: str
    current: float
    adx: float                 # ADX (0-100)
    plus_di: float             # +DI
    minus_di: float            # -DI
    trend_strength: str        # "strong" / "moderate" / "weak"
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "adx": self.adx,
            "plus_di": self.plus_di,
            "minus_di": self.minus_di,
            "trend_strength": self.trend_strength,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_adx(
    closes: list[float],
    window: int = 14,
) -> Optional[tuple[float, float, float]]:
    """ADX (从 close-only 估计)

    用 close-only 时, high=close*1.01, low=close*0.99 近似
    """
    if len(closes) < window + 1:
        return None

    # 构造伪 high/low
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]

    # +DM, -DM, TR
    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm.append(up)
        else:
            plus_dm.append(0.0)
        if down > up and down > 0:
            minus_dm.append(down)
        else:
            minus_dm.append(0.0)
        # TR
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < window:
        return None

    # 简单平均
    avg_tr = sum(trs[-window:]) / window
    avg_plus = sum(plus_dm[-window:]) / window
    avg_minus = sum(minus_dm[-window:]) / window

    if avg_tr == 0:
        return None

    plus_di = 100.0 * avg_plus / avg_tr
    minus_di = 100.0 * avg_minus / avg_tr

    # ADX = average of |+DI - -DI| / (+DI + -DI)
    dx_sum = 0.0
    for i in range(-window, 0):
        if (plus_dm[i] + minus_dm[i]) > 0:
            dx_sum += abs(plus_dm[i] - minus_dm[i]) / (plus_dm[i] + minus_dm[i])

    adx = 100.0 * dx_sum / window

    return adx, plus_di, minus_di


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    *,
    window: int = 14,
    strong_threshold: float = 25.0,
    weak_threshold: float = 20.0,
) -> Optional[ADXSignal]:
    """ADX 信号"""
    res = compute_adx(closes, window=window)
    if res is None:
        return None

    adx, plus_di, minus_di = res
    current = closes[-1]

    if adx > strong_threshold:
        strength_label = "strong"
    elif adx < weak_threshold:
        strength_label = "weak"
    else:
        strength_label = "moderate"

    if adx < weak_threshold:
        # 弱趋势 → hold
        side = "hold"
        strength = 0.3
    elif plus_di > minus_di:
        side = "buy"
        strength = min(1.0, adx / 50)
    else:
        side = "sell"
        strength = min(1.0, adx / 50)

    return ADXSignal(
        code=code, side=side,
        current=round(current, 4),
        adx=round(adx, 2),
        plus_di=round(plus_di, 2),
        minus_di=round(minus_di, 2),
        trend_strength=strength_label,
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, list[float]],
    *,
    top_n: int = 10,
) -> list[ADXSignal]:
    """扫全 universe"""
    signals = []
    for code, closes in universe.items():
        sig = generate_signal(code, closes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[ADXSignal]) -> tuple[list[ADXSignal], list[ADXSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: ADXSignal) -> str:
    return (f"{s.code}: {s.side.upper()} adx={s.adx:.1f} "
            f"+DI={s.plus_di:.1f} -DI={s.minus_di:.1f} strength={s.strength:.2f}")