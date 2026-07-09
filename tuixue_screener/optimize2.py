#!/usr/bin/env python3
"""
tuixue_screener/optimize2.py
精细化优化：
- Phase 1: 止盈/止损细颗粒度 + 允许 RR=2.0（更高止损）
- Phase 2: 仓位档位（10%/15%/20%/25%/30%）对月度收益的影响
- Phase 3: 加入"信号强度加权仓位"（盈亏比越高仓位越大）
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config as C
from backtest import fetch_all_a_codes, fetch_history_klines
from optimize import monthly_stats

log = logging.getLogger("optimize2")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / C.CACHE_DIR_NAME
REPORTS = ROOT / "reports"


def simulate_v2(codes: list[str], indicators: dict,
                trade_dates: list[str],
                target_pct: float, stop_pct: float,
                hold_days: int, top_n: int,
                min_rr: float = 2.0) -> dict:
    """细颗粒度模拟"""
    trades = []

    for i, t_date in enumerate(trade_dates[:-hold_days]):
        candidates = []

        for code, ind in indicators.items():
            idx = np.searchsorted(ind["dates"], np.datetime64(t_date))
            if idx >= len(ind["dates"]):
                continue
            if pd.Timestamp(ind["dates"][idx]).strftime("%Y-%m-%d") != t_date:
                idx = np.searchsorted(ind["dates"], np.datetime64(t_date), side="right") - 1
            if idx < 0:
                continue

            close_t = ind["closes"][idx]
            ma5_t = ind["ma5"][idx]
            ma10_t = ind["ma10"][idx]
            ma20_t = ind["ma20"][idx]

            if np.isnan(ma5_t) or np.isnan(ma10_t) or np.isnan(ma20_t):
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
            if rr < min_rr:
                continue

            change_pct = (close_t / ind["closes"][idx-1] - 1) * 100 if idx > 0 else 0
            score = rr * 10
            if 3 <= change_pct <= 8:
                score += 20
            if ma5_t > ma10_t:
                score += 5

            candidates.append({
                "code": code, "idx": idx, "entry": entry,
                "rr": rr, "score": score,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        picks = candidates[:top_n]
        if not picks:
            continue

        for pick in picks:
            code = pick["code"]
            ind = indicators[code]
            buy_idx = pick["idx"] + 1
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

                if idx_j >= buy_idx + 5:
                    closes_j = ind["closes"][buy_idx:idx_j+1]
                    if len(closes_j) >= 5:
                        ma5_j = closes_j[-5:].mean()
                        if ind["closes"][idx_j] < ma5_j:
                            sell_price = ind["closes"][idx_j]
                            sell_idx = idx_j
                            break

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
                "rr": pick["rr"],
                "ret_pct": round(ret_pct, 2),
                "month": str(ind["dates"][sell_idx])[:7],
            })

    return {"trades": trades, "params": {
        "target_pct": target_pct, "stop_pct": stop_pct,
        "hold_days": hold_days, "top_n": top_n, "min_rr": min_rr,
    }}


def monthly_with_position(trades: list[dict], position_pct: float, dynamic: bool = False) -> dict:
    """
    月度收益（含仓位）
    dynamic=True: 信号强（rr 高）仓位放大
    """
    if not trades:
        return {"months": [], "summary": {
            "total_trades": 0, "win_rate": 0,
            "total_return_pct": 0, "avg_monthly": 0,
            "best_month": 0, "worst_month": 0,
            "win_months": 0, "total_months": 0,
            "position_used": position_pct,
        }}

    df = pd.DataFrame(trades)

    if dynamic:
        # 信号强度加权仓位（rr >= 4 → 1.5x, rr >= 3 → 1.2x, rr >= 2.5 → 1.0x）
        df["position_mult"] = df["rr"].apply(lambda r: 1.5 if r >= 4 else (1.2 if r >= 3 else 1.0))
        df["position_pct"] = df["position_mult"] * position_pct
    else:
        df["position_pct"] = position_pct

    df["position_return"] = df["ret_pct"] * df["position_pct"] / 100

    monthly = df.groupby("month").agg(
        trade_count=("ret_pct", "count"),
        win_rate=("ret_pct", lambda x: (x > 0).sum() / len(x) * 100),
        avg_return=("ret_pct", "mean"),
        total_return=("position_return", "sum"),
        avg_position=("position_pct", "mean"),
    ).round(3)

    total_return = (1 + monthly["total_return"] / 100).prod() - 1
    win_months = sum(1 for r in monthly["total_return"] if r > 0)

    return {
        "months": monthly.reset_index().to_dict("records"),
        "summary": {
            "total_trades": len(trades),
            "win_rate": round((df["ret_pct"] > 0).sum() / len(df) * 100, 2),
            "total_return_pct": round(total_return * 100, 2),
            "avg_monthly": round(monthly["total_return"].mean(), 2),
            "best_month": round(monthly["total_return"].max(), 2),
            "worst_month": round(monthly["total_return"].min(), 2),
            "win_months": win_months,
            "total_months": len(monthly),
            "avg_position": round(df["position_pct"].mean(), 3),
        }
    }


def run_optimization2():
    print("=" * 60)
    print("🔧 退学战法精细化优化")
    print("=" * 60)

    all_codes = fetch_all_a_codes()
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

    codes = list(history.keys())
    print(f"交易日: {len(trade_dates)}")

    # 预计算指标（无 MA60 严格度，使用 MA5>MA10>MA20）
    print("\n[预计算] 一次性算所有技术指标...")
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
        if n < 25:
            continue
        ma5 = np.full(n, np.nan); ma10 = np.full(n, np.nan); ma20 = np.full(n, np.nan)
        cumsum = np.cumsum(closes)
        for i in range(4, n): ma5[i] = (cumsum[i] - cumsum[i-5]) / 5 if i >= 5 else (cumsum[i] / (i+1))
        for i in range(9, n): ma10[i] = (cumsum[i] - cumsum[i-10]) / 10
        for i in range(19, n): ma20[i] = (cumsum[i] - cumsum[i-20]) / 20
        gain_20 = np.full(n, np.nan)
        for i in range(20, n): gain_20[i] = (closes[i] / closes[i-20] - 1) * 100
        vol_cumsum = np.cumsum(volumes)
        vol_5 = np.full(n, np.nan); vol_15 = np.full(n, np.nan)
        for i in range(4, n): vol_5[i] = (vol_cumsum[i] - vol_cumsum[i-5]) / 5
        for i in range(14, n): vol_15[i] = (vol_cumsum[i] - vol_cumsum[i-15]) / 15
        indicators[code] = {
            "dates": dates, "closes": closes, "opens": opens,
            "highs": highs, "volumes": volumes, "amounts": amounts,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gain_20": gain_20, "vol_5": vol_5, "vol_15": vol_15,
        }
    print(f"  完成: {len(indicators)} 只票, {time.time()-t0:.1f}s")

    # Phase 1: 细颗粒度 (target/stop/hold/min_rr)
    print("\n[Phase 1] 细颗粒度参数搜索（无 MA60 要求）")
    print("─" * 60)
    configs_phase1 = []
    for target_pct in [0.06, 0.08, 0.10, 0.12, 0.15]:
        for stop_pct in [0.02, 0.03, 0.04, 0.05]:
            for hold_days in [3, 5, 7, 10]:
                for min_rr in [2.0, 2.5, 3.0]:
                    configs_phase1.append({
                        "target_pct": target_pct, "stop_pct": stop_pct,
                        "hold_days": hold_days, "min_rr": min_rr, "top_n": 5,
                    })

    print(f"  总配置数: {len(configs_phase1)}")

    results_p1 = []
    for i, cfg in enumerate(configs_phase1):
        bt = simulate_v2(codes, indicators, trade_dates, **cfg)
        if bt["trades"]:
            ms = monthly_stats(bt["trades"], position_pct=0.20)
            ms["summary"]["min_rr"] = cfg["min_rr"]
            ms["summary"]["target_pct"] = cfg["target_pct"]
            ms["summary"]["stop_pct"] = cfg["stop_pct"]
            ms["summary"]["hold_days"] = cfg["hold_days"]
            results_p1.append(ms["summary"])
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(configs_phase1)}")

    # 排序：总收益 × 月度胜率 × 1/(最大回撤+1)
    results_p1.sort(key=lambda x: x.get("total_return_pct", 0), reverse=True)

    print(f"\n  Top 15 配置：")
    print(f"  {'T%':>4} {'S%':>4} {'H':>3} {'minRR':>5} {'笔数':>5} {'胜率':>5} "
          f"{'总收益%':>8} {'月均%':>7} {'最佳月%':>8} {'最差月%':>8}")
    for r in results_p1[:15]:
        print(f"  {r.get('target_pct', 0)*100:>4.0f} {r.get('stop_pct', 0)*100:>4.0f} "
              f"{r.get('hold_days', 0):>3} {r.get('min_rr', 0):>5.1f} "
              f"{r.get('total_trades', 0):>5} {r.get('win_rate', 0):>5.1f} "
              f"{r.get('total_return_pct', 0):>8.2f} {r.get('avg_monthly', 0):>7.2f} "
              f"{r.get('best_month', 0):>8.2f} {r.get('worst_month', 0):>8.2f}")

    # Phase 2: 仓位档位
    print("\n[Phase 2] 仓位档位测试（用 Phase1 最佳参数）")
    print("─" * 60)
    best = results_p1[0] if results_p1 else None
    if not best:
        print("  无有效配置")
        return

    print(f"  基础参数: T={best['target_pct']*100:.0f}% S={best['stop_pct']*100:.0f}% "
          f"H={best['hold_days']}d minRR={best['min_rr']}")

    bt_base = simulate_v2(codes, indicators, trade_dates,
                          target_pct=best["target_pct"], stop_pct=best["stop_pct"],
                          hold_days=best["hold_days"], top_n=5, min_rr=best["min_rr"])

    positions_results = []
    for pos in [0.10, 0.15, 0.20, 0.25, 0.30]:
        ms = monthly_with_position(bt_base["trades"], pos, dynamic=False)
        positions_results.append({**ms["summary"], "position_pct": pos, "dynamic": False})
        print(f"  仓位 {pos*100:.0f}%: 总收益 {ms['summary']['total_return_pct']:.2f}%, "
              f"月均 {ms['summary']['avg_monthly']:.2f}%")

    # 动态仓位
    print("\n  动态仓位（信号强度加权）:")
    for pos in [0.15, 0.20, 0.25]:
        ms = monthly_with_position(bt_base["trades"], pos, dynamic=True)
        positions_results.append({**ms["summary"], "position_pct": pos, "dynamic": True})
        print(f"  基础 {pos*100:.0f}% × 动态加权: 总收益 {ms['summary']['total_return_pct']:.2f}%, "
              f"月均 {ms['summary']['avg_monthly']:.2f}%, 平均仓位 {ms['summary']['avg_position']*100:.1f}%")

    # Phase 3: top_n 优化
    print("\n[Phase 3] top_n 优化")
    print("─" * 60)
    topn_results = []
    for top_n in [3, 5, 8, 10]:
        bt = simulate_v2(codes, indicators, trade_dates,
                         target_pct=best["target_pct"], stop_pct=best["stop_pct"],
                         hold_days=best["hold_days"], top_n=top_n, min_rr=best["min_rr"])
        ms = monthly_stats(bt["trades"], position_pct=0.20)
        ms["summary"]["top_n"] = top_n
        topn_results.append(ms["summary"])
        print(f"  top_n={top_n}: 笔数 {ms['summary']['total_trades']}, "
              f"总收益 {ms['summary']['total_return_pct']}%, 月均 {ms['summary']['avg_monthly']}%")

    # 汇总最佳
    best_pos = max(positions_results, key=lambda x: x["total_return_pct"])
    best_topn = max(topn_results, key=lambda x: x["total_return_pct"])

    print("\n" + "=" * 60)
    print("🏆 最终最佳组合")
    print("=" * 60)
    print(f"  T={best['target_pct']*100:.0f}% S={best['stop_pct']*100:.0f}% "
          f"H={best['hold_days']}d minRR={best['min_rr']} top_n={best_topn['top_n']} "
          f"position={best_pos['position_pct']*100:.0f}% "
          f"{'(动态)' if best_pos.get('dynamic') else ''}")
    print(f"  月度明细：")
    bt_final = simulate_v2(codes, indicators, trade_dates,
                           target_pct=best["target_pct"], stop_pct=best["stop_pct"],
                           hold_days=best["hold_days"], top_n=best_topn["top_n"], min_rr=best["min_rr"])
    ms_final = monthly_with_position(bt_final["trades"],
                                      best_pos["position_pct"], best_pos.get("dynamic", False))
    print(f"  {'月份':<10} {'笔数':>5} {'胜率%':>7} {'均收益%':>9} {'月度收益%':>11}")
    for m in ms_final["months"]:
        print(f"  {m['month']:<10} {m['trade_count']:>5} {m['win_rate']:>7.2f} "
              f"{m['avg_return']:>9.2f} {m['total_return']:>11.2f}")
    print(f"\n  累计收益: {ms_final['summary']['total_return_pct']}%")
    print(f"  月均: {ms_final['summary']['avg_monthly']}%")
    print(f"  胜率: {ms_final['summary']['win_rate']}%")

    output = {
        "phase1_top": results_p1[:20],
        "phase2_positions": positions_results,
        "phase3_topn": topn_results,
        "best_config": best,
        "best_position": best_pos,
        "best_topn": best_topn,
        "best_monthly": ms_final,
        "best_trades": bt_final,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    output_path = REPORTS / "optimize_v2_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    print(f"\n✅ 报告: {output_path}")


if __name__ == "__main__":
    run_optimization2()