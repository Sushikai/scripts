"""
tuixue_v3/backtest.py
回测引擎：T 日选股 → T+1 开盘买入 → Hold N 日卖出
按月汇总：胜率 / 平均收益 / 月度收益率 / 最大回撤 / 盈亏比
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
    """T+1：date_str 后第一个交易日"""
    for d in dates:
        if d > date_str:
            return d
    return None


def _simulate_trade(df: pd.DataFrame, buy_date: str, sell_date: str, mode: str = "rule") -> dict | None:
    """
    T+1 开盘买入，hold N 日，按规则/最大收益卖出。
    mode: "rule"（按规则止盈止损）/ "max"（期间最高收盘价） / "close"（持有到期收盘）
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

    if mode == "max":
        # 持有期内最大收盘价卖出（理论上限）
        sell_price = float(sub["收盘"].max())
        sell_idx = sub["收盘"].idxmax()
        sell_d = sub.iloc[sell_idx]["日期"]
        return {
            "buy_date": buy_date, "sell_date": str(sell_d),
            "buy_price": round(buy_price, 3),
            "sell_price": round(sell_price, 3),
            "return_pct": round((sell_price / buy_price - 1) * 100 - cfg.BACKTEST_SLIPPAGE_PCT * 100 - cfg.BACKTEST_FEE_PCT * 100 - cfg.BACKTEST_STAMP_TAX_PCT * 100, 2),
            "mode": "max",
        }

    if mode == "close":
        sell_price = float(sub.iloc[-1]["收盘"])
        sell_d = sub.iloc[-1]["日期"]
        return {
            "buy_date": buy_date, "sell_date": str(sell_d),
            "buy_price": round(buy_price, 3),
            "sell_price": round(sell_price, 3),
            "return_pct": round((sell_price / buy_price - 1) * 100 - cfg.BACKTEST_SLIPPAGE_PCT * 100 - cfg.BACKTEST_FEE_PCT * 100, 2),
            "mode": "close",
        }

    # 规则卖出：止盈 6% 触发后回落 3% / 跌破 MA10 清仓 / 时间止损 N 日
    triggered = None
    sell_price = None
    sell_d = None
    for i, row in sub.iterrows():
        if i == 0:
            continue
        high = float(row["最高"])
        low = float(row["最低"])
        close = float(row["收盘"])
        # 止盈：收盘价 ≥ 买入 × 1.08
        if high >= buy_price * 1.08:
            triggered = "take_profit_trail"
            sell_price = buy_price * 1.05   # 回落 3% 卖出（8% 触发 → 5% 落袋）
            sell_d = row["日期"]
            break
        # 跌破 MA10：日线收盘 < MA10
        if "MA10" in row and pd.notna(row.get("MA10")) and close < float(row["MA10"]):
            triggered = "stop_loss_ma10"
            sell_price = close
            sell_d = row["日期"]
            break
        # 单日跌幅 > 3%
        if (low / buy_price - 1) < -0.03:
            triggered = "stop_loss_3pct"
            sell_price = low
            sell_d = row["日期"]
            break

    if sell_price is None:
        sell_price = float(sub.iloc[-1]["收盘"])
        sell_d = sub.iloc[-1]["日期"]
        triggered = "time_exit"

    return_pct = (sell_price / buy_price - 1) * 100
    return_pct -= cfg.BACKTEST_SLIPPAGE_PCT * 100 + cfg.BACKTEST_FEE_PCT * 100
    if triggered in ("take_profit_trail", "time_exit"):
        return_pct -= cfg.BACKTEST_STAMP_TAX_PCT * 100

    return {
        "buy_date": buy_date, "sell_date": str(sell_d),
        "buy_price": round(buy_price, 3),
        "sell_price": round(sell_price, 3),
        "return_pct": round(return_pct, 2),
        "trigger": triggered,
        "mode": "rule",
    }


def run_backtest(start: str = "2025-01-01", end: str = "2026-06-30",
                 top_n: int | None = None, hold_days: int | None = None,
                 sell_mode: str = "rule", sample: int = 200) -> dict:
    """
    回测入口：
      - start/end: YYYY-MM-DD
      - top_n: 每日入选即买入只数（默认 cfg.BACKTEST_TOP_N）
      - hold_days: 持有天数（默认 cfg.BACKTEST_HOLD_DAYS）
      - sell_mode: rule / max / close
      - sample: 股票池采样（默认 200，加快速度；0=全市场）
    """
    top_n = top_n or cfg.BACKTEST_TOP_N
    hold_days = hold_days or cfg.BACKTEST_HOLD_DAYS

    log.info(f"========== 回测开始 {start} → {end} | top={top_n} hold={hold_days} sell={sell_mode} sample={sample} ==========")
    t0 = systime.time()

    # 1) 交易日历
    dates = dl.fetch_trade_dates(start, end)
    log.info(f"交易日历: {len(dates)} 天")

    # 2) 股票池
    stocks_all = dl.fetch_stock_list()
    if sample and len(stocks_all) > sample:
        stocks_all = stocks_all[:sample]
    log.info(f"股票池采样: {len(stocks_all)} 只")

    # 3) 批量预热日线
    log.info("预热日线数据...")
    daily_cache = dl.batch_fetch_daily([c for c, _ in stocks_all], days=400, progress_every=100)
    log.info(f"日线缓存: {len(daily_cache)} 只命中（耗时 {systime.time()-t0:.1f}s）")

    # 4) 预加载情绪日历 + 主线日历（复用 v2 lib 的缓存）
    log.info("预加载情绪日历 + 主线日历...")
    emotion_cal, mainline_cal = _preload_calendars(dates)

    # 5) 写入回测上下文 + 一次性安装 monkey-patch（避免每日期重建 closure）
    _BT_CTX["daily_cache"] = daily_cache
    _BT_CTX["emotion_cal"] = emotion_cal
    _BT_CTX["mainline_cal"] = mainline_cal
    _install_backtest_patches()
    try:
        # 6) 每日选股 + 模拟交易
        trades: list[dict] = []
        stats_by_date = []
        skipped_no_pick = 0
        cycle_blocked_days = 0

        # ── 铁律三.4：回撤到本轮高点 10% → 强制把 top_n 减半，恢复用 5% 死区 ──
        # 用累计已实现盈亏 (按 top_n 等分摊到每槽) 估算资金曲线，峰值跟踪
        peak_equity = 1.0
        equity_running = 1.0
        effective_top = top_n
        risk_state = "normal"          # normal / reduced
        risk_actions: list[dict] = []  # 每次状态切换的记录
        DD_THRESHOLD = 10.0            # 减仓阈值
        DD_RECOVER   =  5.0            # 恢复阈值（死区，避免反复切）

        for di, d in enumerate(dates):
            if di + hold_days >= len(dates):
                continue
            buy_d = dates[di + 1]
            sell_d = dates[di + 1 + hold_days]

            try:
                r = _screen_for_date(d, stocks_all)
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

            # ── 当日选完股 → 重算资金曲线 → 检查回撤并切换 effective_top ──
            realized_pnl_pct = sum(t["return_pct"] for t in trades) / max(1, top_n)
            equity_running = 1.0 + realized_pnl_pct / 100.0
            peak_equity = max(peak_equity, equity_running)
            drawdown_pct = (peak_equity - equity_running) / peak_equity * 100.0 if peak_equity > 0 else 0.0
            today_dd_pct = drawdown_pct

            # 把回撤写到所有今日买入的 trade 上
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

    monthly = _compute_monthly(trades)
    overall = _compute_overall(trades)

    result = {
        "config": {
            "start": start, "end": end,
            "top_n": top_n, "hold_days": hold_days,
            "sell_mode": sell_mode, "sample": sample,
        },
        "summary": overall,
        "monthly": monthly,
        "trades_count": len(trades),
        "trade_dates_total": len(dates),
        "cycle_blocked_days": cycle_blocked_days,
        "skipped_no_pick_days": skipped_no_pick,
        # 铁律三.4：回撤风控
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
    return result


def _preload_calendars(trade_dates: list[str]) -> tuple[dict, dict]:
    """
    复用 v2 backtest lib 的情绪/主线日历缓存，节省时间。
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
        log.warning(f"v2 lib 预加载失败，走自建 fallback: {e}")
        # Fallback：每日期默认 phase=启动（中性偏乐观）
        for d in trade_dates:
            emotion_cal[d] = {
                "phase": "启动",
                "emotion_score": 50.0,
                "zt_count": 30,
                "max_streak": 3,
                "source": "fallback_neutral",
            }
            mainline_cal[d] = set()  # 无主线 → 走"无成份股放行"

    return emotion_cal, mainline_cal


# ═══════════════════════════════════════════════════
# 回测专属 monkey-patch 上下文（一次性安装，per run_backtest）
# ═══════════════════════════════════════════════════
_BT_CTX: dict[str, Any] = {"date": None, "daily_cache": None,
                          "emotion_cal": None, "mainline_cal": None}


def _install_backtest_patches() -> None:
    """一次性把 dl.fetch_daily + l2.screen 替换为使用 _BT_CTX 的回测版本。"""
    import tuixue_v3.data_layer as dl_mod
    import tuixue_v3.layer2_cycle_mainline as l2_mod

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


def _uninstall_backtest_patches() -> None:
    """还原 dl.fetch_daily + l2.screen。"""
    import tuixue_v3.data_layer as dl_mod
    import tuixue_v3.layer2_cycle_mainline as l2_mod
    if hasattr(dl_mod, "_orig_fetch_daily"):
        dl_mod.fetch_daily = dl_mod._orig_fetch_daily
    if hasattr(l2_mod, "_orig_screen"):
        l2_mod.screen = l2_mod._orig_screen


def _screen_for_date(date_str: str, stocks: list[tuple[str, str]]) -> dict:
    """单日选股：用 _BT_CTX 的预装值；stocks 已由调用方采样好。"""
    _BT_CTX["date"] = date_str
    return run_stock_screen(date_str=date_str, mode="backtest", stocks=stocks)


def _compute_monthly(trades: list[dict]) -> list[dict]:
    """按月聚合：月收益率 + 胜率 + 笔数"""
    if not trades:
        return []

    df = pd.DataFrame(trades)
    df["buy_date"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date"].dt.to_period("M").astype(str)

    rows = []
    for m, g in df.groupby("month"):
        wins = (g["return_pct"] > 0).sum()
        rows.append({
            "month": m,
            "trades": len(g),
            "wins": int(wins),
            "win_rate_pct": round(wins / len(g) * 100, 2) if len(g) else 0,
            "avg_return_pct": round(g["return_pct"].mean(), 2),
            "sum_return_pct": round(g["return_pct"].sum(), 2),
            "max_return_pct": round(g["return_pct"].max(), 2),
            "min_return_pct": round(g["return_pct"].min(), 2),
            # 等权月收益率：每日 top_n 等额，月末不轮转则等于 sum / top_n
            "monthly_return_pct": round(g["return_pct"].sum() / max(1, len(g) / max(1, len(g))), 2),
        })

    return rows


def _compute_overall(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate_pct": 0, "avg_return_pct": 0, "monthly_avg_return_pct": 0}

    df = pd.DataFrame(trades)
    wins = (df["return_pct"] > 0).sum()
    losses = (df["return_pct"] < 0).sum()
    win_sum = df.loc[df["return_pct"] > 0, "return_pct"].sum()
    loss_sum = abs(df.loc[df["return_pct"] < 0, "return_pct"].sum())
    profit_factor = round(win_sum / loss_sum, 2) if loss_sum > 0 else float("inf")

    # 月度聚合：算 monthly avg
    df["buy_date"] = pd.to_datetime(df["buy_date"])
    df["month"] = df["buy_date"].dt.to_period("M").astype(str)
    monthly_avg_per_month = df.groupby("month")["return_pct"].mean()
    monthly_avg_pct = round(monthly_avg_per_month.mean(), 2)

    # 最大回撤（资金曲线）
    equity = (1 + df["return_pct"] / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd = round(drawdown.min(), 2) if len(drawdown) else 0

    return {
        "trades": len(df),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate_pct": round(wins / len(df) * 100, 2),
        "avg_return_pct": round(df["return_pct"].mean(), 2),
        "avg_win_pct": round(df.loc[df["return_pct"] > 0, "return_pct"].mean(), 2) if wins else 0,
        "avg_loss_pct": round(df.loc[df["return_pct"] < 0, "return_pct"].mean(), 2) if losses else 0,
        "profit_factor": profit_factor,
        "monthly_avg_return_pct": monthly_avg_pct,
        "max_drawdown_pct": max_dd,
        "best_trade_pct": round(df["return_pct"].max(), 2),
        "worst_trade_pct": round(df["return_pct"].min(), 2),
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