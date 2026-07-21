"""
退学 v3 · 尾盘战法 Walk-Forward 验证
对 grid_25_result 的 Top 3 参数组合做 OOS 验证:
  - 前半 123 交易日 = 训练 (看参数选择是否稳定)
  - 后半 123 交易日 = 验证 (看参数在样本外是否有效)
"""
from __future__ import annotations
import sys, os, json, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tuixue_v3.web.backtest_optimizer import PARAM_GRID, _format_params, _score_result, _warmup_sectors
from tuixue_v3.web.backtest_screener import run_for_frontend, _prefetch_daily, _is_main_board
from tuixue_v3 import data_layer as _dl

BASE = os.path.dirname(os.path.abspath(__file__))
WF_RESULT = os.path.join(BASE, "wf_result.json")
WF_LOG = os.path.join(BASE, "wf_result.log")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(WF_LOG, "a") as f:
        f.write(line + "\n")

# ── 0. 加载 grid 结果，取 Top 3 ──
grid_path = os.path.join(BASE, "grid_25_result.json")
if not os.path.exists(grid_path):
    log("✗ grid_25_result.json 不存在，先跑 25 轮 grid")
    sys.exit(1)
with open(grid_path) as f:
    grid = json.load(f)
top3 = sorted(grid["history"], key=lambda x: -x["score"])[:3]
BL = grid["baseline"]
log("═" * 60)
log(f"Walk-Forward 验证 · 从 grid_25 取 Top 3 组合")
log(f"Baseline: cum={BL['cum']:.1f}% wr={BL['wr']:.1f}% pf={BL['pf']:.2f}")
for rank, h in enumerate(top3, 1):
    log(f" Top{rank}: {h['label']}  score={h['score']:.2f} cum={h['cum']:.1f}% wr={h['wr']:.1f}%")

# ── 1. 数据预热 ──
import tuixue_v3.web.backtest_screener as _bs_base
_bs_base._fetch_5min_for_code = lambda code, start, end: []
log("[预热] 跳过 5min, 拉股票列表…")
all_stocks = _dl.fetch_stock_list() or []
main_stocks = [(c, n) for c, n in all_stocks if _is_main_board(c)]
max_sample = max(PARAM_GRID["sample"])
warmup_codes = [c for c, _ in main_stocks[:max_sample]]

log(f"[预热] 预下载 {len(warmup_codes)} 只日线…")
daily_cache = _prefetch_daily(warmup_codes, days=400, progress_cb=log)
log(f"[预热] 日线就绪 ({len(daily_cache)} 只)")
log("[预热] 板块缓存…")
_warmup_sectors(warmup_codes, progress_cb=log)

# ── 2. 获取完整交易日历，分成前后半 ──
log("[WF] 取完整交易日历…")
original_fetch = _dl.fetch_trade_dates
# 直接用 data_layer 拿完整 333 天
full_raw = _dl.fetch_trade_dates("20250101", "20260719") or []
all_dates = sorted({str(d).replace("-", "") for d in full_raw if str(d).replace("-", "").isdigit()})
log(f"[WF] 完整交易日期: {len(all_dates)} 天 ({all_dates[0]}~{all_dates[-1]})")
if len(all_dates) < 180:
    log(f"✗ 交易日太少 ({len(all_dates)}), 不足以做 Walk-Forward")
    sys.exit(1)

mid = len(all_dates) // 2
train_raw_dates = sorted(set(str(d).replace("-", "") for d in all_dates[:mid]))
test_raw_dates = sorted(set(str(d).replace("-", "") for d in all_dates[mid:]))
log(f"[WF] 训练期: {len(train_raw_dates)} 天 ({train_raw_dates[0]}~{train_raw_dates[-1]})")
log(f"[WF] 验证期: {len(test_raw_dates)} 天 ({test_raw_dates[0]}~{test_raw_dates[-1]})")

# ── 3. Patch & 跑训练期 grid (25 轮 top 3 在训练期重跑) ──
def _run_patched(patched_dates, params_list, label_fn, period="半年"):
    """对每 params_list 组参数, 在 patched_dates 回测"""
    _dl.fetch_trade_dates = lambda *a, **kw: patched_dates
    results = []
    for p in params_list:
        t0 = time.time()
        try:
            r = run_for_frontend(
                period_keys=[period], strategy_id="WIN_RATE_V2",
                progress_cb=None, _skip_recovery=True, _daily_cache=daily_cache,
                **{k: p.get(k, PARAM_GRID[k][0]) for k in PARAM_GRID},
            )
            s = r.get("summary") or {}
            results.append({
                "params": dict(p),
                "label": label_fn(p) if callable(label_fn) else str(p),
                "score": _score_result(r),
                "cum": float(s.get("cum_return_pct") or 0),
                "wr": float(s.get("win_rate_pct") or 0),
                "pf": float(s.get("profit_factor") or 1.0),
                "dd": abs(float(s.get("max_drawdown_pct") or 0)),
                "trades": int(s.get("trades") or 0),
                "elapsed": round(time.time() - t0, 1),
            })
        except Exception as e:
            log(f"  ✗ {label_fn(p)} exception: {str(e)[:80]}")
    return results

# 看看 grid 的 top 3 在训练期怎样
log("─" * 60)
log("[WF] 训练期 … 重跑 Top 3")
warmup_params = [
    {"top_n": h["params"]["top_n"], "hold_days": h["params"]["hold_days"],
     "breadth_min": h["params"]["breadth_min"], "breadth_min_soft": h["params"]["breadth_min_soft"],
     "sector_hot_topn": h["params"]["sector_hot_topn"], "sector_inflow_topn": h["params"]["sector_inflow_topn"],
     "late_high_discount": h["params"].get("late_high_discount", 1.0),
     "sample": max(PARAM_GRID["sample"])}
    for h in top3
]
# 加 baseline 和纯默认也跑一下对比
train_params_list = [
    {"sample": max(PARAM_GRID["sample"]), "top_n": 1, "hold_days": 3,
     "breadth_min": 0, "breadth_min_soft": 0, "sector_hot_topn": 0, "sector_inflow_topn": 0,
     "late_high_discount": 1.0},
]
for hp in warmup_params:
    # dedup
    if hp not in train_params_list:
        train_params_list.append(hp)

def _plabel(p):
    return _format_params({k: p.get(k) for k in PARAM_GRID})

train_results = _run_patched(train_raw_dates, train_params_list, _plabel)
for r in sorted(train_results, key=lambda x: -x["score"]):
    log(f"  {r['label']}  train_score={r['score']:.2f} cum={r['cum']:.1f}% wr={r['wr']:.1f}% pf={r['pf']:.2f} dd={r['dd']:.1f}% tr={r['trades']}")

# ── 4. Patch & 跑验证期 ──
log("─" * 60)
log("[WF] 验证期 … 重跑 Top 3")
test_results = _run_patched(test_raw_dates, train_params_list, _plabel)
for r in sorted(test_results, key=lambda x: -x["score"]):
    log(f"  {r['label']}  test_score={r['score']:.2f} cum={r['cum']:.1f}% wr={r['wr']:.1f}% pf={r['pf']:.2f} dd={r['dd']:.1f}% tr={r['trades']}")

# ── 5. 汇总 ──
_dl.fetch_trade_dates = original_fetch
log("═" * 60)
log("IS (训练期) vs OOS (验证期) 对比:")
report = ["name,is_cum,is_wr,is_pf,is_dd,is_trades,is_score,oos_cum,oos_wr,oos_pf,oos_dd,oos_trades,oos_score,drop_cum,drop_score"]
for t in train_results:
    t_label = t["label"]
    o = next((r for r in test_results if r["label"] == t_label), None)
    if o:
        drop_cum = t["cum"] - o["cum"]
        drop_score = t["score"] - o["score"]
        log(f"  {t_label:45s} "
            f"IS cum={t['cum']:>7.1f}% score={t['score']:>5.2f} | "
            f"OOS cum={o['cum']:>7.1f}% score={o['score']:>5.2f} | "
            f"Δcum={drop_cum:+>8.1f}% Δscore={drop_score:+>5.2f}")
        report.append(f"{t_label},{t['cum']},{t['wr']},{t['pf']},{t['dd']},{t['trades']},{t['score']},{o['cum']},{o['wr']},{o['pf']},{o['dd']},{o['trades']},{o['score']},{drop_cum:.1f},{drop_score:.2f}")

# 保存
wf_data = {
    "baseline_cum": BL["cum"],
    "train_results": train_results,
    "test_results": test_results,
    "train_dates": {"start": train_raw_dates[0], "end": train_raw_dates[-1], "count": len(train_raw_dates)},
    "test_dates": {"start": test_raw_dates[0], "end": test_raw_dates[-1], "count": len(test_raw_dates)},
}
with open(WF_RESULT, "w") as f:
    json.dump(wf_data, f, indent=2, ensure_ascii=False, default=str)
with open(os.path.join(BASE, "wf_result.csv"), "w") as f:
    f.write("\n".join(report))
log(f"结果保存 {WF_RESULT} + wf_result.csv")
log("═" * 60)
