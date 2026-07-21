"""
zt_backtest.py — 涨停板次日溢价回测引擎

核心思想：
  从涨停池选股 → T+1 开盘买入 → 极短持(1~2天)退出
  与旧 backtest.py 完全独立，但有类似的统计输出结构

退场模式（同时算全部）：
  - close_t1 : T+1 收盘卖出（不可执行，仅供对照）
  - close_t2 : T+2 收盘卖出（T+1 合规，推荐）
  - open_t2  : T+2 开盘卖出（T+1 合规）
  - trail_t2 : T+2 移动止盈止损
  - gap_t1   : T+1 开盘溢价（buy = T+1 open → 卖 = 同一价，实际买 T close→T+1 open 溢价）
  - stop_t1  : T+1 日内硬止损
  - best     : 区间最高价（理论上限）
"""
from __future__ import annotations

import json
import logging
import random
import time as systime
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import data_layer as dl
from . import zt_config as cfg

log = logging.getLogger("tuixue_v3.zt_backtest")

# ── 工具 ──────────────────────────────────────────────────

def _is_limit_up(code: str, close_pct: float) -> bool:
    """按板块判断涨停阈值。"""
    if code.startswith(("300", "301", "688", "689")):
        return close_pct >= cfg.ZT_LIMIT_UP_PCT_20CM
    return close_pct >= cfg.ZT_LIMIT_UP_PCT_MAIN


def _next_trade_day(dates: list[str], date_str: str, offset: int = 1) -> str | None:
    """date_str 后第 offset 个交易日。"""
    try:
        idx = dates.index(date_str)
        if idx + offset < len(dates):
            return dates[idx + offset]
    except ValueError:
        pass
    return None


def _prev_trade_day(dates: list[str], date_str: str) -> str | None:
    """date_str 前一个交易日。"""
    try:
        idx = dates.index(date_str)
        if idx > 0:
            return dates[idx - 1]
    except ValueError:
        pass
    return None


def _board_label(code: str) -> str:
    if code.startswith(("300", "301")):
        return "gem"       # 创业板
    if code.startswith(("688", "689")):
        return "star"      # 科创板
    if code.startswith(("60",)):
        return "sh_main"   # 沪主板
    if code.startswith(("000", "001", "002")):
        return "sz_main"   # 深主板
    if code.startswith(("8", "4", "43", "83", "87", "92")):
        return "bse"       # 北交所
    return "other"


def _board_filter_pass(code: str, board_filter: str) -> bool:
    """按 board_filter 过滤。"""
    label = _board_label(code)
    if label == "bse":
        return False  # 北交所永远排除
    if board_filter == "all":
        return True
    if board_filter == "main":
        return label in ("sh_main", "sz_main")
    if board_filter == "gem+star":
        return label in ("gem", "star")
    if board_filter == "gem":
        return label == "gem"
    if board_filter == "star":
        return label == "star"
    return True


# ── 涨停池重建 ──────────────────────────────────────────

def _detect_limit_up_from_daily(
    df: pd.DataFrame, code: str, date_str: str,
    lookback: int = 10,
) -> dict | None:
    """从日线 OHLC 检测 date_str 是否涨停，返回候选 dict。

    返回 {code, name, streak, ...}，缺失字段填 -1/空。
    """
    if df is None or df.empty or "日期" not in df.columns:
        return None
    # 日期已经是 YYYYMMDD 字符串 (data_layer 统一格式)
    target_idx = df.index[df["日期"] == date_str].tolist()
    if not target_idx:
        return None
    ti = target_idx[0]
    if ti < 1:
        return None
    row = df.iloc[ti]
    prev = df.iloc[ti - 1]
    prev_close = float(prev["收盘"])
    cur_close = float(row["收盘"])
    if prev_close <= 0:
        return None
    pct = (cur_close / prev_close - 1) * 100

    if not _is_limit_up(code, pct):
        return None

    thresh_20 = cfg.ZT_LIMIT_UP_PCT_20CM - 0.5
    thresh_main = cfg.ZT_LIMIT_UP_PCT_MAIN - 0.5

    # 连板数：往前数
    streak = 1
    for i in range(ti - 1, max(ti - lookback, 0), -1):
        c = float(df.iloc[i]["收盘"])
        p = float(df.iloc[i - 1]["收盘"]) if i > 0 else c
        if p <= 0:
            break
        chg = (c / p - 1) * 100
        lim = thresh_20 if code.startswith(("300", "301", "688", "689")) else thresh_main
        if chg >= lim:
            streak += 1
        else:
            break

    return {
        "code": code,
        "name": code,  # zt_pool 会覆盖 name
        "streak": streak,
        "limit_price": cur_close,
        "limit_order_amount": -1.0,
        "amount": float(row.get("成交额", 0) or 0),
        "market_cap": 0.0,  # 从日线不可得
        "sector": "",
        "first_time": "",
        "burst_count": -1,
        "last_time": "",
        "turnover_pct": float(row.get("换手率", 0) or 0) if "换手率" in df.columns else -1.0,
        "source": "ohlc",
    }


def _detect_pool_ohlc(
    daily_cache: dict[str, pd.DataFrame],
    stock_list: list[tuple[str, str]],
    date_str: str,
    board_filter: str = "all",
) -> list[dict]:
    """纯 OHLC 涨停检测（无需外部 API）。"""
    stock_map = {c: n for c, n in stock_list}
    out: list[dict] = []
    for code, name in stock_list:
        if not _board_filter_pass(code, board_filter):
            continue
        df = daily_cache.get(code)
        if df is None:
            continue
        hit = _detect_limit_up_from_daily(df, code, date_str)
        if hit:
            hit["name"] = name or stock_map.get(code, code)
            out.append(hit)
    return out


# ── 评分排序 ──────────────────────────────────────────

def _score_zt_candidate(zt: dict, sector_hot_set: set[str] | None = None) -> float:
    """对涨停候选股评分，用于排序选 top_n。

    因子（权重可优化器调）：
      - streak_bonus:      连板 momentum
      - burst_penalty:     炸板 penalty
      - seal_quality:      封板时间早加分
      - mcap_score:        最佳市值区间加分
      - turnover_score:    换手适中加分
      - sector_bonus:      热门板块加分
      - order_bonus:       封单充足加分
    """
    s = 0.0

    # 连板：首板+10，二板+20，三板+25，四板+20（四板后风险增大）
    streak = int(zt.get("streak", 1) or 1)
    if streak == 1:
        s += 10
    elif streak == 2:
        s += 20
    elif streak == 3:
        s += 25
    elif streak <= 5:
        s += 20
    else:
        s += 10  # 高位连板风险大

    # 炸板
    burst = int(zt.get("burst_count", 0) or 0)
    if burst < 0:
        s += 0  # 未知，不加分不减分
    else:
        s -= burst * 8  # 每次炸板 -8

    # 封板时间
    ft = str(zt.get("first_time", "") or "")
    if ft:
        try:
            hm = ft.strip()
            h, m = hm.split(":")
            minutes = int(h) * 60 + int(m)
            if minutes <= 9 * 60 + 35:      # 09:35 前 → 秒板
                s += 25
            elif minutes <= 10 * 60 + 30:  # 10:30 前 → 早盘板
                s += 18
            elif minutes <= 11 * 60 + 30:  # 上午板
                s += 12
            elif minutes <= 14 * 60:        # 下午板
                s += 5
            else:                           # 尾盘板
                s -= 5
        except Exception:
            pass

    # 市值
    mcap = float(zt.get("market_cap", 0) or 0)
    if mcap > 0:
        if 30 <= mcap <= 100:
            s += 20
        elif 15 <= mcap <= 200:
            s += 12
        elif 5 <= mcap <= 500:
            s += 5

    # 换手
    turn = float(zt.get("turnover_pct", 0) or 0)
    if turn > 0:
        if 5 <= turn <= 15:
            s += 15
        elif 3 <= turn <= 25:
            s += 8
        elif turn <= 3:
            s -= 5  # 换手太低 = 无量一字板，可能买不到

    # 热门板块
    if sector_hot_set and zt.get("sector"):
        sec = str(zt.get("sector", ""))
        if sec in sector_hot_set:
            s += 15

    # 封单金额/流通市值 比
    limit_amt = float(zt.get("limit_order_amount", 0) or 0)
    if limit_amt > 0 and mcap > 0:
        ratio = limit_amt / mcap * 100
        if ratio >= 3.0:
            s += 20
        elif ratio >= 1.0:
            s += 12
        elif ratio >= 0.3:
            s += 5

    return s


# ── 交易模拟 ──────────────────────────────────────────

def _simulate_trade(
    zt_row: dict,
    daily_df: pd.DataFrame,
    trade_dates: list[str],
    buy_date: str,
    entry_rule: str = "open_t1",
    trail_activate: float = 3.0,
    trail_pullback: float = 1.5,
    stop_loss: float = -5.0,
) -> dict | None:
    """模拟一笔交易。

    参数：
      zt_row     : 涨停候选信息
      daily_df   : 该股日线 DF
      trade_dates: 全局交易日列表
      buy_date   : 买入日 (YYYYMMDD)
      entry_rule : "open_t1"(T+1开盘) / "close_t0"(T收盘)

    返回：
      {buy_date, sell_date, buy_price, return_pct, exits_pct, ...}
    """
    if daily_df is None or daily_df.empty or "日期" not in daily_df.columns:
        return None

    # 日期一致化
    df = daily_df.copy()
    dt_col = "日期" if "日期" in df.columns else "date"
    if pd.api.types.is_datetime64_any_dtype(df[dt_col]):
        df[dt_col] = df[dt_col].dt.strftime("%Y%m%d")

    # T = 涨停日（zt_row 的 date 字段，回传统一用 buy_date 相邻前日）
    # 从 trade_dates 拿 T 日（涨停日）
    zt_date = _prev_trade_day(trade_dates, buy_date)
    if zt_date is None:
        zt_date = str(zt_row.get("date", buy_date))

    # 确定买入日
    if entry_rule == "close_t0":
        entry_date = zt_date  # T 日收盘买入
    else:  # open_t1
        entry_date = buy_date  # T+1 开盘买入

    buy_row_idx = df.index[df[dt_col] == entry_date].tolist()
    if not buy_row_idx:
        return None
    buy_row = df.iloc[buy_row_idx[0]]
    buy_price = float(buy_row.get("开盘", 0) if entry_rule == "open_t1" else buy_row.get("收盘", 0))
    if buy_price <= 0:
        return None

    # 交易参数
    slip = 0.002
    fee = 0.0003
    stamp = 0.001
    cost_in = slip + fee
    cost_out = cost_in + slip + fee + stamp
    def _ret(sell_price: float) -> float:
        if sell_price <= 0:
            return 0.0
        return (sell_price / buy_price - 1) * 100 - cost_out * 100

    # T+1 和 T+2 的 OHLC
    t1_date = _next_trade_day(trade_dates, zt_date, 1)
    t2_date = _next_trade_day(trade_dates, zt_date, 2)

    if entry_rule == "open_t1" and t1_date is None:
        return None

    # 获取 T+1, T+2 行
    def _get_row(dt: str) -> pd.Series | None:
        idx = df.index[df[dt_col] == dt].tolist()
        return df.iloc[idx[0]] if idx else None

    t1_row = _get_row(t1_date) if t1_date else None
    t2_row = _get_row(t2_date) if t2_date else None

    # ── 计算各种退场收益 ──
    exits_pct: dict[str, float] = {}
    exits_sell: dict[str, float] = {}
    exits_dates: dict[str, str] = {}
    trail_exit = None  # (sell_price, date, kind)

    # gap_t1: (T close) → (T+1 open) 隔夜溢价（与买入方式无关，纯市场溢价）
    # 找 T 日（涨停日）收盘价
    zt_close = 0.0
    zt_row_idx = df.index[df[dt_col] == zt_date].tolist()
    if zt_row_idx:
        zt_close = float(df.iloc[zt_row_idx[0]].get("收盘", 0))

    if t1_row is not None and zt_close > 0:
        t1_open = float(t1_row.get("开盘", 0))
        t1_close = float(t1_row.get("收盘", 0))
        t1_high = float(t1_row.get("最高", 0))
        t1_low = float(t1_row.get("最低", 0))

        # gap 溢价: (T+1 open / T close - 1) * 100，不扣除成本（纯市场指标）
        gap_ret_pure = (t1_open / zt_close - 1) * 100

        # 按买入规则算 gap 收益: close_t0 买 T收盘→T+1开盘卖 / open_t1 买T+1开盘→T+1开盘卖
        if entry_rule == "close_t0":
            gap_ret = gap_ret_pure - cost_out * 100
        else:
            gap_ret = 0.0  # open_t1 模式下 gap 不可实现

        # close_t1
        close_t1_ret = _ret(t1_close) if t1_row is not None else 0.0

        # stop_t1: T+1 low < buy * stop_level
        stop_t1_ret = 0.0
        stop_t1_triggered = False
        if t1_row is not None:
            stop_level = 1.0 + stop_loss / 100
            if t1_low <= buy_price * stop_level:
                stop_t1_ret = _ret(t1_low)
                stop_t1_triggered = True

        exits_pct["close_t1"] = round(close_t1_ret, 2)
        exits_sell["close_t1"] = round(t1_close, 3)
        exits_dates["close_t1"] = t1_date

        exits_pct["gap_t1"] = round(gap_ret, 2)
        exits_sell["gap_t1"] = round(t1_open, 3)
        exits_dates["gap_t1"] = t1_date

        if stop_t1_triggered:
            exits_pct["stop_t1"] = round(stop_t1_ret, 2)
            exits_sell["stop_t1"] = round(t1_low, 3)
            exits_dates["stop_t1"] = t1_date

        # best (区间最高，仅 T+1)
        best_sell = t1_high
        exits_pct["best"] = round(_ret(best_sell), 2)
        exits_sell["best"] = round(best_sell, 3)
        exits_dates["best"] = t1_date

    # T+2 相关
    if t2_row is not None:
        t2_open = float(t2_row.get("开盘", 0))
        t2_close = float(t2_row.get("收盘", 0))
        t2_high = float(t2_row.get("最高", 0))
        t2_low = float(t2_row.get("最低", 0))

        close_t2_ret = _ret(t2_close)
        open_t2_ret = _ret(t2_open)

        exits_pct["close_t2"] = round(close_t2_ret, 2)
        exits_sell["close_t2"] = round(t2_close, 3)
        exits_dates["close_t2"] = t2_date

        exits_pct["open_t2"] = round(open_t2_ret, 2)
        exits_sell["open_t2"] = round(t2_open, 3)
        exits_dates["open_t2"] = t2_date

        # trail_t2: 分日 trailing stop（T+1 检查，触发则 T+1 退出；否则 T+2 继续）
        if t1_row is not None:
            activate_level = buy_price * (1 + trail_activate / 100)
            pullback_price = t1_high * (1 - trail_pullback / 100)
            if t1_high >= activate_level and pullback_price > buy_price:
                # T+1 触发 → 取 T+1 收盘或 pullback 的较低价
                trail_exit = (max(pullback_price, buy_price * 0.97), t1_date, "trail_t2")
            elif t1_high >= activate_level and pullback_price <= buy_price:
                trail_exit = (buy_price * 1.0, t1_date, "trail_t2")  # 保本出
            elif t2_row is not None:
                # T+1 未触发 → T+2 继续
                t2_pullback = t2_high * (1 - trail_pullback / 100)
                if t2_high >= activate_level:
                    trail_exit = (max(t2_pullback, buy_price * 0.97), t2_date, "trail_t2")
                else:
                    # 从未触发 → T+2 收盘退出
                    trail_exit = (t2_close, t2_date, "trail_t2")
            else:
                # T+2 无数据, T+1 未触发 → T+1 收盘退出
                trail_exit = (t1_close, t1_date, "trail_t2")

        if trail_exit:
            trail_sell, trail_date, _ = trail_exit
            exits_pct["trail_t2"] = round(_ret(trail_sell), 2)
            exits_sell["trail_t2"] = round(trail_sell, 3)
            exits_dates["trail_t2"] = trail_date

        # 更新 best 到 T+2
        best_both = max(t1_high if t1_row is not None else 0, t2_high)
        if best_both > 0:
            exits_pct["best"] = round(_ret(best_both), 2)
            exits_sell["best"] = round(best_both, 3)
            exits_dates["best"] = t2_date if t2_high > (t1_high if t1_row is not None else 0) else t1_date

    # 确定主退场（trail_t2 优先，否则 close_t2/close_t1 兜底）
    if trail_exit:
        main_kind = "trail_t2"
    else:
        main_kind = "close_t2" if t2_row is not None else "close_t1"
    main_ret = exits_pct.get(main_kind, 0.0)
    main_sell = exits_sell.get(main_kind, 0.0)
    main_date = exits_dates.get(main_kind, buy_date)

    # 持有天数
    hold_days = 0
    try:
        d0 = datetime.strptime(buy_date, "%Y%m%d")
        d1 = datetime.strptime(main_date, "%Y%m%d")
        hold_days = (d1 - d0).days
    except Exception:
        hold_days = 1

    # 连板延续：如果 T+1 或 T+2 该股继续涨停
    cont_zt = False
    if t1_row is not None:
        close_pct_t1 = (float(t1_row.get("收盘", 0)) / buy_price - 1) * 100
        if _is_limit_up(zt_row["code"], close_pct_t1):
            cont_zt = True

    return {
        "code": zt_row["code"],
        "name": zt_row.get("name", ""),
        "buy_date": buy_date,
        "sell_date": main_date,
        "buy_price": round(buy_price, 3),
        "sell_price": round(main_sell, 3),
        "return_pct": main_ret,
        "trigger": main_kind,
        "hold_days": hold_days,
        "streak": zt_row.get("streak", 1),
        "sector": zt_row.get("sector", ""),
        "board": _board_label(zt_row["code"]),
        "continued_zt": cont_zt,
        # 全部退场
        "exits_pct": exits_pct,
        "exits_sell_price": exits_sell,
        "exits_sell_date": exits_dates,
    }


# ── 统计聚合 ──────────────────────────────────────────

def _aggregate_metrics(trades: list[dict]) -> dict:
    """总体统计（与 backtest.py 格式兼容）。"""
    if not trades:
        return {"trades": 0}

    df = pd.DataFrame(trades)
    rets = df["return_pct"].astype(float)
    n = len(df)
    wins = int((rets > 0).sum())
    losses = int((rets < 0).sum())
    win_rate = wins / n * 100
    avg_ret = float(rets.mean())
    median_ret = float(rets.median())
    stddev = float(rets.std(ddof=1)) if n > 1 else 0.0

    win_sum = float(rets[rets > 0].sum())
    loss_sum = abs(float(rets[rets < 0].sum()))
    pf = round(win_sum / loss_sum, 2) if loss_sum > 0 else float("inf")

    equity = (1 + rets / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = round(float(drawdown.min()), 2) if len(drawdown) else 0
    total_ret = round(float((equity.iloc[-1] - 1) * 100), 2) if len(equity) else 0

    # 月统计
    df["buy_date_dt"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date_dt"].dt.to_period("M").astype(str)
    monthly_avg = df.groupby("month")["return_pct"].mean()
    pos_months = int((monthly_avg > 0).sum())
    neg_months = int((monthly_avg < 0).sum())
    monthly_avg_pct = round(float(monthly_avg.mean()), 2) if len(monthly_avg) else 0

    # 日平均收益
    trading_days = len(df["buy_date"].unique())
    daily_avg = total_ret / max(trading_days, 1)

    # 月度总收益（用来和 200%/月 目标对比）
    monthly_total = df.groupby("month")["return_pct"].sum()
    max_monthly = round(float(monthly_total.max()), 2) if len(monthly_total) else 0
    avg_monthly_total = round(float(monthly_total.mean()), 2) if len(monthly_total) else 0

    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "avg_return_pct": round(avg_ret, 3),
        "median_return_pct": round(median_ret, 3),
        "stddev_return_pct": round(stddev, 3),
        "total_return_pct": total_ret,
        "daily_avg_return_pct": round(daily_avg, 3),
        "monthly_avg_return_pct": monthly_avg_pct,
        "monthly_max_return_pct": max_monthly,
        "monthly_avg_total_pct": avg_monthly_total,
        "positive_months": pos_months,
        "negative_months": neg_months,
        "max_drawdown_pct": max_dd,
        "profit_factor": pf,
        "best_trade_pct": round(float(rets.max()), 2),
        "worst_trade_pct": round(float(rets.min()), 2),
        "percentiles": _percentiles(rets.tolist(), [5, 10, 25, 50, 75, 90, 95]),
    }


def _percentiles(arr: list[float], qs: list[int]) -> dict:
    if not arr:
        return {f"p{q}": 0.0 for q in qs}
    np_arr = np.asarray(arr, dtype=float)
    return {f"p{q}": round(float(np.percentile(np_arr, q)), 3) for q in qs}


def _compute_monthly(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    df["buy_date"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date"].dt.to_period("M").astype(str)
    rows = []
    for m, g in df.groupby("month"):
        rets = g["return_pct"].astype(float)
        n = len(g)
        wins = int((rets > 0).sum())
        rows.append({
            "month": m,
            "trades": n,
            "wins": wins,
            "win_rate_pct": round(wins / n * 100, 2) if n else 0,
            "avg_return_pct": round(float(rets.mean()), 2),
            "sum_return_pct": round(float(rets.sum()), 2),
            "max_return_pct": round(float(rets.max()), 2),
            "min_return_pct": round(float(rets.min()), 2),
        })
    return rows


def _compute_exit_breakdown(trades: list[dict]) -> dict:
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    if "trigger" not in df.columns:
        return {}
    vc = df["trigger"].value_counts().to_dict()
    total = len(df)
    return {k: {"count": int(v), "pct": round(v / total * 100, 2)} for k, v in vc.items()}


def _compute_scenario_compare(trades: list[dict]) -> dict:
    """多退场方案横向对比。"""
    if not trades or "exits_pct" not in trades[0]:
        return {}
    by_kind: dict[str, list[float]] = {}
    for t in trades:
        ep = t.get("exits_pct", {}) or {}
        for k, v in ep.items():
            if v is None:
                continue
            by_kind.setdefault(k, []).append(float(v))

    out: dict[str, dict] = {}
    for k, arr in by_kind.items():
        if not arr:
            continue
        np_a = np.asarray(arr, dtype=float)
        n = len(np_a)
        wins = int((np_a > 0).sum())
        win_sum = float(np_a[np_a > 0].sum())
        loss_sum = abs(float(np_a[np_a < 0].sum()))
        out[k] = {
            "n": n,
            "wins": wins,
            "win_rate_pct": round(wins / n * 100, 2),
            "avg_pct": round(float(np_a.mean()), 3),
            "median_pct": round(float(np.median(np_a)), 3),
            "stddev_pct": round(float(np_a.std(ddof=1)), 2) if n > 1 else 0,
            "cum_return_pct": round(float(((1 + np_a / 100).cumprod()[-1] - 1) * 100), 2),
            "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
        }
    return out


def _compute_sector_breakdown(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    if "sector" not in df.columns:
        return []
    rows = []
    for sec, g in df.groupby(df["sector"].fillna("—")):
        rets = g["return_pct"].astype(float)
        rows.append({
            "sector": str(sec),
            "trades": len(g),
            "win_rate_pct": round((rets > 0).sum() / len(rets) * 100, 2),
            "avg_return_pct": round(float(rets.mean()), 3),
            "sum_return_pct": round(float(rets.sum()), 2),
        })
    rows.sort(key=lambda r: -r["sum_return_pct"])
    return rows


# ── 批量缓存加载 ──────────────────────────────────────────

def _batch_cache_load(cdb_module) -> dict[str, pd.DataFrame]:
    """从 cache_db SQLite 批量加载所有股票日线 (单 SQL 避免锁竞争)。"""
    try:
        conn = cdb_module.get_conn()
        rows = conn.execute(
            "SELECT code, date, open, high, low, close, volume, amount, turnover "
            "FROM daily ORDER BY code, date"
        ).fetchall()
        log.info("  cache_db 原始行数: %d", len(rows))
        if not rows:
            return {}
        # 按 code 分组构建 DataFrame
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[r[0]].append({
                "日期": str(r[1]),
                "开盘": float(r[2] or 0),
                "最高": float(r[3] or 0),
                "最低": float(r[4] or 0),
                "收盘": float(r[5] or 0),
                "成交量": float(r[6] or 0),
                "成交额": float(r[7] or 0),
                "换手率": float(r[8] or 0),
            })
        import pandas as pd
        out = {}
        for code, recs in groups.items():
            df = pd.DataFrame(recs).sort_values("日期").reset_index(drop=True)
            if len(df) >= 10:
                out[code] = df
        log.info("  batch 构建 %d 只股票日线 (max rows=%d)", len(out), max(len(v) for v in out.values()))
        return out
    except Exception as e:
        log.warning("batch 缓存加载失败, 降级逐只读取: %s", e)
        return {}


# ── 主回测 ──────────────────────────────────────────

def build_zt_cache(
    start: str = cfg.ZT_START,
    end: str = cfg.ZT_END,
    board_filter: str = cfg.ZT_BOARD_FILTER,
) -> tuple[dict[str, pd.DataFrame], list[str], list[tuple[str, str]], dict[str, list[dict]]]:
    """构建涨停池缓存（OHLC 检测阶段，一次运行，多次消费）。

    Returns:
        daily_cache, dates, all_stocks, zt_cache
    """
    log.info("========== 构建 ZT 缓存 %s→%s board=%s ==========", start, end, board_filter)
    t0 = systime.time()

    dates = dl.fetch_trade_dates(start, end)
    log.info("交易日: %d 天", len(dates))
    all_stocks = dl.fetch_stock_list_all()

    # 批量加载日线缓存
    from . import cache_db as _cdb
    daily_cache: dict[str, pd.DataFrame] = _batch_cache_load(_cdb)
    log.info("日线缓存: %d/%d 命中 (%ds)", len(daily_cache), len(all_stocks), systime.time() - t0)

    # OHLC 涨停检测
    zt_cache: dict[str, list[dict]] = {}
    for code, name in all_stocks:
        df = daily_cache.get(code)
        if df is None or len(df) < 10:
            continue
        dates_in_df = df["日期"].tolist()
        for i in range(1, len(dates_in_df)):
            d = dates_in_df[i]
            if d not in dates:
                continue
            hit = _detect_limit_up_from_daily(df, code, d)
            if hit:
                hit["name"] = name
                zt_cache.setdefault(d, []).append(hit)
    log.info("OHLC 涨停检测: %d 天有数据, %d 只次候选 (%ds)",
             len(zt_cache), sum(len(v) for v in zt_cache.values()), systime.time() - t0)
    return daily_cache, dates, all_stocks, zt_cache


def run_zt_backtest(
    start: str = cfg.ZT_START,
    end: str = cfg.ZT_END,
    top_n: int = cfg.ZT_TOP_N,
    board_filter: str = cfg.ZT_BOARD_FILTER,
    entry_rule: str = cfg.ZT_ENTRY_RULE,
    min_streak: int = cfg.ZT_MIN_STREAK,
    max_streak: int = cfg.ZT_MAX_STREAK,
    burst_max: int = cfg.ZT_BURST_MAX,
    sealed_before: str = cfg.ZT_SEALED_BEFORE,
    mcap_min_yi: float = cfg.ZT_MCAP_MIN_YI,
    mcap_max_yi: float = cfg.ZT_MCAP_MAX_YI,
    turnover_min_pct: float = cfg.ZT_TURNOVER_MIN_PCT,
    turnover_max_pct: float = cfg.ZT_TURNOVER_MAX_PCT,
    limit_order_min_yi: float = cfg.ZT_LIMIT_ORDER_MIN_YI,
    sector_hot_required: bool = cfg.ZT_SECTOR_HOT_REQUIRED,
    trail_activate_pct: float = cfg.ZT_TRAIL_ACTIVATE_PCT,
    trail_pullback_pct: float = cfg.ZT_TRAIL_PULLBACK_PCT,
    stop_loss_pct: float = cfg.ZT_STOP_LOSS_PCT,
    sample: int = cfg.ZT_SAMPLE,
    use_akshare: bool = True,
    progress_cb=None,
    _prebuilt: tuple | None = None,  # (daily_cache, dates, all_stocks, zt_cache) 优化器复用
) -> dict:
    """
    涨停板次日溢价回测主入口。

    Args:
        start/end: 回测起止日期
        top_n: 每日选股只数
        board_filter: 板块过滤
        entry_rule: 买入规则
        min_streak/max_streak: 连板数范围
        burst_max: 最大炸板次数
        sealed_before: 封板时间上限
        mcap_min_yi/mcap_max_yi: 市值范围(亿)
        turnover_min_pct/turnover_max_pct: 换手率范围(%)
        limit_order_min_yi: 最小封单金额(亿)
        sector_hot_required: 是否需要热门板块
        trail_activate_pct/trail_pullback_pct/stop_loss_pct: 止盈止损
        sample: 采样数(0=全市场)
        use_akshare: 是否使用akshare获取涨停池
        progress_cb: 进度回调

    Returns:
        dict: {config, summary, monthly, scenario_compare, exit_breakdown, ...}
    """
    log.info("========== ZT回测 %s→%s | top=%d board=%s entry=%s sample=%d ==========",
             start, end, top_n, board_filter, entry_rule, sample)
    t0 = systime.time()

    # 使用预构建缓存或构建
    if _prebuilt is not None:
        daily_cache, dates, all_stocks, zt_cache = _prebuilt
    else:
        dates = dl.fetch_trade_dates(start, end)
        log.info("交易日: %d 天", len(dates))
        if len(dates) < 10:
            return {"error": "交易日太少", "dates": len(dates)}
        all_stocks = dl.fetch_stock_list_all()
        log.info("股票池: %d 只", len(all_stocks))
        from . import cache_db as _cdb
        daily_cache = _batch_cache_load(_cdb)
        log.info("日线缓存: %d/%d 命中", len(daily_cache), len(all_stocks))
        zt_cache = {}
        for code, name in all_stocks:
            df = daily_cache.get(code)
            if df is None or len(df) < 10:
                continue
            for d in df["日期"].tolist()[1:]:
                if d not in dates:
                    continue
                hit = _detect_limit_up_from_daily(df, code, d)
                if hit:
                    hit["name"] = name
                    zt_cache.setdefault(d, []).append(hit)
        log.info("OHLC 涨停检测: %d 天, %d 只次候选 (%ds)",
                 len(zt_cache), sum(len(v) for v in zt_cache.values()), systime.time() - t0)

    trades: list[dict] = []
    total_candidates_found = 0
    total_candidates_filtered = 0
    no_pool_days = 0

    # 解析 sealed_before 为分钟数
    sb_minutes = 24 * 60
    try:
        h, m = sealed_before.split(":")
        sb_minutes = int(h) * 60 + int(m)
    except Exception:
        pass

    # 主循环
    for di, today in enumerate(dates):
        if (di + 1) % 20 == 0:
            log.info("  进度 %d/%d | trades=%d | %.1fs", di + 1, len(dates), len(trades), systime.time() - t0)
            _progress(progress_cb, "progress", iter=di + 1, total=len(dates), trades=len(trades))

        # T+1 买入日必须存在
        t1 = _next_trade_day(dates, today)
        if t1 is None:
            continue

        pool = zt_cache.get(today, [])
        if not pool:
            no_pool_days += 1
            continue

        total_candidates_found += len(pool)

        # 过滤
        filtered = []
        for zt in pool:
            code = zt["code"]

            # 连板数
            streak = int(zt.get("streak", 1) or 1)
            if streak < min_streak or streak > max_streak:
                continue

            # 炸板
            burst = int(zt.get("burst_count", 0) or 0)
            if burst >= 0 and burst > burst_max:
                continue

            # 封板时间
            ft = str(zt.get("first_time", "") or "")
            if ft and sb_minutes < 24 * 60:
                try:
                    h1, m1 = ft.strip().split(":")
                    ft_minutes = int(h1) * 60 + int(m1)
                    if ft_minutes > sb_minutes and ft_minutes < 24 * 60:
                        continue
                except Exception:
                    pass

            # 市值 (akshare 返回亿)
            mcap_em = float(zt.get("market_cap", 0) or 0)
            if mcap_em > 1e6:
                mcap_em /= 1e8
            if mcap_em > 0 and (mcap_em < mcap_min_yi or mcap_em > mcap_max_yi):
                continue

            # 换手
            turn = float(zt.get("turnover_pct", 0) or 0)
            if turn > 0 and (turn < turnover_min_pct or turn > turnover_max_pct):
                continue

            # 封单金额 (akshare 返回亿)
            lamt = float(zt.get("limit_order_amount", 0) or 0)
            if lamt > 1e6:
                lamt /= 1e8
            if lamt > 0 and lamt < limit_order_min_yi:
                continue

            # 板块热门（可选）
            if sector_hot_required and not zt.get("sector"):
                continue

            filtered.append(zt)

        total_candidates_filtered += len(filtered)

        if not filtered:
            continue

        # 评分排序
        scored = [(zt, _score_zt_candidate(zt)) for zt in filtered]
        scored.sort(key=lambda x: -x[1])
        picks = [s[0] for s in scored[:top_n]]

        # 模拟交易（从缓存取日线）
        for zt in picks:
            df = daily_cache.get(zt["code"])
            if df is None:
                continue
            trade = _simulate_trade(
                zt, df, dates, t1,
                entry_rule=entry_rule,
                trail_activate=trail_activate_pct,
                trail_pullback=trail_pullback_pct,
                stop_loss=stop_loss_pct,
            )
            if trade:
                trade["pick_date"] = today
                trades.append(trade)

    elapsed = round(systime.time() - t0, 1)
    log.info("========== ZT回测完成 | trades=%d | %.1fs ==========", len(trades), elapsed)

    # ── 聚合统计 ──
    summary = _aggregate_metrics(trades)
    monthly = _compute_monthly(trades)
    scenario = _compute_scenario_compare(trades)
    exit_breakdown = _compute_exit_breakdown(trades)
    sector_breakdown = _compute_sector_breakdown(trades)

    # 按退场方案分列统计
    scenario_detail = {}
    for exit_key in cfg.ZT_EXIT_RULES:
        exit_trades = []
        for t in trades:
            ep = t.get("exits_pct", {}) or {}
            pct = ep.get(exit_key)
            if pct is not None:
                exit_trades.append({**t, "return_pct": pct})
        if exit_trades:
            scenario_detail[exit_key] = _aggregate_metrics(exit_trades)

    result = {
        "config": {
            "start": start, "end": end,
            "top_n": top_n, "board_filter": board_filter,
            "entry_rule": entry_rule,
            "min_streak": min_streak, "max_streak": max_streak,
            "burst_max": burst_max, "sealed_before": sealed_before,
            "mcap_min_yi": mcap_min_yi, "mcap_max_yi": mcap_max_yi,
            "turnover_min_pct": turnover_min_pct, "turnover_max_pct": turnover_max_pct,
            "limit_order_min_yi": limit_order_min_yi,
            "sector_hot_required": sector_hot_required,
            "trail_activate_pct": trail_activate_pct,
            "trail_pullback_pct": trail_pullback_pct,
            "stop_loss_pct": stop_loss_pct,
            "sample": sample,
        },
        "summary": summary,
        "monthly": monthly,
        "scenario_compare_full": scenario_detail,
        "scenario_compare": scenario,
        "exit_breakdown": exit_breakdown,
        "sector_breakdown": sector_breakdown,
        "trades_count": len(trades),
        "trade_dates_total": len(dates),
        "no_pool_days": no_pool_days,
        "candidates_found": total_candidates_found,
        "candidates_filtered": total_candidates_filtered,
        "elapsed_sec": elapsed,
        "ts": datetime.now().isoformat(),
    }

    # 日志摘要
    if trades:
        log.info("  笔数=%d | 胜率=%.1f%% | 平均=%.2f%% | 日均=%.2f%% | 总收益=%.1f%% | 最大回撤=%.1f%%",
                 summary["trades"], summary["win_rate_pct"],
                 summary["avg_return_pct"], summary["daily_avg_return_pct"],
                 summary["total_return_pct"], summary["max_drawdown_pct"])
        # 各退场方案对比
        for ek, ev in sorted(scenario_detail.items()):
            log.info("  [%-10s] n=%d 胜率=%.1f%% 平均=%.2f%% 累计=%.1f%%",
                     ek, ev["trades"], ev["win_rate_pct"],
                     ev["avg_return_pct"], ev.get("total_return_pct", 0))
    else:
        log.warning("  无任何交易产生！")

    _progress(progress_cb, "done", elapsed=elapsed, trades=len(trades))

    return result


def _progress(cb, phase: str, **kw):
    if cb:
        try:
            cb({"phase": phase, **kw})
        except Exception:
            pass


# ── CLI ──

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="涨停板次日溢价回测")
    p.add_argument("--start", default=cfg.ZT_START)
    p.add_argument("--end", default=cfg.ZT_END)
    p.add_argument("--top", type=int, default=cfg.ZT_TOP_N)
    p.add_argument("--board", choices=["all", "main", "gem+star", "gem", "star"], default=cfg.ZT_BOARD_FILTER)
    p.add_argument("--entry", choices=["open_t1", "close_t0"], default=cfg.ZT_ENTRY_RULE)
    p.add_argument("--streak-min", type=int, default=cfg.ZT_MIN_STREAK)
    p.add_argument("--streak-max", type=int, default=cfg.ZT_MAX_STREAK)
    p.add_argument("--burst-max", type=int, default=cfg.ZT_BURST_MAX)
    p.add_argument("--sample", type=int, default=cfg.ZT_SAMPLE)
    p.add_argument("--save", action="store_true", help="保存结果到 reports/")
    args = p.parse_args()

    r = run_zt_backtest(
        start=args.start, end=args.end,
        top_n=args.top, board_filter=args.board,
        entry_rule=args.entry,
        min_streak=args.streak_min, max_streak=args.streak_max,
        burst_max=args.burst_max,
        sample=args.sample,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if args.save:
        from . import config as _cfg
        out = _cfg.REPORT_DIR / f"zt_backtest_{args.start}_{args.end}.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print(f"\n保存到 {out}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cli()
