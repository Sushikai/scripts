"""
tuixue_v3/cache_db.py
SQLite 日线缓存 — 替代散文件 JSON,支持索引化查询 + 并发读写。

设计目标:
- 一次写入,多用户多进程共享
- (code, date) 主键 → upsert 安全
- TTL 自动过期(默认 4h)
- 单文件,无锁,线程安全(SQLite 默认 + check_same_thread=False)

v2.0 性能:
- 全 A 股日线 ~250K 行,一次写入 ~50ms
- 按 code 查询 ~0.5ms(索引命中)
- 替代原 per-(code,days) JSON 文件方案,5000+ 文件 → 1 文件

v3.0 (2026-07-11):
- daily / ai_verdict 热数据走 Redis (cache_store.py),毫秒级响应
- trades / trade_reviews / watchlist / watchlist_ai 保留 SQLite (冷数据/双写)
- capflow 走 Redis SortedSet (时序)
- 老 daily 表保留做冷归档 + 降级 fallback
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time as systime
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger("tuixue_v3.cache_db")

_DB_PATH = Path(__file__).resolve().parent / "data" / "cache.db"

_init_lock = threading.Lock()
_init_done = False


def _init_db(conn: sqlite3.Connection) -> None:
    """建表 + 索引(只跑一次)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily (
            code   TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume REAL,
            amount REAL,
            turnover REAL,
            ts_updated REAL NOT NULL,
            PRIMARY KEY (code, date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(code);
        CREATE INDEX IF NOT EXISTS idx_daily_code_date ON daily(code, date DESC);
        CREATE INDEX IF NOT EXISTS idx_daily_ts ON daily(ts_updated);

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_verdict (
            date              TEXT NOT NULL,        -- YYYYMMDD; AGG = 综合榜
            code              TEXT NOT NULL,        -- AGG 时存 '_aggregate_'
            model             TEXT NOT NULL,
            verdict           TEXT,
            role              TEXT,                 -- 龙头/中军/杂毛(板块内角色定位)
            conviction        INTEGER,
            summary_md        TEXT,
            layer_pass_json   TEXT,
            rules_passed_json TEXT,
            rules_failed_json TEXT,
            key_risks_json    TEXT,
            sector            TEXT,
            payload_json      TEXT,                 -- 综合榜 / 原始 ai envelope
            ts_updated        REAL NOT NULL,
            PRIMARY KEY (date, code, model)
        );
        CREATE INDEX IF NOT EXISTS idx_ai_verdict_date ON ai_verdict(date);
        CREATE INDEX IF NOT EXISTS idx_ai_verdict_date_code ON ai_verdict(date, code);

        -- 交易复盘 (2026-07-10)
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT    NOT NULL,
            name         TEXT,
            direction    TEXT    NOT NULL,           -- 'buy' | 'sell'
            price        REAL    NOT NULL,           -- 成交价
            shares       INTEGER NOT NULL,           -- 股数(>=100)
            occurred_at  TEXT    NOT NULL,           -- ISO8601 时间(自动填)
            trade_date   TEXT    NOT NULL,           -- YYYYMMDD,索引查询用
            mode         TEXT    DEFAULT 'manual',   -- manual / auto_import
            memo         TEXT,                       -- 用户备注
            tags         TEXT,                       -- JSON array 标签
            created_at   REAL    DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code, trade_date);
        CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
        CREATE INDEX IF NOT EXISTS idx_trades_occurred ON trades(occurred_at);

        -- AI 复盘 (每笔交易 1 条,关联 trades.id)
        CREATE TABLE IF NOT EXISTS trade_reviews (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id       INTEGER NOT NULL,
            model          TEXT    DEFAULT 'MiniMax-M3',
            verdict        TEXT,                     -- 操作评级: 优/及格/失误/严重失误
            score          INTEGER,                  -- 0-100
            summary_md     TEXT,
            rules_passed_json  TEXT,
            rules_failed_json  TEXT,
            mistake_pattern    TEXT,                  -- 错误模式: 追高/不止损/无主线/杂毛/情绪化...
            improvement        TEXT,                  -- 改进建议 (markdown)
            key_risks_json     TEXT,                  -- 关键风险 JSON list (R-cfg-009 schema 补齐)
            context_json       TEXT,                  -- 当时的盘面/铁律命中快照
            ts_created    REAL    DEFAULT 0,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_trade ON trade_reviews(trade_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_pattern ON trade_reviews(mistake_pattern);
        -- 老库补齐 key_risks_json 列(无害,已存在则 no-op)
        -- 注意: SQLite 不支持 IF NOT EXISTS on ADD COLUMN; 用 PRAGMA table_info 判断
        -- (这里 db_init 启动时一次性跑,不会拖性能)

        -- 资金结构 (主力/散户/基金占比) - 2026-07-10
        -- 每个 code 每 60s 一行;前端表格每 10s 拉取最新
        CREATE TABLE IF NOT EXISTS capital_flow (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL,
            ts          INTEGER NOT NULL,         -- epoch 秒
            main_pct    REAL    DEFAULT 0,        -- 主力净占比 %(+流入/-流出)
            retail_pct  REAL    DEFAULT 0,        -- 散户净占比 %
            fund_pct    REAL    DEFAULT 0,        -- 基金净占比 %
            main_amount REAL    DEFAULT 0,        -- 主力净流入(元)
            big_amount  REAL    DEFAULT 0,        -- 大单净流入
            mid_amount  REAL    DEFAULT 0,        -- 中单净流入
            sml_amount  REAL    DEFAULT 0,        -- 小单净流入
            source      TEXT    DEFAULT 'eastmoney',  -- 数据源
            created_at  INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_capflow_code_ts ON capital_flow(code, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_capflow_ts ON capital_flow(ts DESC);

        -- 自选股池 (2026-07-11)
        -- 用户手动添加 / 删除,持久化
        CREATE TABLE IF NOT EXISTS watchlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            name        TEXT,
            tag         TEXT    DEFAULT '自选',
            sort_order  INTEGER DEFAULT 0,
            added_at    REAL    NOT NULL,
            note        TEXT    DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_watchlist_sort ON watchlist(sort_order, id);

        -- 自选股 AI 建议 (2026-07-11)
        -- 触发时机: 个股页加载 AI 完成后 / 用户主动刷新
        -- 选股页表格直接从这里拿,不用再调 AI
        CREATE TABLE IF NOT EXISTS watchlist_ai (
            code              TEXT PRIMARY KEY,
            trade_date        TEXT NOT NULL,
            verdict           TEXT,
            role              TEXT,
            conviction        INTEGER,
            suggested_window  TEXT,            -- "今早竞价 / 9:35-10:00 / 10:30 后 / 14:00 后 / 收盘前 / 暂观望"
            entry_price_range TEXT,            -- "25.20-25.50"  (逗号分隔)
            stop_loss         TEXT,            -- "24.50"
            time_horizon      TEXT,            -- "1-3 天 / 5-10 天 / 中长期"
            summary           TEXT,
            rules_passed_json TEXT,
            rules_failed_json TEXT,
            key_risks_json    TEXT,
            extras_json       TEXT,            -- {pct_5d, pct_10d, main_pct, retail_pct, sector_zt, ...}
            ts_updated        REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_watchlist_ai_date ON watchlist_ai(trade_date);

        -- 个股查询历史 (2026-07-11 用户反馈 #)
        -- 服务端永久持久化(跨设备/跨浏览器),不再是浏览器 localStorage
        -- 去重:同 code 后查的提到最前,记录 hit_count 与 last_query_ts
        CREATE TABLE IF NOT EXISTS stock_history (
            code            TEXT PRIMARY KEY,
            name            TEXT,
            hit_count       INTEGER DEFAULT 1,
            first_query_ts  REAL NOT NULL,
            last_query_ts   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stock_history_recent ON stock_history(last_query_ts DESC);
        """)
    # 老库兼容:补 role 列(2026-07-09 加)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ai_verdict)").fetchall()}
    if "role" not in cols:
        try:
            conn.execute("ALTER TABLE ai_verdict ADD COLUMN role TEXT")
        except Exception:
            pass
    # 老库兼容:补 trade_reviews.key_risks_json(2026-07-10 加)
    tr_cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_reviews)").fetchall()}
    if "key_risks_json" not in tr_cols:
        try:
            conn.execute("ALTER TABLE trade_reviews ADD COLUMN key_risks_json TEXT")
        except Exception:
            pass
    conn.commit()

    # R-cfg-009: trade_reviews (trade_id, model) 去重 + 唯一索引
    # 必须放在 executescript 外,否则老库里有重复行会直接挂掉整个 _init_db,
    # 进而炸掉 to_thread 拉新连接,所有读 API 返回空。2026-07-14 修复。
    try:
        cur = conn.execute(
            "DELETE FROM trade_reviews WHERE id NOT IN ("
            "  SELECT MAX(id) FROM trade_reviews GROUP BY trade_id, model"
            ")"
        )
        if cur.rowcount:
            log.info(f"trade_reviews dedup: 清理 {cur.rowcount} 条重复行")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_unique_trade_model "
            "ON trade_reviews(trade_id, model)"
        )
    except Exception as e:
        log.warning(f"trade_reviews unique index 跳过: {e}")


def get_conn() -> sqlite3.Connection:
    """线程安全 connection(每个线程一份).
    R8 增强: 加 busy_timeout + mmap + temp_store + slow query 记录.
    """
    global _init_done
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")      # 多读少写时 WAL 更友好
    conn.execute("PRAGMA synchronous=NORMAL;")     # 折中: 安全 vs 性能
    conn.execute("PRAGMA busy_timeout=5000;")      # R8: 锁等待最多 5s 自动重试
    conn.execute("PRAGMA temp_store=MEMORY;")      # R8: 临时表放内存
    try:
        conn.execute("PRAGMA mmap_size=268435456;")  # R8: 256MB mmap (读多写少友好)
    except Exception:
        pass
    # R8: 慢查询日志 (>300ms 打印 SQL + 堆栈,定位热点)
    # 注:sqlite3.Connection.execute 是 C 层只读属性, 不能 monkey-patch。
    # 改用 setattr 试,失败就降级到无埋点 (不影响主流程)。
    try:
        import time as _t
        _orig_execute = type(conn).execute
        def _timed(self, sql, params=()):
            t0 = _t.monotonic()
            cur = _orig_execute(self, sql, params)
            dt = (_t.monotonic() - t0) * 1000
            if dt > 300:
                try:
                    import logging as _lg
                    _lg.getLogger("slowdb").warning(
                        f"slow={dt:.0f}ms sql={sql[:200]!r}"
                    )
                except Exception:
                    pass
            return cur
        import sqlite3 as _sq
        _sq.Connection.execute = _timed
    except Exception:
        pass  # sqlite3 不允许替换,降级无埋点
    with _init_lock:
        if not _init_done:
            _init_db(conn)
            _init_done = True
    return conn


# 每个线程一份 connection(线程局部缓存)
_tls = threading.local()
def _thread_conn() -> sqlite3.Connection:
    c = getattr(_tls, "conn", None)
    if c is None:
        c = get_conn()
        _tls.conn = c
    return c


# ═══════════════════════════════════════════════════
# 日线缓存 API — 走 Redis (cache_store) + SQLite 冷归档
# 读:Redis hgetall("tx3:daily:{code}") → 内存 dict
# 写:Redis hset + SQLite INSERT (双写,SQLite 冷归档)
# 降级:Redis 挂了 → 自动走 SQLite
# ═══════════════════════════════════════════════════
from . import cache_store
from .cache_store import get_store, K


class DailyCache:
    """按 (code, days) 拉日线 + 写回。Redis 主用,SQLite 兜底 + 冷归档。"""

    def __init__(self, ttl_sec: int = 4 * 3600):
        self.ttl = ttl_sec
        self._store = get_store()

    def is_fresh(self, code: str, days: int) -> bool:
        """最近数据是否足够(>= days*0.7 行)+ TTL 内。"""
        # 优先 Redis:key 存在即 fresh
        k = K.DAILY.format(code=code)
        if self._store.exists(k):
            mp = self._store.hgetall(k)
            return len(mp) >= days * 0.7
        # 降级 SQLite
        conn = _thread_conn()
        row = conn.execute(
            "SELECT MAX(ts_updated), COUNT(*) FROM daily WHERE code=?",
            (code,),
        ).fetchone()
        if not row or row[1] == 0:
            return False
        ts_updated, cnt = row
        if systime.time() - (ts_updated or 0) > self.ttl:
            return False
        return cnt >= days * 0.7

    def get(self, code: str, days: int) -> pd.DataFrame | None:
        """返回最近 days 行的 DataFrame。"""
        k = K.DAILY.format(code=code)
        mp = self._store.hgetall(k)
        if not mp:
            # Redis miss → 尝试 SQLite
            return self._get_from_sqlite(code, days)
        # Redis hit:mp 是 {date_str: {open, high, ...}}
        rows = []
        for date_str, payload in mp.items():
            if not isinstance(payload, dict):
                continue
            rows.append({
                "日期": str(date_str),
                "开盘": float(payload.get("open", 0) or 0),
                "最高": float(payload.get("high", 0) or 0),
                "最低": float(payload.get("low", 0) or 0),
                "收盘": float(payload.get("close", 0) or 0),
                "成交量": float(payload.get("volume", 0) or 0),
                "成交额": float(payload.get("amount", 0) or 0),
                "换手率": float(payload.get("turnover", 0) or 0),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        # 取最近 days 行
        df = df.sort_values("日期", ascending=False).head(days).sort_values("日期").reset_index(drop=True)
        return df

    def _get_from_sqlite(self, code: str, days: int) -> pd.DataFrame | None:
        """SQLite 兜底读,同时回填 Redis。"""
        conn = _thread_conn()
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, amount, turnover "
            "FROM daily WHERE code=? "
            "ORDER BY date DESC LIMIT ?",
            (code, days),
        ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率"])
        df["日期"] = df["日期"].astype(str)
        df = df.sort_values("日期").reset_index(drop=True)
        # 回填 Redis
        try:
            self.set(code, df)
        except Exception:
            pass
        return df

    def set(self, code: str, df: pd.DataFrame) -> None:
        """双写:Redis + SQLite。"""
        if df is None or df.empty:
            return
        now = systime.time()
        # 1) Redis HSET (Hash field = date, value = json row)
        k = K.DAILY.format(code=code)
        for _, row in df.iterrows():
            d = str(row.get("日期", ""))[:10].replace("-", "")
            if not d or len(d) != 8:
                continue
            payload = {
                "open": float(row.get("开盘", 0) or 0),
                "high": float(row.get("最高", 0) or 0),
                "low": float(row.get("最低", 0) or 0),
                "close": float(row.get("收盘", 0) or 0),
                "volume": float(row.get("成交量", 0) or 0),
                "amount": float(row.get("成交额", 0) or 0),
                "turnover": float(row.get("换手率", 0) or 0),
                "ts_updated": now,
            }
            self._store.hset(k, d, payload, ttl=self.ttl)
        # 2) SQLite 冷归档 (best-effort)
        try:
            records = []
            for _, row in df.iterrows():
                d = str(row.get("日期", ""))[:10].replace("-", "")
                if not d or len(d) != 8:
                    continue
                records.append((
                    code, d,
                    float(row.get("开盘", 0) or 0),
                    float(row.get("最高", 0) or 0),
                    float(row.get("最低", 0) or 0),
                    float(row.get("收盘", 0) or 0),
                    float(row.get("成交量", 0) or 0),
                    float(row.get("成交额", 0) or 0),
                    float(row.get("换手率", 0) or 0),
                    now,
                ))
            if records:
                conn = _thread_conn()
                with conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO daily "
                        "(code, date, open, high, low, close, volume, amount, turnover, ts_updated) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
        except Exception as e:
            log.debug(f"daily 双写 SQLite 失败 {code}: {e}")

    def invalidate(self, code: str) -> None:
        # Redis
        self._store.delete(K.DAILY.format(code=code))
        # SQLite
        try:
            conn = _thread_conn()
            with conn:
                conn.execute("DELETE FROM daily WHERE code=?", (code,))
        except Exception:
            pass

    def stats(self) -> dict:
        """诊断:Redis 日线 code 数 + SQLite 行数 + 文件大小。"""
        try:
            # Redis
            daily_keys = [k for k in self._store.scan("daily:*") if k.startswith("daily:")]
            n_codes_redis = len(daily_keys)
            # SQLite (冷归档)
            conn = _thread_conn()
            n_rows = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
            n_codes_sqlite = conn.execute("SELECT COUNT(DISTINCT code) FROM daily").fetchone()[0]
            size_kb = round(_DB_PATH.stat().st_size / 1024, 1)
            return {
                "rows_sqlite": n_rows,
                "codes_sqlite": n_codes_sqlite,
                "codes_redis": n_codes_redis,
                "size_kb": size_kb,
            }
        except Exception:
            return {"rows_sqlite": 0, "codes_sqlite": 0, "codes_redis": 0, "size_kb": 0}


# 单例
_default_daily: DailyCache | None = None
def daily() -> DailyCache:
    global _default_daily
    if _default_daily is None:
        _default_daily = DailyCache()
    return _default_daily


# ═══════════════════════════════════════════════════
# AI Verdict 缓存 — screen 候选股 per-stock AI + 综合榜
# 主键 (date, code, model),TTL = 当日 23:59
# 走 Redis Hash + SQLite 冷归档
# ═══════════════════════════════════════════════════
import json as _json_ai

# cache_store 单例 (延迟初始化避免循环 import)
_ai_store = None
def _get_ai_store():
    global _ai_store
    if _ai_store is None:
        from . import cache_store as _cs
        _ai_store = _cs.get_store()
    return _ai_store


def _tomorrow_midnight_epoch() -> float:
    """下一个 0 点的 epoch 秒。同日内记录有效,跨日自动过期。"""
    now = systime.time()
    # 取本机本地时间的"明天 0 点";time.localtime 是本地时区
    lt = systime.localtime(now)
    midnight_today = systime.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, 0))
    return midnight_today + 86400.0  # 明天 0 点


def get_cached_ai(date: str, code: str, model: str) -> dict | None:
    """命中且未跨日则返回 ai envelope (dict),否则 None。
    v3.0 (2026-07-11): 优先 Redis,降级 SQLite。
    """
    # 1) Redis 主用
    k = K.AI.format(date=date, code=code)
    payload = _get_ai_store().hget(k, model)
    if payload and isinstance(payload, dict):
        payload = dict(payload)
        payload["from_cache"] = True
        return payload

    # 2) SQLite 兜底
    conn = _thread_conn()
    row = conn.execute(
        "SELECT verdict, conviction, summary_md, layer_pass_json, rules_passed_json, "
        "rules_failed_json, key_risks_json, sector, role, ts_updated "
        "FROM ai_verdict WHERE date=? AND code=? AND model=?",
        (date, code, model),
    ).fetchone()
    if not row:
        return None
    ts = row[9] or 0
    if ts < _tomorrow_midnight_epoch() - 86400:  # 跨日 miss
        return None
    if ts < _tomorrow_midnight_epoch() - 86400 * 2:  # 太旧也 miss
        return None
    return {
        "verdict":        row[0] or "-",
        "conviction":     int(row[1] or 0),
        "summary":        row[2] or "",
        "layer_pass":     _json_ai.loads(row[3]) if row[3] else {},
        "rules_passed":   _json_ai.loads(row[4]) if row[4] else [],
        "rules_failed":   _json_ai.loads(row[5]) if row[5] else [],
        "key_risks":      _json_ai.loads(row[6]) if row[6] else [],
        "sector":         row[7] or "",
        "role":           row[8] or "中军",
        "from_cache":     True,
        "ts_updated":     ts,
    }


def upsert_ai(date: str, code: str, model: str, ai: dict, sector: str = "") -> None:
    """双写:Redis + SQLite。"""
    now = systime.time()
    ai_with_meta = dict(ai)
    ai_with_meta["ts_updated"] = now
    ai_with_meta["sector"] = sector

    # 1) Redis 主用 (TTL 至 23:59)
    try:
        k = K.AI.format(date=date, code=code)
        _get_ai_store().hset(k, model, ai_with_meta, ttl=cache_store.ttl_until_midnight())
    except Exception as e:
        log.debug(f"AI 双写 Redis 失败 {date}/{code}: {e}")

    # 2) SQLite 冷归档
    try:
        conn = _thread_conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_verdict "
                "(date, code, model, verdict, role, conviction, summary_md, layer_pass_json, "
                " rules_passed_json, rules_failed_json, key_risks_json, sector, payload_json, ts_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    date, code, model,
                    str(ai.get("verdict") or "-"),
                    str(ai.get("role") or "中军"),
                    int(ai.get("conviction") or 0),
                    str(ai.get("summary") or ""),
                    _json_ai.dumps(ai.get("layer_pass") or {}, ensure_ascii=False),
                    _json_ai.dumps(ai.get("rules_passed") or [], ensure_ascii=False),
                    _json_ai.dumps(ai.get("rules_failed") or [], ensure_ascii=False),
                    _json_ai.dumps(ai.get("key_risks") or [], ensure_ascii=False),
                    sector or "",
                    _json_ai.dumps(ai, ensure_ascii=False),
                    now,
                ),
            )
    except Exception as e:
        log.debug(f"AI 双写 SQLite 失败 {date}/{code}: {e}")


def get_cached_aggregate(date: str, model: str) -> dict | None:
    """综合榜缓存。code='_aggregate_'。
    v3.0: 优先 Redis String,降级 SQLite。
    """
    # 1) Redis 主用:tx3:ai:{date}:_aggregate_ field={model}
    k = K.AI.format(date=date, code="_aggregate_")
    payload = _get_ai_store().hget(k, model)
    if payload and isinstance(payload, dict):
        return payload
    # 2) SQLite 兜底
    conn = _thread_conn()
    row = conn.execute(
        "SELECT payload_json, ts_updated FROM ai_verdict WHERE date=? AND code='_aggregate_' AND model=?",
        (date, model),
    ).fetchone()
    if not row:
        return None
    ts = row[1] or 0
    if ts < _tomorrow_midnight_epoch() - 86400 * 2:
        return None
    try:
        return _json_ai.loads(row[0])
    except Exception:
        return None


def upsert_aggregate(date: str, model: str, payload: dict) -> None:
    """综合榜写入(双写:Redis + SQLite)。"""
    now = systime.time()
    payload_with_ts = dict(payload)
    payload_with_ts["ts_updated"] = now
    # 1) Redis
    try:
        k = K.AI.format(date=date, code="_aggregate_")
        _get_ai_store().hset(k, model, payload_with_ts, ttl=cache_store.ttl_until_midnight())
    except Exception as e:
        log.debug(f"aggregate 双写 Redis 失败 {date}: {e}")
    # 2) SQLite
    try:
        conn = _thread_conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO ai_verdict "
                "(date, code, model, payload_json, ts_updated) "
                "VALUES (?, '_aggregate_', ?, ?, ?)",
                (date, model, _json_ai.dumps(payload, ensure_ascii=False), now),
            )
    except Exception as e:
        log.debug(f"aggregate 双写 SQLite 失败 {date}: {e}")


def ai_cache_stats() -> dict:
    """诊断:ai_verdict 行数 / 覆盖交易日数 / 当日条数."""
    try:
        conn = _thread_conn()
        n = conn.execute("SELECT COUNT(*) FROM ai_verdict").fetchone()[0]
        return {"rows": n}
    except Exception:
        return {"rows": 0}


# ═══════════════════════════════════════════════════
# 个股查询历史 (2026-07-11) — 服务端永久化,跨浏览器/跨设备
# 之前用 localStorage 浏览器清数据就丢;现在 SQLite 跨端同步
# ═══════════════════════════════════════════════════
def record_stock_query(code: str, name: str | None = None) -> None:
    """记录一次个股查询:同 code 提到最前+hit_count++,time=now。"""
    code = (code or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return
    name = (name or "").strip() or code
    now = systime.time()
    try:
        conn = _thread_conn()
        conn.execute(
            """
            INSERT INTO stock_history (code, name, hit_count, first_query_ts, last_query_ts)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                hit_count = hit_count + 1,
                last_query_ts = excluded.last_query_ts
            """,
            (code, name[:32], now, now),
        )
        conn.commit()
    except Exception as e:
        log.debug(f"record_stock_query {code} 失败: {e}")


def list_stock_history(limit: int = 50) -> list[dict]:
    """最近查询过的个股(按 last_query_ts DESC)。"""
    try:
        conn = _thread_conn()
        rows = conn.execute(
            "SELECT code, name, hit_count, first_query_ts, last_query_ts "
            "FROM stock_history ORDER BY last_query_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "code":        r[0],
                "name":        r[1] or r[0],
                "hit_count":   r[2],
                "first_query_ts": r[3],
                "last_query_ts":  r[4],
            }
            for r in rows
        ]
    except Exception as e:
        log.debug(f"list_stock_history 失败: {e}")
        return []


def remove_stock_history(code: str) -> bool:
    """单条删除。返回是否真的删了一行。"""
    code = (code or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        conn = _thread_conn()
        cur = conn.execute("DELETE FROM stock_history WHERE code=?", (code,))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        log.debug(f"remove_stock_history {code} 失败: {e}")
        return False


def clear_stock_history() -> int:
    """清空全部。返回删除的行数。"""
    try:
        conn = _thread_conn()
        cur = conn.execute("DELETE FROM stock_history")
        conn.commit()
        return cur.rowcount
    except Exception as e:
        log.debug(f"clear_stock_history 失败: {e}")
        return 0


# ════════════════════════════════════════════════════════════
# R8: 自动备份 + 健康指标
# ════════════════════════════════════════════════════════════
import shutil as _shutil
import gzip as _gzip
from datetime import datetime as _dt


def backup_db(dest_dir: str | None = None) -> str | None:
    """用 sqlite 在线 API 备份当前 db 到 dest_dir/cache-YYYYMMDD-HHMMSS.db.gz。

    比直接 cp 安全 — 不会复制到一半的 WAL 页。
    返回备份文件路径,失败返 None。
    """
    try:
        if dest_dir is None:
            dest_dir = str(_DB_PATH.parent / "backups")
        import os as _os
        _os.makedirs(dest_dir, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        out = _os.path.join(dest_dir, f"cache-{ts}.db.gz")
        # 用在线 backup API — 即使有并发写入也安全
        src = _thread_conn()
        with _gzip.open(out, "wb", compresslevel=6) as gz:
            with sqlite3.connect(":memory:") as dst:
                src.backup(dst)
                # 把内存里的内容写到 gz
                for line in dst.iterdump():
                    gz.write((line + "\n").encode("utf-8"))
        # 清理 7 天前的旧备份
        _prune_old_backups(dest_dir, keep_days=7)
        return out
    except Exception as e:
        log.warning(f"backup_db 失败: {e}")
        return None


def _prune_old_backups(dest_dir: str, keep_days: int = 7) -> int:
    """删 N 天前的旧备份,避免磁盘爆。"""
    try:
        import os as _os, glob as _glob, time as _t
        cutoff = _t.time() - keep_days * 86400
        n = 0
        for p in _glob.glob(_os.path.join(dest_dir, "cache-*.db.gz")):
            try:
                if _os.path.getmtime(p) < cutoff:
                    _os.remove(p)
                    n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


def db_health() -> dict:
    """健康指标:R8 — 慢查询数 / WAL 大小 / 表行数 / 上次 backup 时间"""
    try:
        conn = _thread_conn()
        out = {
            "path":         str(_DB_PATH),
            "size_mb":      round(_DB_PATH.stat().st_size / 1024 / 1024, 2) if _DB_PATH.exists() else 0,
        }
        # WAL 文件大小
        wal = _DB_PATH.with_suffix(".db-wal")
        if wal.exists():
            out["wal_mb"] = round(wal.stat().st_size / 1024 / 1024, 2)
        # 主表行数
        try:
            r = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
            out["trades_rows"] = r[0]
            r = conn.execute("SELECT COUNT(*) FROM trade_reviews").fetchone()
            out["trade_reviews_rows"] = r[0]
            r = conn.execute("SELECT COUNT(*) FROM stock_history").fetchone()
            out["stock_history_rows"] = r[0]
        except Exception:
            pass
        # 最近一次 backup
        backup_dir = _DB_PATH.parent / "backups"
        if backup_dir.exists():
            backups = sorted(backup_dir.glob("cache-*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                out["last_backup"] = _dt.fromtimestamp(backups[0].stat().st_mtime).isoformat(timespec="seconds")
                out["backup_count"] = len(backups)
        # PRAGMA stats
        try:
            out["journal_mode"]   = conn.execute("PRAGMA journal_mode").fetchone()[0]
            out["synchronous"]    = conn.execute("PRAGMA synchronous").fetchone()[0]
            out["cache_size_kb"]  = conn.execute("PRAGMA cache_size").fetchone()[0]
        except Exception:
            pass
        return out
    except Exception as e:
        return {"error": str(e)[:200]}
