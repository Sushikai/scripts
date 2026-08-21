#!/usr/bin/env python3
"""R110 批量回填 lhb_detail + lhb_hyyyb 缓存到 2025-12-01 ~ 今天."""
import logging
import sys
import time as systime
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backfill")

CACHE_DIR = Path.home() / ".hermes" / "cache" / "multi_source"


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from multi_source_fetchers import fetch_lhb_detail, fetch_lhb_hyyyb, fetch_trade_dates

    # 1. 已有 cache 的日期
    existing_detail = sorted(p.stem.split("_")[1] for p in CACHE_DIR.glob("lhb_2*.json")
                             if not p.stem.startswith("lhb_hyyyb"))
    existing_hyyyb = sorted(p.stem.split("_")[2] for p in CACHE_DIR.glob("lhb_hyyyb_2*.json"))
    log.info("existing lhb_detail: %d days (range %s ~ %s)",
             len(existing_detail), existing_detail[0] if existing_detail else "-",
             existing_detail[-1] if existing_detail else "-")
    log.info("existing lhb_hyyyb:  %d days (range %s ~ %s)",
             len(existing_hyyyb), existing_hyyyb[0] if existing_hyyyb else "-",
             existing_hyyyb[-1] if existing_hyyyb else "-")

    # 2. 计算所有目标日期 (2025-12-01 ~ 今天)
    start = datetime(2025, 12, 1)
    end = datetime.now()
    all_dates = []
    cur = start
    while cur <= end:
        all_dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    log.info("target range: %s ~ %s (%d calendar days)", start.date(), end.date(), len(all_dates))

    # 3. trade dates (用 akshare 工具函数兜底)
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        trade_set = set(df["trade_date"].dt.strftime("%Y%m%d").tolist())
    except Exception as e:
        log.warning("akshare trade_dates 拉取失败: %s, 用 weekday 兜底", e)
        trade_set = {d for d in all_dates
                     if datetime.strptime(d, "%Y%m%d").weekday() < 5}
    trade_dates = [d for d in all_dates if d in trade_set]
    log.info("trade dates in range: %d", len(trade_dates))

    # 4. 过滤掉已存在的
    to_fetch_detail = [d for d in trade_dates if d not in existing_detail]
    to_fetch_hyyyb = [d for d in trade_dates if d not in existing_hyyyb]
    log.info("to fetch detail: %d, hyyyb: %d", len(to_fetch_detail), len(to_fetch_hyyyb))

    # 5. fetch detail
    n_detail_ok = 0
    for i, d in enumerate(to_fetch_detail):
        try:
            r = fetch_lhb_detail(d)
            n = len(r) if r is not None and not isinstance(r, list) else (len(r) if r else 0)
            if r is not None and n > 0:
                n_detail_ok += 1
            if (i + 1) % 20 == 0 or i == len(to_fetch_detail) - 1:
                log.info("detail %d/%d done, ok=%d, last=%s n=%d", i + 1, len(to_fetch_detail), n_detail_ok, d, n)
        except Exception as e:
            log.warning("detail %s fail: %s", d, e)
        systime.sleep(0.4)

    # 6. fetch hyyyb
    n_hyyyb_ok = 0
    for i, d in enumerate(to_fetch_hyyyb):
        try:
            r = fetch_lhb_hyyyb(d)
            if r is not None and len(r) > 0:
                n_hyyyb_ok += 1
            if (i + 1) % 20 == 0 or i == len(to_fetch_hyyyb) - 1:
                log.info("hyyyb %d/%d done, ok=%d, last=%s n=%d", i + 1, len(to_fetch_hyyyb), n_hyyyb_ok, d, len(r) if r else 0)
        except Exception as e:
            log.warning("hyyyb %s fail: %s", d, e)
        systime.sleep(0.4)

    log.info("=== done: detail ok=%d / %d, hyyyb ok=%d / %d ===",
             n_detail_ok, len(to_fetch_detail), n_hyyyb_ok, len(to_fetch_hyyyb))


if __name__ == "__main__":
    main()
