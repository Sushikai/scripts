"""
tuixue_v3/backtest.py
回测引擎: T 日选股 → T+1 开盘买入 → Hold N 日卖出
按月汇总: 胜率 / 平均收益 / 月度收益率 / 最大回撤 / 盈亏比

2026-07-14 重要重构:
  原版 rule-mode 硬编码"假设有正值我就在正值上面卖出"(+5% 落袋固定) — 无统计支撑。
  现每笔交易一次性算 7 套退场(best/trail_3/5/8/stop_3/close/rule_pri),
  并将经验分布统计化输出(分位数/expected shortfall/恢复因子/sector breakdown/月胜负
  /退出原因分布/策略对比:rule vs hold-end vs theoretical-best)。
  "硬编码假设" → "实证价格分布"。
"""
from __future__ import annotations

import json
import logging
import threading
import time as systime
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config as cfg
from . import data_layer as dl
from . import layer1_basic as l1
from . import layer2_cycle_mainline as l2
from . import layer3_daily as l3
from . import layer4_intraday as l4
from .screen import run_stock_screen

log = logging.getLogger("tuixue_v3.backtest")


def _next_trade_day(dates: list[str], date_str: str) -> str | None:
    """T+1:date_str 后第一个交易日"""
    for d in dates:
        if d > date_str:
            return d
    return None


def _simulate_trade(df: pd.DataFrame, buy_date: str, sell_date: str, mode: str = "rule") -> dict | None:
    """
    T+1 开盘买入 → hold N 日 → 同时算 7 套退场场景 + 选定 1 套作主退场。

    7 套退场(每套都基于真实 OHLC):
      "best"          : 持有期内最高价出场(理论上限,持有期内的最大值)
      "trail_3pct"    : 高点≥买×1.03 后,按"高点-1.5%"出场
      "trail_5pct"    : 高点≥买×1.05 后,按"高点-2%"出场
      "trail_8pct"    : 高点≥买×1.08 后,按"高点-3%"出场 (即用户原"假设+5%落袋"的近似)
      "stop_3pct"     : 单日最低<买×0.97 当日按低止损出场
      "close"         : 持有到期收盘价 (基准 / 朴素买入持有)
      "rule_pri"      : 铁律优先 → stop_3 → trail_8 → close,首触发即成交

    每套退场应用 slippage + 单边佣金;出场侧再加一次双边 + 印花税。
    不再用任何"假设有正值就在正值卖"的硬编码 — 全程用实证价格分布。
    """
    if df is None or df.empty or "日期" not in df.columns:
        return None
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y%m%d")
    sub = df[(df["日期"] >= buy_date) & (df["日期"] <= sell_date)].copy()
    if len(sub) < 2:
        return None

    sub = sub.reset_index(drop=True)
    buy_row = sub.iloc[0]
    buy_price = float(buy_row["开盘"])
    if buy_price <= 0:
        return None

    slip = cfg.BACKTEST_SLIPPAGE_PCT
    fee = cfg.BACKTEST_FEE_PCT
    stamp = cfg.BACKTEST_STAMP_TAX_PCT
    cost_in = slip + fee                            # 进入: 滑点 + 佣金
    cost_out = cost_in + slip + fee + stamp          # 出场: 滑点 + 佣金 + 印花税

    def _ret(sell_price: float, sell_kind: str) -> float:
        """统一退场成本: 进出场双边都算。"""
        if sell_price <= 0:
            return 0.0
        return (sell_price / buy_price - 1) * 100 - cost_out * 100

    highs = [float(r["最高"]) for _, r in sub.iterrows()]
    lows = [float(r["最低"]) for _, r in sub.iterrows()]
    closes = [float(r["收盘"]) for _, r in sub.iterrows()]
    opens = [float(r["开盘"]) for _, r in sub.iterrows()]
    dates = [str(r["日期"]) for _, r in sub.iterrows()]
    n = len(sub)
    last_idx = n - 1
    last_close = closes[last_idx]
    last_d = dates[last_idx]

    # ── best: 期内最高价 ──
    best_sell = max(highs)
    best_d = dates[highs.index(best_sell)]

    # ── trail_N: 当日 high ≥ buy × (1+thresh) → 当日按"高点 - 回撤%"成交 ──
    def _trail(thresh_pct: float, pullback_pct: float) -> tuple[float, str, int]:
        for i in range(1, n):
            if highs[i] >= buy_price * (1 + thresh_pct):
                target = highs[i] * (1 - pullback_pct)
                return max(target, opens[i]), dates[i], i
        return last_close, last_d, last_idx

    trail3_sell, trail3_d, trail3_idx = _trail(0.03, 0.015)
    trail5_sell, trail5_d, trail5_idx = _trail(0.05, 0.02)
    trail8_sell, trail8_d, trail8_idx = _trail(0.08, 0.03)

    # ── stop_3: 单日 low < buy × 0.97 当日按 low 止损 ──
    stop3_sell, stop3_d, stop3_idx = last_close, last_d, last_idx
    for i in range(1, n):
        if lows[i] <= buy_price * 0.97:
            stop3_sell, stop3_d, stop3_idx = lows[i], dates[i], i
            break

    # ── close: 持有到期收盘 ──
    close_sell, close_d = last_close, last_d

    # ── rule_pri: stop_3 → trail_8 → close, 首触发即成交(铁律优先) ──
    if stop3_idx < last_idx:                          # stop 真触发,没拖到最后一天
        rule_sell, rule_d, rule_kind = stop3_sell, stop3_d, "stop_3pct"
    elif trail8_idx < last_idx:                       # trail8 触发到了
        rule_sell, rule_d, rule_kind = trail8_sell, trail8_d, "trail_8pct"
    else:
        rule_sell, rule_d, rule_kind = close_sell, close_d, "time_exit"

    exits_pct = {
        "best":       round(_ret(best_sell,  "best"), 2),
        "trail_3pct": round(_ret(trail3_sell, "trail"), 2),
        "trail_5pct": round(_ret(trail5_sell, "trail"), 2),
        "trail_8pct": round(_ret(trail8_sell, "trail"), 2),
        "stop_3pct":  round(_ret(stop3_sell,  "stop"), 2),
        "close":      round(_ret(close_sell,  "close"), 2),
        "rule_pri":   round(_ret(rule_sell,   rule_kind), 2),
    }
    exits_sell = {
        "best":       round(best_sell, 3),
        "trail_3pct": round(trail3_sell, 3),
        "trail_5pct": round(trail5_sell, 3),
        "trail_8pct": round(trail8_sell, 3),
        "stop_3pct":  round(stop3_sell, 3),
        "close":      round(close_sell, 3),
        "rule_pri":   round(rule_sell, 3),
    }
    exits_d = {
        "best":       best_d,
        "trail_3pct": trail3_d,
        "trail_5pct": trail5_d,
        "trail_8pct": trail8_d,
        "stop_3pct":  stop3_d,
        "close":      close_d,
        "rule_pri":   rule_d,
    }

    # ── 主退场由 mode 决定 ──
    if mode == "max":
        main = ("best", best_sell, best_d)
    elif mode == "close":
        main = ("close", close_sell, close_d)
    else:  # rule
        main = (rule_kind, rule_sell, rule_d)

    main_kind, main_sell, main_d = main
    main_ret = round(_ret(main_sell, main_kind), 2)

    # 实际持有天数 (dates[0] → exits_pri_date)
    try:
        d0 = pd.to_datetime(dates[0])
        d1 = pd.to_datetime(main_d)
        hold_days = (d1 - d0).days
    except Exception:
        hold_days = n - 1

    return {
        "buy_date": buy_date,
        "sell_date": main_d,
        "buy_price": round(buy_price, 3),
        "sell_price": round(main_sell, 3),
        "return_pct": main_ret,
        "trigger": main_kind,
        "mode": mode,
        "hold_days": hold_days,
        # 7 套退场 → 横向对比,可看 rule 到底差 best 多远、相对 hold-end 强多少
        "exits_pct": exits_pct,
        "exits_sell_price": exits_sell,
        "exits_sell_date": exits_d,
        "best_exit_pct": exits_pct["best"],
        "rule_vs_hold_pct": round(exits_pct["rule_pri"] - exits_pct["close"], 2),
        "rule_vs_best_pct": round(exits_pct["rule_pri"] - exits_pct["best"], 2),
    }


def run_backtest(start: str = "2025-01-01", end: str = "2026-06-30",
                 top_n: int | None = None, hold_days: int | None = None,
                 sell_mode: str = "rule", sample: int = 200) -> dict:
    """
    回测入口:
      - start/end: YYYY-MM-DD
      - top_n: 每日入选即买入只数(默认 cfg.BACKTEST_TOP_N)
      - hold_days: 持有天数(默认 cfg.BACKTEST_HOLD_DAYS)
      - sell_mode: rule / max / close
      - sample: 股票池采样(默认 200,加快速度;0=全市场)

    并发安全:
      run_backtest 共享 _BT_CTX + 全局 monkey-patch,所以同一时间只能跑一个。
      用 _BT_RUN_SEMAPHORE 串行化,避免 _LONG_EXECUTOR 多 worker 并发触发
      patched_screen 递归 / _BT_CTX 串扰 (2026-07-12 audit)。
    """
    top_n = top_n or cfg.BACKTEST_TOP_N
    hold_days = hold_days or cfg.BACKTEST_HOLD_DAYS

    with _BT_RUN_SEMAPHORE:
        log.info(f"========== 回测开始 {start} → {end} | top={top_n} hold={hold_days} sell={sell_mode} sample={sample} ==========")
        t0 = systime.time()

        dates = dl.fetch_trade_dates(start, end)
        log.info(f"交易日历: {len(dates)} 天")

        stocks_all = dl.fetch_stock_list()
        if sample and len(stocks_all) > sample:
            stocks_all = stocks_all[:sample]
        log.info(f"股票池采样: {len(stocks_all)} 只")

        log.info("预热日线数据...")
        daily_cache = dl.batch_fetch_daily([c for c, _ in stocks_all], days=400, progress_every=100)
        log.info(f"日线缓存: {len(daily_cache)} 只命中(耗时 {systime.time()-t0:.1f}s)")

        log.info("预加载情绪日历 + 主线日历...")
        emotion_cal, mainline_cal = _preload_calendars(dates)

        _BT_CTX["daily_cache"] = daily_cache
        _BT_CTX["emotion_cal"] = emotion_cal
        _BT_CTX["mainline_cal"] = mainline_cal
        _install_backtest_patches()
        try:
            trades: list[dict] = []
            stats_by_date = []
            skipped_no_pick = 0
            cycle_blocked_days = 0

            peak_equity = 1.0
            equity_running = 1.0
            effective_top = top_n
            risk_state = "normal"
            risk_actions: list[dict] = []
            DD_THRESHOLD = 10.0
            DD_RECOVER = 5.0

            for di, d in enumerate(dates):
                if di + hold_days + 1 >= len(dates):
                    continue
                buy_d = dates[di + 1]
                sell_d = dates[di + 1 + hold_days]

                try:
                    r = _screen_for_date(d, stocks_all)
                except RecursionError as e:
                    import traceback as _tb
                    if not getattr(log, "_recursion_trace_done", False):
                        log.error(f"{d} 选股 RecursionError 完整栈:\n"
                                  + "".join(_tb.format_exc()))
                        log._recursion_trace_done = True
                    log.warning(f"{d} 选股异常: {e}")
                    continue
                except Exception as e:
                    log.warning(f"{d} 选股异常: {e}")
                    continue

                picks = r.get("candidates", [])
                if not picks:
                    if r.get("stats_by_layer", {}).get("l2", {}).get("cycle_blocked", 0):
                        cycle_blocked_days += 1
                    else:
                        skipped_no_pick += 1
                    stats_by_date.append({"date": d, "picks": 0, "reason": r.get("reason")})
                    continue

                picks = picks[:effective_top]

                today_dd_pct = 0.0
                for pick in picks:
                    code = pick["code"]
                    df = daily_cache.get(code)
                    if df is None:
                        continue
                    trade = _simulate_trade(df, buy_d, sell_d, mode=sell_mode)
                    if trade:
                        trade["code"] = code
                        trade["name"] = pick.get("name", "")
                        trade["sector"] = pick.get("sector", "")
                        trade["pick_date"] = d
                        trade["score"] = pick.get("rr_ratio", 0)
                        trade["risk_state"] = risk_state
                        trades.append(trade)

                realized_pnl_pct = sum(t["return_pct"] for t in trades) / max(1, top_n)
                equity_running = 1.0 + realized_pnl_pct / 100.0
                peak_equity = max(peak_equity, equity_running)
                drawdown_pct = (peak_equity - equity_running) / peak_equity * 100.0 if peak_equity > 0 else 0.0
                today_dd_pct = drawdown_pct

                for t in trades:
                    if t["buy_date"] == buy_d:
                        t["drawdown_pct_at_pick"] = round(drawdown_pct, 2)

                new_top = effective_top
                new_state = risk_state
                if drawdown_pct >= DD_THRESHOLD and risk_state == "normal":
                    new_top = max(1, top_n // 2)
                    new_state = "reduced"
                elif drawdown_pct <= DD_RECOVER and risk_state == "reduced":
                    new_top = top_n
                    new_state = "normal"
                if new_top != effective_top or new_state != risk_state:
                    action = {
                        "date": d, "drawdown_pct": round(drawdown_pct, 2),
                        "from_top": effective_top, "to_top": new_top,
                        "from_state": risk_state, "to_state": new_state,
                        "peak_equity": round(peak_equity, 4),
                        "equity": round(equity_running, 4),
                    }
                    risk_actions.append(action)
                    log.info(f"  ⚠ [{d}] 回撤 {drawdown_pct:.2f}% ≥ {DD_THRESHOLD}%  → top_n {effective_top} → {new_top} ({risk_state} → {new_state})")
                    effective_top = new_top
                    risk_state = new_state

                stats_by_date.append({
                    "date": d, "picks": len(picks),
                    "buy_date": buy_d, "sell_date": sell_d,
                    "codes": [p["code"] for p in picks],
                    "drawdown_pct": round(today_dd_pct, 2),
                    "peak_equity": round(peak_equity, 4),
                    "risk_state": risk_state,
                    "effective_top": effective_top,
                })

                if (di + 1) % 20 == 0:
                    log.info(f"  回测进度 {di+1}/{len(dates)} | trades={len(trades)} | drawdown={today_dd_pct:.2f}% | state={risk_state} | 用时 {systime.time()-t0:.1f}s")
        finally:
            _uninstall_backtest_patches()

        # 2026-07-14 重写统计层:
        #   - 月度胜负(胜/负月 count + 胜率)
        #   - 总体分位数(p10/p25/p50/p75/p90) + 期望值 expectancy
        #   - 6 套退场场景横向对比(rule vs best vs close vs trail_3/5/8 vs stop_3)
        #   - 退出原因分布(stop/trail/close各占多少)
        #   - sector 表现(板块层面)
        #   - 持有天数分布
        #   - Recovery factor, Sortino-like, expected shortfall
        monthly = _compute_monthly(trades)
        overall = _compute_overall(trades)
        exit_breakdown = _compute_exit_breakdown(trades)
        scenario_compare = _compute_scenario_compare(trades)
        sector_breakdown = _compute_sector_breakdown(trades)
        hold_dist = _compute_hold_dist(trades)

        result = {
            "config": {
                "start": start, "end": end,
                "top_n": top_n, "hold_days": hold_days,
                "sell_mode": sell_mode, "sample": sample,
            },
            "summary": overall,
            "monthly": monthly,
            "scenario_compare": scenario_compare,
            "exit_breakdown": exit_breakdown,
            "sector_breakdown": sector_breakdown,
            "hold_distribution": hold_dist,
            "trades_count": len(trades),
            "trade_dates_total": len(dates),
            "cycle_blocked_days": cycle_blocked_days,
            "skipped_no_pick_days": skipped_no_pick,
            "risk_actions": risk_actions,
            "risk_reduced_days": sum(1 for s in stats_by_date if s.get("risk_state") == "reduced"),
            "risk_state": risk_state,
            "peak_equity": round(peak_equity, 4),
            "final_equity": round(equity_running, 4),
            "elapsed_sec": round(systime.time() - t0, 1),
            "ts": datetime.now().isoformat(),
        }
        log.info(f"========== 回测完成 | trades={len(trades)} | {result['elapsed_sec']}s ==========")
        log.info(f"  整体: 胜率 {overall['win_rate_pct']}% | 平均 {overall['avg_return_pct']}% | 月均 {overall['monthly_avg_return_pct']}% | 最大回撤 {overall['max_drawdown_pct']}%")
        log.info(f"  月胜/负: {overall['positive_months']}/{overall['negative_months']} ({overall['month_win_rate_pct']}%)")
        log.info(f"  Scenario | rule {scenario_compare.get('rule_pri',{}).get('avg_pct','?')}% | best {scenario_compare.get('best',{}).get('avg_pct','?')}% | close {scenario_compare.get('close',{}).get('avg_pct','?')}%")
        return result


def _preload_calendars(trade_dates: list[str]) -> tuple[dict, dict]:
    """
    复用 v2 backtest lib 的情绪/主线日历缓存,节省时间。
    返回 (emotion_cal, mainline_cal)
    """
    emotion_cal = {}
    mainline_cal = {}

    try:
        from . import dragon_backtest_v2_lib as _dlib
        preload_emotion_calendar = _dlib.preload_emotion_calendar
        preload_mainline_calendar = _dlib.preload_mainline_calendar
        log.info("调用 v2 lib 预加载情绪...")
        emotion_cal = preload_emotion_calendar(trade_dates, use_cache=True)
        log.info(f"情绪日历: {len(emotion_cal)} 天")
        log.info("调用 v2 lib 预加载主线...")
        mainline_cal = preload_mainline_calendar(trade_dates, use_cache=True)
        log.info(f"主线日历: {len(mainline_cal)} 天 ({sum(1 for s in mainline_cal.values() if s)} 有主线)")
    except Exception as e:
        log.warning(f"v2 lib 预加载失败,走自建 fallback: {e}")
        for d in trade_dates:
            emotion_cal[d] = {
                "phase": "启动",
                "emotion_score": 50.0,
                "zt_count": 30,
                "max_streak": 3,
                "source": "fallback_neutral",
            }
            mainline_cal[d] = set()

    return emotion_cal, mainline_cal


# ═══════════════════════════════════════════════════
# 回测专属 monkey-patch 上下文(一次性安装,per run_backtest)
# ═══════════════════════════════════════════════════
_BT_CTX: dict[str, Any] = {"date": None, "daily_cache": None,
                          "emotion_cal": None, "mainline_cal": None}
_BT_INSTALL_LOCK = threading.RLock()
_BT_PATCH_INSTALLED = False
_BT_RUN_SEMAPHORE = threading.Semaphore(1)


def _install_backtest_patches() -> None:
    """一次性把 dl.fetch_daily + l2.screen 替换为使用 _BT_CTX 的回测版本。"""
    global _BT_PATCH_INSTALLED
    with _BT_INSTALL_LOCK:
        import tuixue_v3.data_layer as dl_mod
        import tuixue_v3.layer2_cycle_mainline as l2_mod

        if _BT_PATCH_INSTALLED:
            log.debug("backtest patches 已安装, 跳过 (并发第二线程)")
            return

        def fake_fetch_daily(code: str, days: int = 120, force: bool = False):
            date_str = _BT_CTX["date"]
            df = _BT_CTX["daily_cache"].get(code) if _BT_CTX["daily_cache"] else None
            if df is None or date_str is None:
                return None
            df = df.copy()
            if "日期" in df.columns:
                df["日期"] = pd.to_datetime(df["日期"])
                df = df[df["日期"] <= pd.to_datetime(date_str)]
                df["日期"] = df["日期"].dt.strftime("%Y%m%d")
            return df.tail(days)

        def patched_screen(stocks_, date_str_=None, **_):
            return l2_mod._orig_screen(stocks_, date_str_ or _BT_CTX["date"],
                                       emotion_cal=_BT_CTX["emotion_cal"],
                                       mainline_cal=_BT_CTX["mainline_cal"])

        dl_mod._orig_fetch_daily = dl_mod.fetch_daily
        l2_mod._orig_screen = l2_mod.screen
        dl_mod.fetch_daily = fake_fetch_daily
        l2_mod.screen = patched_screen
        _BT_PATCH_INSTALLED = True


def _uninstall_backtest_patches() -> None:
    """还原 dl.fetch_daily + l2.screen。"""
    global _BT_PATCH_INSTALLED
    with _BT_INSTALL_LOCK:
        import tuixue_v3.data_layer as dl_mod
        import tuixue_v3.layer2_cycle_mainline as l2_mod
        if not _BT_PATCH_INSTALLED:
            return
        if hasattr(dl_mod, "_orig_fetch_daily"):
            dl_mod.fetch_daily = dl_mod._orig_fetch_daily
        if hasattr(l2_mod, "_orig_screen"):
            l2_mod.screen = l2_mod._orig_screen
        _BT_PATCH_INSTALLED = False


def _screen_for_date(date_str: str, stocks: list[tuple[str, str]]) -> dict:
    """单日选股: 用 _BT_CTX 的预装值;stocks 已由调用方采样好。"""
    _BT_CTX["date"] = date_str
    return run_stock_screen(date_str=date_str, mode="backtest", stocks=stocks)


# ═══════════════════════════════════════════════════
# 统计计算 (2026-07-14 大幅扩展)
# ═══════════════════════════════════════════════════
def _percentiles(arr: list[float], qs: list[int]) -> dict[str, float]:
    """分位数 dict,空数组返 0。"""
    if not arr:
        return {f"p{q}": 0.0 for q in qs}
    np_arr = np.asarray(arr, dtype=float)
    return {f"p{q}": round(float(np.percentile(np_arr, q)), 3) for q in qs}


def _compute_monthly(trades: list[dict]) -> list[dict]:
    """按月聚合: 月收益率 + 胜率 + 笔数 + 胜/负月标。"""
    if not trades:
        return []

    df = pd.DataFrame(trades)
    df["buy_date"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date"].dt.to_period("M").astype(str)

    rows = []
    for m, g in df.groupby("month"):
        wins = int((g["return_pct"] > 0).sum())
        losses = int((g["return_pct"] < 0).sum())
        fl = len(g)
        g_returns = g["return_pct"].astype(float)
        avg_win = float(g_returns[g_returns > 0].mean()) if wins else 0.0
        avg_loss = float(g_returns[g_returns < 0].mean()) if losses else 0.0
        rows.append({
            "month": m,
            "trades": fl,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / fl * 100, 2) if fl else 0,
            "avg_return_pct": round(float(g_returns.mean()), 2),
            "sum_return_pct": round(float(g_returns.sum()), 2),
            "max_return_pct": round(float(g_returns.max()), 2),
            "min_return_pct": round(float(g_returns.min()), 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "monthly_return_pct": round(float(g_returns.sum()), 2),  # 等权口径
        })
    return rows


def _compute_overall(trades: list[dict]) -> dict:
    """总体统计: 含分位数 + 月胜负 + expected shortfall 等统计层。"""
    if not trades:
        return {
            "trades": 0, "win_rate_pct": 0, "avg_return_pct": 0,
            "monthly_avg_return_pct": 0, "max_drawdown_pct": 0,
            "positive_months": 0, "negative_months": 0, "month_win_rate_pct": 0,
            "percentiles": {}, "expectancy_per_trade_pct": 0,
            "stddev_return_pct": 0, "sharpe_like": 0, "sortino_like": 0,
            "expected_shortfall_p5_pct": 0, "recovery_factor": 0,
            "profit_factor": 0,
        }
    df = pd.DataFrame(trades)
    rets = df["return_pct"].astype(float)
    n = len(df)
    wins = int((rets > 0).sum())
    losses = int((rets < 0).sum())
    win_rate = wins / n * 100
    avg_return = float(rets.mean())
    median_return = float(rets.median())
    stddev = float(rets.std(ddof=1)) if n > 1 else 0.0

    win_sum = float(rets[rets > 0].sum())
    loss_sum = abs(float(rets[rets < 0].sum()))
    profit_factor = round(win_sum / loss_sum, 2) if loss_sum > 0 else float("inf")

    avg_win = float(rets[rets > 0].mean()) if wins else 0
    avg_loss = float(rets[rets < 0].mean()) if losses else 0
    # 期望值 (per-trade expectancy, R-trading 经典口径)
    expectancy = (win_rate / 100) * avg_win + ((100 - win_rate) / 100) * avg_loss
    # Sharpe-like (无风险=0, 用 stdev 当 risk)
    sharpe_like = round(avg_return / stddev, 2) if stddev > 0 else 0
    # Sortino-like (只用下行 stdev)
    downside = rets[rets < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino_like = round(avg_return / downside_std, 2) if downside_std > 0 else 0

    # 月度胜负
    df["buy_date_dt"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date_dt"].dt.to_period("M").astype(str)
    monthly_avg = df.groupby("month")["return_pct"].mean()
    pos_months = int((monthly_avg > 0).sum())
    neg_months = int((monthly_avg < 0).sum())
    zero_months = int((monthly_avg == 0).sum())
    n_months = len(monthly_avg)
    month_win_rate_pct = round(pos_months / n_months * 100, 2) if n_months else 0

    # 资金曲线 + 最大回撤
    equity = (1 + rets / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = round(float(drawdown.min()), 2) if len(drawdown) else 0
    total_return_pct = round(float((equity.iloc[-1] - 1) * 100), 2) if len(equity) else 0

    # Recovery factor (总收益 / |最大回撤|)
    recovery_factor = round(total_return_pct / abs(max_dd), 2) if max_dd < 0 else None

    # Expected shortfall (CVaR 5%) — 最差 5% 笔的平均亏损
    q05 = float(np.percentile(rets, 5))
    es_t = rets[rets <= q05]
    expected_shortfall = round(float(es_t.mean()), 2) if len(es_t) else 0

    # 月均收益(每月等权)
    monthly_avg_pct = round(float(monthly_avg.mean()), 2) if n_months else 0

    # 持有天数分布
    if "hold_days" in df.columns:
        avg_hold = round(float(df["hold_days"].mean()), 1)
        max_hold = int(df["hold_days"].max())
        min_hold = int(df["hold_days"].min())
    else:
        avg_hold = max_hold = min_hold = 0

    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "avg_return_pct": round(avg_return, 3),
        "median_return_pct": round(median_return, 3),
        "stddev_return_pct": round(stddev, 3),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "profit_factor": profit_factor,
        "expectancy_per_trade_pct": round(expectancy, 3),
        "sharpe_like": sharpe_like,
        "sortino_like": sortino_like,
        "expected_shortfall_p5_pct": expected_shortfall,
        "recovery_factor": recovery_factor,
        "total_return_pct": total_return_pct,
        "monthly_avg_return_pct": monthly_avg_pct,
        "positive_months": pos_months,
        "negative_months": neg_months,
        "zero_months": zero_months,
        "month_win_rate_pct": month_win_rate_pct,
        "max_drawdown_pct": max_dd,
        "best_trade_pct": round(float(rets.max()), 2),
        "worst_trade_pct": round(float(rets.min()), 2),
        "percentiles": _percentiles(rets.tolist(), [5, 10, 25, 50, 75, 90, 95]),
        "avg_hold_days": avg_hold,
        "max_hold_days": max_hold,
        "min_hold_days": min_hold,
    }


def _compute_exit_breakdown(trades: list[dict]) -> dict:
    """退场原因分布: 让用户知道这套规则实际跑出了多少 stop、trail、time_exit。"""
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    if "trigger" not in df.columns:
        return {}
    vc = df["trigger"].value_counts().to_dict()
    total = len(df)
    out = {}
    for k, v in vc.items():
        out[k] = {"count": int(v), "pct": round(v / total * 100, 2)}
    out["_total"] = total
    return out


def _compute_scenario_compare(trades: list[dict]) -> dict:
    """6 套退场策略横向对比: 用同一价格分布,看每套能跑出多少。

    关键:不再用"假设有正值就在正值卖",而是给规则一个实证基准:
      - close: 朴素持有到期 — 规则必须跑赢这个才有意义
      - best: 持股期内最高峰 — 规则上限(理论 best 不可达,但越接近越好)
      - trail_3/5/8: 不同止盈阈值的实证收益
      - stop_3: 单纯止损(不主动止盈)看能躲多少跌
    """
    if not trades:
        return {}
    if "exits_pct" not in trades[0]:
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
        avg = float(np_a.mean())
        std = float(np_a.std(ddof=1)) if n > 1 else 0.0
        # 等权累计 (复利)
        eq = (1 + np_a / 100).cumprod()
        cum_ret = float((eq[-1] - 1) * 100) if len(eq) else 0
        out[k] = {
            "n": n,
            "wins": wins,
            "win_rate_pct": round(wins / n * 100, 2),
            "avg_pct": round(avg, 3),
            "median_pct": round(float(np.median(np_a)), 3),
            "stddev_pct": round(std, 3),
            "best_pct": round(float(np_a.max()), 2),
            "worst_pct": round(float(np_a.min()), 2),
            "cum_return_pct": round(cum_ret, 2),
            "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
            "p25_p75": [round(float(np.percentile(np_a, 25)), 2),
                        round(float(np.percentile(np_a, 75)), 2)],
        }
    # 规则 vs 朴素持有 vs 理论最佳
    rule_p = out.get("rule_pri", {}).get("avg_pct")
    close_p = out.get("close", {}).get("avg_pct")
    best_p = out.get("best", {}).get("avg_pct")
    if rule_p is not None and close_p is not None and best_p is not None:
        out["_rule_vs_close_gap"] = round(rule_p - close_p, 3)
        out["_rule_vs_best_gap"] = round(rule_p - best_p, 3)
        if best_p:
            out["_rule_efficiency_to_best_pct"] = round(rule_p / best_p * 100, 2)
    return out


def _compute_sector_breakdown(trades: list[dict]) -> list[dict]:
    """按 sector 聚合: 哪个板块表现最好/最差(帮助检查主线筛选是否真有效)。"""
    if not trades:
        return []
    df = pd.DataFrame(trades)
    if "sector" not in df.columns:
        return []
    rows = []
    for sec, g in df.groupby(df["sector"].fillna("—")):
        rets = g["return_pct"].astype(float)
        rows.append({
            "sector": str(sec) if sec else "—",
            "trades": len(g),
            "win_rate_pct": round((rets > 0).sum() / len(rets) * 100, 2),
            "avg_return_pct": round(float(rets.mean()), 3),
            "sum_return_pct": round(float(rets.sum()), 2),
            "best_pct": round(float(rets.max()), 2),
            "worst_pct": round(float(rets.min()), 2),
        })
    rows.sort(key=lambda r: -r["sum_return_pct"])
    return rows


def _compute_hold_dist(trades: list[dict]) -> dict:
    """持有天数 (rule_pri 触发之日 - 买入日) 分布。"""
    if not trades:
        return {}
    df = pd.DataFrame(trades)
    if "hold_days" not in df.columns:
        return {}
    hd = df["hold_days"].astype(int)
    vc = hd.value_counts().sort_index()
    return {
        "avg": round(float(hd.mean()), 1),
        "median": round(float(hd.median()), 1),
        "p10": round(float(np.percentile(hd, 10)), 0),
        "p90": round(float(np.percentile(hd, 90)), 0),
        "buckets": {str(int(k)): int(v) for k, v in vc.items()},
    }


# CLI
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="退学 v3 回测")
    p.add_argument("--start", default=cfg.OPT_START)
    p.add_argument("--end", default=cfg.OPT_END)
    p.add_argument("--top", type=int, default=cfg.BACKTEST_TOP_N)
    p.add_argument("--hold", type=int, default=cfg.BACKTEST_HOLD_DAYS)
    p.add_argument("--sample", type=int, default=200)
    p.add_argument("--sell", choices=["rule", "max", "close"], default="rule")
    p.add_argument("--save", action="store_true")
    args = p.parse_args()

    r = run_backtest(args.start, args.end, args.top, args.hold, args.sell, args.sample)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if args.save:
        out = cfg.REPORT_DIR / f"backtest_{args.start}_{args.end}.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print(f"\n已保存到 {out}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cli()
