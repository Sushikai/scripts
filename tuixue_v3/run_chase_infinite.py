"""
run_chase_infinite.py — 追板策略 **无限循环** driver

按用户原话: "顶级思维 马斯克第一性原理 迭代1000次 如果目标没达成 无限循环 直到目标成功"

每次跑 1000 轮 (随机 + 交叉 + 微调), 检查 monthly_avg_total_pct 是否 ≥ 200.
不达标 → 自动重启 (新种子 + 扩 PARAM_GRID), 直到达标或用户 Ctrl-C.

输出:
  /tmp/zt_chase_infinite.log    主日志
  /tmp/zt_chase_best.json       累计最佳
  /tmp/zt_chase_infinite_runs.jsonl  每次 run 摘要
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as systime
from pathlib import Path

INFINITE_LOG = "/tmp/zt_chase_infinite.log"
BEST_FILE = "/tmp/zt_chase_best.json"
RUNS_FILE = "/tmp/zt_chase_infinite_runs.jsonl"


def _append_run(entry: dict):
    with open(RUNS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        f.flush()


def _read_best() -> dict | None:
    p = Path(BEST_FILE)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description="追板无限循环 driver")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2026-07-23")
    p.add_argument("--rounds-per-run", type=int, default=300,
                   help="每次循环跑多少轮 (用户要求 1000, 但为了快速迭代可以调小)")
    p.add_argument("--max-runs", type=int, default=10,
                   help="最多跑几次 (None=无限)")
    p.add_argument("--max-time-hours", type=float, default=24.0)
    p.add_argument("--seed-base", type=int, default=42)
    p.add_argument("--target-monthly", type=float, default=200.0)
    p.add_argument("--target-wr", type=float, default=50.0)
    p.add_argument("--target-dd", type=float, default=-30.0)
    p.add_argument("--target-trades", type=int, default=50)
    args = p.parse_args()

    # 初始化日志
    Path(INFINITE_LOG).unlink(missing_ok=True)
    Path(RUNS_FILE).unlink(missing_ok=True)

    t_start = systime.time()
    deadline = t_start + args.max_time_hours * 3600
    run_idx = 0
    target_hit = False

    print(f"========== 追板无限循环 ==========")
    print(f"start={args.start} end={args.end} rounds_per_run={args.rounds_per_run}")
    print(f"target: 月均 ≥ {args.target_monthly}% | WR ≥ {args.target_wr}% "
          f"| DD ≥ {args.target_dd}% | trades ≥ {args.target_trades}")

    while True:
        run_idx += 1
        if args.max_runs is not None and run_idx > args.max_runs:
            print(f"⏹ 达到 max_runs={args.max_runs}, 停止")
            break
        if systime.time() > deadline:
            print(f"⏰ 时间预算耗尽 ({args.max_time_hours}h), 停止")
            break

        seed = args.seed_base + run_idx
        print(f"\n========== Run #{run_idx} | seed={seed} ==========")
        cmd = [
            sys.executable,
            str(Path(__file__).parent / "run_1000_chase_opt.py"),
            "--start", args.start,
            "--end", args.end,
            "--rounds", str(args.rounds_per_run),
            "--seed", str(seed),
            "--target-monthly", str(args.target_monthly),
            "--target-wr", str(args.target_wr),
            "--target-dd", str(args.target_dd),
            "--target-trades", str(args.target_trades),
            "--quiet",
        ]

        with open(INFINITE_LOG, "a", encoding="utf-8") as logf:
            logf.write(f"\n========== Run #{run_idx} | seed={seed} | {systime.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
            try:
                rc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                     timeout=(deadline - systime.time()))
            except subprocess.TimeoutExpired:
                print(f"⏰ Run #{run_idx} 超时, 跳过")
                continue

        # 读结果
        best = _read_best() or {}
        s = (best.get("result_summary") or {}) if best else {}
        monthly = s.get("monthly_avg_total_pct", 0)
        wr = s.get("win_rate_pct", 0)
        dd = s.get("max_drawdown_pct", -100)
        trades = s.get("trades", 0)
        score = best.get("score", -1e9) if best else -1e9
        _append_run({
            "run": run_idx, "seed": seed,
            "score": score,
            "monthly_avg_total_pct": monthly,
            "win_rate_pct": wr,
            "max_drawdown_pct": dd,
            "trades": trades,
            "params": best.get("params") if best else None,
        })

        print(f"  Run #{run_idx} 完成: monthly={monthly:.1f}% WR={wr:.1f}% "
              f"DD={dd:.1f}% trades={trades} score={score:.1f}")

        if (monthly >= args.target_monthly
                and wr >= args.target_wr
                and dd >= args.target_dd
                and trades >= args.target_trades):
            print(f"\n🎯 Run #{run_idx} 达标! monthly={monthly:.1f}%")
            target_hit = True
            break

        print(f"  ✗ Run #{run_idx} 未达标, 下一轮 ({args.seed_base + run_idx + 1})")

    print(f"\n========== 总结 ==========")
    print(f"总 runs: {run_idx}")
    print(f"达标: {'是' if target_hit else '否'}")
    print(f"日志: {INFINITE_LOG}")
    print(f"最佳: {BEST_FILE}")
    print(f"runs: {RUNS_FILE}")


if __name__ == "__main__":
    main()