"""SQLite WAL + safe_write 仓储层。

safe_write(fn, retries=4) 退避重试,捕获 OperationalError("database is locked")。
thread-local connection,per-call 短事务。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .. import _constants as C

_TLS = threading.local()
_INIT_LOCK = threading.Lock()
_INIT_DONE = False


def _conn() -> sqlite3.Connection:
    """每线程一个连接。"""
    c: sqlite3.Connection | None = getattr(_TLS, "conn", None)
    if c is not None:
        try:
            c.execute("SELECT 1")
            return c
        except sqlite3.ProgrammingError:
            _TLS.conn = None
            c = None
    conn = sqlite3.connect(C.DB_PATH(), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _TLS.conn = conn
    return conn


def init_db() -> None:
    """一次性建表(同进程)。"""
    global _INIT_DONE
    with _INIT_LOCK:
        if _INIT_DONE:
            return
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        conn = _conn()
        conn.executescript(schema)
        _INIT_DONE = True


def safe_write(fn, retries: int = 4, base_ms: int = 150):
    """退避重试包装写操作。"""
    last: Exception | None = None
    for i in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower():
                raise
            time.sleep((base_ms * (2 ** i)) / 1000.0)
    raise last  # type: ignore[misc]


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """显式事务。"""
    conn = _conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# === ID 生成 ===
def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_ms() -> int:
    return int(time.time() * 1000)


# === Projects ===
def project_create(tool_id: str, name: str, params: dict, meta: dict | None = None) -> dict:
    init_db()
    pid = new_id()
    ts = now_ms()
    safe_write(lambda: _conn().execute(
        "INSERT INTO projects(id, tool_id, name, params, status, created_at, updated_at, meta) VALUES(?,?,?,?,?,?,?,?)",
        (pid, tool_id, name, json.dumps(params, ensure_ascii=False), "pending", ts, ts, json.dumps(meta or {}, ensure_ascii=False)),
    ))
    return {"id": pid, "tool_id": tool_id, "name": name, "params": params, "status": "pending", "created_at": ts, "updated_at": ts}


def project_get(pid: str) -> Optional[dict]:
    init_db()
    row = _conn().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return _row_to_project(row) if row else None


def project_list(tool_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    init_db()
    sql = "SELECT * FROM projects WHERE 1=1"
    args: list = []
    if tool_id:
        sql += " AND tool_id=?"
        args.append(tool_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    return [_row_to_project(r) for r in _conn().execute(sql, args).fetchall()]


def project_update_status(pid: str, status: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE projects SET status=?, updated_at=? WHERE id=?",
        (status, now_ms(), pid),
    ))


def _row_to_project(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "tool_id": row["tool_id"],
        "name": row["name"],
        "params": json.loads(row["params"] or "{}"),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "meta": json.loads(row["meta"] or "{}"),
    }


# === Jobs ===
def job_create(project_id: str, step: str) -> dict:
    init_db()
    jid = new_id()
    safe_write(lambda: _conn().execute(
        "INSERT INTO jobs(id, project_id, step, status, progress) VALUES(?,?,?,?,?)",
        (jid, project_id, step, "pending", 0.0),
    ))
    return {"id": jid, "project_id": project_id, "step": step, "status": "pending", "progress": 0.0}


def job_get(jid: str) -> Optional[dict]:
    init_db()
    row = _conn().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return _row_to_job(row) if row else None


def job_list_by_project(pid: str) -> list[dict]:
    init_db()
    return [_row_to_job(r) for r in _conn().execute(
        "SELECT * FROM jobs WHERE project_id=? ORDER BY started_at ASC, id ASC", (pid,)
    ).fetchall()]


def job_set_running(jid: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE jobs SET status=?, started_at=? WHERE id=? AND status='pending'",
        ("running", now_ms(), jid),
    ))


def job_set_progress(jid: str, progress: float) -> None:
    init_db()
    progress = max(0.0, min(1.0, float(progress)))
    safe_write(lambda: _conn().execute(
        "UPDATE jobs SET progress=? WHERE id=?",
        (progress, jid),
    ))


def job_set_done(jid: str, artifacts: dict | None = None) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE jobs SET status=?, progress=?, finished_at=?, artifacts=? WHERE id=?",
        ("done", 1.0, now_ms(), json.dumps(artifacts or {}, ensure_ascii=False), jid),
    ))


def job_set_failed(jid: str, error: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=?",
        ("failed", now_ms(), error[:2000], jid),
    ))


def job_set_cancelled(jid: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE jobs SET status=?, finished_at=? WHERE id=? AND status IN ('pending','running')",
        ("cancelled", now_ms(), jid),
    ))


def _row_to_job(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "step": row["step"],
        "status": row["status"],
        "progress": row["progress"],
        "log_path": row["log_path"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "artifacts": json.loads(row["artifacts"] or "{}"),
        "error": row["error"],
    }


# === Assets ===
def asset_create(source: str, url: str | None, path: str | None, hash_: str | None, tags: list, meta: dict | None = None) -> dict:
    init_db()
    aid = new_id()
    ts = now_ms()
    safe_write(lambda: _conn().execute(
        "INSERT INTO assets(id, source, url, path, hash, tags, meta, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (aid, source, url, path, hash_, json.dumps(tags, ensure_ascii=False), json.dumps(meta or {}, ensure_ascii=False), ts),
    ))
    return {"id": aid, "source": source, "url": url, "path": path, "hash": hash_, "tags": tags, "created_at": ts}


def asset_list(source: str | None = None, limit: int = 50) -> list[dict]:
    init_db()
    sql = "SELECT * FROM assets WHERE 1=1"
    args: list = []
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = _conn().execute(sql, args).fetchall()
    return [{
        "id": r["id"],
        "source": r["source"],
        "url": r["url"],
        "path": r["path"],
        "hash": r["hash"],
        "tags": json.loads(r["tags"] or "[]"),
        "meta": json.loads(r["meta"] or "{}"),
        "created_at": r["created_at"],
    } for r in rows]


# === Uploads ===
def upload_create(project_id: str, platform: str, account: str | None) -> dict:
    init_db()
    uid = new_id()
    ts = now_ms()
    safe_write(lambda: _conn().execute(
        "INSERT INTO uploads(id, project_id, platform, account, status, created_at) VALUES(?,?,?,?,?,?)",
        (uid, project_id, platform, account, "pending", ts),
    ))
    return {"id": uid, "project_id": project_id, "platform": platform, "account": account, "status": "pending"}


def upload_set_success(uid: str, vid_id: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE uploads SET status=?, vid_id=? WHERE id=?",
        ("success", vid_id, uid),
    ))


def upload_set_failed(uid: str, error: str) -> None:
    init_db()
    safe_write(lambda: _conn().execute(
        "UPDATE uploads SET status=?, error=? WHERE id=?",
        ("failed", error[:2000], uid),
    ))


def upload_list(limit: int = 50, platform: str | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM uploads WHERE 1=1"
    args: list = []
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in _conn().execute(sql, args).fetchall()]


# === Accounts ===
def account_upsert(name: str, platform: str, cookie_path: str, status: str = "unknown", meta: dict | None = None) -> dict:
    init_db()
    ts = now_ms()
    conn = _conn()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    existing = conn.execute(
        "SELECT id FROM accounts WHERE name=? AND platform=?", (name, platform)
    ).fetchone()
    if existing:
        aid = existing["id"]
        safe_write(lambda: conn.execute(
            "UPDATE accounts SET cookie_path=?, last_check_at=?, status=?, meta=? WHERE id=?",
            (cookie_path, ts, status, meta_json, aid),
        ))
    else:
        aid = new_id()
        safe_write(lambda: conn.execute(
            "INSERT INTO accounts(id, name, platform, cookie_path, last_check_at, status, meta) VALUES(?,?,?,?,?,?,?)",
            (aid, name, platform, cookie_path, ts, status, meta_json),
        ))
    return {"id": aid, "name": name, "platform": platform, "cookie_path": cookie_path, "status": status}


def account_list() -> list[dict]:
    init_db()
    rows = _conn().execute("SELECT * FROM accounts ORDER BY platform, name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        out.append(d)
    return out