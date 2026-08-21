"""
run_1000_zt_opt_v2.py — ZT 1000+ 轮参数优化 driver (v2: 杠杆 + 扩展网格)

Usage:
    python run_1000_zt_opt_v2.py --rounds 1000 --sample 200
    python run_1000_zt_opt_v2.py --rounds 1000 --resume --max-time-hours 12
    python run_1000_zt_opt_v2.py --rounds 500 --no-leverage  # 仅无杠杆
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time as systime
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tuixue_v3 import zt_optimizer, zt_config as cfg

log = logging.getLogger("tuixue_v3.run_1000_zt_opt_v2")

CHECKPOINT_FILE = "/tmp/zt_opt_v2_checkpoint.json"
BEST_FILE = "/tmp/zt_opt_v2_best.json"
HISTORY_FILE = "/tmp/zt_opt_v2_history.jsonl"


def _save_best(best: dict):
    Path(BEST_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(BEST_FILE, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2, default=str)


def _append_history(entry: dict):
    Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _progress_cb(data: dict):
    """优化器进度回调 — 写 history + 打印里程碑."""
    phase = data.get("phase", "?")
    it = data.get("iter", 0)
    total = data.get("total", 0)
    best = data.get("best_score", -9999)

    if it % 25 == 0 or phase in ("cache_done",):
        ts = datetime.now().strftime("%H:%M:%S")
        pct = f"{it / max(total, 1) * 100:.0f}%" if total else ""
        print(f"  [{ts}] {phase} {it}/{total} {pct}  best={best:.1f}")


def main():
    p = argparse.ArgumentParser(description="ZT 1000+ 轮参数优化 v2")
    p.add_argument("--start", default=cfg.ZT_START)
    p.add_argument("--end", default=cfg.ZT_OPTIMIZE_WINDOW_END)
    p.add_argument("--rounds", type=int, default=1000)
    p.add_argument("--population", type=int, default=cfg.ZT_OPT_POPULATION)
    p.add_argument("--random-ratio", type=float, default=cfg.ZT_OPT_RANDOM_RATIO)
    p.add_argument("--crossover-ratio", type=float, default=cfg.ZT_OPT_CROSSOVER_RATIO)
    p.add_argument("--refine-ratio", type=float, default=cfg.ZT_OPT_REFINE_RATIO)
    p.add_argument("--board", default=cfg.ZT_BOARD_FILTER)
    p.add_argument("--sample", type=int, default=200, help="采样数 (0=全市场, 200=加速)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-time-hours", type=float, default=24.0)
    p.add_argument("--no-leverage", action="store_true", help="只用无杠杆 (leverage=1.0)")
    p.add_argument("--checkpoint", default=CHECKPOINT_FILE, help="checkpoint 路径")
    args = p.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    # 如果 --no-leverage, 限制 PARAM_GRID 只搜 1.0
    if args.no_leverage:
        zt_optimizer.PARAM_GRID["leverage_factor"] = [1.0]
        print("🔒 仅搜索无杠杆 (leverage=1.0)")

    print(f"========== ZT 1000 轮优化 v2 ==========")
    print(f"  window: {args.start} → {args.end}")
    print(f"  rounds: {args.rounds}  pop: {args.population}")
    print(f"  board: {args.board}  sample: {args.sample}")
    print(f"  seed: {args.seed}  resume: {args.resume}")
    print(f"  max_time: {args.max_time_hours}h")
    print(f"  leverage: {zt_optimizer.PARAM_GRID['leverage_factor']}")
    print(f"  target: 月复利 ≥ {cfg.ZT_MIN_MONTHLY_RETURN_PCT}%")
    print(f"  checkpoint: {args.checkpoint}")

    t_start = systime.time()

    checkpoint = args.checkpoint if args.resume else None

    result = zt_optimizer.run_optimize(
        start=args.start,
        end=args.end,
        iterations=args.rounds,
        population=args.population,
        random_ratio=args.random_ratio,
        crossover_ratio=args.crossover_ratio,
        refine_ratio=args.refine_ratio,
        board_filter=args.board,
        checkpoint_path=checkpoint or args.checkpoint,
        sample=args.sample,
        max_time_hours=args.max_time_hours,
        progress_cb=_progress_cb,
    )

    elapsed = systime.time() - t_start
    best = result["best_result"].get("summary", {}) or {}
    monthly = best.get("avg_monthly_compound_pct", 0) or 0
    total = best.get("total_compound_pct", 0) or 0
    total_unlev = best.get("total_compound_unlevered_pct", total)
    leverage = best.get("leverage_factor", 1.0)

    print(f"\n========== 优化完成 ({elapsed/3600:.1f}h) ==========")
    print(f"  best_score: {result['best_score']:.1f}")
    print(f"  params: {json.dumps(result['best_params'], ensure_ascii=False)}")
    print(f"  trades: {best.get('trades', 0)}")
    print(f"  win_rate: {best.get('win_rate_pct', 0):.1f}%")
    print(f"  monthly_compound (levered): {monthly:.1f}%")
    if leverage > 1.0:
        print(f"  total_compound_unlevered: {total_unlev:.1f}%")
    print(f"  total_compound (levered): {total:.1f}%")
    print(f"  max_drawdown: {best.get('max_drawdown_daily_pct', best.get('max_drawdown_pct', 0)):.1f}%")
    print(f"  leverage: {leverage:.2f}x")
    print(f"  margin_interest_monthly: {best.get('margin_interest_monthly_pct', 0):.2f}%")

    # 达标判断
    if monthly >= cfg.ZT_MIN_MONTHLY_RETURN_PCT:
        print(f"\n  TARGET HIT: monthly={monthly:.1f}% >= {cfg.ZT_MIN_MONTHLY_RETURN_PCT}%")
    else:
        gap = cfg.ZT_MIN_MONTHLY_RETURN_PCT - monthly
        print(f"\n  GAP: {gap:.1f}% short of {cfg.ZT_MIN_MONTHLY_RETURN_PCT}% target")

    _save_best({
        "best_params": result["best_params"],
        "best_score": result["best_score"],
        "best_summary": best,
        "elite": [{"score": s, "params": p} for s, p in result.get("elite", [])[:10]],
        "elapsed_sec": elapsed,
        "config": vars(args),
        "ts": datetime.now().isoformat(),
    })

    print(f"\n  best saved: {BEST_FILE}")
    print(f"  checkpoint: {args.checkpoint}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
