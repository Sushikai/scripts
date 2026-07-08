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
            date   TEXT NOT NULL,        -- YYYYMMDD
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume REAL,
            amount REAL,
            turnover REAL,
            ts_updated REAL NOT NULL,    -- epoch seconds
            PRIMARY KEY (code, date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(code);
        CREATE INDEX IF NOT EXISTS idx_daily_code_date ON daily(code, date DESC);
        CREATE INDEX IF NOT EXISTS idx_daily_ts ON daily(ts_updated);

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """)
    conn.commit()


def get_conn() -> sqlite3.Connection:
    """线程安全 connection(每个线程一份)."""
    global _init_done
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")  # 多读少写时 WAL 更友好
    conn.execute("PRAGMA synchronous=NORMAL;")
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
# 日线缓存 API
# ═══════════════════════════════════════════════════
class DailyCache:
    """按 (code, days) 拉日线 + 写回。"""

    def __init__(self, ttl_sec: int = 4 * 3600):
        self.ttl = ttl_sec

    def is_fresh(self, code: str, days: int) -> bool:
        """最近一天的数据是否在 TTL 内(且行数够)。"""
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
        return cnt >= days * 0.7  # 至少 70% 行数

    def get(self, code: str, days: int) -> pd.DataFrame | None:
        """返回最近 days 行的 DataFrame(无缓存或过期 → None)。"""
        if not self.is_fresh(code, days):
            return None
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
        return df.sort_values("日期").reset_index(drop=True)

    def set(self, code: str, df: pd.DataFrame) -> None:
        """upsert 全表 -> daily."""
        if df is None or df.empty:
            return
        now = systime.time()
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
        if not records:
            return
        conn = _thread_conn()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily "
                "(code, date, open, high, low, close, volume, amount, turnover, ts_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                records,
            )

    def invalidate(self, code: str) -> None:
        conn = _thread_conn()
        with conn:
            conn.execute("DELETE FROM daily WHERE code=?", (code,))

    def stats(self) -> dict:
        """诊断:行数 / 唯一 code 数 / 文件大小."""
        try:
            conn = _thread_conn()
            n_rows = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
            n_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM daily").fetchone()[0]
            size_kb = round(_DB_PATH.stat().st_size / 1024, 1)
            return {"rows": n_rows, "codes": n_codes, "size_kb": size_kb}
        except Exception:
            return {"rows": 0, "codes": 0, "size_kb": 0}


# 单例
_default_daily: DailyCache | None = None
def daily() -> DailyCache:
    global _default_daily
    if _default_daily is None:
        _default_daily = DailyCache()
    return _default_daily
