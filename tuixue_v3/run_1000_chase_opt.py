"""
run_1000_chase_opt.py — 追板 (chase) 1000 轮参数优化 driver

按用户要求: 月均 ≥ 200% 是硬指标, 无限循环直到达标.
策略语义: T 日盘中涨幅冲到 [7%, 9.4%) 主板 / [14%, 19.4%) 创科 时追高买入,
          T+1 09:30 开盘卖出 (期望今天封板 → 隔夜溢价).

用法:
    python run_1000_chase_opt.py --rounds 1000 --seed 42 --resume \
        --max-time-hours 24 --checkpoint-every 5 \
        --target-monthly 200 --target-wr 50 --target-dd -30
"""
from __future__ import annotations

import argparse
import json
import logging
import random as _rand
import sys
import time as systime
from pathlib import Path

import numpy as np

from tuixue_v3 import zt_backtest as zt
from tuixue_v3 import zt_config as cfg
from tuixue_v3 import zt_optimizer as opt

ROOT = Path(__file__).resolve().parent

CHECKPOINT_FILE = "/tmp/zt_chase_checkpoint.json"
BEST_FILE = "/tmp/zt_chase_best.json"
HISTORY_FILE = "/tmp/zt_chase_history.jsonl"
MONTHLY_FILE = "/tmp/zt_chase_monthly.json"

log = logging.getLogger("tuixue_v3.chase_opt")


def _save_history(entry: dict):
    Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        f.flush()


def _save_best(best: dict):
    with open(BEST_FILE, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2, default=str)
        f.flush()


def _save_monthly(monthly: list[dict]):
    with open(MONTHLY_FILE, "w", encoding="utf-8") as f:
        json.dump({"monthly": monthly}, f, ensure_ascii=False, indent=2, default=str)
        f.flush()


def _save_checkpoint(state: dict):
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        import os
        os.replace(tmp, CHECKPOINT_FILE)


def _load_checkpoint():
    p = Path(CHECKPOINT_FILE)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _eval_one(params: dict, start: str, end: str, board: str) -> tuple[float, dict, dict]:
    """跑一次 chase 回测, 返回 (score, params, result)."""
    t1 = systime.time()
    kwargs = dict(
        start=start, end=end,
        mode="chase",
        top_n=params["top_n"],
        board_filter=board,
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
        chase_threshold_low_main=params["chase_threshold_low_main"],
        chase_threshold_high_main=params["chase_threshold_high_main"],
        chase_threshold_low_20cm=params["chase_threshold_low_20cm"],
        chase_threshold_high_20cm=params["chase_threshold_high_20cm"],
        chase_slip=params["chase_slip"],
        chase_require_close_locked=params["chase_require_close_locked"],
        chase_min_streak=params["chase_min_streak"],
        chase_exit_rule=params["chase_exit_rule"],
        sample=0,
    )
    try:
        result = zt.run_zt_backtest(**kwargs)
    except Exception as e:
        log.warning("回测失败: %s | params=%s", e, params)
        return -1e9, params, {"summary": {"trades": 0}}
    score = opt._score(result)
    elapsed = systime.time() - t1
    s = result.get("summary", {}) or {}
    log.info("  score=%.1f | 笔数=%d 胜率=%.1f%% 总收益=%.1f%% 月均=%.1f%% 回撤=%.1f%% | %.1fs",
             score, s.get("trades", 0), s.get("win_rate_pct", 0),
             s.get("total_return_pct", 0), s.get("monthly_avg_total_pct", 0),
             s.get("max_drawdown_pct", 0), elapsed)
    return score, params, result


def main():
    p = argparse.ArgumentParser(description="追板 1000 轮优化")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2026-07-23")
    p.add_argument("--rounds", type=int, default=1000)
    p.add_argument("--population", type=int, default=30)
    p.add_argument("--random-ratio", type=float, default=0.4)
    p.add_argument("--crossover-ratio", type=float, default=0.4)
    p.add_argument("--refine-ratio", type=float, default=0.2)
    p.add_argument("--board", default="all")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-time-hours", type=float, default=24.0)
    p.add_argument("--checkpoint-every", type=int, default=5)
    p.add_argument("--target-monthly", type=float, default=200.0)
    p.add_argument("--target-wr", type=float, default=50.0)
    p.add_argument("--target-dd", type=float, default=-30.0)
    p.add_argument("--target-trades", type=int, default=50)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.quiet else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    start_yyyymmdd = args.start.replace("-", "")
    end_yyyymmdd = args.end.replace("-", "")

    print(f"========== 追板 1000 轮优化 ==========")
    print(f"start={args.start} end={args.end} rounds={args.rounds}")
    print(f"seed={args.seed} resume={args.resume} board={args.board}")
    print(f"target: 月均 ≥ {args.target_monthly}% | WR ≥ {args.target_wr}% | "
          f"DD ≥ {args.target_dd}% | trades ≥ {args.target_trades}")

    t_start = systime.time()
    deadline = t_start + args.max_time_hours * 3600

    # Resume
    state = None
    if args.resume:
        state = _load_checkpoint()
        if state:
            print(f"从 checkpoint 恢复: iter={state.get('iter', 0)}")
        else:
            print(f"未找到 checkpoint, 从头开始")

    history = (state or {}).get("history", [])
    best = (state or {}).get("best", {"score": -1e9, "params": None, "result_summary": None})
    n_total = (state or {}).get("n_total", 0)

    _rand.seed(args.seed)
    np.random.seed(args.seed)

    n_rand = int(args.rounds * args.random_ratio)
    n_cross = int(args.rounds * args.crossover_ratio)
    n_refine = int(args.rounds * args.refine_ratio)

    # ── Phase 1: 随机搜索 ──
    print(f"\nPhase 1: 随机搜索 {n_rand} 次...")
    for i in range(n_rand):
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽 ({args.max_time_hours}h)")
            break

        params = opt._random_params()
        score, p, r = _eval_one(params, start_yyyymmdd, end_yyyymmdd, args.board)
        history.append({"iter": n_total + i + 1, "phase": "random",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            s = best["result_summary"] or {}
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f}")
            print(f"    trades={s.get('trades',0)} WR={s.get('win_rate_pct',0):.1f}% "
                  f"avg={s.get('avg_return_pct',0):.2f}% "
                  f"cum={s.get('total_return_pct',0):.1f}% "
                  f"月均={s.get('monthly_avg_total_pct',0):.1f}% "
                  f"DD={s.get('max_drawdown_pct',0):.1f}%")
            _save_monthly(r.get("monthly", []))

        if (n_total + i + 1) % args.checkpoint_every == 0:
            _save_checkpoint({"iter": n_total + i + 1, "best": best,
                              "history": history, "n_total": n_total + i + 1})
            _save_history({"iter": n_total + i + 1, "best_so_far": best["score"]})

    n_total += n_rand
    print(f"Phase 1 完成 | best score={best['score']:.2f}")

    # 截止检查
    if _is_target_met(best, args):
        print(f"\n🎯 Phase 1 已达标!")
        _save_checkpoint({"iter": n_total, "best": best, "history": history,
                          "n_total": n_total, "done": True})
        _save_best(best)
        return

    # ── Phase 2: 交叉搜索 ──
    print(f"\nPhase 2: 交叉搜索 {n_cross} 次...")
    elite = []
    history_sorted = sorted(history, key=lambda h: h.get("score", -1e9), reverse=True)
    for h in history_sorted[:args.population]:
        if h.get("params"):
            elite.append({"params": h["params"], "score": h["score"]})

    for i in range(n_cross):
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽")
            break
        if len(elite) < 2:
            break

        a = _rand.choice(elite)["params"]
        b = _rand.choice(elite)["params"]
        child = opt._mutate(opt._crossover(a, b))
        score, p, r = _eval_one(child, start_yyyymmdd, end_yyyymmdd, args.board)
        history.append({"iter": n_total + i + 1, "phase": "crossover",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            _save_monthly(r.get("monthly", []))
            s = best["result_summary"]
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f} "
                  f"月均={s.get('monthly_avg_total_pct',0):.1f}% "
                  f"WR={s.get('win_rate_pct',0):.1f}% DD={s.get('max_drawdown_pct',0):.1f}%")
        elite.append({"params": p, "score": score})
        elite.sort(key=lambda x: -x["score"])
        elite = elite[:args.population]

        if (n_total + i + 1) % args.checkpoint_every == 0:
            _save_checkpoint({"iter": n_total + i + 1, "best": best,
                              "history": history, "n_total": n_total + i + 1})

    n_total += n_cross
    print(f"Phase 2 完成 | best score={best['score']:.2f}")

    if _is_target_met(best, args):
        print(f"\n🎯 Phase 2 已达标!")
        _save_checkpoint({"iter": n_total, "best": best, "history": history,
                          "n_total": n_total, "done": True})
        _save_best(best)
        return

    # ── Phase 3: 微调搜索 ──
    print(f"\nPhase 3: 微调搜索 {n_refine} 次...")
    for i in range(n_refine):
        if systime.time() > deadline:
            break
        if not elite:
            break

        base = _rand.choice(elite[:max(3, len(elite) // 3)])["params"]
        refined = opt._refine(base)
        score, p, r = _eval_one(refined, start_yyyymmdd, end_yyyymmdd, args.board)
        history.append({"iter": n_total + i + 1, "phase": "refine",
                        "params": p, "score": score,
                        "summary": (r.get("summary", {}) or {}) if r else {}})
        if score > best["score"]:
            best = {"score": score, "params": p, "result_summary": r.get("summary", {})}
            _save_best(best)
            _save_monthly(r.get("monthly", []))
            s = best["result_summary"]
            print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.2f} "
                  f"月均={s.get('monthly_avg_total_pct',0):.1f}% "
                  f"WR={s.get('win_rate_pct',0):.1f}% DD={s.get('max_drawdown_pct',0):.1f}%")

        if (n_total + i + 1) % args.checkpoint_every == 0:
            _save_checkpoint({"iter": n_total + i + 1, "best": best,
                              "history": history, "n_total": n_total + i + 1})

    n_total += n_refine
    print(f"Phase 3 完成 | best score={best['score']:.2f}")

    _save_checkpoint({"iter": n_total, "best": best, "history": history,
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
              f"月均={s.get('monthly_avg_total_pct',0):.1f}% "
              f"DD={s.get('max_drawdown_pct',0):.1f}%")
    print(f"checkpoint: {CHECKPOINT_FILE}")
    print(f"best: {BEST_FILE}")
    print(f"monthly: {MONTHLY_FILE}")


def _is_target_met(best: dict, args) -> bool:
    """检查是否达标."""
    s = best.get("result_summary") or {}
    if not s:
        return False
    monthly = s.get("monthly_avg_total_pct", 0)
    wr = s.get("win_rate_pct", 0)
    dd = s.get("max_drawdown_pct", -100)
    trades = s.get("trades", 0)
    return (monthly >= args.target_monthly
            and wr >= args.target_wr
            and dd >= args.target_dd
            and trades >= args.target_trades)


if __name__ == "__main__":
    main()