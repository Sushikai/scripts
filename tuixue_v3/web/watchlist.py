"""
自选股池 + AI 建议 (2026-07-11)

设计:
- watchlist 表: 用户增删的股票
- watchlist_ai 表: 每只股票最新 AI 判定 (verdict + 建议时间窗口 + 入场价/止损 + 5/10日涨跌等)
- 选股页 GET /api/watchlist 直接读这两张表,不需要再调 AI
- 个股页 loadAIAnalysis 完成后, 触发 _write_watchlist_ai 写入, 选股页立即可见

AI 建议 (watchlist_ai 字段) 来自:
  /api/stock/{code}/ai_analysis (走铁律 + 板块联动 + 龙虎 + 资金)
  + 本模块 _enrich_for_watchlist (附加 5/10 日涨跌 + 主力/散户占比 + 当前价/涨停价距离)
  + AI system prompt 增量字段 (suggested_window / entry_price / stop_loss / time_horizon)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time as systime
from datetime import datetime, timedelta
from typing import Any


# 2026-07-17: 自选删除偶发 "database is locked" — 用 cache_db.safe_write 统一兜底
# (cache_db.py:284-310) — 重试 + 先 ROLLBACK stale txn。DELETE 是 destructive 端点,
# 即便调用失败也要给前端 ok=false + 明确 error,不要静默成功。
def _safe_write(fn, *, retries: int = 4):
    """薄包装,只是模块本地命名以便 watchlist 调用点代码一致。"""
    from .. import cache_db
    return cache_db.safe_write(fn, retries=retries)

log = logging.getLogger("tuixue_v3.web.watchlist")


def _conn() -> sqlite3.Connection:
    from .. import cache_db
    return cache_db._thread_conn()


# ═══════════════════════════════════════════════════
# watchlist CRUD
# ═══════════════════════════════════════════════════
def add(code: str, name: str = "", tag: str = "自选", note: str = "") -> dict:
    """添加股票到自选股池。返回完整 row dict。"""
    code = str(code).strip().zfill(6)
    if not code or len(code) != 6:
        raise ValueError(f"无效的股票代码: {code}")
    if not name:
        try:
            from .. import data_layer as dl
            for c, n in (dl.fetch_stock_list_all() or []):
                if c == code:
                    name = n
                    break
        except Exception:
            pass
    name = name or code
    now = systime.time()

    def _do(conn):
        # 拿最大 sort_order 放末尾
        max_row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM watchlist").fetchone()
        next_sort = (max_row[0] or 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (code, name, tag, sort_order, added_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (code, name, tag or "自选", next_sort, now, note or ""),
        )
        conn.commit()
        return get(code) or {"code": code, "name": name, "tag": tag, "sort_order": next_sort, "note": note}

    return _safe_write(_do)


def remove(code: str) -> bool:
    code = str(code).strip().zfill(6)

    def _do(conn):
        cur = conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
        conn.commit()
        return cur.rowcount > 0

    return _safe_write(_do)


def update(code: str, *, tag: str | None = None, note: str | None = None, sort_order: int | None = None) -> bool:
    code = str(code).strip().zfill(6)
    fields, vals = [], []
    if tag is not None:
        fields.append("tag=?"); vals.append(tag)
    if note is not None:
        fields.append("note=?"); vals.append(note)
    if sort_order is not None:
        fields.append("sort_order=?"); vals.append(int(sort_order))
    if not fields:
        return False
    vals.append(code)

    def _do(conn):
        cur = conn.execute(f"UPDATE watchlist SET {','.join(fields)} WHERE code=?", vals)
        conn.commit()
        return cur.rowcount > 0

    return _safe_write(_do)


def get(code: str) -> dict | None:
    code = str(code).strip().zfill(6)
    conn = _conn()
    r = conn.execute(
        "SELECT code, name, tag, sort_order, added_at, note FROM watchlist WHERE code=?",
        (code,),
    ).fetchone()
    if not r:
        return None
    return {
        "code": r[0], "name": r[1] or r[0], "tag": r[2] or "自选",
        "sort_order": r[3], "added_at": r[4], "note": r[5] or "",
    }


def list_all() -> list[dict]:
    """返回全部自选股(按 sort_order ASC, id ASC)。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT code, name, tag, sort_order, added_at, note FROM watchlist "
        "ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return [
        {"code": r[0], "name": r[1] or r[0], "tag": r[2] or "自选",
         "sort_order": r[3], "added_at": r[4], "note": r[5] or ""}
        for r in rows
    ]


# ═══════════════════════════════════════════════════
# watchlist_ai 读写
# ═══════════════════════════════════════════════════
def upsert_ai(code: str, payload: dict, trade_date: str | None = None) -> None:
    """写一只股票的 AI 建议 (来自 /api/stock/{code}/ai_analysis 的结果 + 增强字段)。

    payload 期望字段:
      verdict / role / conviction / summary / rules_passed / rules_failed / key_risks
      suggested_window / entry_price_range / stop_loss / time_horizon
      extras (dict, 5/10日涨跌 / 资金占比 / 板块涨停数等)
    """
    code = str(code).strip().zfill(6)
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    now = systime.time()
    conn = _conn()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_ai "
            "(code, trade_date, verdict, role, conviction, suggested_window, entry_price_range, "
            " stop_loss, time_horizon, summary, rules_passed_json, rules_failed_json, "
            " key_risks_json, extras_json, ts_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                trade_date,
                str(payload.get("verdict") or "-"),
                str(payload.get("role") or "中军"),
                int(payload.get("conviction") or 0),
                str(payload.get("suggested_window") or "暂观望"),
                str(payload.get("entry_price_range") or ""),
                str(payload.get("stop_loss") or ""),
                str(payload.get("time_horizon") or ""),
                str(payload.get("summary") or ""),
                json.dumps(payload.get("rules_passed") or [], ensure_ascii=False),
                json.dumps(payload.get("rules_failed") or [], ensure_ascii=False),
                json.dumps(payload.get("key_risks") or [], ensure_ascii=False),
                json.dumps(payload.get("extras") or {}, ensure_ascii=False),
                now,
            ),
        )


def get_ai(code: str) -> dict | None:
    """单只股票最新 AI 建议(同日有效)。"""
    code = str(code).strip().zfill(6)
    conn = _conn()
    r = conn.execute(
        "SELECT trade_date, verdict, role, conviction, suggested_window, entry_price_range, "
        "       stop_loss, time_horizon, summary, rules_passed_json, rules_failed_json, "
        "       key_risks_json, extras_json, ts_updated "
        "FROM watchlist_ai WHERE code=?",
        (code,),
    ).fetchone()
    if not r:
        return None
    today = datetime.now().strftime("%Y%m%d")
    if r[0] != today and (systime.time() - (r[13] or 0)) > 86400 * 2:
        # 跨日且过 2 天 → 视作过期
        return None
    return {
        "trade_date":       r[0],
        "verdict":          r[1] or "-",
        "role":             r[2] or "中军",
        "conviction":       int(r[3] or 0),
        "suggested_window": r[4] or "暂观望",
        "entry_price_range": r[5] or "",
        "stop_loss":        r[6] or "",
        "time_horizon":     r[7] or "",
        "summary":          r[8] or "",
        "rules_passed":     json.loads(r[9])  if r[9]  else [],
        "rules_failed":     json.loads(r[10]) if r[10] else [],
        "key_risks":        json.loads(r[11]) if r[11] else [],
        "extras":           json.loads(r[12]) if r[12] else {},
        "ts_updated":       r[13],
        "is_stale":         r[0] != today,
    }


def list_ai_batch(codes: list[str]) -> dict[str, dict | None]:
    """批量取 AI (N+1 兜底)。"""
    if not codes:
        return {}
    codes = [str(c).strip().zfill(6) for c in codes]
    placeholders = ",".join("?" for _ in codes)
    conn = _conn()
    rows = conn.execute(
        f"SELECT code, trade_date, verdict, role, conviction, suggested_window, entry_price_range, "
        f"       stop_loss, time_horizon, summary, rules_passed_json, rules_failed_json, "
        f"       key_risks_json, extras_json, ts_updated "
        f"FROM watchlist_ai WHERE code IN ({placeholders})",
        codes,
    ).fetchall()
    today = datetime.now().strftime("%Y%m%d")
    out: dict[str, dict | None] = {c: None for c in codes}
    for r in rows:
        out[r[0]] = {
            "trade_date":       r[1],
            "verdict":          r[2] or "-",
            "role":             r[3] or "中军",
            "conviction":       int(r[4] or 0),
            "suggested_window": r[5] or "暂观望",
            "entry_price_range": r[6] or "",
            "stop_loss":        r[7] or "",
            "time_horizon":     r[8] or "",
            "summary":          r[9] or "",
            "rules_passed":     json.loads(r[10]) if r[10] else [],
            "rules_failed":     json.loads(r[11]) if r[11] else [],
            "key_risks":        json.loads(r[12]) if r[12] else [],
            "extras":           json.loads(r[13]) if r[13] else {},
            "ts_updated":       r[14],
            "is_stale":         r[1] != today,
        }
    return out


# ═══════════════════════════════════════════════════
# 列表(给 /api/watchlist 用):watchlist + ai + 实时行情快照
# ═══════════════════════════════════════════════════
def list_with_ai_snapshot() -> list[dict]:
    """自选股 + AI 建议 + 实时行情拼装(给选股页)。"""
    items = list_all()
    if not items:
        return []
    codes = [x["code"] for x in items]
    ai_map = list_ai_batch(codes)
    # 拉实时 quote 批量(避免 N+1)
    quote_map = _batch_quote(codes)
    out = []
    for it in items:
        code = it["code"]
        ai = ai_map.get(code)
        quote = quote_map.get(code) or {}
        extras = (ai or {}).get("extras") or {}
        out.append({
            **it,
            "ai": ai,                     # None 表示还没有 AI
            "quote": quote,               # {最新价, 涨跌幅, 换手率, ...}
            # 实时摘要(给表格 cell 用,不调 AI)
            "snapshot": {
                "price":      quote.get("最新价") or quote.get("price"),
                "chg_pct":    quote.get("涨跌幅") or quote.get("change_pct"),
                "turnover":   quote.get("换手率"),
                "vol_ratio":  quote.get("量比"),
                "amount":     quote.get("成交额"),
                "mcap":       quote.get("总市值"),
                "pct_5d":     extras.get("pct_5d"),
                "pct_10d":    extras.get("pct_10d"),
                "main_pct":   extras.get("main_pct"),
                "retail_pct": extras.get("retail_pct"),
                "sector_zt":  extras.get("sector_zt"),
                "streak":     extras.get("streak"),
            },
        })
    return out


def _batch_quote(codes: list[str]) -> dict[str, dict]:
    """批量拉实时行情(走 lib_common.fetch_realtime)。
    R1 (2026-07-21): 优先读 server._cache_quote (TTLCache,realtime poller 已预热),
    命中率通常 ~99%,完全免掉上层 akshare 调用,消除 watchlist 切页 4-24s 阻塞。
    仅对 cache miss 的 stock 才走 lib_common.fetch_realtime (per-stock 3s)。
    """
    import asyncio
    from .. import lib_common as lc
    out: dict[str, dict] = {}

    # 1) 读 server._cache_quote — 走 fastapi 进程内 worker memory,sub-ms
    try:
        from .server import _cache_quote
        for c in codes:
            hit = _cache_quote.get(("quote", c))
            if isinstance(hit, dict) and hit:
                out[c] = hit
    except Exception:
        pass  # server 未导入 / 启动顺序异常时走下层兜底

    miss_codes = [c for c in codes if c not in out]
    if not miss_codes:
        return out
    log.debug(f"_batch_quote cache miss {len(miss_codes)}/{len(codes)}: {miss_codes[:5]}…")

    # 2) miss 的走 lib_common.fetch_realtime (跟原逻辑等价,超时 4s/c)
    async def _one(c: str):
        try:
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(loop.run_in_executor(None, lc.fetch_realtime, c), timeout=4)
        except Exception:
            return None

    async def _all():
        return await asyncio.gather(*[_one(c) for c in miss_codes], return_exceptions=True)

    try:
        results = asyncio.run(_all())
    except RuntimeError:
        results = []
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(miss_codes))) as ex:
            futures = [ex.submit(lc.fetch_realtime, c) for c in miss_codes]
            for f, c in zip(futures, miss_codes):
                try:
                    results.append(f.result(timeout=6))
                except Exception:
                    results.append(None)
    for c, r in zip(miss_codes, results):
        if isinstance(r, dict) and r:
            out[c] = r
            # 顺便写回 cache, 下次命中
            try:
                from .server import _cache_quote as _cq
                _cq.set(("quote", c), r)
            except Exception:
                pass
    return out


# ═══════════════════════════════════════════════════
# AI 增强: 5/10 日涨跌 + 主力/散户占比 → extras
# ═══════════════════════════════════════════════════
def enrich_extras(code: str) -> dict:
    """组装 watchlist_ai.extras: 5/10日涨跌 / 主力散户占比 / 板块涨停数 / 连板 / 数据快照。"""
    from .. import lib_common as lc
    from . import fund_flow
    out: dict[str, Any] = {}

    # 1) 5/10 日涨跌 — 走 K 线
    try:
        df = lc.fetch_daily(code, days=30)
        if df is not None and not df.empty and "收盘" in df.columns and "日期" in df.columns:
            closes = df.sort_values("日期")["收盘"].astype(float).tolist()
            if len(closes) >= 6 and closes[-6] and closes[-1]:
                out["pct_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
            if len(closes) >= 11 and closes[-11] and closes[-1]:
                out["pct_10d"] = round((closes[-1] / closes[-11] - 1) * 100, 2)
            # 5 日均量
            if "成交量" in df.columns:
                vols = df.sort_values("日期")["成交量"].astype(float).tolist()[-5:]
                if vols:
                    out["vol_5d_avg"] = int(sum(vols) / len(vols))
    except Exception as e:
        log.debug(f"enrich kline {code}: {e}")

    # 2) 主力/散户占比 — 走 fund_flow.get_combined 当日数据
    try:
        ff = fund_flow.get_combined(code, days=10)
        today_ff = (ff or {}).get("today") or {}
        if today_ff:
            main = abs(float(today_ff.get("main_net") or 0))
            big = abs(float(today_ff.get("big_net") or 0))
            mid = abs(float(today_ff.get("mid_net") or 0))
            sml = abs(float(today_ff.get("sml_net") or 0))
            total = main + big + mid + sml
            if total > 0:
                # 2026-07-10 修复: 分母包含所有档位, retail = mid+sml
                out["main_pct"] = round(main / total * 100, 1)
                out["big_pct"] = round(big / total * 100, 1)
                out["retail_pct"] = round((mid + sml) / total * 100, 1)
            out["main_net_wan"] = float(today_ff.get("main_net") or 0)
            out["super_net_wan"] = float(today_ff.get("super_net") or 0)
    except Exception as e:
        log.debug(f"enrich fund_flow {code}: {e}")

    # 3) 板块涨停数 + 连板 — 走 limit_up_context
    try:
        from . import limit_up_context as luc
        lu = luc.get_limit_up_context(code, sector_name=None)
        if lu:
            today_lu = (lu or {}).get("today") or {}
            out["streak"] = today_lu.get("连板数") or 0
            out["recent_5d_count"] = len((lu or {}).get("recent_5d") or [])
            sec_today = (lu or {}).get("sector_today") or []
            out["sector_zt"] = len(sec_today)
            out["sector_consecutive"] = sum(1 for x in sec_today if (x.get("连板数") or 0) >= 2)
    except Exception as e:
        log.debug(f"enrich limit_up {code}: {e}")

    # 4) 当前价/涨停价距离 — 走 quote
    try:
        q = lc.fetch_realtime(code)
        if q:
            out["price"] = float(q.get("最新价") or 0)
            out["limit_up_price"] = float(q.get("涨停价") or 0)
            out["limit_dn_price"] = float(q.get("跌停价") or 0)
            out["turnover"] = float(q.get("换手率") or 0)
            out["vol_ratio"] = float(q.get("量比") or 0)
    except Exception as e:
        log.debug(f"enrich quote {code}: {e}")

    return out