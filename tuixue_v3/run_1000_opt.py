"""
退学 v3 · 1000 轮自动寻参 (v2 — 纯进程版)
实时鉴股 尾盘交易 (模式不变)

v2: 去掉 multiprocessing fork, 主线程直跑.
  预热日线 + Sector, 每轮 ~2-5 min, 中途 crash 可续跑.

Usage: cd /Users/kaikai/scripts && PYTHONPATH=. python3 -m tuixue_v3.run_1000_opt
"""
import sys, os, json, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tuixue_v3.web.backtest_optimizer import run_optimization, summarize, DEFAULT_PARAMS, PARAM_GRID

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opt_1000_result.json")
LOG = OUTPUT.replace(".json", ".log")
CHECKPOINT = OUTPUT.replace(".json", "_checkpoint.json")

_iter_log: list[str] = []

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _iter_log.append(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

log("═══ 尾盘战法 1000 轮自动寻参 (v2 直跑版) ═══")
log(f"搜索空间: {sum(len(v) for v in PARAM_GRID.values())} 种取值组合")
log(f"默认参数: {DEFAULT_PARAMS}")

log("开始 1000 轮优化 (WIN_RATE_V2, 1年周期)…")

state = run_optimization(
    strategy_id="WIN_RATE_V2",
    period_keys=["1年"],
    max_iterations=1000,
    progress_cb=log,
)

report = summarize(state)
print("\n" + report)

result_data = {
    "best_params": state.best_params,
    "best_score": state.best_score,
    "total_iterations": state.iteration,
    "successful_iters": len(state.history),
    "elapsed_seconds": time.time() - state.started_at,
    "best_summary": state.best_result.get("summary") if state.best_result else None,
    "top_10": sorted(
        [{
            "params": h["params"],
            "score": h["score"],
            "cum_return": h.get("cum_return"),
            "win_rate": h.get("win_rate"),
            "max_drawdown": h.get("max_drawdown"),
            "profit_factor": h.get("profit_factor"),
            "trades": h.get("trades"),
        } for h in state.history],
        key=lambda x: -x["score"]
    )[:10],
}

with open(OUTPUT, "w") as f:
    json.dump(result_data, f, indent=2, ensure_ascii=False, default=str)

# Also write full history to separate file
hist_path = OUTPUT.replace(".json", "_history.json")
with open(hist_path, "w") as f:
    json.dump({
        "history": state.history,
        "params_grid": {k: list(v) for k, v in PARAM_GRID.items()},
        "defaults": DEFAULT_PARAMS,
    }, f, indent=2, ensure_ascii=False, default=str)

log(f"结果已保存到 {OUTPUT}")
log(f"历史记录: {hist_path}")
