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


def list_trades(limit: int = 50, code: str | None = None, since_days: int | None = None) -> list[dict]:
    """最近 N 笔交易(含最后一条复盘结果)。"""
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
    for r in rows:
        d = {
            "id": r[0], "code": r[1], "name": r[2], "direction": r[3],
            "price": r[4], "shares": r[5], "occurred_at": r[6], "trade_date": r[7],
            "mode": r[8], "memo": r[9] or "",
            "tags": json.loads(r[10]) if r[10] else [],
        }
        out.append(d)
        trade_ids.append(r[0])
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
        advice = ctx_j.get("ai_advice", "") if isinstance(ctx_j, dict) else ""
        rev_map[rr[0]] = {
            "verdict": rr[1] or "—",
            "score": rr[2] or 0,
            "mistake_pattern": rr[3] or "—",
            "rules_passed": rp_n,
            "rules_failed": rf_n,
            "rules_conflict_count": len(rf_n),
            "ai_advice": advice,
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
    """局部更新。"""
    allowed = {"price", "shares", "memo", "tags", "direction", "occurred_at", "trade_date", "name"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            v = json.dumps(v, ensure_ascii=False)
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


# ═══════════════════════════════════════════════════
# AI 复盘 (核心)
# ═══════════════════════════════════════════════════
def _build_context(trade: dict) -> dict:
    """组装复盘上下文: 决策日前后盘面 + 游资 + 资金 + K线 + 板块 + 新闻 + 主力出仓。
    用户要求 (2026-07-10): 必须含前 10 日 + 后 5 日 K线、全部游资席位、主力资金逐日明细。
    """
    from .. import lib_common as lc
    from . import seat_lookup, fund_flow, holder_lookup
    from . import sector_classify, news_lookup
    code = trade["code"]
    ctx: dict[str, Any] = {}
    # 1) K线 (决策前 10 日 + 后 5 日 — 看清趋势 vs 噪声)
    try:
        df = lc.fetch_daily(code, days=120)
        if df is not None and not df.empty:
            df = df.sort_values("日期").reset_index(drop=True)
            tdate = trade["trade_date"]
            tdate_norm = f"{tdate[:4]}-{tdate[4:6]}-{tdate[6:8]}"
            if "日期" in df.columns:
                # 决策日及后 5 日
                mask = df["日期"] >= tdate_norm
                sub = df[mask].head(5)
                ctx["kline_after"] = _safe_records(sub)
                # 决策日前 10 日 — 看清趋势
                pre = df[df["日期"] < tdate_norm].tail(10)
                ctx["kline_before"] = _safe_records(pre)
                # 决策日前 10 日整体走势统计(连板数/放量天数/资金态度)
                ctx["trend_10d_before"] = _compute_trend_stats(pre)
                # 后 5 日资金 + 走势 — 用来评判"买入是否正确"
                ctx["aftermath_5d"] = _compute_aftermath(sub)
    except Exception as e:
        log.warning(f"复盘 kline {code} 失败: {e}")
    # 2) 主力资金流(决策点前后 10 日 逐日明细)
    try:
        df_fund = lc.fetch_index_daily("sh000001", days=2)  # 触发 fetch 链
        from .. import lib_common as lc2
        main_exit = lc2.detect_main_force_exit(code, lookback_days=10)
        if main_exit:
            ctx["main_exit_10d"] = main_exit
    except Exception as e:
        log.debug(f"复盘 main_exit {code} 失败: {e}")
    # 3) 资金流 (60 日完整历史 + 当日决策点单日)
    try:
        ff = fund_flow.get_combined(code, days=60)
        if ff.get("history"):
            ctx["fund_flow"] = ff
    except Exception as e:
        log.warning(f"复盘 fund_flow {code} 失败: {e}")
    # 4) 龙虎榜 (60 日内全部席位 — 看清游资进出节奏)
    try:
        seats = seat_lookup.get_stock_seats(code, lookback_days=60)
        if seats.get("rows"):
            ctx["seats"] = seats
            # 进一步: 提取近 10 日 vs 远 10 日 席位活跃度变化
            ctx["seats_recent_10d"] = _seats_recent_stats(seats, days=10)
    except Exception as e:
        log.warning(f"复盘 seats {code} 失败: {e}")
    # 5) 板块
    try:
        s = sector_classify.get_sector(code, force_refresh=False)
        if s:
            ctx["sector"] = s
    except Exception as e:
        log.warning(f"复盘 sector {code} 失败: {e}")
    # 6) 新闻(优先按股票代码过滤,再回退到近期热点)
    try:
        news_data = news_lookup.get_cached_news(force_refresh=False, num=80)
        all_news = news_data.get("items", []) if isinstance(news_data, dict) else []
        # 优先挑相关
        related = [n for n in all_news if code in (n.get("title", "") + str(n.get("codes", [])))]
        ctx["news"] = (related + all_news)[:8]
    except Exception as e:
        log.warning(f"复盘 news {code} 失败: {e}")
    # 7) 散户/主力持股占比
    try:
        h = holder_lookup.fetch_holder_info(code)
        if h:
            ctx["holders"] = h
    except Exception:
        pass
    # 8) 大市(沪深300 / 上证 — 决策前后 10 日)
    try:
        idx = lc.fetch_index_daily("sh000001", days=15)
        if idx is not None and not idx.empty:
            ctx["market_10d"] = idx.to_dict(orient="records")[-10:]
    except Exception:
        pass
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

你是用户的复盘教练,精通退学战法 64 条铁律(4 大类 + 龙头四步流水线)。
对这次交易,**逐条铁律独立 PASS/FAIL 判定**,要求:

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
3. 每条 fail 必须有量化理由(不是"主观感觉") — 引用以上数据作为依据
4. rules_passed/failed 写编号 + 一句话说明
5. 同时给出表格列用的简短建议(≤30 字)
6. 严格 JSON(无 markdown 围栏):
{
  "verdict": "优"|"及格"|"失误"|"严重失误",
  "score": 0-100,
  "summary": "一句话总结这次操作(50字内)",
  "rules_passed": [{"id": "一.5", "text": "不眼红他人收益"}, ...],   // 2-4 条
  "rules_failed": [{"id": "三.1", "text": "禁止加仓", "reason": "亏损时加仓 100 股"}],   // 1-4 条
  "mistake_pattern": "追高|不止损|无主线|杂毛|情绪化|早盘冲动|打板不成|其他",
  "improvement": "下次怎么改(2-3条具体动作)",
  "ai_advice": "表格里显示的简短建议(≤30字,如:明早冲高减半,跌破25.2止损)",
  "key_risks": ["复盘当时没看到的风险点"]
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

    # 8) 板块
    s = ctx.get('sector') or {}
    if s:
        parts.append(f"""\n## 板块
{json.dumps(s, ensure_ascii=False, default=str)[:300]}""")

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

    user_p = "\n".join(parts)
    return sys_p, user_p


def review_trade(trade_id: int, *, force: bool = False) -> dict:
    """AI 复盘:返回 review dict,自动写入 trade_reviews。"""
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
    # 调 AI (用 subprocess 绕开 server 进程网络限制 — 2026-07-10)
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        log.warning("复盘: MINIMAX_API_KEY 未配置,降级为基础评分")
        ai = _fallback_review(trade, ctx)
    else:
        url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
        model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
        body = {
            "model": model,
            "messages": [{"role": "system", "content": sys_p},
                         {"role": "user", "content": user_p}],
            "max_tokens": 4000,
            "temperature": 0.3,
        }
        # 用独立子进程调 MiniMax API (子进程网络栈独立,绕开 server 进程问题)
        helper = (
            "import sys, json, os\n"
            f"sys.path.insert(0, '/Users/kaikai/scripts')\n"
            "from pathlib import Path\n"
            "_env_sh = Path.home() / '.hermes' / 'env.sh'\n"
            "import subprocess as _sp\n"
            "r = _sp.run(['bash', '-c', f'source {_env_sh} && env -0'], capture_output=True, timeout=5, text=True)\n"
            "for line in (r.stdout or '').split('\\x00'):\n"
            "    if '=' in line and not line.startswith('_'):\n"
            "        k, _, v = line.partition('=')\n"
            "        if k and k not in os.environ:\n"
            "            os.environ[k] = v\n"
            "import requests\n"
            f"api_key = os.environ.get('MINIMAX_API_KEY','')\n"
            f"url = os.environ.get('MINIMAX_BASE_URL', 'https://api.minimaxi.com/v1/text/chatcompletion_v2')\n"
            f"body = json.loads({json.dumps(json.dumps(body))})\n"
            "try:\n"
            "    r = requests.post(url, json=body,\n"
            "                      headers={'Authorization': f'Bearer {api_key}',\n"
            "                               'Content-Type': 'application/json'},\n"
            "                      timeout=120)\n"
            "    if r.status_code != 200:\n"
            f"        print(json.dumps({{'err': f'HTTP {{r.status_code}}: {{r.text[:200]}}'}})); sys.exit(1)\n"
            "    j = r.json()\n"
            "    msg = (j.get('choices', [{}])[0].get('message', {}) or {})\n"
            "    text = msg.get('content') or msg.get('reasoning_content') or ''\n"
            "    if not text:\n"
            "        print(json.dumps({'err': 'content empty'})); sys.exit(1)\n"
            "    print(json.dumps({'text': text, 'usage': j.get('usage', {})}))\n"
            "except Exception as e:\n"
            f"    print(json.dumps({{'err': str(e)[:300]}})); sys.exit(1)\n"
        )
        try:
            import subprocess as _sp
            r = _sp.run(
                [sys.executable, "-c", helper],
                capture_output=True, timeout=75, text=True,
            )
            if r.returncode == 0 and r.stdout.strip():
                # 取最后一行 JSON
                last_line = [l for l in r.stdout.strip().splitlines() if l.strip()][-1]
                ai_resp = json.loads(last_line)
                if "err" in ai_resp:
                    raise RuntimeError(ai_resp["err"])
                text = ai_resp.get("text", "")
                ai = _parse_ai_text(text)
                log.info(f"复盘 AI 调用成功,文本 {len(text)} 字符,usage={ai_resp.get('usage', {})}")
            else:
                raise RuntimeError(f"helper rc={r.returncode} stderr={r.stderr[:200]}")
        except Exception as e:
            log.warning(f"复盘 AI 失败,降级: {e}")
            ai = _fallback_review(trade, ctx)
    # 写入
    now = systime.time()
    # ai_advice 存到 summary_md 末尾 ([建议]:xxx)
    summary_md = ai.get("summary", "")
    advice = ai.get("ai_advice", "")
    if advice:
        summary_md = f"{summary_md}\n[建议]{advice}"
    conn.execute(
        "INSERT INTO trade_reviews "
        "(trade_id, model, verdict, score, summary_md, rules_passed_json, rules_failed_json, "
        " mistake_pattern, improvement, key_risks_json, context_json, ts_created) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_id, "MiniMax-M3",
         ai.get("verdict", "及格"), int(ai.get("score", 60)),
         summary_md,
         json.dumps(ai.get("rules_passed", []), ensure_ascii=False),
         json.dumps(ai.get("rules_failed", []), ensure_ascii=False),
         ai.get("mistake_pattern", "其他"),
         ai.get("improvement", ""),
         json.dumps(ai.get("key_risks", []), ensure_ascii=False),
         json.dumps({"ctx_size": len(json.dumps(ctx, default=str)),
                     "ts_review": now, "ai_advice": advice}, ensure_ascii=False),
         now),
    )
    conn.commit()
    return ai


def _review_row_to_dict(row, trade: dict) -> dict:
    rules_passed = json.loads(row[4]) if row[4] else []
    rules_failed = json.loads(row[5]) if row[5] else []
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
    return {
        "id": row[0],
        "trade": trade,
        "verdict": row[1] or "及格",
        "score": row[2] or 60,
        "summary": row[3] or "",
        "rules_passed": rules_passed_n,
        "rules_failed": rules_failed_n,
        "rules_passed_raw": rules_passed,
        "rules_failed_raw": rules_failed,
        "rules_conflict_count": len(rules_failed_n),
        "rules_total_count": len(rules_passed_n) + len(rules_failed_n),
        "mistake_pattern": row[6] or "其他",
        "improvement": row[7] or "",
        "key_risks": json.loads(row[8]) if row[8] else [],
        "ts_created": row[9],
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
    """胜率/平均盈亏/常见错误模式。"""
    cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y%m%d")
    conn = _conn()
    # 配对 buy → sell 算盈亏
    rows = conn.execute(
        "SELECT id, code, name, direction, price, shares, trade_date "
        "FROM trades WHERE trade_date>=? ORDER BY trade_date, id",
        (cutoff,),
    ).fetchall()
    # 简化的 FIFO 配对
    holdings: dict[str, list[dict]] = {}  # code -> [buy 队列]
    closed = []  # [{code, name, buy, sell, pnl_pct}]
    for r in rows:
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
    # 1) 拉候选 (sign: run_stock_screen(date_str, mode, stocks))
    picks: list[dict] = []
    try:
        result = scr_mod.run_stock_screen(date_str=None, mode="live")
        picks = (result.get("candidates") or [])[:5]
    except Exception as e:
        log.warning(f"次日选股 失败: {e}")
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