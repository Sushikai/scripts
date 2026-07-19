"""
三分之一回升位 · 计算策略
─────────────────────────
A = 上一轮上涨的最低点 (谷底)
B = 这一轮上涨的最高点 (山顶)
支撑位 = (B - A) / 3 + A    ← 1/3 回升位
       = A + (B-A) * 0.333  ← 数学等价

意义: 价格从顶部 B 回落后,1/3 位 (A 到 B 的下三分之一) 通常是强支撑区,
也是"老前辈"口传的"安全买点"参考位置。

复盘实战: 002432 九安医疗 2022 年低点 7.9 → 高点 12.28,
         1/3 回升位 = 7.9 + (12.28-7.9)/3 = 7.9 + 1.46 = 9.36,
         实际股价回踩 9.36 附近企稳,后展开新一轮上涨。

数据源: 复用 web.server.stock_kline_loader (250 天日线)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


def find_prev_uptrend(daily: List[dict], lookback: int = 120) -> Optional[dict]:
    """从最近往前回溯,找到最近一段完成的上涨行情。

    找法: 取最近 lookback 天的日线,扫描所有 (低点 → 高点) 配对,
    选择最近一次"先下跌 / 横盘后启动上涨 → 创区间新高"的段。

    返回: {A: low, A_date, B: high, B_date, A_idx, B_idx, change_pct, bars}
    或 None (找不到明显的上涨段)
    """
    if not daily or len(daily) < 30:
        return None
    data = daily[-lookback:] if len(daily) > lookback else daily
    n = len(data)
    if n < 20:
        return None

    # 1) 找整段最低点 (谷底 A) — 区间内所有 low 中的最小
    min_i = min(range(n), key=lambda i: data[i].get("low") or 1e18)
    A = data[min_i].get("low") or 0
    A_date = data[min_i].get("date") or ""

    # 2) 从 A 之后, 找最高点 B — 区间内 high 中的最大 (且 index > min_i)
    high_after = [(i, data[i].get("high") or 0) for i in range(min_i + 1, n)]
    if not high_after:
        return None
    max_i, B = max(high_after, key=lambda x: x[1])
    if B <= A:
        return None
    B_date = data[max_i].get("date") or ""

    # 3) 从 B 之后, 看是否回落到 A 附近 (即"完成一轮上涨后回调")
    #    如果 B 是当前 (last bar), 也算有效 — 表示行情刚启动
    last_i = n - 1

    return {
        "A": round(A, 3),
        "A_date": A_date,
        "A_idx": min_i,
        "B": round(B, 3),
        "B_date": B_date,
        "B_idx": max_i,
        "change_pct": round((B / A - 1) * 100, 2) if A else 0,
        "bars": max_i - min_i,
        "current_close": data[last_i].get("close") or 0,
        "current_date": data[last_i].get("date") or "",
        "distance_to_B_pct": round(((data[last_i].get("close") or 0) / B - 1) * 100, 2) if B else 0,
        "data_n": n,
    }


def compute_levels(A: float, B: float) -> Dict[str, float]:
    """计算 A→B 区间的多个参考位。

    1/3 回升位: A + (B-A) / 3
    1/2 中位:    A + (B-A) / 2
    2/3 位:      A + (B-A) * 2 / 3
    """
    if not A or not B or B <= A:
        return {}
    span = B - A
    return {
        "A":         round(A, 3),
        "B":         round(B, 3),
        "span":      round(span, 3),
        "level_1_3": round(A + span / 3, 3),       # 1/3 回升位 — 主支撑
        "level_1_2": round(A + span / 2, 3),       # 1/2 中位 — 强弱分界
        "level_2_3": round(A + span * 2 / 3, 3),   # 2/3 位 — 偏强支撑
        "B_top":     round(B, 3),
    }


def analyze_recovery(code: str, kline_loader=None) -> dict:
    """单股 1/3 回升位分析。

    返回:
    {
      code,
      has_signal: bool,
      A, B, level_1_3, level_1_2, level_2_3,  # 关键位
      A_date, B_date,
      change_pct: 上轮涨幅,
      current_close, distance_to_level_1_3_pct,  # 当前价距 1/3 位距离
      near_support: bool,  # 当前价距 1/3 位 ±3% 之内
      explanation: "..."  # 文字解释
    }
    """
    if kline_loader is None:
        try:
            from .server import stock_kline_loader
        except Exception:
            import tuixue_v3.web.server as _srv
            kline_loader = _srv.stock_kline_loader

    out = {
        "code": code,
        "has_signal": False,
        "A": None, "B": None,
        "level_1_3": None, "level_1_2": None, "level_2_3": None,
        "A_date": "", "B_date": "",
        "change_pct": 0,
        "current_close": 0,
        "distance_to_level_1_3_pct": 0,
        "near_support": False,
        "explanation": "",
        "_skip": False,
    }

    try:
        daily = kline_loader(code, 250)
    except Exception as e:
        out["_skip"] = True
        out["_err"] = str(e)[:80]
        return out

    if not daily or len(daily) < 30:
        out["_skip"] = True
        out["_err"] = "K线 < 30 天"
        return out

    trend = find_prev_uptrend(daily, lookback=120)
    if not trend:
        out["explanation"] = "未找到明显的上一轮上涨 (K线不足或单边)"
        return out

    A, B = trend["A"], trend["B"]
    levels = compute_levels(A, B)
    if not levels:
        out["explanation"] = "A/B 无效"
        return out

    current = trend["current_close"]
    L13 = levels["level_1_3"]

    distance_pct = round((current / L13 - 1) * 100, 2) if L13 else 0
    near_support = abs(distance_pct) < 3.0 and current <= B  # 当前价距 1/3 位 ±3% 内

    explanation = (
        f"上一轮低点 A={A} ({trend['A_date']}), "
        f"高点 B={B} ({trend['B_date']}), "
        f"涨幅 +{trend['change_pct']}%。"
        f"1/3 回升位 (A + (B-A)/3) = {L13}, "
        f"当前价 {current} 距该位 {distance_pct:+.2f}%。"
    )
    if near_support:
        explanation += " ✅ 当前价接近 1/3 回升位, 关注企稳信号 — 这是历史回踩的关键支撑区。"
    elif current > levels["level_2_3"]:
        explanation += " 当前价已在 2/3 位之上, 属偏强区间, 留意能否突破前高 B。"
    elif current < levels["level_1_2"]:
        explanation += " 当前价跌破 1/2 中位, 弱势区间, 关注能否在 1/3 位附近企稳。"

    out.update({
        "has_signal": True,
        "A": A, "B": B,
        "level_1_3": L13,
        "level_1_2": levels["level_1_2"],
        "level_2_3": levels["level_2_3"],
        "A_date": trend["A_date"],
        "B_date": trend["B_date"],
        "change_pct": trend["change_pct"],
        "current_close": current,
        "current_date": trend["current_date"],
        "distance_to_level_1_3_pct": distance_pct,
        "near_support": near_support,
        "explanation": explanation,
        "span_bars": trend["bars"],
    })
    return out
