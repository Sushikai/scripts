"""
tuixue_v3/screen.py
顶层入口：run_stock_screen() / run_backtest()
四层流水线编排 + 排序 + 输出
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg
from . import data_layer as dl
from . import blacklist as bl_mod
from . import layer1_basic as l1
from . import layer2_cycle_mainline as l2
from . import layer3_daily as l3
from . import layer4_intraday as l4
from . import multi_source_fetchers as msf
from .ma_helpers import apply_ma60_override

log = logging.getLogger("tuixue_v3.screen")

LOG_FILE = cfg.LOG_FILE

# 启动时根据 cfg.L3_REQUIRE_MA60 应用 MA60 override
apply_ma60_override()


def _setup_logging() -> None:
    """懒初始化 logging（CLI / 模块调用都可）"""
    root = logging.getLogger("tuixue_v3")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _sort_picks(picks: list[dict]) -> list[dict]:
    """
    优先级：中军龙头 > 换手二板龙头 > 低位首板
    启发式排序：score_dragon_v2 风格的综合分
    + 2026-07: 加分项 — 近期涨停次数 + 热门板块排名
    """
    def _num(x, default=0.0):
        try:
            v = float(x)
            return v if v == v else default  # NaN check
        except (TypeError, ValueError):
            return default

    def score_of(p: dict) -> float:
        s = 0.0
        # 流通市值适中（100-200亿最佳）
        mv = _num(p.get("free_mv_yi"))
        if 80 <= mv <= 200:
            s += 30
        elif 50 <= mv <= 300:
            s += 15
        # 盈亏比
        rr = _num(p.get("rr_ratio"))
        s += min(rr * 5, 25)
        # 量能趋势
        vr = _num(p.get("vol_ratio"))
        s += min(vr * 10, 15)
        # 换手（适中）
        tr = _num(p.get("turnover_pct"))
        if 6 <= tr <= 12:
            s += 15
        elif 5 <= tr <= 15:
            s += 8
        # 20 日涨幅（温和）
        g20 = _num(p.get("gain_20d_pct"))
        if 5 <= g20 <= 25:
            s += 15
        elif 0 <= g20 <= 35:
            s += 5
        # === 2026-07 推荐池加分 ===
        # 近 N 天涨停次数 — 涨停越多越热门
        zt = int(p.get("recent_zt_count", 0) or 0)
        s += min(zt * 5, 20)          # 1 天 +5, 2 天 +10, 3 天 +15, 4+ +20
        # 热门板块排名 — 排名越靠前分越高
        hs_rank = p.get("recent_hot_sector_rank")
        if hs_rank is not None:
            try:
                r = int(hs_rank)
                if r <= 3:
                    s += 15
                elif r <= 10:
                    s += 10
                elif r <= 20:
                    s += 5
            except (TypeError, ValueError):
                pass
        return s

    return sorted(picks, key=score_of, reverse=True)


def run_stock_screen(date_str: str | None = None, mode: str = "live",
                     stocks: list | None = None) -> dict:
    """
    顶层入口：
      - date_str: YYYYMMDD or None=今天
      - mode: "live" / "backtest"
      - stocks: 调用方注入的股票池（回测/压测用）；None=按 dl.fetch_stock_list() 全市场
    返回：{
      "date", "candidates", "stats_by_layer", "health", "ts"
    }
    """
    _setup_logging()
    t0 = systime.time()
    log.info(f"========== run_stock_screen start | date={date_str} mode={mode} ==========")

    # 0) 健康检查 — 回测模式跳过（用预热的本地缓存，不再打实时源；省 5–8s/天）
    if mode == "backtest":
        health = {"mode": "backtest", "skipped_health_check": True, "spot_ok": True}
    else:
        health = dl.data_health_check()
        if not health.get("spot_ok"):
            log.error("全市场快照源失效 → 返回空")
            return {"date": date_str or datetime.now().strftime("%Y%m%d"),
                    "candidates": [], "stats_by_layer": {},
                    "health": health, "reason": "spot_source_failed", "ts": datetime.now().isoformat()}

    # 1) 股票池
    _excluded_n = 0
    if stocks is None:
        stocks = dl.fetch_stock_list()
        # 显式排除创业板/科创板/北交所（fetch_stock_list 已过滤，这里是防御 + 显式）
        # 仅在调用方未注入池时生效（注入的池是回测/压测有意为之）
        _excl_prefixes = tuple(
            p for ps in cfg.L1_EXCLUDE_BOARD_PREFIXES.values() for p in ps
        )
        _excluded_boards = tuple(cfg.L1_EXCLUDE_BOARDS)
        _before = len(stocks)
        stocks = [(c, n) for c, n in stocks if not c.startswith(_excl_prefixes)]
        _excluded_n = _before - len(stocks)
        if _excluded_n > 0:
            log.info(f"Board filter: 排除 {_excluded_n} 只 {','.join(_excluded_boards)}")
    log.info(f"股票池: {len(stocks)} 只")

    # 1.5) Prefilter — 2026-07 新增：缩窄到「近期涨停 ∩ 热门板块」
    # 触发：live 模式 + (无注入池 或 注入池 ≥ 5)。backtest 模式跳过（caller 注入的池是有意为之）。
    pre_meta: dict[str, dict] = {}
    pre_stats: dict = {"skipped": False, "reason": ""}
    # 记录 board 排除信息（即使 prefilter 跳过也要传出去）
    pre_stats["board_excluded"] = {
        "boards": list(cfg.L1_EXCLUDE_BOARDS),
        "count": _excluded_n,
    }
    if mode != "backtest" and (stocks is None or len(stocks) >= 5):
        try:
            zt_pool = msf.fetch_recent_zt_pool(days=cfg.RECENT_ZT_DAYS)
            hot_sectors = msf.fetch_hot_sectors(
                top_n_flow=cfg.HOT_SECTOR_TOP_FLOW,
                top_n_pct=cfg.HOT_SECTOR_TOP_PCT,
            )
            filtered = msf.filter_zt_by_hot_sectors(zt_pool, hot_sectors)
            if filtered:
                keep_codes = set(filtered.keys())
                pre_pool = [(c, n) for c, n in stocks if c in keep_codes]
                # 过滤掉 cfg.RECENT_ZT_MIN_COUNT 以下的
                pre_pool = [(c, n) for c, n in pre_pool
                            if filtered[c]["zt_count"] >= cfg.RECENT_ZT_MIN_COUNT]
                for c, _n in pre_pool:
                    f = filtered[c]
                    pre_meta[c] = {
                        "recent_zt_count": f.get("zt_count", 0),
                        "recent_hot_sector_rank": f.get("hot_sector_rank"),
                        "recent_hot_sector_name": f.get("hot_sector_name"),
                        "recent_hot_sector_kind": f.get("hot_sector_kind"),
                    }
                pre_stats = {
                    "skipped": False,
                    "zt_pool_size": len(zt_pool),
                    "hot_sector_count": len(hot_sectors),
                    "after_filter": len(pre_pool),
                    "input_size": len(stocks),
                    "recent_zt_days": cfg.RECENT_ZT_DAYS,
                    "min_zt_count": cfg.RECENT_ZT_MIN_COUNT,
                    "hot_sector_top_flow": cfg.HOT_SECTOR_TOP_FLOW,
                    "hot_sector_top_pct": cfg.HOT_SECTOR_TOP_PCT,
                    "board_excluded": {
                        "boards": list(cfg.L1_EXCLUDE_BOARDS),
                        "count": _excluded_n,
                    },
                }
                log.info(f"Prefilter: 全市场 {len(stocks)} → {len(pre_pool)} "
                         f"(涨停池 {len(zt_pool)} ∩ 热门板块 {len(hot_sectors)})")
                stocks = pre_pool
            else:
                pre_stats = {"skipped": False, "reason": "empty_intersection",
                          "board_excluded": {
                              "boards": list(cfg.L1_EXCLUDE_BOARDS),
                              "count": _excluded_n,
                          }}
                log.warning("Prefilter: 涨停池 ∩ 热门板块 = 空，回退到全量池")
        except Exception as e:
            pre_stats = {"skipped": False, "reason": f"error: {e}",
                      "board_excluded": {
                          "boards": list(cfg.L1_EXCLUDE_BOARDS),
                          "count": _excluded_n,
                      }}
            log.warning(f"Prefilter 失败，回退全量: {e}")
    else:
        pre_stats = {"skipped": True, "reason": "small_pool",
                     "board_excluded": {
                         "boards": list(cfg.L1_EXCLUDE_BOARDS),
                         "count": _excluded_n,
                     }}

    # 2) Layer 1
    l1_passed, l1_stats = l1.screen(stocks, date_str)
    # 把 prefilter 元数据注入到每条候选 dict，供后面排序用
    for p in l1_passed:
        meta = pre_meta.get(p["code"])
        if meta:
            p["recent_zt_count"] = meta["recent_zt_count"]
            p["recent_hot_sector_rank"] = meta["recent_hot_sector_rank"]
            p["recent_hot_sector_name"] = meta["recent_hot_sector_name"]
            p["recent_hot_sector_kind"] = meta["recent_hot_sector_kind"]
    log.info(f"Layer1: {l1_stats.get('passed')} 只通过")
    if not l1_passed:
        return _finalize(date_str, [], {"prefilter": pre_stats, "l1": l1_stats}, health, t0, reason="l1_empty")

    # 3) Layer 2
    try:
        l2_passed, l2_stats = l2.screen(l1_passed, date_str)
    except RecursionError:
        import traceback as _tb
        log.error(f"[{date_str}] Layer2 RecursionError 完整栈:\n"
                  + "".join(_tb.format_exc()))
        raise
    log.info(f"Layer2: {l2_stats.get('passed')} 只通过")
    if not l2_passed:
        return _finalize(date_str, [], {"prefilter": pre_stats, "l1": l1_stats, "l2": l2_stats}, health, t0, reason="l2_empty")

    # 4) Layer 3
    try:
        l3_passed, l3_stats = l3.screen(l2_passed, date_str)
    except RecursionError:
        import traceback as _tb
        log.error(f"[{date_str}] Layer3 RecursionError 完整栈:\n"
                  + "".join(_tb.format_exc()))
        raise
    log.info(f"Layer3: {l3_stats.get('passed')} 只通过")
    if not l3_passed:
        return _finalize(date_str, [], {"prefilter": pre_stats, "l1": l1_stats, "l2": l2_stats, "l3": l3_stats}, health, t0, reason="l3_empty")

    # 5) Layer 4
    try:
        l4_passed, l4_stats = l4.screen(l3_passed, date_str, mode=mode)
    except RecursionError:
        import traceback as _tb
        log.error(f"[{date_str}] Layer4 RecursionError 完整栈:\n"
                  + "".join(_tb.format_exc()))
        raise
    log.info(f"Layer4: {l4_stats.get('passed')} 只通过")

    # 6) 排序 + 截取
    sorted_picks = _sort_picks(l4_passed)[:cfg.OUTPUT_MAX]

    # 7) 输出（去掉内部 _df_ref）
    out = []
    for p in sorted_picks:
        out.append({k: v for k, v in p.items() if not k.startswith("_")})

    result = _finalize(
        date_str, out,
        {"prefilter": pre_stats, "l1": l1_stats, "l2": l2_stats, "l3": l3_stats, "l4": l4_stats},
        health, t0,
    )
    log.info(f"========== run_stock_screen done | {len(out)} picks | {systime.time()-t0:.1f}s ==========")
    return result


def _finalize(date_str: str | None, candidates: list[dict],
              stats_by_layer: dict, health: dict, t0: float, reason: str = "ok") -> dict:
    return {
        "date": date_str or datetime.now().strftime("%Y%m%d"),
        "candidates": candidates,
        "stats_by_layer": stats_by_layer,
        "health": health,
        "reason": reason,
        "elapsed_sec": round(systime.time() - t0, 1),
        "ts": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="退学 v3 选股入口")
    p.add_argument("--date", help="YYYYMMDD（默认今日）")
    p.add_argument("--mode", choices=["live", "backtest"], default="live")
    p.add_argument("--save", action="store_true", help="保存结果到 reports/")
    args = p.parse_args()
    r = run_stock_screen(date_str=args.date, mode=args.mode)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if args.save:
        out = cfg.REPORT_DIR / f"screen_{r['date']}.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print(f"\n已保存到 {out}")


# 回测占位（实际实现见 backtest.py）
def run_backtest(*args, **kwargs):
    from .backtest import run_backtest as _bt
    return _bt(*args, **kwargs)


if __name__ == "__main__":
    _cli()