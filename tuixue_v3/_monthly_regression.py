"""
退学 v3 · 逐月回归测试
对最优参数组合逐月回测, 输出 19 个月的 cum/wr/DD/PF/score 时间序列
"""
from __future__ import annotations
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib
dl = importlib.import_module("tuixue_v3.data_layer")
bs = importlib.import_module("tuixue_v3.web.backtest_screener")
opt = importlib.import_module("tuixue_v3.web.backtest_optimizer")

PARAM_GRID = opt.PARAM_GRID
run_for_frontend = bs.run_for_frontend
_prefetch_daily = bs._prefetch_daily
_is_main_board = bs._is_main_board

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "monthly_regression.json")
LOG_PATH = os.path.join(BASE, "monthly_regression.log")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

# ── 最优参数 ──
BEST = {
    "top_n": 4,
    "hold_days": 2,
    "breadth_min": 1500,
    "breadth_min_soft": 3500,
    "sector_hot_topn": 3,
    "sector_inflow_topn": 3,
    "late_high_discount": 0.7,
    "require_vwap_strict": False,
    "regime_adaptive": True,
}

# Baseline (原始默认)
BASELINE = {
    "top_n": 1,
    "hold_days": 3,
    "breadth_min": 0,
    "breadth_min_soft": 0,
    "sector_hot_topn": 0,
    "sector_inflow_topn": 0,
    "late_high_discount": 1.0,
    "require_vwap_strict": False,
    "regime_adaptive": False,
}

log("═" * 70)
log("尾盘战法 · 逐月回归测试")
log(f"最优参数: {BEST}")
log(f"Baseline: tn=1 hd=3 无过滤")

# ── 1. 获取完整交易日历 ──
full_raw = dl.fetch_trade_dates("20250101", "20260731") or []
all_dates = sorted({str(d).replace("-", "") for d in full_raw if str(d).replace("-", "").isdigit()})
log(f"完整交易日期: {len(all_dates)} 天 ({all_dates[0]}~{all_dates[-1]})")

# ── 2. 按月份分组 ──
from collections import OrderedDict
by_month: dict[str, list[str]] = OrderedDict()
for d in all_dates:
    ym = d[:6]
    if ym not in by_month:
        by_month[ym] = []
    by_month[ym].append(d)

# 过滤: 至少 10 个交易日的月
monthly = [(ym, days) for ym, days in by_month.items() if len(days) >= 10]
log(f"有效月份: {len(monthly)} 个 (>=10 交易日)")
for ym, days in monthly:
    log(f"  {ym}: {len(days)} 天 ({days[0]}~{days[-1]})")

# ── 3. 数据预热 ──
bs._fetch_5min_for_code = lambda code, start, end: []
log("[预热] 跳过 5min, 拉股票列表…")
all_stocks = dl.fetch_stock_list() or []
main_stocks = [(c, n) for c, n in all_stocks if _is_main_board(c)]
warmup_codes = [c for c, _ in main_stocks[:1000]]

log(f"[预热] 预下载 {len(warmup_codes)} 只日线…")
daily_cache = _prefetch_daily(warmup_codes, days=500, progress_cb=log)
log(f"[预热] 日线就绪 ({len(daily_cache)} 只)")
log("[预热] 板块缓存…")
opt._warmup_sectors(warmup_codes, progress_cb=log)

# ── 4. 逐月回测 ──
# 每个月的测试方法: patch fetch_trade_dates 为该月(含前序预热数据), 跑 "1月" 窗口
# 但 run_for_frontend 内部会取 period_days_map["1月"]=21 天从 end 往前数
# 所以把 end 设为本月最后一天, 在前序数据基础上运行

results = []
original_fetch = dl.fetch_trade_dates

for idx, (ym, month_days) in enumerate(monthly):
    log("─" * 70)
    log(f"[{idx+1}/{len(monthly)}] {ym} ({month_days[0]}~{month_days[-1]})")

    # 给 enough warmup: 从该月第一天往前取 250 天 (MA60 需要 ~60)
    first_of_month = month_days[0]
    cutoff_idx = all_dates.index(first_of_month)
    warmup_start_idx = max(0, cutoff_idx - 250)
    warmup_dates = all_dates[warmup_start_idx:cutoff_idx + len(month_days)]

    # Patch: 只暴露到该月末
    dl.fetch_trade_dates = lambda *a, **kw: warmup_dates

    # Period 设为"1月"
    for label, params, tag in [
        ("最优参数", BEST, "BEST"),
        ("Baseline", BASELINE, "BL"),
    ]:
        try:
            t0 = time.time()
            result = run_for_frontend(
                period_keys=["1月"], strategy_id="WIN_RATE_V2",
                progress_cb=None, _skip_recovery=True, _daily_cache=daily_cache,
                sample=1000, **params,
            )
            elapsed = time.time() - t0
            s = result.get("summary") or {}
            cum = float(s.get("cum_return_pct") or 0)
            wr = float(s.get("win_rate_pct") or 0)
            pf = float(s.get("profit_factor") or 1.0)
            dd = abs(float(s.get("max_drawdown_pct") or 0))
            trades = int(s.get("trades") or 0)
            score = (1 + cum / 100) * (1 + pf) / max(0.5, dd + 2)
            if trades < 3:
                score = -999
            elif trades < 10:
                score *= 0.5

            results.append({
                "month": ym, "tag": tag, "label": label,
                "cum": round(cum, 2), "wr": round(wr, 2),
                "pf": round(pf, 2), "dd": round(dd, 2),
                "trades": trades, "score": round(score, 4),
                "elapsed": round(elapsed, 1),
            })
            log(f"  {tag}: cum={cum:>7.1f}% wr={wr:>5.1f}% PF={pf:.2f} DD={dd:>4.1f}% tr={trades:>3d} score={score:.2f} ({elapsed:.0f}s)")
        except Exception as e:
            log(f"  {tag} ERROR: {str(e)[:80]}")

dl.fetch_trade_dates = original_fetch

# ── 5. 汇总 ──
log("═" * 70)
log("月度回归汇总:")

best_rows = [r for r in results if r["tag"] == "BEST"]
bl_rows = [r for r in results if r["tag"] == "BL"]

log(f"\n{'月份':>6s}  {'最优cum':>8s} {'最优wr':>6s} {'最优PF':>6s} {'最优DD':>5s}  {'BL cum':>8s} {'BL wr':>6s}  {'Δcum':>8s} {'Δscore':>7s}")
log("-" * 70)

total_best_cum = 0
total_bl_cum = 0
best_wins = 0
score_deltas = []

for br in best_rows:
    ym = br["month"]
    bl = next((r for r in bl_rows if r["month"] == ym), None)
    dcum = br["cum"] - (bl["cum"] if bl else 0)
    ds = br["score"] - (bl["score"] if bl else 0)
    delta_cum_str = f"+{dcum:.1f}%" if dcum >= 0 else f"{dcum:.1f}%"
    delta_score_str = f"+{ds:.2f}" if ds >= 0 else f"{ds:.2f}"
    total_best_cum += br["cum"]
    total_bl_cum += bl["cum"] if bl else 0
    if br["cum"] > (bl["cum"] if bl else 0):
        best_wins += 1
    score_deltas.append(ds)
    log(f"{ym:>6s}  {br['cum']:>7.1f}% {br['wr']:>5.1f}% {br['pf']:>5.2f} {br['dd']:>4.1f}%  "
        f"{bl['cum']:>7.1f}% {bl['wr']:>5.1f}%  {dcum:>+7.1f}% {ds:>+6.2f}")

log("-" * 70)
avg_best = total_best_cum / len(best_rows) if best_rows else 0
avg_bl = total_bl_cum / len(bl_rows) if bl_rows else 0
win_rate_vs_bl = best_wins / len(best_rows) * 100 if best_rows else 0
avg_ds = sum(score_deltas) / len(score_deltas) if score_deltas else 0
n_positive_ds = sum(1 for d in score_deltas if d > 0)
log(f"最优月均: {avg_best:.1f}%  Baseline月均: {avg_bl:.1f}%")
log(f"最优胜Baseline: {best_wins}/{len(best_rows)} ({win_rate_vs_bl:.0f}%)")
log(f"Δscore 均值: {avg_ds:+.2f}  正值月: {n_positive_ds}/{len(score_deltas)}")

# 稳定性统计
best_scores = sorted([r["score"] for r in best_rows if r["score"] > -999])
if best_scores:
    median_score = best_scores[len(best_scores) // 2]
    best_scores_sorted = sorted([r["score"] for r in best_rows], reverse=True)
    top3_avg = sum(best_scores_sorted[:3]) / 3
    log(f"score 中位数: {median_score:.2f}  Top3 均值: {top3_avg:.2f}")

# 保存
data = {
    "params": {k: BEST[k] for k in ["top_n","hold_days","breadth_min","breadth_min_soft","sector_hot_topn","sector_inflow_topn","late_high_discount","regime_adaptive"]},
    "baseline": BASELINE,
    "monthly": results,
    "summary": {
        "months_tested": len(best_rows),
        "avg_best_cum": round(avg_best, 2),
        "avg_bl_cum": round(avg_bl, 2),
        "best_wins_vs_bl": f"{best_wins}/{len(best_rows)}",
        "win_rate_vs_bl_pct": round(win_rate_vs_bl, 1),
        "avg_delta_score": round(avg_ds, 4),
        "months_beat_baseline": n_positive_ds,
    },
}
with open(OUT_PATH, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
log(f"结果保存到 {OUT_PATH}")
log("═" * 70)
