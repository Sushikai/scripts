"""实跑 10000 轮 ZT 科学训练 — 写 history + 落 cache_store + /tmp 文件."""
import json
import logging
import os
import sys
import time as t

# 必须在 import zt_optimizer 前设 log level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/zt_optimize_run.log", mode="w"),
    ],
)
log = logging.getLogger("10000run")

t0 = t.time()
log.info("=" * 70)
log.info("开始 10000 轮 ZT 科学训练 (T+1 开盘买入 + hold_for_zt)")
log.info("=" * 70)

import multiprocessing as mp
from tuixue_v3 import zt_optimizer as zo, cache_store as cs, zt_config as cfg

WORKERS = 8
ITER = 10000
POP = 100
START = cfg.ZT_OPTIMIZE_WINDOW_END.replace("-", "")  # 文件名用日期
END_DATE = cfg.ZT_OPTIMIZE_WINDOW_END

log.info("Entry rule: %s (T+1 09:30 开盘价买入)", cfg.ZT_ENTRY_RULE)
log.info("Workers=%d Iter=%d Pop=%d Start=2025-12-01 End=%s", WORKERS, ITER, POP, END_DATE)
log.info("打分权重维度纳入训练: %s", zo.WEIGHT_KEYS)

r = zo.run_optimize(
    start="2025-12-01",
    end=END_DATE,
    iterations=ITER,
    population=POP,
    board_filter="all",
    progress_cb=None,
    n_workers=WORKERS,
)
elapsed = t.time() - t0

s = r["best_result"].get("summary", {}) or {}
log.info("=" * 70)
log.info("10000 轮完成 | 耗时 %.0fs (%.1fmin)", elapsed, elapsed / 60)
log.info("Best Score=%.2f | 笔数=%d 胜率=%.1f%% 均单=%.2f%%",
         r["best_score"], s.get("trades", 0), s.get("win_rate_pct", 0),
         s.get("avg_return_pct", 0))
log.info("  复利口径: equity=%.2f%% annualized=%.2f%% equity_dd=%.2f%%",
         s.get("equity_return_pct", 0), s.get("annualized_return_pct", 0),
         s.get("equity_max_drawdown_pct", 0))
log.info("Best Params=%s", r["best_params"])
log.info("History len=%d (证明确实跑了 %d 次)", len(r.get("history", [])), ITER)
log.info("=" * 70)

# 1) 落 /tmp 文件
out_path = f"/tmp/zt_optimize_2025-12-01_{END_DATE}.json"
with open(out_path, "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2, default=str)
log.info("已保存到 %s (%d KB)", out_path, os.path.getsize(out_path) // 1024)

# 1.5) 跑一次 best params 的逐月明细 (供 /api/zt/optimized_summary 直接返, 不再触发长 eval)
# 拆出打分权重 (best_params 含 14 过滤参数 + 5 权重)
log.info("跑 best params 逐月明细 (单次, 90s)...")
t1 = t.time()
from tuixue_v3 import zt_backtest as _ztb
_best_fp, _best_w = zo.split_weights(r["best_params"])
log.info("训练权重: %s", _best_w)
bt_res = _ztb.run_zt_backtest(
    start="2025-12-01", end=END_DATE, sample=0,
    weights=_best_w, **_best_fp,
)
bt_summary = bt_res.get("summary") or {}
mb_summary = bt_summary.get("monthly_breakdown", [])
log.info("逐月明细 done (%.1fs, %d 个月)", t.time() - t1, len(mb_summary))

# 2) 落 cache_store (Redis) — 跨 worker 共享
try:
    store = cs.get_store()
    store.set(cs.K.OPTIMIZER_BEST, {
        # 2026-08-09: params = 过滤/交易参数 (不含权重), weights = 打分维度权重 (可训练)
        "params": _best_fp,
        "weights": _best_w,
        "score": r["best_score"],
        # 2026-08-08: 用 bt_summary 而非 s (s 是 optimizer 内存 summary, 缺 equity 字段)
        "trades": bt_summary.get("trades", 0),
        "win_rate": bt_summary.get("win_rate_pct", 0),
        "total_ret": bt_summary.get("total_return_pct", 0),  # per-trade cumprod
        "max_dd": bt_summary.get("max_drawdown_pct", 0),
        "equity_ret": bt_summary.get("equity_return_pct", 0),  # 复利收益
        "equity_dd": bt_summary.get("equity_max_drawdown_pct", 0),
        "annualized_ret": bt_summary.get("annualized_return_pct", 0),  # 年化复利
        "monthly_compound_avg": bt_summary.get("monthly_compound_avg_pct", 0),  # 月均复利
        "trading_days": bt_summary.get("trading_days", 0),
        "iterations": ITER,
        "elapsed_sec": round(elapsed, 1),
        "updated_at": t.time(),
        "source_file": out_path,
        "monthly_breakdown": mb_summary,  # 2026-08-06: 直接存, 端点不需重跑
        "data_window": {
            "start": "2025-12-01",
            "end": END_DATE,
            "board_filter": cfg.ZT_BOARD_FILTER,
            "entry_rule": cfg.ZT_ENTRY_RULE,
        },
    })
    log.info("已写入 cache_store OPTIMIZER_BEST (Redis 共享) + monthly_breakdown %d 月", len(mb_summary))
except Exception as e:
    log.error("cache_store 写入失败: %s", e)

log.info("=" * 70)
log.info("DONE")
log.info("=" * 70)