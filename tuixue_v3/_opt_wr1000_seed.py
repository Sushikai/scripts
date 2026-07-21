#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以 WIN_RATE_1000 (旧) 冠军参数为种子, 跑 100 轮优化, 看能否超过 +539.97%。

策略:
  - initial_params = WIN_RATE_1000 OPTIMAL_PARAMS (sample=1000, hot=3, inf=3,
    bh=1500/3500, late=0.7, regime_adaptive=True)  → 种子得分约 2.x (按 sample=500 跑)
  - 之后 100 轮从 PARAM_GRID 随机采样 (sample 锁 500)
  - 跑完把 best 写 cache_store.OPTIMIZER_BEST, 前端 ⭐ 按钮会读到

Usage:
    cd /Users/kaikai/scripts
    PYTHONPATH=/Users/kaikai/scripts python3 tuixue_v3/_opt_wr1000_seed.py
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os
os.chdir(str(Path(__file__).resolve().parent))

# WIN_RATE_1000 (旧) 冠军参数 (来自 web/backtest_screener.py OPTIMAL_PARAMS)
WIN_RATE_1000_SEED = {
    "sample": 1000,           # 关键! 优化器默认 500, 这里保留 1000 看是否还能更好
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

MAX_ITERATIONS = 100

def main():
    import threading
    from tuixue_v3.web.backtest_optimizer import (
        run_optimization, _score_result, _format_params
    )
    from tuixue_v3.web.backtest_screener import run_for_frontend as _rff
    from tuixue_v3 import cache_store as _cs

    print(f"[start] WIN_RATE_1000 种子 100 轮优化, 起点 +539.97%")
    print(f"[seed] {WIN_RATE_1000_SEED}")

    # Phase 1: 先跑一遍种子本身, 看当前样本下分数
    print("\n[Phase 1] 跑种子自身 (sample=1000, 应该接近 +540%)")
    t0 = time.time()
    seed_result = _rff(
        period_keys=["半年"],
        strategy_id="WIN_RATE_V2",
        progress_cb=None,
        _skip_recovery=True,
        **WIN_RATE_1000_SEED,
    )
    seed_elapsed = time.time() - t0
    seed_summary = (seed_result.get("summary") or {}) if seed_result else {}
    seed_cum = float(seed_summary.get("cum_return_pct") or 0)
    seed_wr = float(seed_summary.get("win_rate_pct") or 0)
    seed_dd = abs(float(seed_summary.get("max_drawdown_pct") or 0))
    seed_trades = int(seed_summary.get("trades") or 0)
    seed_score = _score_result(seed_result) if seed_result else 0.0
    print(f"[seed] ✓ cum={seed_cum:.2f}% WR={seed_wr:.2f}% DD={seed_dd:.2f}% "
          f"trades={seed_trades} score={seed_score:.4f} took={seed_elapsed:.0f}s")

    # Phase 2: 用种子作为 initial_params 跑 100 轮优化
    print(f"\n[Phase 2] 启动 {MAX_ITERATIONS} 轮优化 (以种子为 best_params 起点)")
    state = run_optimization(
        strategy_id="WIN_RATE_V2",
        period_keys=["半年"],
        max_iterations=MAX_ITERATIONS,
        progress_cb=lambda m: print(f"  {m}", flush=True),
        initial_params=WIN_RATE_1000_SEED,
    )

    print(f"\n[done] {state.iteration} 轮, best_score={state.best_score:.4f}")
    print(f"[best params] {state.best_params}")
    bs = (state.best_result.get("summary") or {}) if state.best_result else {}
    print(f"[best summary] cum={bs.get('cum_return_pct', 0):.2f}% WR={bs.get('win_rate_pct', 0):.2f}% "
          f"DD={bs.get('max_drawdown_pct', 0):.2f}% trades={bs.get('trades', 0)}")

    # 比较种子和 best
    if state.best_score > seed_score:
        print(f"\n[+] 超过种子! {seed_score:.4f} → {state.best_score:.4f}")
        winner = state.best_params
        winner_cum = float(bs.get("cum_return_pct") or 0)
        print(f"[+] winner cum={winner_cum:.2f}% (种子={seed_cum:.2f}%, 旧版={WIN_RATE_1000_SEED})")
    else:
        print(f"\n[=] 100 轮内没超过种子 ({seed_score:.4f})")
        print(f"[=] WIN_RATE_1000 (旧) +539.97% 仍是当前最佳, OPTIMIZER_BEST 维持现状")

    # 把结果写到 cache_store, 前端 ⭐ 按钮下次会读到
    s = _cs.get_store()
    if state.best_result:
        best_payload = {
            "params": dict(state.best_params),
            "score": state.best_score,
            "summary": bs,
            "scenario_trail_80": (state.best_result.get("scenarios") or {}).get("trail_80") or {},
            "completed_at": time.time(),
            "iterations": state.iteration,
            "seed_score": seed_score,
            "seed_summary": seed_summary,
        }
        s.set(_cs.K.OPTIMIZER_BEST, best_payload, ttl=86400 * 30)
        print(f"\n[persist] OPTIMIZER_BEST 已写入 cache_store (TTL 30d)")

    # 同时保存种子本身
    s.set("opt:wr1000_seed", {
        "params": WIN_RATE_1000_SEED,
        "score": seed_score,
        "summary": seed_summary,
        "completed_at": time.time(),
    }, ttl=86400 * 30)
    print(f"[persist] opt:wr1000_seed 已写入 cache_store")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[stopped] 用户中断")
    except Exception as e:
        print(f"\n[error] {e}")
        traceback.print_exc()
        sys.exit(1)