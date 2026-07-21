"""
退学 v3 · 尾盘战法 25 轮寻参
保留 baseline (DEFAULT_PARAMS), 从 1485 种有效组合中随机采样 25 组.
每轮输出 Δ vs baseline, 精确定量 "每一轮优化多少".
"""
from __future__ import annotations
import random, itertools, sys, os, json, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tuixue_v3.web.backtest_optimizer import (
    PARAM_GRID, DEFAULT_PARAMS, RULE_BLACKLIST,
    _format_params, _score_result, _warmup_sectors,
)
from tuixue_v3.web.backtest_screener import run_for_frontend, _prefetch_daily, _is_main_board
from tuixue_v3 import data_layer as _dl

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grid_25_result.json")
LOG_PATH = OUTPUT.replace(".json", ".log")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

# ── 0. 枚举全部有效组合 ──
keys = list(PARAM_GRID.keys())
all_params = []
for combo in itertools.product(*[PARAM_GRID[k] for k in keys]):
    p = dict(zip(keys, combo))
    if not any(rule(p) for rule in RULE_BLACKLIST):
        all_params.append(p)

N_ROUNDS = min(25, len(all_params))
random.seed(42)
sampled = random.sample(all_params, N_ROUNDS)  # 25 组有代表性采样

log("═" * 80)
log(f"尾盘战法 25 轮寻参 (从 {len(all_params)} 种有效组合中随机采样{N_ROUNDS}组)")
log(f"搜索空间: {keys}")
log(f"各维取值: {dict((k, len(v)) for k,v in PARAM_GRID.items())}")
log(f"默认参数 (baseline): {DEFAULT_PARAMS}")

# ── 1. 数据预热 ──
import tuixue_v3.web.backtest_screener as _bs_base
_bs_base._fetch_5min_for_code = lambda code, start, end: []
log("[预热] 跳过 5min 数据, 获取股票列表…")
all_stocks = _dl.fetch_stock_list() or []
main_stocks = [(c, n) for c, n in all_stocks if _is_main_board(c)]
max_sample = max(PARAM_GRID["sample"])
warmup_codes = [c for c, _ in main_stocks[:max_sample]]

log(f"[预热] 预下载 {len(warmup_codes)} 只日线…")
daily_cache = _prefetch_daily(warmup_codes, days=400, progress_cb=log)
log(f"[预热] 日线就绪 ({len(daily_cache)} 只)")
log("[预热] 板块缓存…")
_warmup_sectors(warmup_codes, progress_cb=log)

# ── 2. Baseline ──
baseline_params = {k: DEFAULT_PARAMS.get(k) for k in PARAM_GRID}
baseline_params["sample"] = max(PARAM_GRID.get("sample", [500]))
log(f"[BASELINE] {_format_params(baseline_params)}…")
t0 = time.time()
baseline_result = run_for_frontend(
    period_keys=["半年"], strategy_id="WIN_RATE_V2",
    progress_cb=None, _skip_recovery=True, _daily_cache=daily_cache, **baseline_params,
)
baseline_sec = time.time() - t0
baseline_score = _score_result(baseline_result)
bs = baseline_result.get("summary") or {}
BL = {
    "cum": float(bs.get("cum_return_pct") or 0),
    "wr":  float(bs.get("win_rate_pct") or 0),
    "pf":  float(bs.get("profit_factor") or 1.0),
    "dd":  abs(float(bs.get("max_drawdown_pct") or 0)),
    "avg": float(bs.get("avg_return_pct") or 0),
    "trades": int(bs.get("trades") or 0),
    "score": baseline_score,
}
log(f"[BASELINE] 累计{BL['cum']:.1f}% 胜率{BL['wr']:.1f}% 盈亏比{BL['pf']:.2f} "
    f"score={BL['score']:.2f} 交易{BL['trades']}笔 耗时{baseline_sec:.0f}s")
log("─" * 80)

# ── 3. 25 轮 ──
t_start = time.time()
history = []
best_score = -999.0
best_params_s = ""
best_result = None

for i, params in enumerate(sampled):
    label = _format_params(params)
    t_iter = time.time()
    try:
        result = run_for_frontend(
            period_keys=["半年"], strategy_id="WIN_RATE_V2",
            progress_cb=None, _skip_recovery=True, _daily_cache=daily_cache, **params,
        )
    except Exception as e:
        log(f"[{i+1:2d}/{N_ROUNDS}] ✗ {label} 异常: {str(e)[:80]}")
        continue

    elapsed = time.time() - t_iter
    score = _score_result(result)
    s = result.get("summary") or {}
    cum = float(s.get("cum_return_pct") or 0)
    wr  = float(s.get("win_rate_pct") or 0)
    dd  = abs(float(s.get("max_drawdown_pct") or 0))
    pf  = float(s.get("profit_factor") or 1.0)
    trades = int(s.get("trades") or 0)
    avg_ret = float(s.get("avg_return_pct") or 0)

    entry = {
        "round": i + 1, "label": label, "params": dict(params),
        "score": score, "cum": cum, "wr": wr, "dd": dd, "pf": pf,
        "trades": trades, "avg_return": avg_ret, "elapsed": round(elapsed, 1),
        "d_cum": round(cum - BL["cum"], 2),
        "d_wr":  round(wr - BL["wr"], 2),
        "d_pf":  round(pf - BL["pf"], 2),
        "d_dd":  round(dd - BL["dd"], 2),
        "d_score": round(score - baseline_score, 4),
    }
    history.append(entry)

    if score > best_score:
        best_score = score
        best_params_s = label
        best_result = result

    # 一行显示: params + 核心指标 + Δ vs baseline
    dc = entry["d_cum"]; dw = entry["d_wr"]; dp = entry["d_pf"]; ds = entry["d_score"]
    star = " ⭐ BEST" if score == best_score else ""
    # parse params for readability
    p = params
    parts = []
    if p.get("breadth_min"): parts.append(f"h={p['breadth_min']}")
    if p.get("breadth_min_soft"): parts.append(f"s={p['breadth_min_soft']}")
    if p.get("sector_hot_topn"): parts.append(f"top={p['sector_hot_topn']}")
    if p.get("sector_inflow_topn"): parts.append(f"inf={p['sector_inflow_topn']}")
    if p.get("late_high_discount") != 1.0: parts.append(f"lhd={p['late_high_discount']}")
    if p.get("require_vwap_strict"): parts.append("VWAP")
    if p.get("regime_adaptive"): parts.append("REG")
    desc = " ".join(parts) if parts else "默认(基线全部=0)"
    log(
        f"[{i+1:2d}/{N_ROUNDS}] tn={p['top_n']} hd={p['hold_days']} {desc:45s}"
        f"cum={cum:>7.1f}% (Δ{dc:+>7.1f}%) | "
        f"wr={wr:>5.1f}% (Δ{dw:+>5.1f}%) | "
        f"PF={pf:>4.2f} (Δ{dp:+>4.2f}) | "
        f"DD={dd:>5.1f}% | tr={trades:>3d} | "
        f"score={score:>6.2f} (Δ{ds:+>6.2f}) | {elapsed:>4.0f}s{star}"
    )

# ── 4. 汇总 ──
elapsed_total = time.time() - t_start
log("─" * 80)
log(f"完成 {len(history)} 轮, 总耗时 {elapsed_total:.0f}s")
log(f"Baseline: cum={BL['cum']:.1f}% wr={BL['wr']:.1f}% pf={BL['pf']:.2f} score={BL['score']:.2f}")
if best_result:
    s = best_result.get("summary") or {}
    best_cum = s.get("cum_return_pct", BL["cum"])
    best_wr = s.get("win_rate_pct", BL["wr"])
    log(f"最佳[{best_params_s}]: cum={best_cum:.1f}% (Δ{best_cum-BL['cum']:+>+.1f}%) "
        f"wr={best_wr:.1f}% (Δ{best_wr-BL['wr']:+>+.1f}%) "
        f"score={best_score:.2f} (Δ{best_score-BL['score']:+>+.2f})")

# Top 3
sorted_h = sorted(history, key=lambda x: -x["score"])
log("")
log("Top 3 (delta vs baseline):")
for rank, h in enumerate(sorted_h[:3], 1):
    log(f" #{rank}: {h['label']:50s}"
        f"cum={h['cum']:.1f}% (Δ{h['d_cum']:+>+.1f}%)  "
        f"wr={h['wr']:.1f}% (Δ{h['d_wr']:+>+.1f}%)  "
        f"score={h['score']:.2f} (Δ{h['d_score']:+>+.2f})  "
        f"trades={h['trades']}")

# Δ 分布
dsc = [h["d_score"] for h in history]
dcum = [h["d_cum"] for h in history]
dwr = [h["d_wr"] for h in history]
n_beat = sum(1 for x in dsc if x > 0)
n_worse = sum(1 for x in dsc if x < 0)
log("")
log("25 轮 Δ 分布统计:")
log(f"  超 baseline: {n_beat}/{len(history)} ({n_beat*100/len(history):.0f}%)")
log(f"  低于       : {n_worse}/{len(history)} ({n_worse*100/len(history):.0f}%)")
log(f"  Δscore:  mean {sum(dsc)/len(dsc):+.3f}  max {max(dsc):+.3f}  min {min(dsc):+.3f}  σ {math.sqrt(sum((x-sum(dsc)/len(dsc))**2 for x in dsc)/len(dsc)):.3f}")
log(f"  Δcum:   mean {sum(dcum)/len(dcum):+.1f}%  max {max(dcum):+.1f}%  min {min(dcum):+.1f}%")
log(f"  Δwr:    mean {sum(dwr)/len(dwr):+.1f}%  max {max(dwr):+.1f}%  min {min(dwr):+.1f}%")
log("═" * 80)

# Save
result_data = {
    "strategy": "尾盘战法 25轮寻参 v2",
    "baseline": {"params": baseline_params, **BL, "elapsed_s": baseline_sec},
    "best": {"label": best_params_s, "params": best_params_s, "score": best_score,
             "summary": best_result.get("summary") if best_result else None},
    "history": sorted(history, key=lambda x: x["round"]),
    "delta_stats": {
        "n_beat_baseline": n_beat, "n_worse_baseline": n_worse,
        "avg_delta_score": round(sum(dsc)/len(dsc), 4) if dsc else 0,
        "max_delta_score": max(dsc) if dsc else 0,
        "avg_delta_cum": round(sum(dcum)/len(dcum), 2) if dcum else 0,
        "max_delta_cum": max(dcum) if dcum else 0,
    },
    "total_elapsed_s": round(elapsed_total, 1),
}
with open(OUTPUT, "w") as f:
    json.dump(result_data, f, indent=2, ensure_ascii=False, default=str)
log(f"结果保存到 {OUTPUT}")
