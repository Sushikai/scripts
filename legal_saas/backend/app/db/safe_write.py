"""
SQLite safe_write — retry+rollback,移植自 tuixue_v3 cache_db.py。
解决: 多进程/多线程抢 WAL writer 致 "database is locked"。
"""
from __future__ import annotations
import time
import sqlite3
from ..core.logger import get_logger

log = get_logger()

MAX_RETRIES = 8
RETRY_BASE_SLEEP = 0.05


def safe_write(conn: sqlite3.Connection, sql_or_callable, params=()) -> int:
    """
    带 retry 的写操作。
    sql_or_callable 可以是:
      - str: 单条 SQL
      - callable: 接受 conn 返回 rowcount
    """
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            if callable(sql_or_callable):
                return sql_or_callable(conn)
            cur = conn.execute(sql_or_callable, params)
            return cur.rowcount
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                last_err = e
                sleep_s = RETRY_BASE_SLEEP * (2 ** attempt)
                time.sleep(sleep_s)
                continue
            if "no such table" in msg:
                raise
            log.warning(f"safe_write OperationalError attempt={attempt}: {e}")
            last_err = e
            time.sleep(RETRY_BASE_SLEEP)
            continue
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            log.exception(f"safe_write unexpected: {e}")
            raise
    log.error(f"safe_write exhausted {MAX_RETRIES} retries: {last_err}")
    raise last_err if last_err else RuntimeError("safe_write failed")