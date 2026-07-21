"""
zt_optimizer.py — 涨停板次日溢价策略 进化算法参数搜索。
"""
from __future__ import annotations

import itertools
import json
import logging
import random
import time as systime
from typing import Any

import numpy as np

from . import zt_backtest as zt
from . import zt_config as cfg

log = logging.getLogger("tuixue_v3.zt_optimizer")

# ── 参数空间 ──────────────────────────────────────────

PARAM_GRID = {
    "min_streak":         [1, 2],
    "max_streak":         [3, 5, 10],
    "burst_max":          [0, 1, 2],
    "sealed_before":      ["10:00", "11:30", "14:00"],
    "mcap_min_yi":        [10.0, 15.0, 20.0],
    "mcap_max_yi":        [200.0, 300.0, 500.0],
    "turnover_min_pct":   [2.0, 3.0, 5.0],
    "turnover_max_pct":   [25.0, 35.0, 50.0],
    "limit_order_min_yi": [0.0, 0.3, 0.5],
    "top_n":              [1, 2, 3, 5],
    "trail_activate_pct": [1.0, 2.0, 3.0, 5.0],
    "trail_pullback_pct": [0.5, 1.0, 1.5, 2.0],
    "stop_loss_pct":      [-3.0, -5.0, -7.0, -10.0],
}


def _random_params() -> dict:
    return {k: random.choice(v) for k, v in PARAM_GRID.items()}


def _crossover(a: dict, b: dict) -> dict:
    return {k: a[k] if random.random() < 0.5 else b[k] for k in PARAM_GRID}


def _mutate(p: dict, rate: float = 0.3) -> dict:
    return {k: random.choice(PARAM_GRID[k]) if random.random() < rate else p[k] for k in PARAM_GRID}


def _refine(p: dict) -> dict:
    """微调：在邻域内随机扰动参数。"""
    out = dict(p)
    for k in PARAM_GRID:
        if random.random() < 0.4:
            choices = PARAM_GRID[k]
            idx = choices.index(out[k]) if out[k] in choices else 0
            idx = max(0, min(len(choices) - 1, idx + random.choice([-1, 1])))
            out[k] = choices[idx]
    return out


# ── 评分 ──────────────────────────────────────────

def _score(result: dict) -> float:
    """评分函数：正回报且胜率 > 50% 时打分，否则负分。"""
    s = result.get("summary", {}) or {}
    trades = s.get("trades", 0)
    if trades < 20:
        return -1000 + trades  # 交易太少，強く惩罚

    win_rate = s.get("win_rate_pct", 0) or 0
    total_ret = s.get("total_return_pct", 0) or 0
    max_dd = s.get("max_drawdown_pct", -100) or -100
    avg_ret = s.get("avg_return_pct", 0) or 0

    # core: total return with win rate gate
    base_score = total_ret * (win_rate / 50.0)

    # drawdown penalty
    if max_dd < -50:
        base_score *= 0.5
    elif max_dd < -30:
        base_score *= 0.7

    # avg return bonus
    if avg_ret > 1.0:
        base_score *= 1.2

    # win rate bonus
    if win_rate >= 60:
        base_score *= 1.3
    elif win_rate >= 50:
        base_score *= 1.1
    else:
        base_score *= 0.3  # <50% win rate heavy penalty

    return base_score


# ── 优化循环 ──────────────────────────────────────────

def run_optimize(
    start: str = cfg.ZT_START,
    end: str = cfg.ZT_OPTIMIZE_WINDOW_END,
    iterations: int = cfg.ZT_OPT_ITERATIONS,
    population: int = cfg.ZT_OPT_POPULATION,
    random_ratio: float = cfg.ZT_OPT_RANDOM_RATIO,
    crossover_ratio: float = cfg.ZT_OPT_CROSSOVER_RATIO,
    refine_ratio: float = cfg.ZT_OPT_REFINE_RATIO,
    board_filter: str = cfg.ZT_BOARD_FILTER,
    progress_cb=None,
) -> dict:
    """进化算法参数搜索。

    Phase 1: 随机搜索 (random_ratio * iterations)
    Phase 2: 交叉搜索 (crossover_ratio * iterations)
    Phase 3: 微调搜索 (refine_ratio * iterations)
    """
    log.info("========== ZT 优化 %s→%s | iter=%d pop=%d board=%s ==========",
             start, end, iterations, population, board_filter)

    # Step 1: 构建基础缓存（一次）
    t0 = systime.time()
    prebuilt = zt.build_zt_cache(start=start, end=end, board_filter=board_filter)
    log.info("预构建缓存完成 (%ds)", systime.time() - t0)
    _progress(progress_cb, "cache_done", elapsed=systime.time() - t0)

    n_rand = int(iterations * random_ratio)
    n_cross = int(iterations * crossover_ratio)
    n_refine = int(iterations * refine_ratio)

    population_results: list[tuple[float, dict, dict]] = []  # (score, params, result)

    def _eval(params: dict) -> tuple[float, dict, dict]:
        """评估一组参数，返回 (score, params, result)。"""
        t1 = systime.time()
        result = zt.run_zt_backtest(
            start=start, end=end,
            top_n=params["top_n"],
            board_filter=board_filter,
            min_streak=params["min_streak"],
            max_streak=params["max_streak"],
            burst_max=params["burst_max"],
            sealed_before=params["sealed_before"],
            mcap_min_yi=params["mcap_min_yi"],
            mcap_max_yi=params["mcap_max_yi"],
            turnover_min_pct=params["turnover_min_pct"],
            turnover_max_pct=params["turnover_max_pct"],
            limit_order_min_yi=params["limit_order_min_yi"],
            trail_activate_pct=params["trail_activate_pct"],
            trail_pullback_pct=params["trail_pullback_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            sample=0,
            _prebuilt=prebuilt,
        )
        score = _score(result)
        elapsed = systime.time() - t1
        s = result.get("summary", {}) or {}
        log.info("  score=%.1f | 笔数=%d 胜率=%.1f%% 总收益=%.1f%% 回撤=%.1f%% | %.1fs",
                 score, s.get("trades", 0), s.get("win_rate_pct", 0),
                 s.get("total_return_pct", 0), s.get("max_drawdown_pct", 0), elapsed)
        return score, params, result

    # ── Phase 1: 随机搜索 ──
    log.info("Phase 1: 随机搜索 %d 次...", n_rand)
    for i in range(n_rand):
        params = _random_params()
        score, p, r = _eval(params)
        population_results.append((score, p, r))
        _progress(progress_cb, "phase1", iter=i + 1, total=n_rand, best_score=max(
            (s for s, _, _ in population_results), default=-9999))

    # 排序保留 Top population
    population_results.sort(key=lambda x: -x[0])
    elite = population_results[:population]

    # ── Phase 2: 交叉搜索 ──
    log.info("Phase 2: 交叉搜索 %d 次 (保留 top %d)...", n_cross, population)
    for i in range(n_cross):
        a = random.choice(elite)[1]
        b = random.choice(elite)[1]
        child = _mutate(_crossover(a, b))
        score, p, r = _eval(child)
        elite.append((score, p, r))
        elite.sort(key=lambda x: -x[0])
        elite = elite[:population]
        _progress(progress_cb, "phase2", iter=i + 1, total=n_cross,
                  best_score=elite[0][0] if elite else -9999)

    # ── Phase 3: 微调 ──
    log.info("Phase 3: 微调搜索 %d 次...", n_refine)
    for i in range(n_refine):
        base = random.choice(elite[:max(3, len(elite) // 3)])[1]
        refined = _refine(base)
        score, p, r = _eval(refined)
        elite.append((score, p, r))
        elite.sort(key=lambda x: -x[0])
        elite = elite[:population]
        _progress(progress_cb, "phase3", iter=i + 1, total=n_refine,
                  best_score=elite[0][0] if elite else -9999)

    best_score, best_params, best_result = elite[0]
    elapsed = systime.time() - t0

    log.info("========== ZT 优化完成 | best=%.1f | %.0fs ==========", best_score, elapsed)
    log.info("最佳参数: %s", best_params)

    s = best_result.get("summary", {}) or {}
    log.info("  笔数=%d 胜率=%.1f%% 平均=%.2f%% 总收益=%.1f%% 回撤=%.1f%%",
             s.get("trades", 0), s.get("win_rate_pct", 0),
             s.get("avg_return_pct", 0), s.get("total_return_pct", 0),
             s.get("max_drawdown_pct", 0))

    return {
        "best_params": best_params,
        "best_score": best_score,
        "best_result": best_result,
        "elite": [(score, params) for score, params, _ in elite],
        "iterations": iterations,
        "elapsed_sec": elapsed,
        "config": {
            "start": start, "end": end,
            "population": population,
            "random_ratio": random_ratio,
            "crossover_ratio": crossover_ratio,
            "refine_ratio": refine_ratio,
        },
    }


def _progress(cb, phase: str, **kw):
    if cb:
        try:
            cb({"phase": phase, **kw})
        except Exception:
            pass


# ── CLI ──

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ZT 参数优化")
    p.add_argument("--start", default=cfg.ZT_START)
    p.add_argument("--end", default=cfg.ZT_OPTIMIZE_WINDOW_END)
    p.add_argument("--iter", type=int, default=cfg.ZT_OPT_ITERATIONS)
    p.add_argument("--pop", type=int, default=cfg.ZT_OPT_POPULATION)
    p.add_argument("--board", default=cfg.ZT_BOARD_FILTER)
    p.add_argument("--save", action="store_true")
    args = p.parse_args()

    r = run_optimize(
        start=args.start, end=args.end,
        iterations=args.iter,
        population=args.pop,
        board_filter=args.board,
    )
    print(json.dumps(r["best_params"], ensure_ascii=False, indent=2))
    print(f"\nScore: {r['best_score']:.1f}")
    s = r["best_result"].get("summary", {}) or {}
    print(f"Trades: {s.get('trades',0)} | 胜率: {s.get('win_rate_pct',0)}% | "
          f"总收益: {s.get('total_return_pct',0)}% | 回撤: {s.get('max_drawdown_pct',0)}%")

    if args.save:
        path = f"/tmp/zt_optimize_{args.start}_{args.end}.json"
        import json as _json
        _json.dump(r, open(path, "w"), ensure_ascii=False, indent=2, default=str)
        print(f"\n保存到 {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cli()
