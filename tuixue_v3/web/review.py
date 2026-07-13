"""
复盘系统 (2026-07-10 启用)

设计目标:
- 用户记录每笔交易 (code, direction, price, shares, occurred_at)
- 自动按交易时间回看当时盘面 → 调用 AI 复盘 (基于铁律 + 盘面 + 游资/主力)
- AI 复盘要带"记忆":用历史 30 笔 review 摘要做 system 注入,让模型看到自己的判断演变
- AI 复盘结果存 trade_reviews 表

API (web/review.py 暴露):
  - record_trade(code, direction, price, shares, occurred_at=None, memo="", tags=[]) -> trade_id
  - list_trades(limit=50, code=None) -> [dict]
  - update_trade(trade_id, **fields) -> bool
  - delete_trade(trade_id) -> bool
  - get_trade(trade_id) -> dict | None
  - review_trade(trade_id, *, force=False) -> dict  (核心:AI 复盘)
  - list_reviews(trade_id) -> [dict]
  - summary_stats() -> dict  (胜率/平均盈亏/常见错误)
  - next_day_picks() -> dict  (基于次日选股 + 历史错模式预警)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time as systime
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("tuixue_v3.web.review")

_tls = threading.local()
_DB_PATH = None  # 走 cache_db.daily() 同一个 sqlite 文件


def _conn() -> sqlite3.Connection:
    """复用 cache_db 的 sqlite 连接(单数据库,避免锁竞争)。"""
    from .. import cache_db
    return cache_db._thread_conn()


# ═══════════════════════════════════════════════════
# 交易记录 CRUD
# ═══════════════════════════════════════════════════
def record_trade(
    code: str,
    direction: str,
    price: float,
    shares: int,
    occurred_at: str | None = None,
    trade_date: str | None = None,
    mode: str = "manual",
    memo: str = "",
    tags: list[str] | None = None,
    name: str | None = None,
) -> int:
    """记一笔交易。occurred_at 缺省 = now。返回 trade_id。"""
    code = str(code).strip().zfill(6)
    direction = (direction or "").lower()
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction 必须是 buy/sell, 收到 {direction}")
    if shares <= 0 or shares % 100 != 0:
        raise ValueError(f"shares 必须是 100 的整数倍, 收到 {shares}")
    if price <= 0:
        raise ValueError(f"price 必须 > 0, 收到 {price}")
    if not occurred_at:
        occurred_at = datetime.now().isoformat(timespec="seconds")
    if not trade_date:
        # 从 occurred_at 取 YYYYMMDD
        trade_date = occurred_at[:10].replace("-", "")
    # 自动补 name
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
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO trades (code, name, direction, price, shares, occurred_at, trade_date, mode, memo, tags, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, name, direction, float(price), int(shares),
         occurred_at, trade_date, mode, memo, json.dumps(tags or [], ensure_ascii=False),
         systime.time()),
    )
    conn.commit()
    return cur.lastrowid


def find_duplicate_trade(
    code: str,
    direction: str,
    price: float,
    shares: int,
    occurred_at: str | None = None,
    trade_date: str | None = None,
) -> int | None:
    """查库里是否已有"同一笔"交易(股票+方向+价格+股数+时间全对上)。
    返回已存在记录的 id,没有返回 None。用于批量录入去重防止反复入库。
    """
    code = str(code).strip().zfill(6)
    direction = (direction or "").lower()
    try:
        price = round(float(price), 3)
        shares = int(shares)
    except Exception:
        return None
    if not trade_date and occurred_at:
        trade_date = occurred_at[:10].replace("-", "")
    conn = _conn()
    rows = conn.execute(
        "SELECT id, occurred_at FROM trades WHERE code=? AND direction=? AND shares=? "
        "AND ABS(price-?)<0.005 AND trade_date=?",
        (code, direction, shares, price, trade_date or ""),
    ).fetchall()
    if not rows:
        return None
    # 有 occurred_at 就精确到分钟比对(OCR 秒可能不同);否则同日同价同股数即视为重复
    if occurred_at:
        want = occurred_at[:16]  # YYYY-MM-DDTHH:MM
        for r in rows:
            oa = (r[1] or "")[:16]
            if oa == want:
                return r[0]
        # 没有精确到分钟对上的 → 仍按同日同价视为重复(时间可能缺失)
    return rows[0][0]


def list_trades(limit: int = 50, code: str | None = None, since_days: int | None = None) -> list[dict]:
    """最近 N 笔交易(含最后一条复盘结果)。

    读时 name→code 兜底:历史脏数据 (code='000000') 按 name 反查真实代码,
    保证前端按 code 分组/行情/汇总正确(2026-07-14)。
    """
    conn = _conn()
    sql = "SELECT id, code, name, direction, price, shares, occurred_at, trade_date, mode, memo, tags FROM trades"
    params: list = []
    if code:
        sql += " WHERE code=?"
        params.append(str(code).strip().zfill(6))
    if since_days:
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y%m%d")
        sql += (" AND" if code else " WHERE") + " trade_date>=?"
        params.append(cutoff)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    out = []
    trade_ids = []
    placeholder_codes_seen = False
    for r in rows:
        code_raw = r[1]
        d = {
            "id": r[0], "code": code_raw, "name": r[2], "direction": r[3],
            "price": r[4], "shares": r[5], "occurred_at": r[6], "trade_date": r[7],
            "mode": r[8], "memo": r[9] or "",
            "tags": json.loads(r[10]) if r[10] else [],
        }
        if not str(code_raw or "").strip() or str(code_raw).strip() == "000000":
            placeholder_codes_seen = True
        out.append(d)
        trade_ids.append(r[0])

    # 历史 000000 占位反查 name→code (DB 不动,只在响应里替换)
    if placeholder_codes_seen:
        try:
            from .. import data_layer as _dl_for_lookup
            market = _dl_for_lookup.fetch_stock_list_all() or []
            name_to_code: dict[str, str] = {}
            for c, n in market:
                nn = (n or "").strip()
                cc = (c or "").strip().zfill(6)
                if nn and cc.isdigit() and cc != "000000" and nn not in name_to_code:
                    name_to_code[nn] = cc
            patched = 0
            for t in out:
                code_s = str(t.get("code") or "").strip()
                if not code_s or code_s == "000000":
                    nn = (t.get("name") or "").strip()
                    if nn in name_to_code:
                        t["code"] = name_to_code[nn]
                        patched += 1
            if patched:
                log.info(f"list_trades: name→code 反查 {patched} 笔")
        except Exception as e:
            log.debug(f"list_trades name→code lookup: {e}")
    if not trade_ids:
        return out
    # 批量拉最新复盘(每笔 1 条)
    placeholders = ",".join("?" for _ in trade_ids)
    rev_rows = conn.execute(
        f"SELECT r.trade_id, r.verdict, r.score, r.mistake_pattern, "
        f"       r.rules_passed_json, r.rules_failed_json, r.summary_md, r.context_json "
        f"FROM trade_reviews r "
        f"INNER JOIN ("
        f"  SELECT trade_id, MAX(id) AS max_id FROM trade_reviews GROUP BY trade_id"
        f") m ON r.id = m.max_id "
        f"WHERE r.trade_id IN ({placeholders})",
        trade_ids,
    ).fetchall()
    rev_map: dict[int, dict] = {}
    for rr in rev_rows:
        rp = json.loads(rr[4]) if rr[4] else []
        rf = json.loads(rr[5]) if rr[5] else []
        # 兼容
        def _n(it):
            return {"id": "?", "text": it} if isinstance(it, str) else it
        rp_n = [_n(x) for x in rp]
        rf_n = [_n(x) for x in rf]
        ctx_j = json.loads(rr[7]) if rr[7] else {}
        if not isinstance(ctx_j, dict):
            ctx_j = {}
        advice = ctx_j.get("ai_advice", "")
        rev_map[rr[0]] = {
            "verdict": rr[1] or "—",
            "score": rr[2] or 0,
            "mistake_pattern": rr[3] or "—",
            "rules_passed": rp_n,
            "rules_failed": rf_n,
            "rules_conflict_count": len(rf_n),
            "ai_advice": advice,
            "main_mistake": ctx_j.get("main_mistake", ""),
            "limit_up_recap": ctx_j.get("limit_up_recap", ""),
            "summary": rr[6] or "",
        }
    for d in out:
        d["last_review"] = rev_map.get(d["id"])
    return out


def get_trade(trade_id: int) -> dict | None:
    conn = _conn()
    r = conn.execute(
        "SELECT id, code, name, direction, price, shares, occurred_at, trade_date, mode, memo, tags "
        "FROM trades WHERE id=?", (trade_id,)
    ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "code": r[1], "name": r[2], "direction": r[3],
        "price": r[4], "shares": r[5], "occurred_at": r[6], "trade_date": r[7],
        "mode": r[8], "memo": r[9] or "",
        "tags": json.loads(r[10]) if r[10] else [],
    }


def update_trade(trade_id: int, **fields) -> bool:
    """局部更新。R1-B 修复: 镜像 record_trade 的所有校验,避免更新绕过。"""
    allowed = {"price", "shares", "memo", "tags", "direction", "occurred_at", "trade_date", "name"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        # 镜像 create 时的硬校验
        if k == "direction" and str(v).lower() not in ("buy", "sell"):
            raise ValueError(f"direction 必须是 buy/sell, 收到 {v}")
        if k == "price":
            try:
                p = float(v)
            except Exception:
                raise ValueError(f"price 非数字: {v!r}")
            if p <= 0:
                raise ValueError(f"price 必须 > 0, 收到 {p}")
            v = p
        if k == "shares":
            try:
                s = int(v)
            except Exception:
                raise ValueError(f"shares 非整数: {v!r}")
            if s <= 0 or s % 100 != 0:
                raise ValueError(f"shares 必须是 100 的整数倍, 收到 {s}")
            v = s
        if k == "tags" and isinstance(v, list):
            v = json.dumps(v, ensure_ascii=False)
        if k == "occurred_at" and v:
            # ISO YYYY-MM-DDTHH:MM:SS 粗校验
            if not isinstance(v, str) or len(v) < 16 or v[4] != "-" or v[7] != "-":
                raise ValueError(f"occurred_at 必须为 ISO 字符串, 收到 {v!r}")
            # 同步 trade_date (审计报告里 audit-bug-12)
            if "trade_date" not in fields:
                sets.append("trade_date=?"); vals.append(v[:10].replace("-", ""))
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return False
    vals.append(trade_id)
    conn = _conn()
    cur = conn.execute(f"UPDATE trades SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return cur.rowcount > 0


def delete_trade(trade_id: int) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.execute("DELETE FROM trade_reviews WHERE trade_id=?", (trade_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_trades_by_code(code: str) -> int:
    """删除某只股票的全部交易 + 复盘记录。返回删除的 trades 行数。"""
    code = str(code).strip().zfill(6)
    conn = _conn()
    ids = [r[0] for r in conn.execute("SELECT id FROM trades WHERE code=?", (code,)).fetchall()]
    if not ids:
        return 0
    cur = conn.execute("DELETE FROM trades WHERE code=?", (code,))
    # 删 reviews (按 trade_id in ...)
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM trade_reviews WHERE trade_id IN ({placeholders})", ids)
    conn.commit()
    return cur.rowcount


# ═══════════════════════════════════════════════════
# AI 复盘 (核心)
# ═══════════════════════════════════════════════════

# R-cfg-1: 上下文拉取线程池 — sections 1-8 全并发,section 9 依赖 sector
_ctx_executor = None
def _get_ctx_executor():
    global _ctx_executor
    if _ctx_executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _ctx_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ctx")
        import atexit as _atexit
        _atexit.register(lambda: _ctx_executor.shutdown(wait=False))
    return _ctx_executor


def _build_context(trade: dict) -> dict:
    """组装复盘上下文: 决策日前后盘面 + 游资 + 资金 + K线 + 板块 + 新闻 + 主力出仓。
    用户要求 (2026-07-10): 必须含前 10 日 + 后 5 日 K线、全部游资席位、主力资金逐日明细。
    R14: 9 段 IO 拉取, 8 段 (1-8) 并发到 4 路线程池, 仅 9 段 (limit_up_landscape)
        依赖 sector 所以串行在 sector 之后。整体由原本 ≈sum(t_i) 降到 ≈max(t_i)。
    """
    from .. import lib_common as lc
    from . import seat_lookup, fund_flow, holder_lookup
    from . import sector_classify, news_lookup
    code = trade["code"]

    # ── section factories:每一个返回一个 dict,放进 ctx[k] ──
    def _sec_kline():
        out: dict[str, Any] = {}
        df = lc.fetch_daily(code, days=120)
        if df is None or df.empty:
            return out
        df = df.sort_values("日期").reset_index(drop=True)
        tdate = trade["trade_date"]
        tdate_norm = f"{tdate[:4]}-{tdate[4:6]}-{tdate[6:8]}"
        if "日期" not in df.columns:
            return out
        sub = df[df["日期"] >= tdate_norm].head(5)
        out["kline_after"] = _safe_records(sub)
        pre = df[df["日期"] < tdate_norm].tail(10)
        out["kline_before"] = _safe_records(pre)
        out["trend_10d_before"] = _compute_trend_stats(pre)
        out["aftermath_5d"] = _compute_aftermath(sub)
        return out

    def _sec_main_exit():
        out: dict[str, Any] = {}
        # R14: 删了原先的 `lc.fetch_index_daily("sh000001", days=2)` 触发调用 —
        # 那行只是 legacy 占位,不进入任何字段,白浪费一个网络往返
        v = lc.detect_main_force_exit(code, lookback_days=10)
        if v:
            out["main_exit_10d"] = v
        return out

    def _sec_fund_flow():
        out: dict[str, Any] = {}
        ff = fund_flow.get_combined(code, days=60)
        if ff.get("history"):
            out["fund_flow"] = ff
        return out

    def _sec_seats():
        out: dict[str, Any] = {}
        s = seat_lookup.get_stock_seats(code, lookback_days=60)
        if s.get("rows"):
            out["seats"] = s
            out["seats_recent_10d"] = _seats_recent_stats(s, days=10)
        return out

    def _sec_sector():
        out: dict[str, Any] = {}
        s = sector_classify.get_sector(code, force_refresh=False)
        if s:
            out["sector"] = s
        return out

    def _sec_news():
        out: dict[str, Any] = {}
        nd = news_lookup.get_cached_news(force_refresh=False, num=80)
        all_news = nd.get("items", []) if isinstance(nd, dict) else []
        related = [n for n in all_news if code in (n.get("title", "") + str(n.get("codes", [])))]
        out["news"] = (related + all_news)[:8]
        return out

    def _sec_holders():
        out: dict[str, Any] = {}
        h = holder_lookup.fetch_holder_info(code)
        if h:
            out["holders"] = h
        return out

    def _sec_market():
        out: dict[str, Any] = {}
        idx = lc.fetch_index_daily("sh000001", days=15)
        if idx is not None and not idx.empty:
            out["market_10d"] = idx.to_dict(orient="records")[-10:]
        return out

    # ── 并发 8 段,每段单独超时 18s,失败不阻塞其它段 ──
    pool = _get_ctx_executor()
    from concurrent.futures import TimeoutError as _FutTE
    sections = {
        "kline":     _sec_kline,
        "main_exit": _sec_main_exit,
        "fund_flow": _sec_fund_flow,
        "seats":     _sec_seats,
        "sector":    _sec_sector,
        "news":      _sec_news,
        "holders":   _sec_holders,
        "market":    _sec_market,
    }
    futures = {k: pool.submit(fn) for k, fn in sections.items()}
    ctx: dict[str, Any] = {}
    for k, f in futures.items():
        try:
            d = f.result(timeout=18)
            if d:
                ctx.update(d)
        except _FutTE:
            log.warning(f"复盘 {k} {code} 超时 (18s)")
        except Exception as e:
            log.warning(f"复盘 {k} {code} 失败: {e}")

    # ── section 9: limit_up_landscape 依赖 sector,所以放在最后 ──
    try:
        from . import limit_up_context as luc
        sector_name = None
        s = ctx.get("sector")
        if isinstance(s, dict):
            sector_name = s.get("name") or s.get("sector") or s.get("板块")
        v = luc.get_limit_up_context(code, sector_name)
        if v:
            ctx["limit_up_landscape"] = v
    except Exception as e:
        log.debug(f"复盘 limit_up_landscape {code} 失败: {e}")
    return ctx


def _safe_records(df) -> list:
    """DataFrame → list[dict] 安全转换, 处理 NaN/Timestamp."""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, row in df.iterrows():
        item = {}
        for k, v in row.items():
            try:
                import math as _m
                if isinstance(v, float) and _m.isnan(v):
                    item[k] = None
                else:
                    item[k] = v
            except Exception:
                item[k] = str(v) if v is not None else None
        out.append(item)
    return out


def _compute_trend_stats(df_pre_10d) -> dict:
    """决策日前 10 日: 连板 / 放量 / 趋势统计。"""
    if df_pre_10d is None or len(df_pre_10d) == 0:
        return {}
    closes = [float(r.get("收盘") or 0) for r in df_pre_10d.to_dict("records")]
    vols = [float(r.get("成交量") or 0) for r in df_pre_10d.to_dict("records")]
    if not closes:
        return {}
    change_total = round((closes[-1] / closes[0] - 1) * 100, 2) if closes[0] else 0
    # 涨幅>9% 视为涨停
    limit_up_days = 0
    for i in range(1, len(closes)):
        if closes[i - 1] and (closes[i] / closes[i - 1] - 1) * 100 >= 9.0:
            limit_up_days += 1
    # 放量(成交量 > 前均量 1.5 倍)天数
    avg_vol = sum(vols) / len(vols) if vols else 0
    big_vol_days = sum(1 for v in vols if avg_vol and v > avg_vol * 1.5) if vols else 0
    return {
        "change_total_pct": change_total,
        "limit_up_days": limit_up_days,
        "big_vol_days": big_vol_days,
        "last_close": closes[-1] if closes else 0,
    }


def _compute_aftermath(df_after_5d) -> dict:
    """决策后 5 日的实际走势 — 用来判断这次操作对错。"""
    recs = df_after_5d.to_dict("records") if df_after_5d is not None else []
    if not recs:
        return {}
    closes = [float(r.get("收盘") or 0) for r in recs]
    if not closes or closes[0] == 0:
        return {}
    return {
        "5d_change_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "first_close": closes[0],
        "last_close": closes[-1],
        "high_after": max(float(r.get("最高") or 0) for r in recs),
        "low_after": min(float(r.get("最低") or 0) for r in recs),
        "days": len(recs),
    }


def _seats_recent_stats(seats: dict, days: int = 10) -> dict:
    """席位近 N 日 vs 之前 N 日活跃度对比(看出游资是在加速还是撤退)。"""
    rows = seats.get("rows", []) or []
    if not rows:
        return {}
    now = datetime.now().strftime("%Y-%m-%d")
    recent = []
    older = []
    for r in rows:
        d = str(r.get("date", ""))[:10]
        if not d:
            continue
        # 简化:日期含近的就归 recent,否则 older
        # 实际应当算 days_before,这里用日期包含关系粗判
        try:
            import datetime as _dt
            dt = _dt.datetime.strptime(d, "%Y-%m-%d")
            diff = (datetime.now() - dt).days
            if diff <= days:
                recent.append(r)
            else:
                older.append(r)
        except Exception:
            continue
    buy_w_recent = sum(float(r.get("buy_amt_wan") or 0) for r in recent)
    buy_w_older = sum(float(r.get("buy_amt_wan") or 0) for r in older)
    return {
        "recent_count": len(recent),
        "older_count": len(older),
        "recent_buy_wan": buy_w_recent,
        "older_buy_wan": buy_w_older,
        "trend": "加速" if buy_w_recent > buy_w_older * 1.3 else (
            "撤退" if buy_w_recent < buy_w_older * 0.5 else "平稳"),
    }


def _memory_context(limit: int = 30) -> str:
    """拉历史 review 摘要 → system 注入,给 AI "记忆"。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT t.code, t.name, t.direction, t.price, t.shares, t.trade_date, "
        "       r.verdict, r.score, r.mistake_pattern, r.summary_md "
        "FROM trade_reviews r JOIN trades t ON r.trade_id=t.id "
        "ORDER BY r.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return ""
    lines = ["## 你对历史交易的复盘记录(最近 30 笔,越往后越新):"]
    for r in rows:
        # 截短 summary
        sm = (r[9] or "")[:80].replace("\n", " ")
        pat = r[8] or "—"
        lines.append(
            f"- {r[6][:10]} {r[2]}({r[1]}) {r[3]} {r[4]}股@{r[5]}元"
            f" | {r[7] or '?'}/100 | 错:{pat} | {sm}"
        )
    return "\n".join(lines)


def _format_ctx_for_ai(trade: dict, ctx: dict, memory: str) -> tuple[str, str]:
    """组装 system + user 给 LLM。
    铁律独立 PASS/FAIL:每条铁律独立判定,带编号(如 一.1, 三.6, 五.STEP3)。
    用户要求 (2026-07-10): 必须基于前 10 日 + 后 5 日 K线 + 全部游资席位 + 主力资金逐日。
    """
    from .. import laws
    sys_p = laws.as_prompt() + "\n\n" + memory + """

## 4 层板块分类规则 (2026-07-11 接入)
每只股票已绑定 4 层定位,复盘时必须用这套维度判断主线/二线/杂毛:
  Level1 集群 (6 选 1): 大科技 / 高端制造 / 消费 / 医药生物 / 金融 / 周期资源
  Level2 申万 (31 选 1): 作为个股核心行业
  Level3 产业链 (主线识别最小单位): 例如「人形机器人」「高速光互联」「HBM 存储」
  Level4 细分 (多标签): 例如「谐波减速器」「800G 光模块」「HBM」「整机本体」

主线判定标准:
  - 同一 Level3 产业链**当日涨停 ≥ 15 家** → 「当日主线」→ 重点关注
  - 标的所在 L3 不达主线标准 → 不应作为核心推荐

杂毛识别:
  - 个股虽然挂某个 L4 概念,但实质营收 / 主业不沾 L3 主线 → 标记为 noise (杂毛跟风)
  - 杂毛标的复盘时必须在 `mistake_pattern` 中标为「杂毛」
  - 杂毛标的 conviction (确信度) 强制 ≤ 50,即便其他维度看似合理

角色(role)规则:
  - main (主线龙头): L3 主线 + L4 细分纯正 → 可加仓决策
  - second (二线弹性): L3 主线但 L4 非核心 → 谨慎
  - noise (杂毛跟风): 仅概念沾边,主线外 → 应回避
  - 空 / "—": 不属任何已识别主线 → 默认按主线行情一刀切

你是用户的复盘教练,精通退学战法 64 条铁律(4 大类 + 龙头四步流水线)。
复盘必须**分两段**(顺序不能反):

【第一段:先回溯当日涨停全景】
   利用 `limit_up_landscape` (当日涨停池 / 连板梯队 / 板块热度 / 该股连板数),
   先讲清楚这只票操作当天,市场情绪什么档位、主线在哪、这只票在梯队里处于什么位置。
   这段写进 `limit_up_recap` 字段(80~150 字,像盘后复盘口播)。

【第二段:再回溯我的这笔操作】
   在第一段的市场背景下,**逐条铁律独立 PASS/FAIL 判定**,要求:

1. 铁律编号格式:"一.1"(第一类第1条)/"三.6"(第三类第6条)/"五.STEP3.资金"(第五类 STEP3 资金维度)
2. **必须基于以下数据维度**做评判(用户重点要求):
   - `kline_before` (前 10 日 K线): 看趋势 / 连板 / 放量,判断买入位置是否过高
   - `kline_after` (后 5 日 K线): 看这次买入是否真的"对了"
   - `seats.rows` (全部龙虎榜席位, 60 日): 看游资进出 / 顶级游资是否站台 / 共同买入 vs 借机出货
   - `seats_recent_10d` (近期席位活跃度): `trend` 字段 (加速/撤退/平稳)
   - `fund_flow` (主力资金 60 日): 看买入当日的资金态度(主力净流入 vs 主力出仓)
   - `main_exit_10d` (主力出仓检测 10 日): `is_exiting` + `severity` + `reason`
   - `trend_10d_before` (前 10 日统计): `change_total_pct` / `limit_up_days` / `big_vol_days`
   - `aftermath_5d` (后 5 日真实走势): `5d_change_pct` 用来判断买入决策的实际收益
   - `holders` (散户/主力持股占比): `retail_proxy_pct` 与用户主线策略的匹配度
   - `sector` (板块): 判断用户是否在主线,不是追杂毛
   - `market_10d` (大盘环境): 解释是否逆势操作
   - `limit_up_landscape` (当日涨停全景): 判断买点情绪位置(冰点/主升/高潮/退潮)
3. 每条 fail 必须有量化理由(不是"主观感觉") — 引用以上数据作为依据
4. rules_passed/failed 写编号 + 一句话说明
5. `main_mistake` 用一句话点破"铁律错在哪"(≤25 字,是表格/子页最醒目的一句)
6. 同时给出表格列用的简短建议(≤30 字)
7. 严格 JSON(无 markdown 围栏):
{
  "limit_up_recap": "当日涨停全景回溯(80~150字)",
  "verdict": "优"|"及格"|"失误"|"严重失误",
  "score": 0-100,
  "summary": "一句话总结这次操作(50字内)",
  "main_mistake": "铁律错在哪(≤25字,如:三.1 亏损加仓 / 二.4 追高杂毛)",
  "rules_passed": [{"id": "一.5", "text": "不眼红他人收益"}, ...],   // 2-4 条
  "rules_failed": [{"id": "三.1", "text": "禁止加仓", "reason": "亏损时加仓 100 股"}],   // 1-4 条
  "mistake_pattern": "追高|不止损|无主线|杂毛|情绪化|早盘冲动|打板不成|其他",
  "improvement": "下次怎么改(2-3条具体动作)",
  "ai_advice": "表格里显示的简短建议(≤30字,如:明早冲高减半,跌破25.2止损)",
  "key_risks": ["复盘当时没看到的风险点"],
  "taxonomy_role": "main|second|noise|—",   // 2026-07-11 新增 — 与该股 L3/L4 taxonomy 对齐
  "is_mainline": true|false                   // 该股所在 L3 产业链是否当日主线
}"""
    # 把 ctx 拆成"信号卡"逐项列出,确保 AI 一眼看到关键数字
    parts = [f"""交易快照:
代码={trade['code']} 名称={trade['name']} 方向={trade['direction']}
价格={trade['price']} 数量={trade['shares']} 时间={trade['occurred_at']}
备注={trade.get('memo','') or '无'} 标签={trade.get('tags',[])}"""]

    # 1) 决策前 10 日走势统计
    t10 = ctx.get('trend_10d_before') or {}
    if t10:
        parts.append(f"""\n## 决策前 10 日趋势统计
- 累计涨幅: {t10.get('change_total_pct', 0):+.2f}%
- 涨停天数: {t10.get('limit_up_days', 0)} 天
- 放量天数: {t10.get('big_vol_days', 0)} 天 (成交量>均量1.5倍)
- 决策前一日收盘: {t10.get('last_close', 0)}""")

    # 2) 后 5 日实际走势(用来评判)
    aft = ctx.get('aftermath_5d') or {}
    if aft:
        parts.append(f"""\n## 决策后 5 日真实走势
- 5 日累计: {aft.get('5d_change_pct', 0):+.2f}%
- 期间最高/最低: {aft.get('high_after', 0)} / {aft.get('low_after', 0)}""")

    # 3) K 线 JSON (前 10 + 后 5)
    kline_before = ctx.get('kline_before') or []
    kline_after = ctx.get('kline_after') or []
    if kline_before or kline_after:
        parts.append(f"""\n## 决策前 10 日 K 线 (含 OHLC/量)
{json.dumps(kline_before, ensure_ascii=False, default=str)[:2500]}""")
        parts.append(f"""\n## 决策后 5 日 K 线
{json.dumps(kline_after, ensure_ascii=False, default=str)[:1200]}""")

    # 4) 主力资金 + 出仓检测
    me = ctx.get('main_exit_10d') or {}
    if me:
        sev = me.get('severity', '低')
        parts.append(f"""\n## 主力资金 10 日检测
- 是否在出仓: {me.get('is_exiting', False)} | 严重度: {sev}
- 连续流出天数: {me.get('consecutive_out_days', 0)}
- 5 日累计主力净流出: {me.get('total_main_out_5d', 0):.2f} 亿元
- 今日主力净流入: {me.get('today_main_net', 0):+.2f} 亿元
- 触发原因: {me.get('reason', '—')}
- 散户是否接盘(典型派发): {me.get('small_in', False)}""")

    # 5) 资金流历史
    ff = ctx.get('fund_flow') or {}
    if ff.get('history'):
        parts.append(f"""\n## 资金流(最近 60 日)
{json.dumps(ff['history'][-15:], ensure_ascii=False, default=str)[:1500]}""")

    # 6) 龙虎榜 + 近期席位活跃度
    seats = ctx.get('seats') or {}
    sr10 = ctx.get('seats_recent_10d') or {}
    if seats.get('rows'):
        parts.append(f"""\n## 龙虎榜席位(60 日内全部)
{json.dumps(seats['rows'][:10], ensure_ascii=False, default=str)[:2000]}
- 席位总数: {seats.get('seat_count', 0)} | 买入总额: {seats.get('buy_total_wan', 0)}万
- 近 10 日席位活跃度趋势: {sr10.get('trend', '—')} (近: {sr10.get('recent_count', 0)}条 远: {sr10.get('older_count', 0)}条)""")

    # 7) 散户/主力占比
    h = ctx.get('holders') or {}
    if h:
        parts.append(f"""\n## 股东结构
- 散户代理占比: {h.get('retail_proxy_pct', 0):.2f}% | 主力代理占比: {h.get('main_proxy_pct', 0):.2f}%
- 股东户数: {h.get('holder_total', 0):,} | 报告期: {h.get('report_date', '?')}""")

    # 8) 板块 (4 层板块分类 — 2026-07-11 接入 sector_taxonomy)
    s = ctx.get('sector') or {}
    if s:
        from .sector_taxonomy import fmt_taxonomy_full, fmt_taxonomy_short, detect_mainline
        tax = s.get("taxonomy") or {}
        # 取当日涨停池(用于判断该股所在 L3 chain 是否主线)
        mainline_brief = ""
        try:
            from .. import data_layer as dl
            from .sector_classify import get_sector
            zt = dl.fetch_limit_up_pool() or []
            zt_codes = [str(z.get("code") or "").zfill(6) for z in zt]
            ml = detect_mainline(zt_codes=zt_codes, sector_lookup=get_sector, threshold=15)
            # 这只股票所在的 L3 chain 是否主线
            l3 = (tax.get("level3_chain") or "").strip()
            hit = next((m for m in ml if m["chain"] == l3), None)
            if hit:
                mainline_brief = (
                    f"\n  ⚡ 主线判定: 该股所在 L3「{l3}」当日涨停 {hit['zt_count']} 家 → 当日主线")
            elif l3:
                mainline_brief = f"\n  主线判定: L3「{l3}」当日涨停 <15 家,非主线"
        except Exception:
            pass
        parts.append(f"""\n## 板块（4 层标准分类）
{fmt_taxonomy_full(tax)}
  紧凑表达: {fmt_taxonomy_short(tax)}{mainline_brief}""")

    # 9) 大盘环境
    mkt = ctx.get('market_10d') or []
    if mkt:
        parts.append(f"""\n## 大盘沪深300 近 10 日
{json.dumps(mkt[-5:], ensure_ascii=False, default=str)[:600]}""")

    # 10) 新闻
    news = ctx.get('news') or []
    if news:
        parts.append(f"""\n## 相关新闻(可能影响决策的近期事件)
{json.dumps(news[:5], ensure_ascii=False, default=str)[:1500]}""")

    # 11) 当日涨停全景(第一段回溯用)
    lul = ctx.get('limit_up_landscape') or {}
    if lul:
        parts.append(f"""\n## 当日涨停全景(先回溯这段)
- 本股当日: {json.dumps(lul.get('today'), ensure_ascii=False, default=str)[:300]}
- 近 5 日涨停记录: {json.dumps(lul.get('recent_5d', [])[:5], ensure_ascii=False, default=str)[:600]}
- 板块当日涨停清单: {json.dumps(lul.get('sector_today', [])[:8], ensure_ascii=False, default=str)[:800]}
- 一句话热度: {lul.get('summary', '—')}""")

    user_p = "\n".join(parts)
    return sys_p, user_p


def _ensure_ai_env() -> None:
    """把 ~/.hermes/env.sh 的 MINIMAX_API_KEY 等注入到当前进程 (服务端 shell 不一定 source 过)."""
    if os.environ.get("MINIMAX_API_KEY"):
        return
    env_sh = Path.home() / ".hermes" / "env.sh"
    if not env_sh.exists():
        return
    try:
        import subprocess as _sp
        r = _sp.run(["bash", "-c", f"source {env_sh} && env -0"],
                    capture_output=True, timeout=5, text=True)
        for line in (r.stdout or "").split("\x00"):
            if "=" in line and not line.startswith("_"):
                k, _, v = line.partition("=")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        log.debug(f"_ensure_ai_env 失败: {e}")


def review_trade(trade_id: int, *, force: bool = False) -> dict:
    """AI 复盘:返回 review dict,自动写入 trade_reviews。

    R1+R3+R5+R7+R8 升级 (2026-07-12):
      - 走 web.ai_client.call (重试/熔断/指标/全局 inflight 节流)
      - 走 ai_client.parse_json_loose 兜底解析
      - 多 section 按预算截断 (R8 上下文治理)
      - ai_advice/main_mistake 等字段白名单校验 (R7)
    """
    from . import ai_client
    _ensure_ai_env()
    trade = get_trade(trade_id)
    if not trade:
        raise ValueError(f"trade_id {trade_id} 不存在")
    conn = _conn()
    # 已有则直接返(force=False)
    if not force:
        existing = conn.execute(
            "SELECT id, verdict, score, summary_md, rules_passed_json, rules_failed_json, "
            "       mistake_pattern, improvement, key_risks_json, context_json, ts_created "
            "FROM trade_reviews WHERE trade_id=? ORDER BY id DESC LIMIT 1",
            (trade_id,),
        ).fetchone()
        if existing:
            return _review_row_to_dict(existing, trade)
    # 拉盘面
    ctx = _build_context(trade)
    memory = _memory_context(limit=30)
    sys_p, user_p = _format_ctx_for_ai(trade, ctx, memory)

    # R8 上下文治理 — 总 user_p 控制在 ~2500 tokens
    user_p = ai_client.truncate_to_tokens(user_p, max_tokens=2500)

    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        log.warning("复盘: MINIMAX_API_KEY 未配置,降级为基础评分")
        ai = _fallback_review(trade, ctx)
    else:
        # R2 prompt 注入防御:把 user_p 包 boundary
        user_p_safe = ai_client.wrap_prompt("ctx", user_p)
        spec = ai_client.CallSpec(
            url=ai_client.default_url(),
            headers=ai_client.headers(api_key),
            body={
                "model": ai_client.default_model(),
                "messages": [
                    {"role": "system", "content": sys_p},
                    {"role": "user",   "content": user_p_safe},
                ],
                "temperature": 0.3,
            },
            name="review",
            model=ai_client.default_model(),
            timeout=75.0,                   # 复盘耗时放宽到 75s
            attempts=(1, 2),
            max_tokens_alts=(4000, 5500),
        )
        try:
            _text, _parsed, _info = ai_client.call(spec)
            ai = _parse_ai_text(_text) if _text else {}
        except ai_client.AICallError as e:
            log.warning(f"复盘 AI 失败,降级: {e}")
            ai = _fallback_review(trade, ctx)
    # 写入
    now = systime.time()
    # ai_advice 存到 summary_md 末尾 ([建议]:xxx)
    def _str(v, default=""):
        """AI 偶尔把 string 字段返成 list(dict),统一转 str。"""
        if v is None:
            return default
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "; ".join([str(x) if not isinstance(x, dict) else (x.get("text") or x.get("id") or str(x)) for x in v])
        if isinstance(v, dict):
            return "; ".join(f"{k}: {vv}" for k, vv in v.items())
        return str(v)
    # R7 白名单 + 截短
    summary_md  = ai_client.cap_text(_str(ai.get("summary")), 1000)
    advice      = ai_client.cap_text(_str(ai.get("ai_advice")), 200)
    if advice:
        summary_md = f"{summary_md}\n[建议]{advice}"
    verdict = _str(ai.get("verdict"), "及格")
    if verdict not in ("优", "及格", "失误", "严重失误"):
        verdict = "及格"
    improvement = ai_client.cap_text(_str(ai.get("improvement")), 500)
    mistake = _str(ai.get("mistake_pattern"), "其他")
    limit_up_recap = ai_client.cap_text(_str(ai.get("limit_up_recap")), 400)
    main_mistake = ai_client.cap_text(_str(ai.get("main_mistake")), 50)
    # 2026-07-11 新增 — taxonomy_role + is_mainline
    taxonomy_role_raw = ai.get("taxonomy_role")
    if taxonomy_role_raw in ("main", "second", "noise", "—", "-"):
        taxonomy_role = "noise" if taxonomy_role_raw == "-" else taxonomy_role_raw
    else:
        taxonomy_role = ""
    is_mainline = bool(ai.get("is_mainline"))
    # 杂毛 → verdict 强制不优于"及格";即便 AI 给"优"也降级
    if taxonomy_role == "noise" and verdict in ("优", "及格"):
        verdict = "及格"
    # 回填到返回 dict,子页面/表格直接用
    ai["limit_up_recap"] = limit_up_recap
    ai["main_mistake"] = main_mistake
    ai["ai_advice"] = advice
    ai["taxonomy_role"] = taxonomy_role
    ai["is_mainline"] = is_mainline
    conn.execute(
        "INSERT INTO trade_reviews "
        "(trade_id, model, verdict, score, summary_md, rules_passed_json, rules_failed_json, "
        " mistake_pattern, improvement, key_risks_json, context_json, ts_created) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_id, "MiniMax-M3",
         verdict, int(ai.get("score", 60)),
         summary_md,
         json.dumps(ai.get("rules_passed", []) or [], ensure_ascii=False, default=str),
         json.dumps(ai.get("rules_failed", []) or [], ensure_ascii=False, default=str),
         mistake,
         improvement,
         json.dumps(ai.get("key_risks", []) or [], ensure_ascii=False, default=str),
         json.dumps({"ctx_size": len(json.dumps(ctx, default=str)),
                     "ts_review": now, "ai_advice": advice,
                     "limit_up_recap": limit_up_recap,
                     "main_mistake": main_mistake,
                     "taxonomy_role": taxonomy_role,
                     "is_mainline": is_mainline}, ensure_ascii=False),
         now),
    )
    conn.commit()
    return ai


def _review_row_to_dict(row, trade: dict) -> dict:
    rules_passed = json.loads(row[4]) if row[4] else []
    rules_failed = json.loads(row[5]) if row[5] else []
    # row 布局兼容:
    #  11 列 = id,verdict,score,summary,rp,rf,mistake,improvement,key_risks,context_json,ts_created
    #  10 列 = ...,key_risks,ts_created  (无 context_json)
    context_json: dict = {}
    if len(row) >= 11:
        try:
            context_json = json.loads(row[9]) if row[9] else {}
        except Exception:
            context_json = {}
        ts_created = row[10]
    else:
        ts_created = row[9]
    if not isinstance(context_json, dict):
        context_json = {}
    # 兼容老格式:list[str] → list[{id, text}]
    def _normalize(items):
        out = []
        for it in items:
            if isinstance(it, str):
                out.append({"id": "?", "text": it})
            elif isinstance(it, dict):
                out.append(it)
        return out
    rules_passed_n = _normalize(rules_passed)
    rules_failed_n = _normalize(rules_failed)
    summary = row[3] or ""
    return {
        "id": row[0],
        "trade": trade,
        "verdict": row[1] or "及格",
        "score": row[2] or 60,
        "summary": summary.split("\n[建议]")[0],
        "rules_passed": rules_passed_n,
        "rules_failed": rules_failed_n,
        "rules_passed_raw": rules_passed,
        "rules_failed_raw": rules_failed,
        "rules_conflict_count": len(rules_failed_n),
        "rules_total_count": len(rules_passed_n) + len(rules_failed_n),
        "mistake_pattern": row[6] or "其他",
        "improvement": row[7] or "",
        "key_risks": json.loads(row[8]) if row[8] else [],
        "ai_advice": context_json.get("ai_advice", ""),
        "limit_up_recap": context_json.get("limit_up_recap", ""),
        "main_mistake": context_json.get("main_mistake", ""),
        "taxonomy_role": context_json.get("taxonomy_role", ""),
        "is_mainline": bool(context_json.get("is_mainline", False)),
        "ts_created": ts_created,
        "from_cache": True,
    }


def _parse_ai_text(text: str) -> dict:
    """复用 server._parse_ai_json 的逻辑(宽松解析)。"""
    import re
    text = text.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]+?)(?:```|$)", text)
        if m:
            text = m.group(1).strip()
    # 找首个 { 和最后 }
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        text = text[i:j+1]
    try:
        return json.loads(text)
    except Exception:
        # 进一步:截断补 }
        try:
            return json.loads(text + "}")
        except Exception:
            return {}


def _fallback_review(trade: dict, ctx: dict) -> dict:
    """AI 不可用时的兜底:基于规则的粗评分。"""
    score = 60
    rules_passed, rules_failed = [], []
    pattern = "其他"
    # 简单规则:有 memo + tags → 严谨;无 → 情绪化
    if trade.get("memo"):
        rules_passed.append("有交易备注")
        score += 5
    else:
        rules_failed.append("未写交易备注")
        score -= 5
        pattern = "情绪化"
    if trade.get("tags"):
        rules_passed.append("有标签归类")
    # 有没有决策依据(盘面数据)→ 主要
    if ctx.get("kline_before"):
        rules_passed.append("复盘时 K 线数据齐")
    if not ctx.get("seats", {}).get("rows"):
        rules_failed.append("无龙虎席位参考")
    return {
        "verdict": "及格" if score >= 60 else ("失误" if score >= 40 else "严重失误"),
        "score": max(0, min(100, score)),
        "summary": f"{trade['direction']} {trade['name']} @ {trade['price']}元,降级评分(AI 不可用)",
        "rules_passed": rules_passed,
        "rules_failed": rules_failed,
        "mistake_pattern": pattern,
        "improvement": "下次建议:(1)写交易理由;(2)截图当时盘面留档;(3)次日复盘对照",
        "key_risks": ["AI 不可用,评分仅供参考"],
    }


def list_reviews(trade_id: int) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, verdict, score, summary_md, rules_passed_json, rules_failed_json, "
        "       mistake_pattern, improvement, key_risks_json, ts_created "
        "FROM trade_reviews WHERE trade_id=? ORDER BY id DESC",
        (trade_id,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(_review_row_to_dict((r[0], r[1], r[2], r[3], r[4], r[5],
                                        r[6], r[7], r[8], r[9]), {"id": trade_id}))
    return out


# ═══════════════════════════════════════════════════
# 统计 + 次日预警
# ═══════════════════════════════════════════════════
def summary_stats(since_days: int = 90) -> dict:
    """胜率/平均盈亏/常见错误模式。

    读时 name→code 兜底:历史脏数据 (code='000000') 必须按 name 反查回真实代码,
    否则 FIFO 会跨股票匹配,胜率/最大/最小全部错位 (2026-07-14 修复)。
    """
    cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y%m%d")
    conn = _conn()
    # 配对 buy → sell 算盈亏
    rows = conn.execute(
        "SELECT id, code, name, direction, price, shares, trade_date "
        "FROM trades WHERE trade_date>=? ORDER BY trade_date, id",
        (cutoff,),
    ).fetchall()
    # name→code 兜底:扫一次,有占位符 (000000/空) 的 code 按 name 反查
    rows_list = [list(r) for r in rows]
    has_placeholder = any((not str(r[1] or "").strip()) or str(r[1]).strip() == "000000"
                          for r in rows_list)
    if has_placeholder:
        try:
            from .. import data_layer as _dl_stats
            market = _dl_stats.fetch_stock_list_all() or []
            name_to_code: dict[str, str] = {}
            for c, n in market:
                nn = (n or "").strip()
                cc = (c or "").strip().zfill(6)
                if nn and cc.isdigit() and cc != "000000" and nn not in name_to_code:
                    name_to_code[nn] = cc
            patched = 0
            for r in rows_list:
                code_s = str(r[1] or "").strip()
                if not code_s or code_s == "000000":
                    nn = (r[2] or "").strip()
                    if nn in name_to_code:
                        r[1] = name_to_code[nn]
                        patched += 1
            if patched:
                log.info(f"summary_stats: name→code 反查 {patched} 笔")
        except Exception as e:
            log.debug(f"summary_stats name→code lookup: {e}")
    # 简化的 FIFO 配对
    holdings: dict[str, list[dict]] = {}  # code -> [buy 队列]
    closed = []  # [{code, name, buy, sell, pnl_pct}]
    for r in rows_list:
        tid, code, name, direction, price, shares, tdate = r
        d = direction
        if d == "buy":
            holdings.setdefault(code, []).append({"id": tid, "price": price, "shares": shares, "date": tdate, "name": name})
        elif d == "sell":
            q = holdings.get(code, [])
            while q and shares > 0:
                buy = q[0]
                used = min(buy["shares"], shares)
                pnl = (price - buy["price"]) / buy["price"] * 100
                closed.append({"code": code, "name": buy["name"],
                               "buy": buy["price"], "sell": price,
                               "shares": used, "pnl_pct": round(pnl, 2),
                               "buy_date": buy["date"], "sell_date": tdate})
                buy["shares"] -= used
                shares -= used
                if buy["shares"] <= 0:
                    q.pop(0)
            if not q:
                holdings.pop(code, None)
    if not closed:
        return {"closed": 0, "win_rate": None, "avg_pnl": None,
                "best": None, "worst": None, "by_pattern": {}}
    win = [c for c in closed if c["pnl_pct"] > 0]
    win_rate = round(len(win) / len(closed) * 100, 2)
    avg_pnl = round(sum(c["pnl_pct"] for c in closed) / len(closed), 2)
    best = max(closed, key=lambda c: c["pnl_pct"])
    worst = min(closed, key=lambda c: c["pnl_pct"])
    # 错误模式分布
    pat_rows = conn.execute(
        "SELECT mistake_pattern, COUNT(*) FROM trade_reviews "
        "WHERE mistake_pattern IS NOT NULL AND mistake_pattern!='' "
        "GROUP BY mistake_pattern ORDER BY 2 DESC LIMIT 10"
    ).fetchall()
    return {
        "closed": len(closed),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "best": best, "worst": worst,
        "by_pattern": [{"pattern": p, "count": n} for p, n in pat_rows],
    }


def next_day_picks() -> dict:
    """次日选股:
    1) 拉 screen 候选 (live)
    2) 结合用户历史常见错模式 → 标注风险
    3) 返 [{code, name, sector, ai_verdict, risk_warnings[]}]
    """
    from .. import screen as scr_mod
    from . import holder_lookup
    # 1) 拉候选 — 沙箱里 run_stock_screen 可能 hang 在 eastmoney/akshare (DNS 劫持见 feedback_network_dns_hijack),
    #    所以用线程 + 超时保护,失败回退空 picks (用户至少能看到 user_patterns 提示)
    picks: list[dict] = []
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTE
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(scr_mod.run_stock_screen, None, "live")
            try:
                result = fut.result(timeout=8)
                picks = (result.get("candidates") or [])[:5]
            except _FutTE:
                log.warning("次日选股 超时(8s) → 空 picks(数据源可能在沙箱里 hang)")
            except Exception as e:
                log.warning(f"次日选股 失败: {e}")
    except Exception as e:
        log.warning(f"次日选股 executor 失败: {e}")
    # 2) 常见错模式
    conn = _conn()
    pat_rows = conn.execute(
        "SELECT mistake_pattern, COUNT(*) FROM trade_reviews "
        "WHERE mistake_pattern IS NOT NULL AND mistake_pattern!='' "
        "GROUP BY mistake_pattern ORDER BY 2 DESC LIMIT 5"
    ).fetchall()
    user_patterns = [p for p, _ in pat_rows]
    # 3) 拼装
    out_picks = []
    for p in picks:
        risk = []
        # 散户占比高 → 警惕(用户的错模式如果是"追高")
        try:
            h = holder_lookup.fetch_holder_info(p["code"])
            if h and h.get("focus_label", "").endswith("分散"):
                risk.append("持仓分散度高,易被洗")
        except Exception:
            pass
        # 用户常见错模式
        if user_patterns:
            risk.append(f"⚠ 历史常见错:{','.join(user_patterns[:3])}")
        out_picks.append({
            "code": p.get("code"),
            "name": p.get("name"),
            "sector": p.get("sector"),
            "ai_verdict": p.get("ai", {}).get("verdict") if isinstance(p.get("ai"), dict) else None,
            "ai_score": p.get("ai", {}).get("conviction") if isinstance(p.get("ai"), dict) else None,
            "risk_warnings": risk,
        })
    return {
        "picks": out_picks,
        "user_patterns": user_patterns,
        "ts": systime.time(),
    }


# ═══════════════════════════════════════════════════
# 设置 (meta 表) — 总资金等
# ═══════════════════════════════════════════════════
def get_setting(key: str, default=None):
    conn = _conn()
    r = conn.execute("SELECT value FROM meta WHERE key=?", (f"review.{key}",)).fetchone()
    return r[0] if r and r[0] is not None else default


def set_setting(key: str, value) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (f"review.{key}", str(value)),
    )
    conn.commit()


# ═══════════════════════════════════════════════════
# 实时报价 (curl → 腾讯 qt.gtimg,一次拿多只) + FIFO 账本
# ═══════════════════════════════════════════════════
def _batch_quotes(codes: list[str]) -> dict:
    """批量实时报价。返回 {code: {"price", "prev_close", "name"}}。
    走 curl 子进程打腾讯 qt.gtimg(与资金流同一稳定通道,绕开 server 进程网络栈)。
    """
    out: dict[str, dict] = {}
    codes = [str(c).strip().zfill(6) for c in codes if str(c).strip()]
    if not codes:
        return out
    # 腾讯支持一次多只:q=sh600519,sz000001
    qs = ",".join(
        (("sh" if c.startswith(("6", "9")) else ("sh" if c.startswith("5") else "sz")) + c)
        for c in codes
    )
    try:
        import subprocess as _sp
        r = _sp.run(
            ["curl", "-s", "--max-time", "8",
             "-H", "User-Agent: Mozilla/5.0",
             "-H", "Referer: https://gu.qq.com/",
             f"https://qt.gtimg.cn/q={qs}"],
            capture_output=True, timeout=10,
        )
        text = (r.stdout or b"").decode("gbk", errors="ignore")
        for line in text.splitlines():
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip().rstrip(";").rstrip('"')
            fs = body.split("~")
            if len(fs) < 5:
                continue
            code = fs[2].strip().zfill(6)
            try:
                price = float(fs[3] or 0)
                prev = float(fs[4] or 0)
            except Exception:
                continue
            if price <= 0:
                continue
            out[code] = {"name": fs[1], "price": price, "prev_close": prev}
    except Exception as e:
        log.warning(f"批量报价失败: {e}")
    return out


def _all_trades_asc(codes: list[str] | None = None) -> list[dict]:
    """按时间升序取交易(FIFO 需要),可按 code 过滤。

    读时 name→code 兜底:对 code='000000' / 空 的历史脏数据,按 name
    从全市场反查回正确代码,保证 FIFO / 行情 / 分组都按真实代码走。
    不动 DB;写入路径(record_trade)仍保留 6 位数字校验。

    R13 关键修复:历史脏数据 (code 全是 '000000') 时,WHERE code IN
    (codes) 永远 0 行;必须先 fetch-all → name→code patch → 再过滤。
    """
    conn = _conn()
    sql = ("SELECT id, code, name, direction, price, shares, occurred_at, trade_date "
           "FROM trades "
           "ORDER BY trade_date ASC, occurred_at ASC, id ASC")
    rows = conn.execute(sql).fetchall()
    out = [
        {"id": r[0], "code": r[1], "name": r[2], "direction": r[3],
         "price": r[4], "shares": r[5], "occurred_at": r[6], "trade_date": r[7]}
        for r in rows
    ]
    # name→code 兜底 — 先做 (因为 WHERE 之前已废)
    target_set: set[str] | None = {str(c).zfill(6) for c in codes} if codes else None
    has_placeholder = any((not t["code"]) or str(t["code"]).strip() == "000000" for t in out)
    if has_placeholder:
        try:
            from .. import data_layer as _dl_for_lookup
            market = _dl_for_lookup.fetch_stock_list_all() or []
            name_to_code: dict[str, str] = {}
            for c, n in market:
                nn = (n or "").strip()
                cc = (c or "").strip().zfill(6)
                if nn and cc.isdigit() and cc != "000000" and nn not in name_to_code:
                    name_to_code[nn] = cc
            patched = 0
            for t in out:
                code = str(t["code"] or "").strip()
                if not code or code == "000000":
                    nn = (t.get("name") or "").strip()
                    if nn in name_to_code:
                        t["code"] = name_to_code[nn]
                        patched += 1
            if patched:
                log.info(f"_all_trades_asc: name→code 兜底反查 {patched} 笔 (历史脏数据)")
        except Exception as e:
            log.debug(f"_all_trades_asc name→code lookup failed: {e}")
    # 现在按 target_set 过滤
    if target_set is not None:
        out = [t for t in out if t["code"] in target_set]
    return out


def _fifo_book(trades_asc: list[dict], quotes: dict, today_str: str):
    """FIFO 逐笔盈亏账本。
    返回 (per_trade: {trade_id: metrics}, positions: {code: pos})。
    每笔:
      - buy  → 记录未卖出部分的浮动盈亏 / 今日盈亏
      - sell → 记录该次卖出的已实现盈亏(对已卖出的买单不再重复计,避免双算)
    """
    per: dict[int, dict] = {}
    for t in trades_asc:
        per[t["id"]] = {
            "today_pnl": 0.0, "cum_pnl": 0.0, "cum_pnl_pct": 0.0,
            "held_shares": 0, "status": "-", "price_now": None, "realized": 0.0,
        }
    by_code: dict[str, list[dict]] = {}
    for t in trades_asc:
        by_code.setdefault(t["code"], []).append(t)
    positions: dict[str, dict] = {}
    for code, ts in by_code.items():
        q = quotes.get(code) or {}
        now = float(q.get("price") or 0)
        prev = float(q.get("prev_close") or 0)
        lots: list[dict] = []  # {tid, price, remaining}
        for t in ts:
            if t["direction"] == "buy":
                lots.append({"tid": t["id"], "price": float(t["price"]), "remaining": int(t["shares"])})
                per[t["id"]]["status"] = "open"
            else:  # sell
                to_sell = int(t["shares"]); realized = 0.0; cost = 0.0
                while to_sell > 0 and lots:
                    lot = lots[0]
                    used = min(lot["remaining"], to_sell)
                    realized += used * (float(t["price"]) - lot["price"])
                    cost += used * lot["price"]
                    lot["remaining"] -= used
                    to_sell -= used
                    if lot["remaining"] <= 0:
                        lots.pop(0)
                m = per[t["id"]]
                m["realized"] = round(realized, 2)
                m["cum_pnl"] = round(realized, 2)
                m["cum_pnl_pct"] = round(realized / cost * 100, 2) if cost else 0.0
                m["today_pnl"] = round(realized, 2) if t["trade_date"] == today_str else 0.0
                m["status"] = "sold"
                m["price_now"] = float(t["price"])
        # 剩余未卖的买单 → 浮动盈亏归到该买单
        pos_shares = 0; pos_cost = 0.0
        held_buy_ids = set()
        for lot in lots:
            rem = lot["remaining"]
            if rem <= 0:
                continue
            pos_shares += rem; pos_cost += rem * lot["price"]
            held_buy_ids.add(lot["tid"])
            m = per[lot["tid"]]
            m["held_shares"] = rem
            m["status"] = "holding"
            m["price_now"] = now or None
            if now > 0:
                m["cum_pnl"] = round(rem * (now - lot["price"]), 2)
                m["cum_pnl_pct"] = round((now - lot["price"]) / lot["price"] * 100, 2) if lot["price"] else 0.0
                m["today_pnl"] = round(rem * (now - prev), 2) if prev > 0 else 0.0
        # 已全部卖出的买单 → 标记清仓(盈亏已在卖单上体现,不再重复)
        for t in ts:
            if t["direction"] == "buy" and t["id"] not in held_buy_ids:
                per[t["id"]]["status"] = "cleared"
                per[t["id"]]["price_now"] = now or None
        if pos_shares > 0 and now > 0:
            positions[code] = {
                "code": code, "name": ts[-1]["name"],
                "shares": pos_shares, "avg_cost": round(pos_cost / pos_shares, 3),
                "price": now, "prev_close": prev,
                "market_value": round(pos_shares * now, 2),
                "cost_value": round(pos_cost, 2),
                "unrealized": round(pos_shares * now - pos_cost, 2),
                "unrealized_pct": round((pos_shares * now - pos_cost) / pos_cost * 100, 2) if pos_cost else 0.0,
                "today_pnl": round(pos_shares * (now - prev), 2) if prev > 0 else 0.0,
            }
    return per, positions


def portfolio_overview(total_capital: float | None = None) -> dict:
    """顶部资金栏:总资金 / 仓位 / 今日盈亏 / 总盈亏 / 盈亏比 + 当前持仓明细。"""
    if total_capital is None:
        try:
            total_capital = float(get_setting("total_capital", 0) or 0)
        except Exception:
            total_capital = 0.0
    trades = _all_trades_asc()
    codes = sorted({t["code"] for t in trades})
    quotes = _batch_quotes(codes)
    today_str = datetime.now().strftime("%Y%m%d")
    per, positions = _fifo_book(trades, quotes, today_str)
    position_value = round(sum(p["market_value"] for p in positions.values()), 2)
    position_cost = round(sum(p["cost_value"] for p in positions.values()), 2)
    unrealized = round(sum(p["unrealized"] for p in positions.values()), 2)
    today_hold = round(sum(p["today_pnl"] for p in positions.values()), 2)
    realized_total = 0.0; realized_today = 0.0
    for t in trades:
        if t["direction"] == "sell":
            m = per[t["id"]]
            realized_total += m["realized"]
            if t["trade_date"] == today_str:
                realized_today += m["realized"]
    today_pnl = round(today_hold + realized_today, 2)
    total_pnl = round(unrealized + realized_total, 2)
    cap = float(total_capital or 0)
    # 剩余满仓资金 = 总资金 − 累计亏损(若总盈亏≥0则仍按总资金算)
    # 总盈亏为正则亏损视为 0 (没有"亏",只是少赚,可用资金仍是总资金)
    loss_amount = max(0.0, -total_pnl)
    available_capital = round(cap - loss_amount, 2)  # 真正能"满仓再买"的资金
    # 仓位 = 持仓市值 / 剩余满仓资金
    position_ratio = round(position_value / available_capital * 100, 2) if available_capital > 0 else None
    # 剩余资金 = 剩余满仓资金 − 持仓市值
    cash = round(available_capital - position_value, 2) if available_capital > 0 else None
    return {
        "total_capital": cap,
        "available_capital": available_capital,
        "position_value": position_value,
        "position_cost": position_cost,
        "position_ratio": position_ratio,
        "cash": cash,
        "today_pnl": today_pnl,
        "today_pnl_pct": round(today_pnl / cap * 100, 2) if cap else None,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl / cap * 100, 2) if cap else None,  # 盈亏比 = 总盈亏/总资金
        "realized_pnl": round(realized_total, 2),
        "unrealized_pnl": unrealized,
        "positions": sorted(positions.values(), key=lambda p: -p["market_value"]),
        "position_count": len(positions),
        "quotes_ok": sum(1 for c in codes if c in quotes),
        "codes": len(codes),
        "trade_count": len(trades),  # 总交易笔数 — 用于资金栏手续费估算 (用户口径每笔 5 元)
        "ts": systime.time(),
    }


def live_trades(limit: int = 80, code: str | None = None, since_days: int | None = 180) -> list[dict]:
    """list_trades + 逐笔实时盈亏(今日盈亏 / 累计盈亏 / 累计盈亏比)。"""
    trades = list_trades(limit=limit, code=code, since_days=since_days)  # DESC + last_review
    if not trades:
        return trades
    inv_codes = sorted({t["code"] for t in trades})
    all_asc = _all_trades_asc(codes=inv_codes)  # 全历史保证 FIFO 归属正确
    quotes = _batch_quotes(inv_codes)
    today_str = datetime.now().strftime("%Y%m%d")
    per, _ = _fifo_book(all_asc, quotes, today_str)
    for t in trades:
        m = per.get(t["id"]) or {}
        q = quotes.get(t["code"]) or {}
        t["live"] = {
            "today_pnl": m.get("today_pnl", 0.0),
            "cum_pnl": m.get("cum_pnl", 0.0),
            "cum_pnl_pct": m.get("cum_pnl_pct", 0.0),
            "held_shares": m.get("held_shares", 0),
            "status": m.get("status", "-"),
            "price_now": m.get("price_now") or q.get("price"),
        }
    return trades


# ═══════════════════════════════════════════════════
# 买入时刻点推算(按买入价从分时数据反推)
# ═══════════════════════════════════════════════════
def infer_time_points(code: str, date: str | None = None, price: float | None = None,
                      tol: float = 0.015) -> dict:
    """按买入价从当日分时(1分钟)反推可能的成交时刻。
    - date: YYYYMMDD;None = 最近交易日
    - price: 目标价;None = 返回全部分钟点
    返回 {available, points:[{time,close,high,low,match}], reason}
    分时源(akshare stock_zh_a_hist_min_em)通常只覆盖近 5 个交易日,更早日期取不到 → available=False。
    """
    code = str(code).strip().zfill(6)
    try:
        from .. import data_layer as dl
        df = dl.fetch_intraday(code, date_str=date)
    except Exception as e:
        return {"available": False, "points": [], "reason": f"分时获取异常: {e}"}
    if df is None or len(df) == 0:
        return {"available": False, "points": [],
                "reason": "分时数据不可用(可能非近 5 个交易日或该源无数据),请手动填时间"}
    cols = list(df.columns)
    def _col(*names):
        for n in names:
            if n in cols:
                return n
        return None
    tcol = _col("时间", "datetime", "time") or cols[0]
    ccol = _col("收盘", "close")
    hcol = _col("最高", "high")
    lcol = _col("最低", "low")
    exact, allpts = [], []
    for _, r in df.iterrows():
        tval = str(r.get(tcol) or "")
        d = tval[:10].replace("-", "")
        if date and d and d != date:
            continue
        hhmm = tval[11:16] if len(tval) >= 16 else tval[-5:]
        try:
            cl = float(r.get(ccol) or 0) if ccol else 0
            hi = float(r.get(hcol) or cl) if hcol else cl
            lo = float(r.get(lcol) or cl) if lcol else cl
        except Exception:
            continue
        item = {"time": hhmm, "close": round(cl, 3), "high": round(hi, 3), "low": round(lo, 3)}
        allpts.append(item)
        if price and lo and hi and (lo - tol) <= price <= (hi + tol):
            item = dict(item); item["match"] = "exact"
            exact.append(item)
    if price and exact:
        return {"available": True, "points": exact[:40], "reason": f"命中 {len(exact)} 个时刻"}
    if price and not exact and allpts:
        # 无精确命中 → 取最接近的 5 个
        near = sorted(allpts, key=lambda x: abs(x["close"] - price))[:5]
        for n in near:
            n["match"] = "near"
        near = sorted(near, key=lambda x: x["time"])
        return {"available": True, "points": near,
                "reason": "无精确命中,给出最接近的 5 个时刻(供参考)"}
    return {"available": True, "points": allpts[:240], "reason": f"全日 {len(allpts)} 个分钟点"}