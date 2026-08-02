#!/usr/bin/env python3
"""
tuixue_v3/strat_era32_cmf.py
Ship 88/100 — 量化 era 2026 高级策略 #32

CMF Strategy (Chaikin Money Flow)

设计:
MFM = ((close - low) - (high - close)) / (high - low)
MFV = MFM * volume
CMF = sum(MFV, N) / sum(volume, N)

信号:
- CMF > 0.2: 资金流入 → buy
- CMF < -0.2: 资金流出 → sell
- 中性: hold

输入: {code: list[(high, low, close, volume)]}
输出: signal 列表

2026-08-03 Ship 88 — 10000 轮迭代 P5 第三十三步
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
class CMFSignal:
    code: str
    side: str
    current: float
    cmf: float                 # CMF 当前值
    mf_volume: float           # 资金流量
    total_volume: float
    strength: float

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "current": self.current,
            "cmf": self.cmf,
            "mf_volume": self.mf_volume,
            "total_volume": self.total_volume,
            "strength": self.strength,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def compute_cmf(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    window: int = 20,
) -> Optional[float]:
    """CMF"""
    n = min(len(closes), len(highs), len(lows), len(volumes))
    if n < window:
        return None

    sub_c = closes[-window:]
    sub_h = highs[-window:]
    sub_l = lows[-window:]
    sub_v = volumes[-window:]

    mfv_total = 0.0
    v_total = 0.0
    for i in range(window):
        h = sub_h[i]
        l = sub_l[i]
        c = sub_c[i]
        v = sub_v[i]
        if h == l:
            mfm = 0.0
        else:
            mfm = ((c - l) - (h - c)) / (h - l)
        mfv_total += mfm * v
        v_total += v

    if v_total == 0:
        return None
    return mfv_total / v_total


# ═══════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════

def generate_signal(
    code: str,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    *,
    window: int = 20,
    inflow_threshold: float = 0.2,
    outflow_threshold: float = -0.2,
) -> Optional[CMFSignal]:
    """CMF 信号"""
    cmf = compute_cmf(closes, highs, lows, volumes, window=window)
    if cmf is None:
        return None

    current = closes[-1]

    if cmf > inflow_threshold:
        side = "buy"
        strength = min(1.0, (cmf - inflow_threshold) / 0.5)
    elif cmf < outflow_threshold:
        side = "sell"
        strength = min(1.0, (outflow_threshold - cmf) / 0.5)
    else:
        side = "hold"
        strength = 1 - abs(cmf) / max(abs(inflow_threshold), abs(outflow_threshold))

    # 估算 mf_volume 和 total_volume
    n = min(len(closes), len(highs), len(lows), len(volumes))
    sub_c = closes[max(0, n - window):n]
    sub_h = highs[max(0, n - window):n]
    sub_l = lows[max(0, n - window):n]
    sub_v = volumes[max(0, n - window):n]
    mf_vol = 0.0
    total_vol = 0.0
    for i in range(len(sub_c)):
        h = sub_h[i]
        l = sub_l[i]
        c = sub_c[i]
        v = sub_v[i]
        mfm = ((c - l) - (h - c)) / (h - l) if h != l else 0
        mf_vol += mfm * v
        total_vol += v

    return CMFSignal(
        code=code, side=side,
        current=round(current, 4),
        cmf=round(cmf, 4),
        mf_volume=round(mf_vol, 2),
        total_volume=round(total_vol, 2),
        strength=round(strength, 4),
    )


# ═══════════════════════════════════════════════════════
# 批量
# ═══════════════════════════════════════════════════════

def screen_universe(
    universe: dict[str, tuple[list[float], list[float], list[float], list[float]]],
    *,
    top_n: int = 10,
) -> list[CMFSignal]:
    """扫全 universe"""
    signals = []
    for code, (closes, highs, lows, volumes) in universe.items():
        sig = generate_signal(code, closes, highs, lows, volumes)
        if sig and sig.side != "hold":
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return signals[:top_n]


def split_buy_sell(signals: list[CMFSignal]) -> tuple[list[CMFSignal], list[CMFSignal]]:
    """按信号分两组"""
    buys = [s for s in signals if s.side == "buy"]
    sells = [s for s in signals if s.side == "sell"]
    return buys, sells


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(s: CMFSignal) -> str:
    return f"{s.code}: {s.side.upper()} cmf={s.cmf:+.3f} strength={s.strength:.2f}"