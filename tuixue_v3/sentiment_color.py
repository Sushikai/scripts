#!/usr/bin/env python3
"""
tuixue_v3/sentiment_color.py
Ship 52/100 — 情绪颜色映射 (Sentiment Color Mapping)

设计:
把情绪分数 (0-100) 映射到颜色:
- 0-20: 深红 (极度恐惧)
- 20-40: 浅红
- 40-60: 中性灰
- 60-80: 浅绿
- 80-100: 深绿 (极度贪婪)

同时支持:
- 主色 (背景)
- 趋势色 (上升绿/下降红)
- 反向色 (反转色)
- 渐变色 (用于 bar/line)

降级: 异常输入 → 中性灰

2026-08-03 Ship 52 — 10000 轮迭代 P4 第十二步
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 调色板 (HSL)
# ═══════════════════════════════════════════════════════

STOP_COLORS = [
    (0, 20, "深红", "#8b0000"),       # 极度恐惧
    (20, 40, "浅红", "#d97070"),
    (40, 50, "中性红", "#c0a0a0"),
    (50, 60, "灰", "#999999"),       # 中性
    (60, 80, "浅绿", "#70c070"),
    (80, 100, "深绿", "#006400"),    # 极度贪婪
]


def color_for_score(score: float) -> str:
    """0-100 → 颜色 hex"""
    score = max(0.0, min(100.0, score))
    for lo, hi, _, color in STOP_COLORS:
        if lo <= score < hi:
            return color
    # score = 100 时
    if score >= 100:
        return STOP_COLORS[-1][3]
    return STOP_COLORS[3][3]   # 中性


def color_with_blend(score: float) -> str:
    """带渐变混合的颜色"""
    score = max(0.0, min(100.0, score))
    if score <= 50:
        # 红 → 灰
        ratio = score / 50.0
        return _blend("#d97070", "#999999", ratio)
    else:
        # 灰 → 绿
        ratio = (score - 50) / 50.0
        return _blend("#999999", "#70c070", ratio)


# ═══════════════════════════════════════════════════════
# 趋势色
# ═══════════════════════════════════════════════════════

def trend_color(delta: float) -> str:
    """上升绿色, 下降红色, 平稳灰"""
    if delta > 5:
        return "#00b050"   # 强绿
    if delta > 0:
        return "#a0d090"   # 浅绿
    if delta < -5:
        return "#c00000"   # 强红
    if delta < 0:
        return "#d97070"   # 浅红
    return "#999999"        # 平


def sign_color(is_pos: bool) -> str:
    return "#00b050" if is_pos else "#c00000"


# ═══════════════════════════════════════════════════════
# 区间色
# ═══════════════════════════════════════════════════════

def zone_color(zone: str) -> str:
    """情绪区间 → 颜色"""
    table = {
        "extreme_greed": "#006400",
        "greed": "#70c070",
        "mild_greed": "#a0c0a0",
        "neutral": "#999999",
        "mild_fear": "#c0a0a0",
        "fear": "#d97070",
        "extreme_fear": "#8b0000",
    }
    return table.get(zone, "#999999")


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def _hex_to_rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))   # type: ignore


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _blend(c1: str, c2: str, ratio: float) -> str:
    """0..1 颜色混合"""
    ratio = max(0.0, min(1.0, ratio))
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * ratio)
    g = int(g1 + (g2 - g1) * ratio)
    b = int(b1 + (b2 - b1) * ratio)
    return _rgb_to_hex(r, g, b)


def gradient_stops(n: int = 5) -> list[str]:
    """N 段渐变 stop 颜色"""
    if n <= 1:
        return [color_for_score(50)]
    out = []
    for i in range(n):
        score = (i / (n - 1)) * 100
        out.append(color_for_score(score))
    return out


# ═══════════════════════════════════════════════════════
# 主题 (前端使用)
# ═══════════════════════════════════════════════════════

def theme_palette() -> dict:
    """返回前端主题色板"""
    return {
        "fear": "#c00000",
        "warning": "#d97070",
        "neutral": "#999999",
        "ok": "#70c070",
        "greed": "#006400",
        "boost": "#00b050",
        "suppress": "#c00000",
    }
