#!/usr/bin/env python3
"""
tuixue_screener/optimize_v3.py
v2 引擎上的精细化优化：
- hold_days: 5 / 7 / 10
- target_pct: 0.08 / 0.10 / 0.15
- stop_pct: 0.03 / 0.05 / 0.07
- min_rr: 2.0 / 2.5 / 3.0
- position_pct: 0.15 / 0.20 / 0.25

每组配置跑一次完整回测，按月均收益 + 胜率 + 回撤综合排序。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_v2 as bv2
import config as C

log = logging.getLogger("opt_v3")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"


def score(result: dict, monthly: dict) -> float:
    """综合打分：月均 * 胜率 / (1 + 回撤)"""
    summary = monthly.get("summary", {})
    avg_m = summary.get("avg_monthly_return_pct", 0)
    wr = (sum(1 for t in result["trades"] if t["ret_pct"] > 0) /
          max(1, len(result["trades"]))) * 100
    worst = abs(summary.get("worst_month_pct", 0))
    win_m_rate = summary.get("win_month_rate", 0)
    # 权重：月均 50% + 胜率 20% + 盈利月数 20% - 最差月 10%
    return avg_m * 5 + wr * 0.2 + win_m_rate * 0.2 - worst * 0.1


def run_one(hold_days, target_pct, stop_pct, min_rr, position_pct, max_pos=5, sample_n=300, _shared=None):
    """单次回测（共享数据加速）"""
    result = bv2.backtest_v2(
        start="2025-07-01", end="2026-06-30",
        hold_days=hold_days, top_n=5,
        sample_n=sample_n, initial_cash=100000,
        max_positions=max_pos, position_pct=position_pct,
        target_pct=target_pct, stop_pct=stop_pct,
        min_rr=min_rr, use_ma60=False,
        _shared_data=_shared,
    )
    monthly = bv2.monthly_stats(result["daily_log"])
    return result, monthly


def main():
    print("=" * 60)
    print("🔧 v3 精细化优化（多组配置 + 共享数据加速）")
    print("=" * 60)

    # 预加载共享数据（指数 + 全 A 代码 + 历史 K 线）
    print("\n[预加载] 指数 + 全 A 代码...")
    spot, _ = bv2.ds.get_spot()
    all_codes = []
    for r in (spot or []):
        code = str(r.get("f12", "")).zfill(6)
        if code.startswith(("60", "603", "605", "000", "002", "003")):
            name = r.get("f14", "")
            if not any(k in name for k in C.EXCLUDE_KEYWORDS):
                all_codes.append(code)

    import random
    random.seed(42)
    sample_n = 300  # 优化用小样本快扫
    sample = random.sample(all_codes, sample_n) if len(all_codes) > sample_n else all_codes

    from backtest import fetch_history_klines
    from datetime import timedelta
    pre_start = (pd.to_datetime("2025-07-01") - timedelta(days=120)).strftime("%Y-%m-%d")
    history = fetch_history_klines(sample, pre_start, "2026-06-30")
    print(f"  历史: {len(history)} 只")

    klines_raw = bv2.fetch_index_kline("1.000300", 500)
    rows = []
    for line in (klines_raw or []):
        parts = line.split(",")
        if len(parts) < 11:
            continue
        rows.append({
            "date": pd.to_datetime(parts[0]),
            "open": float(parts[1]), "close": float(parts[2]),
            "high": float(parts[3]), "low": float(parts[4]),
            "volume": float(parts[5]), "amount": float(parts[6]),
            "change_pct": float(parts[8]),
        })
    index_df = pd.DataFrame(rows)
    index_df["ma20"] = index_df["close"].rolling(20).mean()
    index_df["ma60"] = index_df["close"].rolling(60).mean()
    index_df["ma20_slope"] = (index_df["ma20"] - index_df["ma20"].shift(5)) / 5

    # 计算 trade_dates 和 breadth（共享）
    start = "2025-07-01"
    end = "2026-06-30"
    all_dates = set()
    for df in history.values():
        if not df.empty:
            mask = (df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))
            all_dates.update(df.loc[mask, "date"].dt.strftime("%Y-%m-%d").tolist())
    idx_dates = index_df[(index_df["date"] >= pd.to_datetime(start)) &
                         (index_df["date"] <= pd.to_datetime(end))]["date"].dt.strftime("%Y-%m-%d").tolist()
    trade_dates = sorted(set(all_dates) & set(idx_dates))
    breadth = bv2.compute_daily_breadth(history, trade_dates)

    shared_data = {
        "sample": sample_n, "start": start, "end": end,
        "history": history, "index_df": index_df,
        "trade_dates": trade_dates, "breadth": breadth,
    }
    print(f"  交易日: {len(trade_dates)}")

    # 预计算一次指标（共享给所有 config，省去每组重算）
    print(f"  预计算指标中...", end="", flush=True)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _precompute(code, df):
        s = df["close"]
        ma5 = s.rolling(5, min_periods=5).mean().values
        ma10 = s.rolling(10, min_periods=10).mean().values
        ma20 = s.rolling(20, min_periods=20).mean().values
        gain_20 = s.pct_change(20).values * 100
        v = df["volume"] if "volume" in df.columns else pd.Series(np.zeros(len(df)))
        vol_5 = v.rolling(5, min_periods=5).mean().values
        vol_15 = v.rolling(15, min_periods=15).mean().values
        return code, {
            "dates": df["date"].values, "closes": df["close"].values,
            "opens": df["open"].values, "highs": df["high"].values,
            "volumes": v.values,
            "amounts": df["amount"].values if "amount" in df.columns else np.zeros(len(df)),
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "gain_20": gain_20, "vol_5": vol_5, "vol_15": vol_15,
            "date_to_idx": {str(d)[:10]: i for i, d in enumerate(df["date"].values)},
        }

    indicators = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_precompute, c, df): c for c, df in history.items() if len(df) >= 25}
        for fut in as_completed(futs):
            code, ind = fut.result()
            if ind is not None:
                indicators[code] = ind
    print(f" {len(indicators)} 只")

    shared_data["indicators"] = indicators
    print(f"  共享数据准备完成")

    configs = []
    for hold_days in [7, 10, 14]:
        for target_pct in [0.10, 0.15, 0.20]:
            for stop_pct in [0.03, 0.05, 0.07]:
                # 仅跑 RR ≤ target/stop 的可行配置
                rr = target_pct / stop_pct
                for min_rr in [2.0, 2.5]:
                    if min_rr > rr + 0.1:
                        continue  # 跳过不可能达成 RR 的组合
                    for position_pct in [0.15, 0.20, 0.25]:
                        configs.append({
                            "hold_days": hold_days,
                            "target_pct": target_pct,
                            "stop_pct": stop_pct,
                            "min_rr": min_rr,
                            "position_pct": position_pct,
                        })
    print(f"总配置数: {len(configs)}（跳过不可能达 RR 的组合）")
    print(f"预估耗时: {len(configs) * 5 / 60:.0f} 分钟（v2 加速后）")
    print()

    results = []
    t_start = time.time()
    for i, cfg in enumerate(configs):
        result, monthly = run_one(**cfg, _shared=shared_data)
        s = score(result, monthly)
        summary = monthly["summary"]
        results.append({
            **cfg,
            "score": round(s, 2),
            "total_return_pct": summary["total_return_pct"],
            "avg_monthly": summary["avg_monthly_return_pct"],
            "best_month": summary["best_month_pct"],
            "worst_month": summary["worst_month_pct"],
            "win_months": summary["win_months"],
            "total_trades": len(result["trades"]),
            "win_rate": round(sum(1 for t in result["trades"] if t["ret_pct"] > 0) /
                              max(1, len(result["trades"])) * 100, 2),
            "max_drawdown": summary.get("max_drawdown_pct", 0),
        })
        elapsed = time.time() - t_start
        avg_time = elapsed / (i + 1)
        eta = avg_time * (len(configs) - i - 1)
        r = results[-1]
        print(f"  [{i+1:>3}/{len(configs)}] "
              f"H={cfg['hold_days']}d T={cfg['target_pct']*100:.0f}% "
              f"S={cfg['stop_pct']*100:.0f}% RR≥{cfg['min_rr']} P={cfg['position_pct']*100:.0f}% "
              f"→ 总{r['total_return_pct']:>+6.2f}% 月均{r['avg_monthly']:>+5.2f}% "
              f"胜{r['win_rate']:.0f}% 月胜{r['win_months']}/12 "
              f"(ETA: {eta/60:.0f}m)")

    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 60)
    print("🏆 Top 10 配置（按综合分）")
    print("=" * 60)
    print(f"{'排名':>3} {'总分':>6} {'H':>2} {'T%':>4} {'S%':>4} {'RR':>4} {'P%':>4} "
          f"{'总收益%':>8} {'月均%':>7} {'最佳月%':>8} {'最差月%':>8} {'胜率%':>6} {'盈利月':>7}")
    for i, r in enumerate(results[:10], 1):
        print(f"{i:>3} {r['score']:>6.2f} {r['hold_days']:>2} {r['target_pct']*100:>3.0f} "
              f"{r['stop_pct']*100:>3.0f} {r['min_rr']:>3.1f} {r['position_pct']*100:>3.0f} "
              f"{r['total_return_pct']:>8.2f} {r['avg_monthly']:>7.2f} "
              f"{r['best_month']:>8.2f} {r['worst_month']:>8.2f} "
              f"{r['win_rate']:>6.1f} {r['win_months']:>4}/12")

    # 保存
    output = {
        "all_results": results,
        "top10": results[:10],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    output_path = REPORTS / "optimize_v3_report.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n✅ 报告: {output_path}")

    # 用最佳参数跑详细报告
    best = results[0]
    print(f"\n📊 最佳参数详细月度:")
    print(f"  H={best['hold_days']}d T={best['target_pct']*100:.0f}% S={best['stop_pct']*100:.0f}% "
          f"RR≥{best['min_rr']} P={best['position_pct']*100:.0f}%")

    result, monthly = run_one(best["hold_days"], best["target_pct"],
                              best["stop_pct"], best["min_rr"], best["position_pct"],
                              _shared=shared_data)
    bv2.print_report(result, monthly)

    final = {
        "best_config": best,
        "result": result,
        "monthly": monthly,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (REPORTS / "backtest_v3_best.json").write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()