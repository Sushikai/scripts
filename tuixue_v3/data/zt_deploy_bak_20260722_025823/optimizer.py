"""
tuixue_v3/optimizer.py
10 次迭代网格扫描调优：
  - 每次调整一组阈值，跑短期回测
  - 按「月均收益」+「胜率」+「盈亏比」综合评分
  - 保留最佳配置

调优目标函数（综合得分）：
  score = 0.5 * monthly_return + 0.3 * win_rate + 0.2 * profit_factor
"""
from __future__ import annotations

import json
import logging
import sys
import time as systime
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg
from . import backtest as bt_mod

log = logging.getLogger("tuixue_v3.optimizer")


# ═══════════════════════════════════════════════════
# 参数扰动空间
# ═══════════════════════════════════════════════════
PARAM_SPACE = {
    "L1_MA5_VOL_RATIO": [0.8, 1.0, 1.2],            # 5日均量/20日均量 阈值
    "L1_MIN_TURNOVER_YI": [0.5, 0.8, 1.2, 2.0],     # 成交额下限（亿）
    "L2_RR_RATIO_MIN": [1.5, 2.0, 2.5, 3.0],        # 盈亏比下限
    "L3_GAIN_20D_MAX_PCT": [25.0, 35.0, 50.0, 80.0],  # 20日涨幅上限
    "L3_TURN_OVER_MIN_PCT": [3.0, 5.0, 8.0],        # 换手率下限
    "L3_TURN_OVER_MAX_PCT": [12.0, 15.0, 20.0, 25.0], # 换手率上限
    "L3_PULLBACK_VOL_MAX_RATIO": [0.5, 0.7, 1.0],   # 回调量占比上限
    "L3_REQUIRE_MA60": [False, True],                # 是否必须 MA60 参与多头
    "BACKTEST_HOLD_DAYS": [3, 5, 7, 10],             # 持仓天数
    "BACKTEST_TOP_N": [1, 2, 3, 5],                  # 每日买入只数
}


# ═══════════════════════════════════════════════════
# 评分函数
# ═══════════════════════════════════════════════════
def _score(result: dict) -> float:
    """综合得分：月均收益 × 0.5 + 胜率 × 0.3 + 盈亏比 × 0.2"""
    s = result.get("summary", {})
    if not s or s.get("trades", 0) == 0:
        return -100.0  # 无交易直接最低分
    monthly = s.get("monthly_avg_return_pct", 0)
    win = s.get("win_rate_pct", 0)
    pf = s.get("profit_factor", 0)
    # 惩罚项：笔数太少 (<10) → 减分
    n = s.get("trades", 0)
    penalty = 0
    if n < 10:
        penalty = (10 - n) * 2
    return round(0.5 * monthly + 0.3 * win + 0.2 * min(pf, 5) * 10 - penalty, 3)


def _apply_params(params: dict) -> None:
    """运行时修改 cfg 模块的阈值"""
    for k, v in params.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)


def _base_params() -> dict:
    """恢复默认参数"""
    return {
        "L1_MA5_VOL_RATIO": 1.0,
        "L1_MIN_TURNOVER_YI": 0.8,
        "L2_RR_RATIO_MIN": 2.5,
        "L3_GAIN_20D_MAX_PCT": 35.0,
        "L3_TURN_OVER_MIN_PCT": 5.0,
        "L3_TURN_OVER_MAX_PCT": 15.0,
        "L3_PULLBACK_VOL_MAX_RATIO": 0.5,
        "L3_REQUIRE_MA60": True,
        "BACKTEST_HOLD_DAYS": 5,
        "BACKTEST_TOP_N": 3,
    }


# ═══════════════════════════════════════════════════
# 单次回测（短窗口 3 个月以求快）
# ═══════════════════════════════════════════════════
def _quick_bt(params: dict) -> dict:
    _apply_params(params)
    try:
        r = bt_mod.run_backtest(
            start=cfg.OPT_START, end=cfg.OPT_END,
            top_n=params.get("BACKTEST_TOP_N", 3),
            hold_days=params.get("BACKTEST_HOLD_DAYS", 5),
            sell_mode="rule", sample=cfg.OPT_SAMPLE,
        )
    except Exception as e:
        log.warning(f"回测失败 {params}: {e}")
        return {"summary": {"trades": 0}}
    return r


# ═══════════════════════════════════════════════════
# 主优化器（10 次迭代）
# ═══════════════════════════════════════════════════
def run_optimize(iterations: int = None, progress_cb=None) -> dict:
    """
    10 次迭代调优：
      iter 1: 基线（默认参数）
      iter 2-4: 单独扰动各维度，保留改进
      iter 5-7: 组合最优组合
      iter 8-10: 局部微调 + 验证
    """
    iterations = iterations or cfg.OPT_ITERATIONS
    log.info(f"========== 优化器开始 {iterations} 次迭代 ==========")
    t0 = systime.time()

    history = []
    best_params = _base_params()
    best_score = -float("inf")
    best_result = None

    def _progress(phase, **kw):
        if progress_cb:
            try:
                progress_cb({"phase": phase, "elapsed_sec": round(systime.time() - t0, 1), **kw})
            except Exception:
                pass

    # Iter 1：基线
    log.info(f"\n>>> Iter 1/10: 基线")
    _progress("iter_start", iter=1, total=iterations)
    r = _quick_bt(best_params)
    sc = _score(r)
    history.append({"iter": 1, "params": best_params, "score": sc, "summary": r.get("summary", {})})
    if sc > best_score:
        best_score = sc
        best_params = best_params.copy()
        best_result = r
    _progress("iter_done", iter=1, total=iterations, score=sc, trials=len(history), best_score=best_score)
    log.info(f"  得分={sc} | 笔数={r['summary'].get('trades', 0)} | 月均={r['summary'].get('monthly_avg_return_pct', 0)}%")

    # Iter 2-4：单维度扰动（贪心）
    for it in range(2, 5):
        log.info(f"\n>>> Iter {it}/10: 单维度扰动")
        _progress("iter_start", iter=it, total=iterations)
        improved = False
        for key, choices in PARAM_SPACE.items():
            for v in choices:
                p = best_params.copy()
                p[key] = v
                # 依赖约束
                if key == "L3_TURN_OVER_MIN_PCT" and p["L3_TURN_OVER_MIN_PCT"] >= p["L3_TURN_OVER_MAX_PCT"]:
                    continue
                log.info(f"  尝试 {key}={v}", )
                r = _quick_bt(p)
                sc = _score(r)
                history.append({"iter": it, "param_key": key, "params": p, "score": sc,
                                "summary": r.get("summary", {})})
                if sc > best_score:
                    best_score = sc
                    best_params = p.copy()
                    best_result = r
                    improved = True
                    _progress("new_best", iter=it, key=key, value=v, score=sc)
                    log.info(f"    ⭐ 新最佳 score={sc} ({key}={v}) | 月均={r['summary'].get('monthly_avg_return_pct', 0)}% 笔数={r['summary'].get('trades', 0)}")
            _progress("iter_done", iter=it, total=iterations, trials=len(history), best_score=best_score)
        if not improved:
            log.info(f"  Iter {it} 无改进")

    # Iter 5-7：组合扰动
    for it in range(5, 8):
        log.info(f"\n>>> Iter {it}/10: 组合扰动")
        improved = False
        # 随机扰动 3 个维度
        import random
        random.seed(it)
        keys = random.sample(list(PARAM_SPACE.keys()), 3)
        for k in keys:
            choices = PARAM_SPACE[k]
            v = random.choice(choices)
            p = best_params.copy()
            p[k] = v
            if k == "L3_TURN_OVER_MIN_PCT" and p["L3_TURN_OVER_MIN_PCT"] >= p["L3_TURN_OVER_MAX_PCT"]:
                continue
            log.info(f"  尝试 {k}={v}")
            r = _quick_bt(p)
            sc = _score(r)
            history.append({"iter": it, "param_key": k, "params": p, "score": sc,
                            "summary": r.get("summary", {})})
            if sc > best_score:
                best_score = sc
                best_params = p.copy()
                best_result = r
                improved = True
                _progress("new_best", iter=it, key=k, value=v, score=sc)
                log.info(f"    ⭐ 新最佳 score={sc} ({k}={v})")
        _progress("iter_done", iter=it, total=iterations, trials=len(history), best_score=best_score)
        if not improved:
            log.info(f"  Iter {it} 无改进")

    # Iter 8-10：局部微调（围绕 best 上下浮动）
    for it in range(8, 11):
        log.info(f"\n>>> Iter {it}/10: 局部微调")
        _progress("iter_start", iter=it, total=iterations)
        improved = False
        for key in list(best_params.keys()):
            if key not in PARAM_SPACE:
                continue
            cur = best_params[key]
            for v in PARAM_SPACE[key]:
                if v == cur:
                    continue
                p = best_params.copy()
                p[key] = v
                if key == "L3_TURN_OVER_MIN_PCT" and p["L3_TURN_OVER_MIN_PCT"] >= p["L3_TURN_OVER_MAX_PCT"]:
                    continue
                log.info(f"  微调 {key}: {cur} → {v}")
                r = _quick_bt(p)
                sc = _score(r)
                history.append({"iter": it, "param_key": key, "params": p, "score": sc,
                                "summary": r.get("summary", {})})
                if sc > best_score:
                    best_score = sc
                    best_params = p.copy()
                    best_result = r
                    improved = True
                    _progress("new_best", iter=it, key=key, value=v, score=sc)
                    log.info(f"    ⭐ 新最佳 score={sc} ({key}={v})")
        _progress("iter_done", iter=it, total=iterations, trials=len(history), best_score=best_score)
        if not improved:
            log.info(f"  Iter {it} 无改进")

    # 还原最佳参数
    _apply_params(best_params)
    _progress("done", total_trials=len(history), best_score=best_score)

    # 保存优化报告
    report = {
        "iterations_run": iterations,
        "total_trials": len(history),
        "best_params": best_params,
        "best_score": best_score,
        "best_summary": best_result.get("summary") if best_result else {},
        "best_monthly": best_result.get("monthly") if best_result else [],
        "history": history,
        "elapsed_sec": round(systime.time() - t0, 1),
        "ts": datetime.now().isoformat(),
    }
    out_path = cfg.REPORT_DIR / f"optimize_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log.info(f"\n========== 优化完成 | best_score={best_score} | trials={len(history)} | {report['elapsed_sec']}s ==========")
    log.info(f"最佳参数: {json.dumps(best_params, ensure_ascii=False)}")
    log.info(f"最佳结果: {best_result.get('summary') if best_result else {}}")
    log.info(f"报告: {out_path}")
    return report


# ═══════════════════════════════════════════════════
# L3_REQUIRE_MA60 动态应用（独立函数，避免循环 import）
# ═══════════════════════════════════════════════════
def apply_ma60_override() -> None:
    """全局 patch Layer3 的 MA60 严格性（按 cfg.L3_REQUIRE_MA60）"""
    from . import layer3_daily as l3
    if not getattr(cfg, "L3_REQUIRE_MA60", True):
        def relaxed(df):
            need = ["MA5", "MA10"]
            if not all(c in df.columns for c in need):
                return False, {"reason": "指标缺失"}
            last = df.iloc[-1]
            ma5 = float(last["MA5"]) if pd.notna(last["MA5"]) else 0
            ma10 = float(last["MA10"]) if pd.notna(last["MA10"]) else 0
            if ma5 <= 0 or ma10 <= 0:
                return False, {"reason": "指标为 NaN"}
            price = float(last["收盘"])
            ok = ma5 > ma10 and price > ma5
            return ok, {"MA5": round(ma5, 2), "MA10": round(ma10, 2), "price": round(price, 2)}

        l3._check_ma_alignment = relaxed
        log.info("L3 MA 检查已放宽（仅 MA5 > MA10 + price > MA5）")


apply_ma60_override()
import pandas as pd


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_optimize()