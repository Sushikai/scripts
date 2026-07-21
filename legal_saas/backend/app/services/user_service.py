"""
User service — 用户 CRUD + 登录/登出 + 登录尝试日志。

预埋模式:
- 单用户模式(SINGLE_USER_MODE=True):启动时自动建 admin/admin123
- 多用户模式(False):需注册流程(暂未开放 UI)
"""
from __future__ import annotations
import time
import uuid
from typing import Any
from app.db.database import query_one, execute, query
from app.core.auth import (
    hash_password, verify_password,
    make_access_token, make_refresh_token, decode_token, hash_token,
    validate_username, validate_password,
)
from app.config import SINGLE_USER_MODE, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD


def _now() -> int:
    return int(time.time())


# ── 用户 CRUD ────────────────────────────────
def get_by_id(user_id: int) -> dict | None:
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return dict(row) if row else None


def get_by_username(username: str) -> dict | None:
    row = query_one("SELECT * FROM users WHERE username = ?", (username,))
    return dict(row) if row else None


def count_users() -> int:
    row = query_one("SELECT COUNT(*) as n FROM users")
    return row["n"] if row else 0


def create_user(username: str, password: str, email: str | None = None,
                role: str = "user", display_name: str | None = None) -> dict:
    """创建用户(不做强度校验,由调用方决定)。"""
    err_u = validate_username(username)
    if err_u:
        raise ValueError(err_u)
    err_p = validate_password(password)
    if err_p:
        raise ValueError(err_p)
    if get_by_username(username):
        raise ValueError(f"用户名 '{username}' 已存在")

    pwd_hash, salt = hash_password(password)
    now = _now()
    execute(
        """INSERT INTO users (uuid, username, email, password_hash, salt, role, display_name, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, username, email, pwd_hash, salt, role,
         display_name or username, now, now),
    )
    return get_by_username(username)


def update_last_login(user_id: int, ip: str | None = None):
    execute(
        "UPDATE users SET last_login_at = ?, last_login_ip = ? WHERE id = ?",
        (_now(), ip, user_id),
    )


# ── 登录/登出 ───────────────────────────────
def login(username: str, password: str, ip: str | None = None,
          user_agent: str | None = None) -> dict[str, Any]:
    """登录,返回 {ok, access_token, refresh_token, user} 或 {ok:False, reason}。"""
    # 1. 登录尝试限频
    if _is_rate_limited(username, ip):
        _log_attempt(username, ip, user_agent, success=False, reason="rate_limited")
        return {"ok": False, "reason": "rate_limited", "message": "尝试过于频繁,请稍后再试"}

    user = get_by_username(username)
    if not user:
        _log_attempt(username, ip, user_agent, success=False, reason="user_not_found")
        return {"ok": False, "reason": "user_not_found", "message": "用户名或密码错误"}
    if not user["is_active"]:
        _log_attempt(username, ip, user_agent, success=False, reason="user_disabled")
        return {"ok": False, "reason": "user_disabled", "message": "账号已停用"}

    # 2. 密码校验
    if not verify_password(password, user["password_hash"], user["salt"]):
        _log_attempt(username, ip, user_agent, success=False, reason="bad_password")
        return {"ok": False, "reason": "bad_password", "message": "用户名或密码错误"}

    # 3. 签发 token
    access_token, access_exp = make_access_token(user["id"], user["username"], user["role"])
    refresh_token, refresh_exp, refresh_hash = make_refresh_token(user["id"])

    # 4. refresh token 存 DB
    execute(
        """INSERT INTO auth_tokens (user_id, token_hash, token_type, expires_at, user_agent, ip, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user["id"], refresh_hash, "refresh", refresh_exp, user_agent, ip, _now()),
    )

    # 5. 更新最后登录
    update_last_login(user["id"], ip)
    _log_attempt(username, ip, user_agent, success=True, reason=None)

    return {
        "ok": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_at": access_exp,
        "refresh_expires_at": refresh_exp,
        "user": _public_user(user),
    }


def logout(refresh_token: str | None = None, access_token: str | None = None):
    """撤销 token(若提供)。"""
    revoked = 0
    for tok in (refresh_token, access_token):
        if not tok:
            continue
        h = hash_token(tok)
        execute(
            "UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now(), h),
        )
        revoked += 1
    return revoked


def refresh(refresh_token: str, ip: str | None = None) -> dict[str, Any] | None:
    """用 refresh 换新的 access。"""
    payload = decode_token(refresh_token, expected_type="refresh")
    if not payload:
        return None
    h = hash_token(refresh_token)
    row = query_one("SELECT user_id, expires_at, revoked_at FROM auth_tokens WHERE token_hash = ?", (h,))
    if not row or row["revoked_at"] or row["expires_at"] < _now():
        return None
    user = get_by_id(row["user_id"])
    if not user or not user["is_active"]:
        return None
    new_access, new_exp = make_access_token(user["id"], user["username"], user["role"])
    return {
        "ok": True,
        "access_token": new_access,
        "access_expires_at": new_exp,
    }


# ── 中间件:从 Authorization 头取当前用户 ─────
def current_user_from_token(token: str) -> dict | None:
    payload = decode_token(token, expected_type="access")
    if not payload:
        return None
    user = get_by_id(int(payload["sub"]))
    return user if user and user["is_active"] else None


# ── 内部工具 ─────────────────────────────────
def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "uuid": user["uuid"],
        "username": user["username"],
        "email": user.get("email"),
        "role": user["role"],
        "display_name": user.get("display_name"),
        "avatar_url": user.get("avatar_url"),
        "created_at": user["created_at"],
        "last_login_at": user.get("last_login_at"),
    }


def _log_attempt(username: str, ip: str | None, ua: str | None,
                 success: bool, reason: str | None):
    execute(
        "INSERT INTO login_attempts (username, ip, user_agent, success, failure_reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (username, ip, ua, 1 if success else 0, reason, _now()),
    )


def _is_rate_limited(username: str, ip: str | None) -> bool:
    """1 小时内失败次数 >= 10 → 限频。"""
    one_hour_ago = _now() - 3600
    row = query_one(
        """SELECT COUNT(*) as n FROM login_attempts
           WHERE username = ? AND success = 0 AND created_at >= ?""",
        (username, one_hour_ago),
    )
    return (row["n"] if row else 0) >= 10


# ── 启动钩子:首次启动自动建 admin ──────────
def ensure_default_admin():
    """单用户模式下,首次启动自动建默认 admin 账号。"""
    if not SINGLE_USER_MODE:
        return None
    if count_users() > 0:
        return None
    admin = create_user(
        username=DEFAULT_ADMIN_USERNAME,
        password=DEFAULT_ADMIN_PASSWORD,
        role="admin",
        display_name="系统管理员",
        email=None,
    )
    return admin