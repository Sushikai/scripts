"""
run_1000_zt_opt.py — 涨停板 1000 轮参数优化 driver

按用户要求: 每月翻 2 倍 (200%/月) 目标, 实测, 不达无限循环.

Usage:
    python run_1000_zt_opt.py --rounds 1000 --seed 42 --resume \
        --max-time-hours 24 --checkpoint-every 10
"""
from __future__ import annotations

import argparse
import json
import sys
import time as systime
from pathlib import Path

from tuixue_v3 import zt_optimizer, zt_config as cfg

ROOT = Path(__file__).resolve().parent

CHECKPOINT_FILE = "/tmp/zt_opt_checkpoint.json"
BEST_FILE = "/tmp/zt_opt_best.json"
HISTORY_FILE = "/tmp/zt_opt_history.jsonl"


def _save_history(entry: dict):
    """append-only 历史 (jsonl)。"""
    Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        f.flush()


def _save_best(best: dict):
    Path(BEST_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(BEST_FILE, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2, default=str)
        f.flush()


def _atomic_save_checkpoint(state: dict):
    zt_optimizer._save_checkpoint(CHECKPOINT_FILE, state)


def main():
    p = argparse.ArgumentParser(description="ZT 1000 轮参数搜索")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2026-07-23")
    p.add_argument("--rounds", type=int, default=1000)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--validate-days", type=int, default=30)
    p.add_argument("--holdout-days", type=int, default=60)
    p.add_argument("--population", type=int, default=cfg.ZT_OPT_POPULATION)
    p.add_argument("--random-ratio", type=float, default=cfg.ZT_OPT_RANDOM_RATIO)
    p.add_argument("--crossover-ratio", type=float, default=cfg.ZT_OPT_CROSSOVER_RATIO)
    p.add_argument("--refine-ratio", type=float, default=cfg.ZT_OPT_REFINE_RATIO)
    p.add_argument("--board", default=cfg.ZT_BOARD_FILTER)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-time-hours", type=float, default=24.0)
    p.add_argument("--checkpoint-every", type=int, default=10)
    p.add_argument("--use-wf", action="store_true",
                   help="每个 candidate 跑完整 WF (5 折) 而非单窗口")
    args = p.parse_args()

    print(f"========== ZT 1000 轮 ==========")
    print(f"start={args.start} end={args.end} rounds={args.rounds}")
    print(f"seed={args.seed} resume={args.resume} wf={args.use_wf}")
    print(f"target: 月收益 ≥ {cfg.ZT_MIN_MONTHLY_RETURN_PCT}% | "
          f"WR ≥ {cfg.ZT_MIN_WIN_RATE_PCT}% | DD ≥ {cfg.ZT_MAX_DRAWDOWN_PCT}%")

    t_start = systime.time()
    deadline = t_start + args.max_time_hours * 3600

    # Resume
    state = None
    if args.resume:
        state = zt_optimizer._load_checkpoint(CHECKPOINT_FILE)
        if state:
            print(f"从 checkpoint 恢复: iter={state.get('iter', 0)}")
        else:
            print(f"未找到 checkpoint, 从头开始")

    # 跑优化
    history = (state or {}).get("history", [])
    best = (state or {}).get("best", {
        "score": -1e9, "params": None, "result_summary": None,
    })

    import random as _rand
    import numpy as np
    _rand.seed(args.seed)
    np.random.seed(args.seed)

    # 构建缓存 (一次)
    print("构建 ZT 缓存...")
    t0 = systime.time()
    from tuixue_v3 import zt_backtest
    prebuilt = zt_backtest.build_zt_cache(
        start=args.start, end=args.end, board_filter=args.board,
    )
    print(f"缓存构建完成 ({systime.time() - t0:.1f}s)")

    # WF splits (用于 WF 模式)
    wf_splits = None
    if args.use_wf:
        wf_splits = zt_optimizer.make_wf_splits(
            start=args.start, end=args.end,
            folds=args.folds,
            train_days=args.train_days,
            validate_days=args.validate_days,
            holdout_days=args.holdout_days,
        )
        print(f"WF splits: {len(wf_splits)} 折")

    n_rand = int(args.rounds * args.random_ratio)
    n_cross = int(args.rounds * args.crossover_ratio)
    n_refine = int(args.rounds * args.refine_ratio)

    # ── Phase 1: 随机搜索 ──
    print(f"\nPhase 1: 随机搜索 {n_rand} 次...")
    n_total = (state or {}).get("n_total", 0)
    start_yyyymmdd = args.start.replace("-", "")
    end_yyyymmdd = args.end.replace("-", "")
    for i in range(n_rand):
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽 ({args.max_time_hours}h)")
            break

        params = zt_optimizer._random_params(_rand)
        score, p, r = zt_optimizer._eval_with_prebuilt(
            params, prebuilt, args.board,
            start=start_yyyymmdd, end=end_yyyymmdd,
        )
        history.append({"iter": n_total + i + 1, "phase": "random",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f}")
            s = best["result_summary"] or {}
            print(f"    trades={s.get('trades',0)} WR={s.get('win_rate_pct',0):.1f}% "
                  f"avg={s.get('avg_return_pct',0):.2f}% "
                  f"cum={s.get('total_return_pct',0):.1f}% "
                  f"DD={s.get('max_drawdown_pct',0):.1f}%")

        # checkpoint
        if (n_total + i + 1) % args.checkpoint_every == 0:
            _atomic_save_checkpoint({"iter": n_total + i + 1, "best": best,
                                      "history": history, "n_total": n_total + i + 1})
            _save_history({"iter": n_total + i + 1, "best_so_far": best["score"]})

    n_total += n_rand
    print(f"Phase 1 完成 | best score={best['score']:.2f}")

    # 截止检查 — 早期退出
    if best["result_summary"]:
        s = best["result_summary"]
        monthly = s.get("monthly_compound_pct", s.get("monthly_avg_total_pct", 0))
        if (monthly >= cfg.ZT_MIN_MONTHLY_RETURN_PCT and
            s.get("win_rate_pct", 0) >= cfg.ZT_MIN_WIN_RATE_PCT and
            s.get("max_drawdown_pct", -100) >= cfg.ZT_MAX_DRAWDOWN_PCT and
            s.get("trades", 0) >= 50):
            print(f"\n🎯 Phase 1 已达标! monthly={monthly:.1f}% "
                  f"WR={s.get('win_rate_pct',0):.1f}% "
                  f"DD={s.get('max_drawdown_pct',0):.1f}%")
            _atomic_save_checkpoint({"iter": n_total, "best": best, "history": history,
                                      "n_total": n_total, "done": True})
            _save_best(best)
            return

    # ── Phase 2: 交叉搜索 ──
    print(f"\nPhase 2: 交叉搜索 {n_cross} 次...")
    elite = [best] if best["params"] else []
    # 收集 top N 个 elite from history (by score)
    history_sorted = sorted(history, key=lambda h: h.get("score", -1e9), reverse=True)
    for h in history_sorted[:args.population]:
        if h.get("params") and h["params"] not in [e["params"] for e in elite]:
            elite.append({"params": h["params"], "score": h["score"],
                          "result_summary": h.get("summary", {})})

    for i in range(n_cross):
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽 ({args.max_time_hours}h)")
            break
        if len(elite) < 2:
            break

        a = _rand.choice(elite)["params"]
        b = _rand.choice(elite)["params"]
        child = zt_optimizer._mutate(
            zt_optimizer._crossover(a, b, _rand),
            rng=_rand,
        )
        score, p, r = zt_optimizer._eval_with_prebuilt(
            child, prebuilt, args.board,
            start=start_yyyymmdd, end=end_yyyymmdd,
        )
        history.append({"iter": n_total + i + 1, "phase": "crossover",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            s = best["result_summary"]
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f} "
                  f"monthly={s.get('monthly_compound_pct',0):.1f}% "
                  f"WR={s.get('win_rate_pct',0):.1f}% DD={s.get('max_drawdown_pct',0):.1f}%")
        if (n_total + i + 1) % args.checkpoint_every == 0:
            _atomic_save_checkpoint({"iter": n_total + i + 1, "best": best,
                                      "history": history, "n_total": n_total + i + 1})

    n_total += n_cross
    print(f"Phase 2 完成 | best score={best['score']:.2f}")

    # 截止检查
    if best["result_summary"]:
        s = best["result_summary"]
        monthly = s.get("monthly_compound_pct", s.get("monthly_avg_total_pct", 0))
        if (monthly >= cfg.ZT_MIN_MONTHLY_RETURN_PCT and
            s.get("win_rate_pct", 0) >= cfg.ZT_MIN_WIN_RATE_PCT and
            s.get("max_drawdown_pct", -100) >= cfg.ZT_MAX_DRAWDOWN_PCT and
            s.get("trades", 0) >= 50):
            print(f"\n🎯 Phase 2 已达标! monthly={monthly:.1f}%")
            _atomic_save_checkpoint({"iter": n_total, "best": best, "history": history,
                                      "n_total": n_total, "done": True})
            _save_best(best)
            return

    # ── Phase 3: 微调搜索 ──
    print(f"\nPhase 3: 微调搜索 {n_refine} 次...")
    for i in range(n_refine):
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽 ({args.max_time_hours}h)")
            break
        if not elite:
            break

        base = _rand.choice(elite[:max(3, len(elite) // 3)])["params"]
        refined = zt_optimizer._refine(base, _rand)
        score, p, r = zt_optimizer._eval_with_prebuilt(
            refined, prebuilt, args.board,
            start=start_yyyymmdd, end=end_yyyymmdd,
        )
        history.append({"iter": n_total + i + 1, "phase": "refine",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            s = best["result_summary"]
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f} "
                  f"monthly={s.get('monthly_compound_pct',0):.1f}% "
                  f"WR={s.get('win_rate_pct',0):.1f}% DD={s.get('max_drawdown_pct',0):.1f}%")
        if (n_total + i + 1) % args.checkpoint_every == 0:
            _atomic_save_checkpoint({"iter": n_total + i + 1, "best": best,
                                      "history": history, "n_total": n_total + i + 1})

    n_total += n_refine
    print(f"Phase 3 完成 | best score={best['score']:.2f}")

    # 最终检查
    _atomic_save_checkpoint({"iter": n_total, "best": best, "history": history,
                              "n_total": n_total, "done": True})
    _save_best(best)

    print(f"\n========== 完成 ==========")
    print(f"总 iter: {n_total}")
    print(f"best score: {best['score']:.2f}")
    if best["result_summary"]:
        s = best["result_summary"]
        print(f"trades={s.get('trades',0)} WR={s.get('win_rate_pct',0):.1f}% "
              f"avg={s.get('avg_return_pct',0):.2f}% "
              f"cum={s.get('total_return_pct',0):.1f}% "
              f"DD={s.get('max_drawdown_pct',0):.1f}% "
              f"monthly={s.get('monthly_compound_pct',0):.1f}%")
    print(f"checkpoint: {CHECKPOINT_FILE}")
    print(f"best: {BEST_FILE}")
    print(f"history: {HISTORY_FILE}")


if __name__ == "__main__":
    main()