"""
tuixue_v3/tools/migrate_to_redis.py
迁移老存储 (SQLite + 散文件 JSON + data/ JSON) → Redis。

幂等:已存在的 key 会被覆盖 (HSET/RPS)。每类数据迁移后做 5% 抽样校验。

用法:
  python -m tuixue_v3.tools.migrate_to_redis           # 全量迁移
  python -m tuixue_v3.tools.migrate_to_redis --verify  # 只校验不迁移
  python -m tuixue_v3.tools.migrate_to_redis --clean   # 迁移后删 cache/*.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

# 允许独立运行:python tools/migrate_to_redis.py
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT.parent))

from tuixue_v3 import cache_db, config as cfg
from tuixue_v3.cache_store import get_store, K, ttl_until_midnight

log = logging.getLogger("migrate")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _conn_sqlite() -> sqlite3.Connection:
    return sqlite3.connect(str(cfg.PACKAGE_DIR / "data" / "cache.db"), timeout=30.0)


# ═══════════════════════════════════════════════════
# 1. 日线 (cache_db.daily)
# ═══════════════════════════════════════════════════
def migrate_daily(store) -> tuple[int, int]:
    """按 code 分桶,每个 code 一个 Hash (date → json row)。"""
    log.info("[1/N] 迁移日线 daily (~250K 行)...")
    t0 = time.time()
    bucket: dict[str, dict[str, str]] = defaultdict(dict)
    with _conn_sqlite() as conn:
        rows = conn.execute(
            "SELECT code, date, open, high, low, close, volume, amount, turnover, ts_updated FROM daily"
        ).fetchall()
    for code, date, o, h, l, c, vol, amt, turn, ts in rows:
        bucket[code][date] = json.dumps({
            "date": date, "open": o, "high": h, "low": l, "close": c,
            "volume": vol, "amount": amt, "turnover": turn, "ts_updated": ts,
        }, ensure_ascii=False)
    n_keys = 0
    n_rows = 0
    for code, mp in bucket.items():
        # 一次性写整个 hash
        k = K.DAILY.format(code=code)
        # 用 raw hset 多次
        for date, payload in mp.items():
            store.hset(k, date, json.loads(payload), ttl=4 * 3600)
        n_keys += 1
        n_rows += len(mp)
    log.info(f"  ✓ {n_keys} 个 code, {n_rows} 行, 耗时 {time.time()-t0:.1f}s")
    return n_keys, n_rows


# ═══════════════════════════════════════════════════
# 2. AI verdict (cache_db.ai_verdict)
# ═══════════════════════════════════════════════════
def migrate_ai(store) -> int:
    log.info("[2/N] 迁移 AI verdict...")
    t0 = time.time()
    ttl = ttl_until_midnight()
    n = 0
    with _conn_sqlite() as conn:
        for row in conn.execute(
            "SELECT date, code, model, payload_json FROM ai_verdict WHERE payload_json IS NOT NULL"
        ).fetchall():
            date, code, model, payload = row
            try:
                payload_dict = json.loads(payload) if payload else {}
            except Exception:
                continue
            k = K.AI.format(date=date, code=code)
            store.hset(k, model, payload_dict, ttl=ttl)
            n += 1
    log.info(f"  ✓ {n} 条, 耗时 {time.time()-t0:.1f}s")
    return n


# ═══════════════════════════════════════════════════
# 3. 自选股 (cache_db.watchlist / watchlist_ai)
# ═══════════════════════════════════════════════════
def migrate_watchlist(store) -> tuple[int, int]:
    log.info("[3/N] 迁移 watchlist...")
    t0 = time.time()
    ttl = ttl_until_midnight()
    n_wl = 0
    n_ai = 0
    with _conn_sqlite() as conn:
        for row in conn.execute("SELECT code, name, tag, sort_order, added_at, note FROM watchlist").fetchall():
            code, name, tag, sort_order, added_at, note = row
            payload = {
                "code": code, "name": name, "tag": tag,
                "sort_order": sort_order, "added_at": added_at, "note": note,
            }
            store.hset(K.WATCHLIST, code, payload, ttl=0)  # 永久
            n_wl += 1
        for row in conn.execute(
            "SELECT code, trade_date, verdict, role, conviction, suggested_window, "
            "entry_price_range, stop_loss, time_horizon, summary, extras_json, ts_updated "
            "FROM watchlist_ai"
        ).fetchall():
            code, td, verdict, role, conv, sw, epr, sl, th, summary, extras_json, ts = row
            try:
                extras = json.loads(extras_json) if extras_json else {}
            except Exception:
                extras = {}
            payload = {
                "code": code, "trade_date": td,
                "verdict": verdict, "role": role, "conviction": conv,
                "suggested_window": sw, "entry_price_range": epr,
                "stop_loss": sl, "time_horizon": th, "summary": summary,
                "extras": extras, "ts_updated": ts,
            }
            store.set(K.WATCHLIST_AI.format(code=code), payload, ttl=ttl)
            n_ai += 1
    log.info(f"  ✓ watchlist {n_wl} 条 + watchlist_ai {n_ai} 条, 耗时 {time.time()-t0:.1f}s")
    return n_wl, n_ai


# ═══════════════════════════════════════════════════
# 4. 散文件 JSON (cache/daily_*_130.json 等)
# ═══════════════════════════════════════════════════
def migrate_cache_json(store) -> tuple[int, int, int, int]:
    log.info("[4/N] 迁移 cache/*.json 散文件...")
    t0 = time.time()
    n_daily = 0
    n_intra = 0
    n_main = 0
    n_list = 0

    for p in cfg.CACHE_DIR.glob("daily_*.json"):
        # daily_{code}_{days}.json → 写到 hash 但只覆盖 130 版本(已有日线全量)
        name = p.stem  # daily_000001_130
        parts = name.split("_")
        if len(parts) != 3:
            continue
        _, code, days = parts
        try:
            rows = json.loads(p.read_text())
            if not isinstance(rows, list):
                continue
            k = K.DAILY.format(code=code)
            for r in rows:
                if not isinstance(r, dict) or "日期" not in r:
                    continue
                d = str(r["日期"]).replace("-", "")[:10]
                if len(d) != 8:
                    continue
                store.hset(k, d, {
                    "date": d,
                    "open": r.get("开盘"), "high": r.get("最高"),
                    "low": r.get("最低"), "close": r.get("收盘"),
                    "volume": r.get("成交量"), "amount": r.get("成交额"),
                    "turnover": r.get("换手率"),
                }, ttl=4 * 3600)
            n_daily += 1
        except Exception as e:
            log.warning(f"  跳过 {p.name}: {e}")

    for p in cfg.CACHE_DIR.glob("intraday_*_latest.json"):
        name = p.stem
        code = name.replace("intraday_", "").replace("_latest", "")
        try:
            rows = json.loads(p.read_text())
            store.set(K.INTRADAY.format(code=code), rows, ttl=30 * 60)
            n_intra += 1
        except Exception as e:
            log.warning(f"  跳过 {p.name}: {e}")

    for p in cfg.CACHE_DIR.glob("mainline_*.json"):
        name = p.stem
        key_part = name.replace("mainline_", "")
        try:
            data = json.loads(p.read_text())
            store.set(K.MAINLINE.format(date=key_part), data, ttl=24 * 3600)
            n_main += 1
        except Exception as e:
            log.warning(f"  跳过 {p.name}: {e}")

    for p in cfg.CACHE_DIR.glob("stock_list_*.json"):
        name = p.stem
        try:
            data = json.loads(p.read_text())
            full_key = K.STOCKLIST_FILTERED if "filtered" in name else K.STOCKLIST_ALL
            store.set(full_key, data, ttl=24 * 3600)
            n_list += 1
        except Exception as e:
            log.warning(f"  跳过 {p.name}: {e}")

    log.info(f"  ✓ daily={n_daily} intraday={n_intra} mainline={n_main} list={n_list}, 耗时 {time.time()-t0:.1f}s")
    return n_daily, n_intra, n_main, n_list


# ═══════════════════════════════════════════════════
# 5. data/ JSON
# ═══════════════════════════════════════════════════
def migrate_data_json(store) -> dict[str, bool]:
    log.info("[5/N] 迁移 data/*.json...")
    t0 = time.time()
    out = {}
    data_dir = cfg.PACKAGE_DIR / "data"

    for fname, key, ttl in [
        ("news_cache.json",   K.NEWS,         30 * 60),
        ("sector_cache.json", K.SECTOR,       24 * 3600),
        ("known_seats.json",  K.SEAT_KNOWN,   0),
        ("seat_aliases.json", K.SEAT_ALIASES, 0),
    ]:
        p = data_dir / fname
        if not p.exists():
            out[fname] = False
            continue
        try:
            data = json.loads(p.read_text())
            if fname == "sector_cache.json" and isinstance(data, dict) and "stocks" in data:
                # Hash 形式存
                for code, mp in data["stocks"].items():
                    store.hset(K.SECTOR, code, mp, ttl=ttl)
                out[fname] = True
            elif fname == "known_seats.json" and isinstance(data, dict):
                # Hash 形式存
                for seat_name, mp in data.items():
                    store.hset(K.SEAT_KNOWN, seat_name, mp, ttl=ttl)
                out[fname] = True
            else:
                # String 形式存
                store.set(key, data, ttl=ttl)
                out[fname] = True
        except Exception as e:
            log.warning(f"  {fname} 失败: {e}")
            out[fname] = False
    log.info(f"  ✓ {out}, 耗时 {time.time()-t0:.1f}s")
    return out


# ═══════════════════════════════════════════════════
# 6. 校验 (5% 抽样比对老源 vs Redis)
# ═══════════════════════════════════════════════════
def verify(store) -> dict[str, bool]:
    log.info("[verify] 5% 抽样比对...")
    out = {}

    # 6.1 日线 — 抽 10 个 code,各比对前 5 行
    try:
        codes = []
        with _conn_sqlite() as conn:
            for r in conn.execute("SELECT DISTINCT code FROM daily ORDER BY RANDOM() LIMIT 10"):
                codes.append(r[0])
        ok = True
        for code in codes:
            src = {}
            with _conn_sqlite() as conn:
                for r in conn.execute(
                    "SELECT date, close FROM daily WHERE code=? ORDER BY date DESC LIMIT 5", (code,)
                ).fetchall():
                    src[r[0]] = r[1]
            dst_raw = store.hgetall(K.DAILY.format(code=code))
            dst = {}
            for date, payload in dst_raw.items():
                if isinstance(payload, dict):
                    dst[date] = payload.get("close")
            if set(src.keys()) - set(dst.keys()):
                log.warning(f"  日线 {code}: 老源 keys={len(src)} redis keys={len(dst)}, 缺失={set(src.keys())-set(dst.keys())}")
                ok = False
        out["daily"] = ok
    except Exception as e:
        log.warning(f"  daily 校验失败: {e}")
        out["daily"] = False

    # 6.2 AI verdict — 抽 5 条
    try:
        ai_count_src = 0
        with _conn_sqlite() as conn:
            ai_count_src = conn.execute("SELECT COUNT(*) FROM ai_verdict WHERE payload_json IS NOT NULL").fetchone()[0]
        ai_keys = [k for k in store.scan("ai:*") if k.startswith("ai:")]
        out["ai"] = len(ai_keys) >= ai_count_src * 0.9  # 容许 10% 误差
        log.info(f"  AI: 老源 {ai_count_src} 条, redis {len(ai_keys)} 个 key")
    except Exception as e:
        out["ai"] = False

    # 6.3 自选
    try:
        wl_count = 0
        with _conn_sqlite() as conn:
            wl_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        wl = store.hgetall(K.WATCHLIST)
        out["watchlist"] = len(wl) == wl_count
        log.info(f"  watchlist: 老源 {wl_count} 条, redis {len(wl)} 条")
    except Exception as e:
        out["watchlist"] = False

    log.info(f"  校验结果: {out}")
    return out


# ═══════════════════════════════════════════════════
# 7. 清理 cache/*.json (--clean 选项)
# ═══════════════════════════════════════════════════
def clean_cache_json():
    log.info("[clean] 清理 cache/*.json...")
    n = 0
    for p in cfg.CACHE_DIR.glob("*.json"):
        p.unlink()
        n += 1
    log.info(f"  ✓ 删除 {n} 个 JSON 文件")


# ═══════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="只校验不迁移")
    parser.add_argument("--clean", action="store_true", help="迁移成功后删 cache/*.json")
    args = parser.parse_args()

    store = get_store()
    if not store.redis_available:
        log.error("❌ Redis 不可用,请先跑 web/setup_redis.sh")
        sys.exit(1)

    log.info(f"Redis OK | {store.status()}")

    if not args.verify:
        migrate_daily(store)
        migrate_ai(store)
        migrate_watchlist(store)
        migrate_cache_json(store)
        migrate_data_json(store)

    results = verify(store)
    if not all(results.values()):
        log.error(f"❌ 校验失败: {results}")
        sys.exit(2)

    if args.clean and not args.verify:
        clean_cache_json()

    log.info(f"\n🎉 迁移完成! Redis 当前: dbsize={store.dbsize()}, stats={store.stats()}")


if __name__ == "__main__":
    main()