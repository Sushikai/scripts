#!/usr/bin/env python3
"""
tuixue_screener/optimize.py
参数优化器：对 hold_days、target_pct、stop_pct、sample_size、top_n 等参数
进行网格搜索，找到最优组合。

不做暴力穷举（会触限流），而是分阶段：
1. Phase 1: 优化 hold_days / target / stop（固定 top_n=3, sample=300）
2. Phase 2: 优化 top_n（1/3/5）
3. Phase 3: 优化 MA 严格度（MA5>MA10>MA20>MA60 vs MA5>MA10>MA20）
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import data_source as ds
import pipeline as P
import config as C
from backtest import fetch_all_a_codes, fetch_history_klines

log = logging.getLogger("optimize")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / C.CACHE_DIR_NAME
REPORTS = ROOT / "reports"


def simulate(codes: list[str], history: dict[str, pd.DataFrame],
             trade_dates: list[str],
             target_pct: float, stop_pct: float,
             hold_days: int, top_n: int,
             require_ma60: bool = True,
             indicators: dict | None = None) -> dict:
    """单次模拟（使用 numpy 加速）"""
    import numpy as np

    # 预计算：每只票每日的技术指标（仅算一次）
    if indicators is None:
        print(f"    预计算指标...", end="", flush=True)
        t0 = time.time()
        indicators = {}
        for code in codes:
            df = history.get(code)
            if df is None or df.empty:
                continue
            closes = df["close"].values
            volumes = df["volume"].values if "volume" in df.columns else np.zeros_like(closes)
            amounts = df["amount"].values if "amount" in df.columns else np.zeros_like(closes)
            highs = df["high"].values
            dates = df["date"].values
            opens = df["open"].values
            n = len(closes)

            if n < (65 if require_ma60 else 25):
                continue

            # 滚动 MA
            ma5 = np.full(n, np.nan)
            ma10 = np.full(n, np.nan)
            ma20 = np.full(n, np.nan)
            ma60 = np.full(n, np.nan)
            cumsum = np.cumsum(closes)
            for i in range(4, n):
                ma5[i] = (cumsum[i] - cumsum[i-5]) / 5 if i >= 5 else (cumsum[i] / (i+1))
            for i in range(9, n):
                ma10[i] = (cumsum[i] - cumsum[i-10]) / 10
            for i in range(19, n):
                ma20[i] = (cumsum[i] - cumsum[i-20]) / 20
            for i in range(59, n):
                ma60[i] = (cumsum[i] - cumsum[i-60]) / 60

            # 20 日累计涨幅
            gain_20 = np.full(n, np.nan)
            for i in range(20, n):
                gain_20[i] = (closes[i] / closes[i-20] - 1) * 100

            # 5日均量 / 15日均量
            vol_cumsum = np.cumsum(volumes)
            vol_5 = np.full(n, np.nan)
            vol_15 = np.full(n, np.nan)
            for i in range(4, n):
                vol_5[i] = (vol_cumsum[i] - vol_cumsum[i-5]) / 5
            for i in range(14, n):
                vol_15[i] = (vol_cumsum[i] - vol_cumsum[i-15]) / 15

            indicators[code] = {
                "dates": dates, "closes": closes, "opens": opens,
                "highs": highs, "volumes": volumes, "amounts": amounts,
                "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
                "gain_20": gain_20, "vol_5": vol_5, "vol_15": vol_15,
            }
        print(f" {time.time()-t0:.1f}s, {len(indicators)} 只票")

    trades = []
    rejected_dates = 0

    for i, t_date in enumerate(trade_dates[:-hold_days]):
        candidates = []

        for code, ind in indicators.items():
            # 用 numpy searchsorted 找日期索引
            idx = np.searchsorted(ind["dates"], np.datetime64(t_date))
            if idx >= len(ind["dates"]):
                continue
            # 确认是当天或之前最后一条
            if pd.Timestamp(ind["dates"][idx]).strftime("%Y-%m-%d") != t_date:
                # 找最近的 ≤ t_date 的索引
                idx = np.searchsorted(ind["dates"], np.datetime64(t_date), side="right") - 1
            if idx < 0:
                continue

            close_t = ind["closes"][idx]
            ma5_t = ind["ma5"][idx]
            ma10_t = ind["ma10"][idx]
            ma20_t = ind["ma20"][idx]
            ma60_t = ind["ma60"][idx]

            if np.isnan(ma5_t) or np.isnan(ma10_t):
                continue

            # MA 严格度
            if require_ma60:
                if np.isnan(ma60_t):
                    continue
                if not (ma5_t > ma10_t > ma20_t > ma60_t):
                    continue
            else:
                if np.isnan(ma20_t):
                    continue
                if not (ma5_t > ma10_t > ma20_t):
                    continue

            if close_t < ma5_t:
                continue

            gain_20_t = ind["gain_20"][idx]
            if not np.isnan(gain_20_t) and gain_20_t >= C.PHASE_GAIN_MAX:
                continue

            amount_t = ind["amounts"][idx]
            if amount_t > 0 and amount_t < C.MIN_TURNOVER_YUAN:
                continue

            vol_5_t = ind["vol_5"][idx]
            vol_15_t = ind["vol_15"][idx]
            if not np.isnan(vol_5_t) and not np.isnan(vol_15_t):
                if vol_5_t > vol_15_t * 3:
                    continue

            entry = close_t
            target = entry * (1 + target_pct)
            stop = entry * (1 - stop_pct)
            upside = target - entry
            downside = entry - stop
            if downside <= 0:
                continue
            rr = upside / downside
            if rr < C.MIN_RR_RATIO:
                continue

            # 多因子打分
            score = rr * 10
            change_pct = (close_t / ind["closes"][idx-1] - 1) * 100 if idx > 0 else 0
            if 3 <= change_pct <= 8:
                score += 20
            if ma5_t > ma10_t:
                score += 5

            candidates.append({
                "code": code,
                "idx": idx,
                "entry": entry,
                "rr": rr,
                "score": score,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        picks = candidates[:top_n]

        if not picks:
            rejected_dates += 1
            continue

        # 模拟交易
        for pick in picks:
            code = pick["code"]
            ind = indicators[code]
            buy_idx = pick["idx"] + 1  # t+1 买入
            if buy_idx >= len(ind["dates"]):
                continue

            buy_price = ind["opens"][buy_idx]
            if np.isnan(buy_price) or buy_price <= 0:
                continue

            sell_price = None
            sell_idx = None

            for j in range(hold_days):
                idx_j = buy_idx + j
                if idx_j >= len(ind["dates"]):
                    break

                # 移动止盈
                if j >= 2:
                    high_so_far = ind["highs"][buy_idx:idx_j+1].max()
                    if high_so_far > 0:
                        profit_pct = (high_so_far / buy_price - 1) * 100
                        if profit_pct >= C.TRAILING_ACTIVATION_PCT:
                            drawdown = (high_so_far - ind["closes"][idx_j]) / high_so_far * 100
                            if drawdown >= C.TRAILING_PULLBACK_PCT:
                                sell_price = ind["closes"][idx_j]
                                sell_idx = idx_j
                                break

                # MA5 止损
                if idx_j >= buy_idx + 5:
                    closes_j = ind["closes"][buy_idx:idx_j+1]
                    if len(closes_j) >= 5:
                        ma5_j = closes_j[-5:].mean()
                        if ind["closes"][idx_j] < ma5_j:
                            sell_price = ind["closes"][idx_j]
                            sell_idx = idx_j
                            break

                # 硬止损
                if ind["closes"][idx_j] < buy_price * (1 - stop_pct):
                    sell_price = ind["closes"][idx_j]
                    sell_idx = idx_j
                    break

            if sell_price is None:
                sell_idx = min(buy_idx + hold_days - 1, len(ind["dates"]) - 1)
                sell_price = ind["closes"][sell_idx]

            ret_pct = (sell_price / buy_price - 1) * 100
            trades.append({
                "code": code,
                "buy_date": str(ind["dates"][buy_idx])[:10],
                "sell_date": str(ind["dates"][sell_idx])[:10],
                "buy_price": round(float(buy_price), 2),
                "sell_price": round(float(sell_price), 2),
                "ret_pct": round(ret_pct, 2),
                "month": str(ind["dates"][sell_idx])[:7],
            })

    return {
        "trades": trades,
        "rejected_dates": rejected_dates,
        "trade_dates_count": len(trade_dates),
        "params": {
            "target_pct": target_pct, "stop_pct": stop_pct,
            "hold_days": hold_days, "top_n": top_n,
            "require_ma60": require_ma60,
        },
    }


def monthly_stats(trades: list[dict], position_pct: float = 0.20) -> dict:
    if not trades:
        return {"months": [], "summary": {
            "total_trades": 0, "win_rate": 0,
            "total_return_pct": 0, "avg_monthly": 0,
            "best_month": 0, "worst_month": 0,
        }}
    df = pd.DataFrame(trades)
    df["position_return"] = df["ret_pct"] * position_pct / 100

    monthly = df.groupby("month").agg(
        trade_count=("ret_pct", "count"),
        win_rate=("ret_pct", lambda x: (x > 0).sum() / len(x) * 100),
        avg_return=("ret_pct", "mean"),
        total_return=("position_return", "sum"),
    ).round(2)

    total_return = (1 + monthly["total_return"] / 100).prod() - 1
    win_months = sum(1 for r in monthly["total_return"] if r > 0)

    summary = {
        "total_trades": len(trades),
        "win_rate": round((df["ret_pct"] > 0).sum() / len(df) * 100, 2),
        "total_return_pct": round(total_return * 100, 2),
        "avg_monthly": round(monthly["total_return"].mean(), 2),
        "best_month": round(monthly["total_return"].max(), 2),
        "worst_month": round(monthly["total_return"].min(), 2),
        "win_months": win_months,
        "total_months": len(monthly),
    }
    return {"months": monthly.reset_index().to_dict("records"), "summary": summary}


def run_optimization():
    """主优化流程"""
    print("=" * 60)
    print("🔧 退学战法参数优化")
    print("=" * 60)

    # 准备数据
    all_codes = fetch_all_a_codes()
    print(f"沪深主板票池: {len(all_codes)} 只")
    import random
    random.seed(42)
    sample = random.sample(all_codes, 500)

    pre_start = "2025-03-01"
    end = "2026-06-30"
    history = fetch_history_klines(sample, pre_start, end)
    print(f"历史数据: {len(history)} 只")

    all_dates = set()
    for df in history.values():
        if not df.empty:
            mask = (df["date"] >= pd.to_datetime("2025-07-01")) & (df["date"] <= pd.to_datetime(end))
            all_dates.update(df.loc[mask, "date"].dt.strftime("%Y-%m-%d").tolist())
    trade_dates = sorted(all_dates)
    print(f"交易日: {len(trade_dates)}")

    codes = list(history.keys())

    # 预计算一次指标（共享给所有 config）
    print("\n[预计算] 一次性算所有技术指标...")
    import numpy as np
    indicators = {}
    t0 = time.time()
    for code in codes:
        df = history.get(code)
        if df is None or df.empty:
            continue
        closes = df["close"].values
        volumes = df["volume"].values if "volume" in df.columns else np.zeros_like(closes)
        amounts = df["amount"].values if "amount" in df.columns else np.zeros_like(closes)
        highs = df["high"].values
        dates = df["date"].values
        opens = df["open"].values
        n = len(closes)
        if n < 65:
            continue
        ma5 = np.full(n, np.nan); ma10 = np.full(n, np.nan)
        ma20 = np.full(n, np.nan); ma60 = np.full(n, np.nan)
        cumsum = np.cumsum(closes)
        for i in range(4, n):
            ma5[i] = (cumsum[i] - cumsum[i-5]) / 5 if i >= 5 else (cumsum[i] / (i+1))
        for i in range(9, n): ma10[i] = (cumsum[i] - cumsum[i-10]) / 10
        for i in range(19, n): ma20[i] = (cumsum[i] - cumsum[i-20]) / 20
        for i in range(59, n): ma60[i] = (cumsum[i] - cumsum[i-60]) / 60
        gain_20 = np.full(n, np.nan)
        for i in range(20, n): gain_20[i] = (closes[i] / closes[i-20] - 1) * 100
        vol_cumsum = np.cumsum(volumes)
        vol_5 = np.full(n, np.nan); vol_15 = np.full(n, np.nan)
        for i in range(4, n): vol_5[i] = (vol_cumsum[i] - vol_cumsum[i-5]) / 5
        for i in range(14, n): vol_15[i] = (vol_cumsum[i] - vol_cumsum[i-15]) / 15
        indicators[code] = {
            "dates": dates, "closes": closes, "opens": opens,
            "highs": highs, "volumes": volumes, "amounts": amounts,
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "gain_20": gain_20, "vol_5": vol_5, "vol_15": vol_15,
        }
    print(f"  完成: {len(indicators)} 只票, {time.time()-t0:.1f}s")

    # Phase 1: target/stop/hold
    print("\n[Phase 1] 优化止盈/止损/持仓")
    print("─" * 60)
    configs_phase1 = []
    for target_pct in [0.08, 0.10, 0.12]:
        for stop_pct in [0.03, 0.05, 0.07]:
            for hold_days in [3, 5, 7]:
                configs_phase1.append({
                    "target_pct": target_pct,
                    "stop_pct": stop_pct,
                    "hold_days": hold_days,
                    "top_n": 3,
                    "require_ma60": True,
                })

    results_p1 = []
    for cfg in configs_phase1:
        bt = simulate(codes, history, trade_dates, indicators=indicators, **cfg)
        ms = monthly_stats(bt["trades"])
        results_p1.append({**cfg, **ms["summary"]})
        print(f"  T={cfg['target_pct']*100:.0f}% S={cfg['stop_pct']*100:.0f}% H={cfg['hold_days']}d "
              f"→ 总收益 {ms['summary']['total_return_pct']}%, 月均 {ms['summary']['avg_monthly']}%, "
              f"胜率 {ms['summary']['win_rate']}%")

    # Phase 2: top_n 优化
    print("\n[Phase 2] 优化每日选股数量（用 Phase1 最佳参数）")
    print("─" * 60)
    best_p1 = max(results_p1, key=lambda x: x["total_return_pct"])
    print(f"  最佳参数: {best_p1}")

    configs_phase2 = []
    for top_n in [1, 2, 3, 5, 8]:
        configs_phase2.append({
            **{k: v for k, v in best_p1.items() if k in ("target_pct", "stop_pct", "hold_days", "require_ma60")},
            "top_n": top_n,
        })

    results_p2 = []
    for cfg in configs_phase2:
        bt = simulate(codes, history, trade_dates, indicators=indicators, **cfg)
        ms = monthly_stats(bt["trades"])
        results_p2.append({**cfg, **ms["summary"]})
        print(f"  top_n={cfg['top_n']} → 总收益 {ms['summary']['total_return_pct']}%, 月均 {ms['summary']['avg_monthly']}%")

    # Phase 3: MA 严格度
    print("\n[Phase 3] 优化 MA 严格度")
    print("─" * 60)
    best_p2 = max(results_p2, key=lambda x: x["total_return_pct"])
    print(f"  最佳 top_n: {best_p2}")

    configs_phase3 = []
    for ma60 in [True, False]:
        configs_phase3.append({
            **{k: v for k, v in best_p2.items() if k in ("target_pct", "stop_pct", "hold_days", "top_n")},
            "require_ma60": ma60,
        })

    results_p3 = []
    for cfg in configs_phase3:
        bt = simulate(codes, history, trade_dates, indicators=indicators, **cfg)
        ms = monthly_stats(bt["trades"])
        results_p3.append({**cfg, **ms["summary"]})
        print(f"  MA60={cfg['require_ma60']} → 总收益 {ms['summary']['total_return_pct']}%, 月均 {ms['summary']['avg_monthly']}%")

    # 保存最佳结果
    print("\n" + "=" * 60)
    print("🏆 最佳参数组合")
    print("=" * 60)
    best = max(results_p3, key=lambda x: x["total_return_pct"])
    for k, v in best.items():
        print(f"  {k}: {v}")

    # 用最佳参数跑详细月度报告
    print("\n" + "─" * 60)
    print("📅 最佳参数下的月度明细")
    print("─" * 60)
    bt_final = simulate(codes, history, trade_dates, indicators=indicators,
                         target_pct=best["target_pct"], stop_pct=best["stop_pct"],
                         hold_days=best["hold_days"], top_n=best["top_n"],
                         require_ma60=best["require_ma60"])
    ms_final = monthly_stats(bt_final["trades"])
    print(f"{'月份':<10} {'笔数':>5} {'胜率%':>7} {'均收益%':>9} {'月度收益%':>11}")
    for m in ms_final["months"]:
        print(f"{m['month']:<10} {m['trade_count']:>5} {m['win_rate']:>7.2f} "
              f"{m['avg_return']:>9.2f} {m['total_return']:>11.2f}")

    # 保存
    output = {
        "phase1": results_p1,
        "phase2": results_p2,
        "phase3": results_p3,
        "best": best,
        "best_monthly": ms_final,
        "best_trades": bt_final,
        "generated_at": datetime.now().isoformat(),
    }
    output_path = REPORTS / "optimize_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print(f"\n✅ 优化报告: {output_path}")


if __name__ == "__main__":
    run_optimization()