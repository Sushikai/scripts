"""
zt_verify.py — 验证循环：优化 → 验证 → 指标检查 → 循环直到达标。
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime
from typing import Any

from . import zt_backtest as zt
from . import zt_config as cfg
from . import zt_optimizer as opt

log = logging.getLogger("tuixue_v3.zt_verify")


def verify(
    iterations: int = cfg.ZT_OPT_ITERATIONS,
    max_loops: int = cfg.ZT_VERIFY_MAX_ITERATIONS,
) -> dict:
    """验证循环：优化 → 验证 → 指标检查 → 循环直到达标。

    达标条件（日均收益 ≥ 5% 且月收益 ≥ 200% 且 胜率 ≥ 50% 且 回撤 > -30%）：
      满足 → 返回结果，标记 passed=True
      不满足 → 扩大搜索空间继续优化
    """
    log.info("========== ZT 验证循环开始 ==========")
    t0 = systime.time()

    # 阶段1: 优化窗口
    log.info("阶段1: 优化窗口 %s→%s", cfg.ZT_START, cfg.ZT_OPTIMIZE_WINDOW_END)

    current_iters = iterations
    best_result = None

    for loop in range(1, max_loops + 1):
        log.info("--- 验证轮次 %d/%d (iter=%d) ---", loop, max_loops, current_iters)

        opt_result = opt.run_optimize(
            start=cfg.ZT_START,
            end=cfg.ZT_OPTIMIZE_WINDOW_END,
            iterations=current_iters,
            population=min(50, current_iters // 2),
        )

        best_params = opt_result["best_params"]
        best_score = opt_result["best_score"]
        log.info("优化完成: score=%.1f params=%s", best_score, best_params)

        # 阶段2: 留出验证
        log.info("阶段2: 留出验证...")
        holdout = zt.run_zt_backtest(
            start=cfg.ZT_OPTIMIZE_WINDOW_END,
            end=cfg.ZT_END,
            **{k: best_params[k] for k in [
                "min_streak", "max_streak", "burst_max", "sealed_before",
                "mcap_min_yi", "mcap_max_yi",
                "turnover_min_pct", "turnover_max_pct",
                "limit_order_min_yi", "top_n",
                "trail_activate_pct", "trail_pullback_pct", "stop_loss_pct",
            ]},
            board_filter=cfg.ZT_BOARD_FILTER,
            entry_rule=cfg.ZT_ENTRY_RULE,
            sample=0,
        )

        hs = holdout.get("summary", {}) or {}
        log.info("留出验证: 笔数=%d 胜率=%.1f%% 日均=%.2f%% 总收益=%.1f%% 回撤=%.1f%%",
                 hs.get("trades", 0), hs.get("win_rate_pct", 0),
                 hs.get("daily_avg_return_pct", 0),
                 hs.get("total_return_pct", 0), hs.get("max_drawdown_pct", 0))

        # 阶段3: 达标检查
        passed, reasons = _check_targets(hs, holdout)
        result = {
            "loop": loop,
            "passed": passed,
            "reasons": reasons,
            "best_params": best_params,
            "best_score": best_score,
            "train_result": opt_result["best_result"],
            "holdout_result": holdout,
            "config": {
                "start": cfg.ZT_START,
                "optimize_end": cfg.ZT_OPTIMIZE_WINDOW_END,
                "end": cfg.ZT_END,
                "iterations": current_iters,
            },
        }

        if passed:
            log.info("✅ 验证通过! 轮次 %d", loop)
            best_result = result
            break
        else:
            log.warning("❌ 验证未通过: %s", reasons)
            current_iters = min(current_iters * 2, 1000)  # 加倍搜索
            log.info("扩大搜索空间至 %d 次迭代", current_iters)
            best_result = result

    elapsed = systime.time() - t0
    log.info("========== ZT 验证循环%s | %.0fs ==========",
             "通过" if (best_result and best_result["passed"]) else "终止", elapsed)

    return {
        "passed": best_result["passed"] if best_result else False,
        "best_params": best_result["best_params"] if best_result else {},
        "final_result": best_result,
        "holdout_result": best_result["holdout_result"] if best_result else {},
        "elapsed_sec": elapsed,
        "ts": datetime.now().isoformat(),
    }


def _check_targets(summary: dict, raw: dict) -> tuple[bool, list[str]]:
    """检查是否达标。"""
    reasons = []
    trades = summary.get("trades", 0)

    if trades < 20:
        return False, [f"交易笔数太少: {trades}"]

    # 日均收益 (compound)
    total_ret = summary.get("total_return_pct", 0) or 0
    trading_days = raw.get("trade_dates_total", 1)
    if trading_days > 0:
        compound_daily = ((1 + total_ret / 100) ** (1 / max(trading_days, 1)) - 1) * 100
    else:
        compound_daily = 0

    win_rate = summary.get("win_rate_pct", 0) or 0
    max_dd = summary.get("max_drawdown_pct", 0) or 0

    # Compound monthly
    trading_days_month = 20
    compound_monthly = ((1 + compound_daily / 100) ** trading_days_month - 1) * 100

    checks = {
        "日均收益 ≥ 5%": compound_daily >= 5.0,
        "月收益 ≥ 200%": compound_monthly >= 200.0,
        "胜率 ≥ 50%": win_rate >= 50.0,
        "回撤 > -30%": max_dd > -30.0,
    }

    for label, ok in checks.items():
        if not ok:
            val_map = {
                "日均收益": f"{compound_daily:.2f}%",
                "月收益": f"{compound_monthly:.1f}%",
                "胜率": f"{win_rate:.1f}%",
                "回撤": f"{max_dd:.1f}%",
            }
            actual = next((v for k, v in val_map.items() if k in label), "?")
            reasons.append(f"{label}: 当前 {actual}")

    return len(reasons) == 0, reasons


# ── CLI ──

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ZT 验证循环")
    p.add_argument("--iter", type=int, default=cfg.ZT_OPT_ITERATIONS)
    p.add_argument("--max-loops", type=int, default=cfg.ZT_VERIFY_MAX_ITERATIONS)
    args = p.parse_args()

    r = verify(iterations=args.iter, max_loops=args.max_loops)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cli()
