"""
SQLite 引擎 (WAL, busy_timeout, safe_write)。
移植自 tuixue_v3 cache_db.py 模式。
"""
from __future__ import annotations
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from ..config import DB_PATH
from .safe_write import safe_write

_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect()
    return _local.conn


@contextmanager
def transaction():
    """事务上下文。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def query(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | dict = ()) -> int:
    """单语句写,带 retry+rollback(走 safe_write)。"""
    return safe_write(get_conn(), sql, params)


def executemany(sql: str, params_list: list[tuple]) -> int:
    """批量写,带 retry。"""
    def _do(conn):
        cur = conn.executemany(sql, params_list)
        return cur.rowcount
    return safe_write(get_conn(), _do)


def init_schema(migrations_dir: Path):
    """按序执行 migrations/*.sql。"""
    conn = get_conn()
    files = sorted(migrations_dir.glob("*.sql"))
    for f in files:
        sql = f.read_text(encoding="utf-8")
        conn.executescript(sql)
    conn.commit()