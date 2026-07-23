"""L2 SQLite cache + safe_write + 可选 L1 Redis。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional

from .. import _constants as C

_TLS = threading.local()
_INIT_LOCK = threading.Lock()
_INIT_DONE = False


def _conn() -> sqlite3.Connection:
    c: sqlite3.Connection | None = getattr(_TLS, "conn", None)
    if c is not None:
        try:
            c.execute("SELECT 1")
            return c
        except sqlite3.ProgrammingError:
            _TLS.conn = None
    conn = sqlite3.connect(C.CACHE_DB_PATH(), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _TLS.conn = conn
    return conn


def init_cache() -> None:
    global _INIT_DONE
    with _INIT_LOCK:
        if _INIT_DONE:
            return
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kv (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv(expires_at)")
        _INIT_DONE = True


def _now() -> int:
    return int(time.time())


def get(key: str) -> Optional[Any]:
    """读缓存,过期自动返回 None。"""
    init_cache()
    row = _conn().execute("SELECT v, expires_at FROM kv WHERE k=?", (key,)).fetchone()
    if not row:
        return None
    if row["expires_at"] <= _now():
        return None
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return None


def set_(key: str, value: Any, ttl: int = C.CACHE_TTL_DEFAULT) -> None:
    """写缓存(下划线避免和 built-in set 撞)。"""
    init_cache()
    payload = json.dumps(value, ensure_ascii=False, default=str)
    # ttl<=0 视为已过期,直接用 _now()-1,这样下次 get 必返回 None
    ttl = max(ttl, 0)
    expires = _now() + ttl if ttl > 0 else _now() - 1
    for i in range(4):
        try:
            _conn().execute(
                "INSERT OR REPLACE INTO kv(k, v, expires_at, updated_at) VALUES(?,?,?,?)",
                (key, payload, expires, _now()),
            )
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            time.sleep(0.15 * (2 ** i) / 1000.0)


def delete(key: str) -> None:
    init_cache()
    _conn().execute("DELETE FROM kv WHERE k=?", (key,))


def clear_expired() -> int:
    """清理过期键,返回删除条数。"""
    init_cache()
    cur = _conn().execute("DELETE FROM kv WHERE expires_at<?", (_now(),))
    return cur.rowcount or 0


def stats() -> dict:
    init_cache()
    row = _conn().execute("SELECT COUNT(*) AS n FROM kv").fetchone()
    return {
        "total": row["n"] if row else 0,
        "ttl_default": C.CACHE_TTL_DEFAULT,
        "backend": "sqlite",
    }


def cache_or_compute(key: str, compute_fn, ttl: int = C.CACHE_TTL_DEFAULT):
    """命中即返回,未命中调用 compute_fn 并写缓存。"""
    hit = get(key)
    if hit is not None:
        return hit, True
    val = compute_fn()
    set_(key, val, ttl=ttl)
    return val, False