"""
阶段判断 — 根据本地时间判断当前盘中阶段。
参考 yeren_ai / zt_screener 的 phase 字段约定。
"""
import time


def _now() -> tuple[int, int]:
    """返回 (hour, minute) 本地时间。"""
    t = time.localtime()
    return t.tm_hour, t.tm_min


def bv_phase() -> str:
    """返回当前 BV 战法推票阶段。

    阶段:
      pre_market       — 盘前 09:00-09:30 集合竞价
      early            — 早盘 09:30-11:30 (10:40 前是买点窗口)
      midday           — 午休 11:30-13:00
      late_afternoon   — 尾盘 13:00-14:57 (14:40 后是买点窗口)
      closing          — 收盘集合 14:57-15:00
      close            — 盘后
    """
    h, m = _now()
    mm = h * 60 + m
    if mm < 9 * 60 + 30:
        return "pre_market"
    if mm < 11 * 60 + 30:
        return "early"
    if mm < 13 * 60:
        return "midday"
    if mm < 14 * 60 + 57:
        return "late_afternoon"
    if mm < 15 * 60:
        return "closing"
    return "close"


PHASE_META = {
    "pre_market":       {"ttl": 60, "label": "集合竞价监控",   "tone": "warn",  "icon": "🟡"},
    "early":            {"ttl": 30, "label": "早盘实时推票",     "tone": "good",  "icon": "🟢"},
    "midday":           {"ttl": 60, "label": "午间守候",         "tone": "warn",  "icon": "🟡"},
    "late_afternoon":   {"ttl": 20, "label": "尾盘抢筹",         "tone": "bad",   "icon": "🔴"},
    "closing":          {"ttl": 10, "label": "收盘集合竞价",     "tone": "info",  "icon": "🟣"},
    "close":            {"ttl": 300, "label": "盘后守候",        "tone": "mute",  "icon": "⚫"},
}


def phase_meta() -> dict:
    """返回当前阶段 + ttl + 标签 + tone (前端顶部 banner 用)。"""
    p = bv_phase()
    m = dict(PHASE_META.get(p, PHASE_META["close"]))
    m["phase"] = p
    return m


def is_buy_window() -> bool:
    """是否在 Bryan 战法的买点窗口: 10:40 前 / 14:40 后。"""
    h, m = _now()
    mm = h * 60 + m
    # 09:30 - 10:40 (仅在 early 阶段前半)
    if 9 * 60 + 30 <= mm < 10 * 60 + 40:
        return True
    # 14:40 - 14:57 (尾盘阶段后半)
    if 14 * 60 + 40 <= mm < 14 * 60 + 57:
        return True
    return False