"""
run_10000_winrate.py — 涨停 T+1 策略胜率提升至 ≥80% 训练 driver (2026-08-10 R65)

设计目标:
  - 10000 轮起点 → 不达标 → 翻倍 → 无限循环, 直到 win_rate ≥ 80%
  - 时间预算默认 6h (避免无限跑挂死)
  - checkpoint + resume 支持
  - cache_store OPTIMIZER_BEST 落盘
  - 输出 /api/zt/winrate_progress 端点 (前端实时看进度)

用户原话:
  - "涨停溢价页面胜率迭代 提高到80%以上 迭代10000次 如果达不到 则无限迭代"
  - "允许空仓 各种环境结合起来 维度越多越好"
  - "各种环境 各种交易手段 枚举迭代优化 达不到目标 则无限循环 直到目标达成"
  - "维度不够吧 你尽可能枚举各种维度"

用法:
    python run_10000_winrate.py --initial-rounds 10000 --target-wr 80 \
        --max-time-hours 6 --workers 8 --seed 42 --resume
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import random
import sys
import time as systime
from pathlib import Path

# 2026-08-11: tuixue_v3 是 src-layout 包, 父目录加入 path 让 `from tuixue_v3 import ...` 工作
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT.parent))

import numpy as np

# 必须在 import zt_optimizer 前设 log level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/zt_winrate_run.log", mode="a"),
    ],
)
log = logging.getLogger("10000_winrate")

ROOT = Path(__file__).resolve().parent

CHECKPOINT_FILE = "/tmp/zt_winrate_checkpoint.json"
BEST_FILE = "/tmp/zt_winrate_best.json"
HISTORY_FILE = "/tmp/zt_winrate_history.jsonl"
PROGRESS_FILE = "/tmp/zt_winrate_progress.json"  # 给前端 /api/zt/winrate_progress 读


# ── 进度落盘 (前端 API) ──────────────────────────────────────

def _save_progress(phase: str, iter_done: int, total: int,
                   best_wr: float, best_trades: int, best_score: float,
                   target_wr: float, started_at: float, elapsed: float,
                   round_idx: int = 1, max_rounds: int = 99,
                   status: str = "running", note: str = "") -> None:
    """写 /tmp/zt_winrate_progress.json (供前端 /api/zt/winrate_progress 读)"""
    eta = ""
    if iter_done > 0 and elapsed > 0:
        per_iter = elapsed / iter_done
        remain = (total - iter_done) * per_iter
        eta = f"{remain:.0f}s ({remain/60:.1f}min)"
    data = {
        "phase": phase,
        "iter_done": iter_done,
        "total": total,
        "best_win_rate_pct": best_wr,
        "best_trades": best_trades,
        "best_score": best_score,
        "target_win_rate_pct": target_wr,
        "target_met": best_wr >= target_wr,
        "round_idx": round_idx,
        "max_rounds": max_rounds,
        "started_at": started_at,
        "elapsed_sec": elapsed,
        "eta_sec": eta,
        "status": status,
        "note": note,
        "updated_at": systime.time(),
    }
    try:
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_FILE)
    except Exception as e:
        log.debug(f"_save_progress err: {e}")


def _save_history(entry: dict):
    Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        f.flush()


def _save_best(best: dict):
    with open(BEST_FILE, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2, default=str)
        f.flush()


def _save_checkpoint(state: dict):
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
    os.replace(tmp, CHECKPOINT_FILE)


def _load_checkpoint():
    p = Path(CHECKPOINT_FILE)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _publish_to_cache_store(best: dict, n_total: int, round_idx: int,
                            target_wr: float, target_met: bool,
                            elapsed_sec: float, wr_floor: float = 50.0) -> None:
    """把当前 best 的 filter params + weights 写入 cache_store OPTIMIZER_BEST。

    R125 迭代循环: 每轮末调用, 让 live_pick 实时用上训练出的最优参数,
    不需要等整晚训练结束才生效。只升不降:
    - 未达标且 WR < wr_floor 不发布 (避免把比默认更差的权重推给 live)
    - 已发布过更优 score 则跳过
    """
    if not best.get("params"):
        return
    s = best.get("result_summary") or {}
    wr = s.get("win_rate_pct", 0) or 0
    if not target_met and wr < wr_floor:
        log.info(f"round {round_idx}: best WR={wr:.1f}% < {wr_floor}% 质量线, 暂不发布")
        return
    try:
        from tuixue_v3 import cache_store as cs
        from tuixue_v3 import zt_optimizer as opt
        prev = cs.get_store().get(cs.K.OPTIMIZER_BEST)
        if (isinstance(prev, dict) and prev.get("score") is not None
                and best.get("score", -1e9) <= prev.get("score", -1e9)):
            log.info(f"round {round_idx}: best score {best['score']:.1f} "
                     f"<= 已发布 {prev.get('score')}, 跳过")
            return
        fp, w = opt.split_weights(best["params"])
        cs.get_store().set(cs.K.OPTIMIZER_BEST, {
            "params": fp,
            "weights": w,
            "score": best["score"],
            "trades": best["result_summary"].get("trades", 0),
            "win_rate": best["result_summary"].get("win_rate_pct", 0),
            "total_ret": best["result_summary"].get("total_return_pct", 0),
            "max_dd": best["result_summary"].get("max_drawdown_pct", 0),
            "equity_ret": best["result_summary"].get("equity_return_pct", 0),
            "equity_dd": best["result_summary"].get("equity_max_drawdown_pct", 0),
            "annualized_ret": best["result_summary"].get("annualized_return_pct", 0),
            "iterations": n_total,
            "elapsed_sec": round(elapsed_sec, 1),
            "updated_at": systime.time(),
            "source_file": BEST_FILE,
            "winrate_target_met": target_met,
            "winrate_target_pct": target_wr,
            "rounds_done": round_idx,
            # R126: 把月度明细也带上, 前端面板与回测调用方能直接读
            "monthly_breakdown": best["result_summary"].get("monthly_breakdown") or [],
        })
        log.info(f"已写入 cache_store OPTIMIZER_BEST "
                 f"(WR={best['result_summary'].get('win_rate_pct', 0):.1f}%, "
                 f"target_met={target_met}, round={round_idx})")
    except Exception as e:
        log.error(f"cache_store 写入失败: {e}")


# ── 单次评估 (multiprocessing 安全, 接 fp + weights) ─────────

_GLOBAL_PREBUILT = None
# R125 2026-08-12: 评分用真实目标胜率 (由 main() 从 --target-wr 设置).
# 之前 score_winrate_focused 硬编码 80%, GA 在诚实天花板 ~58-63% 附近
# 毫无梯度 (30+ 笔需 >63% 才能赢过 -971 平台), 训练只会朝 29 笔死磕.
_SCORE_TARGET_WR = 80.0


def _eval_one(params: dict, start: str, end: str, board: str) -> tuple:
    """跑一次回测, 返回 (score, params, result)。复用 _GLOBAL_PREBUILT 大幅加速。"""
    from tuixue_v3 import zt_backtest as zt
    from tuixue_v3 import zt_optimizer as opt

    # 2026-08-12 R69: 可执行性硬过滤 (只检查 filter params, 不含 weights)
    fp_chk, _ = opt.split_weights(params)
    ok, reason = opt._verify_executable(fp_chk)
    if not ok:
        return -3000, params, {"summary": {"trades": 0}, "_rejected": reason}

    t1 = systime.time()
    fp, weights = opt.split_weights(params)
    try:
        result = zt.run_zt_backtest(
            start=start, end=end,
            top_n=fp["top_n"],
            board_filter=board,
            min_streak=fp["min_streak"],
            max_streak=fp["max_streak"],
            burst_max=fp["burst_max"],
            sealed_before=fp["sealed_before"],
            mcap_min_yi=fp["mcap_min_yi"],
            mcap_max_yi=fp["mcap_max_yi"],
            turnover_min_pct=fp["turnover_min_pct"],
            turnover_max_pct=fp["turnover_max_pct"],
            limit_order_min_yi=fp["limit_order_min_yi"],
            trail_activate_pct=fp["trail_activate_pct"],
            trail_pullback_pct=fp["trail_pullback_pct"],
            stop_loss_pct=fp["stop_loss_pct"],
            hold_for_zt=fp["hold_for_zt"],
            weights=weights,
            exit_strategy=fp.get("exit_strategy", "trail_t2"),
            market_filter_mode=fp.get("market_filter_mode", "off"),
            breadth_up_min=int(fp.get("breadth_up_min", 0)),
            zt_pool_min=int(fp.get("zt_pool_min", 0)),
            vol_ratio_min=float(fp.get("vol_ratio_min", 0.0)),
            limit_up_pct_min=float(fp.get("limit_up_pct_min", 9.5)),
            burst_window_3d=int(fp.get("burst_window_3d", 99)),
            yiziban_required=bool(fp.get("yiziban_required", False)),
            gap_open_required=bool(fp.get("gap_open_required", False)),
            pct_chg_5d_max=float(fp.get("pct_chg_5d_max", 999.0)),
            pct_chg_5d_min=float(fp.get("pct_chg_5d_min", 0.0)),
            sector_limit=int(fp.get("sector_limit", 0)),
            mcap_strict=bool(fp.get("mcap_strict", False)),
            is_st_exclude=bool(fp.get("is_st_exclude", True)),
            entry_rule=fp.get("entry_rule", "open_t1"),
            # 2026-08-11: R66 + R67 维度 (K线/技术指标/量价/时间) 真实传递到回测
            ma_align_required=int(fp.get("ma_align_required", 0)),
            trend_5d_min=float(fp.get("trend_5d_min", -999.0)),
            vol_trend_min=float(fp.get("vol_trend_min", 0.0)),
            upper_shadow_max=float(fp.get("upper_shadow_max", 999.0)),
            body_at_pct_min=float(fp.get("body_at_pct_min", 0.0)),
            ma_converge_max=float(fp.get("ma_converge_max", 999.0)),
            kdj_k_max=float(fp.get("kdj_k_max", 999.0)),
            rsi_6_max=float(fp.get("rsi_6_max", 999.0)),
            boll_dist_upper_min=float(fp.get("boll_dist_upper_min", -999.0)),
            macd_dif_min=float(fp.get("macd_dif_min", -999.0)),
            macd_dif_chg_required=int(fp.get("macd_dif_chg_required", 0)),
            gap_pct_min=float(fp.get("gap_pct_min", -999.0)),
            vp_same_dir_min=int(fp.get("vp_same_dir_min", 0)),
            vol_shrink_required=bool(fp.get("vol_shrink_required", False)),
            vol_top_div_max=int(fp.get("vol_top_div_max", 99)),
            vol_step_min=float(fp.get("vol_step_min", 0.0)),
            promote_ratio_min=float(fp.get("promote_ratio_min", 0.0)),
            prev_zt_avg_ret_min=float(fp.get("prev_zt_avg_ret_min", -999.0)),
            strong_zt_ratio_min=float(fp.get("strong_zt_ratio_min", 0.0)),
            yang_bao_yin_required=bool(fp.get("yang_bao_yin_required", False)),
            weekday_allow=str(fp.get("weekday_allow", "all")),
            avoid_report_window=bool(fp.get("avoid_report_window", False)),
            sample=0,
            _prebuilt=_GLOBAL_PREBUILT,
        )
    except Exception as e:
        log.warning("回测失败: %s | params=%s", e, params)
        return -1e9, params, {"summary": {"trades": 0}}

    score = opt.score_winrate_focused(
        result, target_wr=_SCORE_TARGET_WR,
        exit_strategy=fp.get("exit_strategy", "trail_t2"))
    elapsed = systime.time() - t1
    s = result.get("summary", {}) or {}
    return score, params, result


def _eval_worker(args):
    """multiprocessing pool worker — 接 (params, start, end, board)"""
    params, start, end, board = args
    score, p, r = _eval_one(params, start, end, board)
    return score, p, r


def _init_worker(prebuilt):
    """Pool initializer — 全 worker 共享 prebuilt (fork 后只读, 避免 pickle)"""
    global _GLOBAL_PREBUILT
    _GLOBAL_PREBUILT = prebuilt


# ── 目标达成判断 ────────────────────────────────────────────

def _is_target_met(best: dict, target_wr: float, target_trades: int,
                   target_dd: float = -40.0) -> bool:
    """检查胜率 / 笔数 / 回撤 全部达标 (2026-08-12 R69: 防御性拒掉 _rejected)"""
    if best.get("_rejected"):
        return False
    s = best.get("result_summary", {}) or {}
    wr = s.get("win_rate_pct", 0)
    trades = s.get("trades", 0)
    dd = s.get("max_drawdown_pct", -100)
    return (wr >= target_wr
            and trades >= target_trades
            and dd >= target_dd)


# ── 主入口 ──────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="涨停 T+1 胜率≥80% 无限循环训练")
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default="2026-08-04")
    p.add_argument("--initial-rounds", type=int, default=10000,
                   help="每轮 round 训练次数 (起步 10000)")
    p.add_argument("--rounds-mult", type=int, default=2,
                   help="不达标时翻倍倍数 (默认 ×2)")
    p.add_argument("--max-rounds", type=int, default=10,
                   help="最多循环多少轮 (默认 10 = 1024000 轮封顶)")
    p.add_argument("--target-wr", type=float, default=80.0,
                   help="目标胜率 (默认 80 percent)")
    p.add_argument("--target-trades", type=int, default=30,
                   help="目标笔数 (默认 30, 防过拟合)")
    p.add_argument("--target-dd", type=float, default=-40.0,
                   help="目标回撤 (默认 -40 percent)")
    p.add_argument("--population", type=int, default=100)
    p.add_argument("--random-ratio", type=float, default=0.4)
    p.add_argument("--crossover-ratio", type=float, default=0.3)
    p.add_argument("--refine-ratio", type=float, default=0.3)
    p.add_argument("--board", default="all")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-time-hours", type=float, default=6.0)
    p.add_argument("--checkpoint-every", type=int, default=20)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    if not args.quiet:
        logging.getLogger().setLevel(logging.INFO)

    # R125: 让评分函数用真实目标 (fork 后 worker 继承此全局)
    global _SCORE_TARGET_WR
    _SCORE_TARGET_WR = args.target_wr

    start_yyyymmdd = args.start.replace("-", "")
    end_yyyymmdd = args.end.replace("-", "")

    print(f"========== 涨停 T+1 胜率 ≥ {args.target_wr}% 训练 ==========")
    print(f"start={args.start} end={args.end} board={args.board}")
    print(f"initial_rounds={args.initial_rounds} max_rounds={args.max_rounds} "
          f"rounds_mult={args.rounds_mult}")
    print(f"target: 胜率 ≥ {args.target_wr}% | 笔数 ≥ {args.target_trades} | "
          f"DD ≥ {args.target_dd}%")
    print(f"workers={args.workers} max_time={args.max_time_hours}h seed={args.seed}")

    t_start = systime.time()
    deadline = t_start + args.max_time_hours * 3600

    # ── 一次性构建 prebuilt 缓存 (加速 worker, 避免每次重头) ──
    global _GLOBAL_PREBUILT
    print(f"\n构建 prebuilt 缓存 {args.start}→{args.end} board={args.board}...")
    from tuixue_v3 import zt_backtest as zt
    _GLOBAL_PREBUILT = zt.build_zt_cache(start=args.start, end=args.end, board_filter=args.board)
    print(f"prebuilt 完成 (dates={len(_GLOBAL_PREBUILT[1])}, "
          f"stocks={len(_GLOBAL_PREBUILT[2])})\n")
    # 主进程也设置 _GLOBAL_PREBUILT (workers=1 单进程模式时使用)
    # 上面 global 已经设了, 这里无需重复

    # ── Resume ──
    state = None
    if args.resume:
        state = _load_checkpoint()
        if state:
            print(f"从 checkpoint 恢复: iter={state.get('iter', 0)} round={state.get('round_idx', 1)}")
        else:
            print(f"未找到 checkpoint, 从头开始")

    history = (state or {}).get("history", [])
    best = (state or {}).get("best")
    if not best or not isinstance(best, dict):
        best = {"score": -1e9, "params": None,
                "result_summary": {"trades": 0, "win_rate_pct": 0,
                                   "avg_return_pct": 0, "total_return_pct": 0,
                                   "max_drawdown_pct": 0, "equity_return_pct": 0,
                                   "equity_max_drawdown_pct": 0}}
    elif not best.get("result_summary"):
        best["result_summary"] = {"trades": 0, "win_rate_pct": 0}
    n_total = (state or {}).get("n_total", 0)
    round_idx = (state or {}).get("round_idx", 1)
    rounds_done = (state or {}).get("rounds_done", 0)

    random.seed(args.seed)
    np.random.seed(args.seed)

    rounds = args.initial_rounds  # 当前轮训练次数

    # ── 无限循环直到胜率达标 ──
    while round_idx <= args.max_rounds:
        # 时间检查
        if systime.time() > deadline:
            print(f"\n⏰ 时间预算耗尽 ({args.max_time_hours}h)")
            break

        n_rand = int(rounds * args.random_ratio)
        n_cross = int(rounds * args.crossover_ratio)
        n_refine = rounds - n_rand - n_cross

        print(f"\n=== Round {round_idx} | rounds={rounds} | "
              f"rand={n_rand} cross={n_cross} refine={n_refine} ===")

        # 进度 (round 开始时)
        _save_progress(
            phase=f"round{round_idx}.phase1",
            iter_done=n_total, total=n_total + rounds,
            best_wr=best.get("result_summary", {}).get("win_rate_pct", 0),
            best_trades=best.get("result_summary", {}).get("trades", 0),
            best_score=best.get("score", 0),
            target_wr=args.target_wr,
            started_at=t_start,
            elapsed=systime.time() - t_start,
            round_idx=round_idx,
            max_rounds=args.max_rounds,
        )

        from tuixue_v3 import zt_optimizer as opt

        # ── Phase 1: 随机搜索 (并行) ──
        print(f"\nPhase 1: 随机搜索 {n_rand} 次 × {args.workers} workers...")
        # 2026-08-12 R69: round 1 时 5% 种群以 OPTIMAL_PARAMS 为种子的邻域扰动
        # 加速收敛到已知高胜率区域
        phase1_params = []
        if round_idx == 1:
            n_seed = max(1, n_rand // 20)
            try:
                from tuixue_v3.zt_config import OPTIMAL_PARAMS
                # 构造完整 seed (含 weights 默认值, 因为 _random_near 只在 PARAM_GRID 里扰动)
                seed_params = {}
                for k in opt.PARAM_GRID:
                    if k in OPTIMAL_PARAMS:
                        seed_params[k] = OPTIMAL_PARAMS[k]
                    else:
                        seed_params[k] = opt.PARAM_GRID[k][0]
                for w in opt.WEIGHT_KEYS:
                    if w not in seed_params:
                        seed_params[w] = opt.PARAM_GRID[w][0] if w in opt.PARAM_GRID else 0
                for _ in range(n_seed):
                    phase1_params.append(opt._random_near(seed_params, 0.5))
                print(f"  [seed] {n_seed} 个 params 来自 OPTIMAL_PARAMS 邻域扰动")
            except Exception as e:
                log.warning(f"seed OPTIMAL_PARAMS 失败: {e}")
        # 补齐到 n_rand
        while len(phase1_params) < n_rand:
            phase1_params.append(opt._random_params())
        if args.workers > 1:
            ctx = mp.get_context("fork")
            with ctx.Pool(processes=args.workers, initializer=_init_worker,
                          initargs=(_GLOBAL_PREBUILT,)) as pool:
                args_iter = [(p, start_yyyymmdd, end_yyyymmdd, args.board)
                             for p in phase1_params]
                for i, (score, p, r) in enumerate(
                        pool.imap_unordered(_eval_worker, args_iter)):
                    s = r.get("summary", {}) or {}
                    history.append({"iter": n_total + i + 1, "phase": "random",
                                    "params": p, "score": score,
                                    "win_rate": s.get("win_rate_pct", 0),
                                    "trades": s.get("trades", 0),
                                    "summary": s})
                    if score > best["score"]:
                        best = {"score": score, "params": p,
                                "result_summary": s}
                        _save_best(best)
                        print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                              f"WR={s.get('win_rate_pct',0):.1f}% "
                              f"trades={s.get('trades',0)}")
                    _save_progress(
                        phase=f"round{round_idx}.phase1",
                        iter_done=n_total + i + 1,
                        total=n_total + rounds,
                        best_wr=best["result_summary"].get("win_rate_pct", 0),
                        best_trades=best["result_summary"].get("trades", 0),
                        best_score=best["score"],
                        target_wr=args.target_wr,
                        started_at=t_start,
                        elapsed=systime.time() - t_start,
                        round_idx=round_idx,
                        max_rounds=args.max_rounds,
                    )
                    if (n_total + i + 1) % args.checkpoint_every == 0:
                        _save_history({"iter": n_total + i + 1,
                                       "best_score": best["score"]})
                        _save_checkpoint({"iter": n_total + i + 1,
                                          "best": best, "history": history,
                                          "n_total": n_total + i + 1,
                                          "round_idx": round_idx,
                                          "rounds_done": rounds_done})
                    if systime.time() > deadline:
                        print("⏰ 时间预算耗尽")
                        break
        else:
            for i, params in enumerate(phase1_params):
                if systime.time() > deadline:
                    break
                score, p, r = _eval_one(params, start_yyyymmdd,
                                        end_yyyymmdd, args.board)
                s = r.get("summary", {}) or {}
                history.append({"iter": n_total + i + 1, "phase": "random",
                                "params": p, "score": score,
                                "win_rate": s.get("win_rate_pct", 0),
                                "trades": s.get("trades", 0),
                                "summary": s})
                if score > best["score"]:
                    best = {"score": score, "params": p, "result_summary": s}
                    _save_best(best)
                    print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                          f"WR={s.get('win_rate_pct',0):.1f}%")
                if (n_total + i + 1) % args.checkpoint_every == 0:
                    _save_checkpoint({"iter": n_total + i + 1,
                                      "best": best, "history": history,
                                      "n_total": n_total + i + 1,
                                      "round_idx": round_idx,
                                      "rounds_done": rounds_done})

        n_total += n_rand
        rounds_done += n_rand

        # 目标检查
        if _is_target_met(best, args.target_wr, args.target_trades, args.target_dd):
            print(f"\n🎯 Round {round_idx} Phase 1 已达标! WR ≥ {args.target_wr}%")
            break

        # ── Phase 2: 交叉搜索 ──
        elite = sorted(history, key=lambda h: h.get("score", -1e9), reverse=True)
        elite = [h for h in elite if h.get("params")][:args.population]
        print(f"\nPhase 2: 交叉搜索 {n_cross} 次 × {args.workers} workers...")
        cross_children = []
        for _ in range(n_cross):
            if len(elite) < 2:
                break
            a = random.choice(elite)["params"]
            b = random.choice(elite)["params"]
            cross_children.append(opt._mutate(opt._crossover(a, b)))

        if args.workers > 1:
            ctx = mp.get_context("fork")
            with ctx.Pool(processes=args.workers, initializer=_init_worker,
                          initargs=(_GLOBAL_PREBUILT,)) as pool:
                args_iter = [(p, start_yyyymmdd, end_yyyymmdd, args.board)
                             for p in cross_children]
                for i, (score, p, r) in enumerate(
                        pool.imap_unordered(_eval_worker, args_iter)):
                    s = r.get("summary", {}) or {}
                    history.append({"iter": n_total + i + 1, "phase": "crossover",
                                    "params": p, "score": score,
                                    "win_rate": s.get("win_rate_pct", 0),
                                    "trades": s.get("trades", 0),
                                    "summary": s})
                    if score > best["score"]:
                        best = {"score": score, "params": p, "result_summary": s}
                        _save_best(best)
                        print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                              f"WR={s.get('win_rate_pct',0):.1f}%")
                    _save_progress(
                        phase=f"round{round_idx}.phase2",
                        iter_done=n_total + i + 1,
                        total=n_total + rounds,
                        best_wr=best["result_summary"].get("win_rate_pct", 0),
                        best_trades=best["result_summary"].get("trades", 0),
                        best_score=best["score"],
                        target_wr=args.target_wr,
                        started_at=t_start,
                        elapsed=systime.time() - t_start,
                        round_idx=round_idx,
                        max_rounds=args.max_rounds,
                    )
                    if (n_total + i + 1) % args.checkpoint_every == 0:
                        _save_checkpoint({"iter": n_total + i + 1,
                                          "best": best, "history": history,
                                          "n_total": n_total + i + 1,
                                          "round_idx": round_idx,
                                          "rounds_done": rounds_done})
                    if systime.time() > deadline:
                        break
        else:
            for i, params in enumerate(cross_children):
                if systime.time() > deadline:
                    break
                score, p, r = _eval_one(params, start_yyyymmdd,
                                        end_yyyymmdd, args.board)
                s = r.get("summary", {}) or {}
                history.append({"iter": n_total + i + 1, "phase": "crossover",
                                "params": p, "score": score,
                                "win_rate": s.get("win_rate_pct", 0),
                                "trades": s.get("trades", 0),
                                "summary": s})
                if score > best["score"]:
                    best = {"score": score, "params": p, "result_summary": s}
                    _save_best(best)
                    print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                          f"WR={s.get('win_rate_pct',0):.1f}%")
                if (n_total + i + 1) % args.checkpoint_every == 0:
                    _save_checkpoint({"iter": n_total + i + 1,
                                      "best": best, "history": history,
                                      "n_total": n_total + i + 1,
                                      "round_idx": round_idx,
                                      "rounds_done": rounds_done})

        n_total += n_cross
        rounds_done += n_cross

        # 目标检查
        if _is_target_met(best, args.target_wr, args.target_trades, args.target_dd):
            print(f"\n🎯 Round {round_idx} Phase 2 已达标!")
            break

        # ── Phase 3: 微调 ──
        print(f"\nPhase 3: 微调搜索 {n_refine} 次 × {args.workers} workers...")
        refined_params = []
        for _ in range(n_refine):
            if not elite:
                break
            base = random.choice(elite[:max(3, len(elite) // 3)])["params"]
            refined_params.append(opt._refine(base))

        if args.workers > 1:
            ctx = mp.get_context("fork")
            with ctx.Pool(processes=args.workers, initializer=_init_worker,
                          initargs=(_GLOBAL_PREBUILT,)) as pool:
                args_iter = [(p, start_yyyymmdd, end_yyyymmdd, args.board)
                             for p in refined_params]
                for i, (score, p, r) in enumerate(
                        pool.imap_unordered(_eval_worker, args_iter)):
                    s = r.get("summary", {}) or {}
                    history.append({"iter": n_total + i + 1, "phase": "refine",
                                    "params": p, "score": score,
                                    "win_rate": s.get("win_rate_pct", 0),
                                    "trades": s.get("trades", 0),
                                    "summary": s})
                    if score > best["score"]:
                        best = {"score": score, "params": p, "result_summary": s}
                        _save_best(best)
                        print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                              f"WR={s.get('win_rate_pct',0):.1f}%")
                    _save_progress(
                        phase=f"round{round_idx}.phase3",
                        iter_done=n_total + i + 1,
                        total=n_total + rounds,
                        best_wr=best["result_summary"].get("win_rate_pct", 0),
                        best_trades=best["result_summary"].get("trades", 0),
                        best_score=best["score"],
                        target_wr=args.target_wr,
                        started_at=t_start,
                        elapsed=systime.time() - t_start,
                        round_idx=round_idx,
                        max_rounds=args.max_rounds,
                    )
                    if (n_total + i + 1) % args.checkpoint_every == 0:
                        _save_checkpoint({"iter": n_total + i + 1,
                                          "best": best, "history": history,
                                          "n_total": n_total + i + 1,
                                          "round_idx": round_idx,
                                          "rounds_done": rounds_done})
                    if systime.time() > deadline:
                        break
        else:
            for i, params in enumerate(refined_params):
                if systime.time() > deadline:
                    break
                score, p, r = _eval_one(params, start_yyyymmdd,
                                        end_yyyymmdd, args.board)
                s = r.get("summary", {}) or {}
                history.append({"iter": n_total + i + 1, "phase": "refine",
                                "params": p, "score": score,
                                "win_rate": s.get("win_rate_pct", 0),
                                "trades": s.get("trades", 0),
                                "summary": s})
                if score > best["score"]:
                    best = {"score": score, "params": p, "result_summary": s}
                    _save_best(best)
                    print(f"  ✓ 新最佳 iter={n_total+i+1} score={score:.1f} "
                          f"WR={s.get('win_rate_pct',0):.1f}%")
                if (n_total + i + 1) % args.checkpoint_every == 0:
                    _save_checkpoint({"iter": n_total + i + 1,
                                      "best": best, "history": history,
                                      "n_total": n_total + i + 1,
                                      "round_idx": round_idx,
                                      "rounds_done": rounds_done})

        n_total += n_refine
        rounds_done += n_refine

        # ── Round 末: 目标检查 + 翻倍决策 ──
        if _is_target_met(best, args.target_wr, args.target_trades, args.target_dd):
            print(f"\n🎯 Round {round_idx} 已达标! WR ≥ {args.target_wr}%")
            _publish_to_cache_store(best, n_total, round_idx, args.target_wr,
                                    True, systime.time() - t_start)
            break

        # R125: 每轮末把当前 best 发布给 live_pick, 迭代中持续生效
        _publish_to_cache_store(best, n_total, round_idx, args.target_wr,
                                False, systime.time() - t_start)

        rounds *= args.rounds_mult
        round_idx += 1
        print(f"\n--- Round {round_idx-1} 未达标, 翻倍至 rounds={rounds} ---")

        _save_checkpoint({"iter": n_total, "best": best, "history": history,
                          "n_total": n_total, "round_idx": round_idx,
                          "rounds_done": rounds_done})

    # ── 收尾 ──
    elapsed = systime.time() - t_start
    # best 兜底: 若 n_total=0 没有任何 evals (时间耗尽在 prebuilt)
    if best is None or best.get("result_summary") is None:
        best = {"score": -1e9, "params": None, "result_summary": {"trades": 0, "win_rate_pct": 0, "avg_return_pct": 0, "total_return_pct": 0, "max_drawdown_pct": 0}}
    target_met = _is_target_met(best, args.target_wr, args.target_trades, args.target_dd)

    print(f"\n========== 完成 ==========")
    print(f"总 iter: {n_total}")
    print(f"轮数: {round_idx}/{args.max_rounds}")
    print(f"耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    if best.get("result_summary"):
        s = best["result_summary"]
        print(f"best score: {best['score']:.2f}")
        print(f"  胜率: {s.get('win_rate_pct', 0):.2f}%")
        print(f"  笔数: {s.get('trades', 0)}")
        print(f"  均单: {s.get('avg_return_pct', 0):.2f}%")
        print(f"  累计: {s.get('total_return_pct', 0):.2f}%")
        print(f"  回撤: {s.get('max_drawdown_pct', 0):.2f}%")
    print(f"\n目标 {args.target_wr}% 胜率: {'✅ 已达成' if target_met else '❌ 未达成 (时间预算耗尽)'}")
    print(f"checkpoint: {CHECKPOINT_FILE}")
    print(f"best: {BEST_FILE}")

    # 落 cache_store (收尾发布)
    _publish_to_cache_store(best, n_total, round_idx, args.target_wr,
                            target_met, elapsed)

    # 进度 (最终)
    _save_progress(
        phase="done",
        iter_done=n_total,
        total=n_total,
        best_wr=best.get("result_summary", {}).get("win_rate_pct", 0),
        best_trades=best.get("result_summary", {}).get("trades", 0),
        best_score=best.get("score", 0),
        target_wr=args.target_wr,
        started_at=t_start,
        elapsed=elapsed,
        round_idx=round_idx,
        max_rounds=args.max_rounds,
        status="done" if target_met else "timeout",
        note=f"{'🎯 已达标' if target_met else '❌ 未达标'} | "
             f"WR={best.get('result_summary', {}).get('win_rate_pct', 0):.1f}% "
             f"trades={best.get('result_summary', {}).get('trades', 0)}",
    )


if __name__ == "__main__":
    main()
