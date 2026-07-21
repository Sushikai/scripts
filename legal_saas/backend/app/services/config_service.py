"""配置 service — system_config 表读写 + 加密。"""
from __future__ import annotations
import time
import json
from typing import Any
from app.db.database import query_one, execute, query
from app.core.security import encrypt, decrypt


def get_config(key: str) -> str | None:
    """读单个配置(自动解密)。"""
    row = query_one("SELECT value_encrypted FROM system_config WHERE key = ?", (key,))
    if not row:
        return None
    return decrypt(row["value_encrypted"])


def set_config(key: str, value: str):
    """写单个配置(自动加密)。"""
    now = int(time.time())
    token = encrypt(value)
    row = query_one("SELECT key FROM system_config WHERE key = ?", (key,))
    if row:
        execute(
            "UPDATE system_config SET value_encrypted = ?, updated_at = ? WHERE key = ?",
            (token, now, key),
        )
    else:
        execute(
            "INSERT INTO system_config (key, value_encrypted, updated_at) VALUES (?, ?, ?)",
            (key, token, now),
        )


def delete_config(key: str):
    execute("DELETE FROM system_config WHERE key = ?", (key,))


def get_all_public() -> dict[str, Any]:
    """返回脱敏后的公开配置(给前端)。"""
    keys = [
        "minimax_api_key", "minimax_base_url", "minimax_model",
        "embedding_provider", "embedding_model",
        "statute_api_key", "statute_api_base",
        "active_role",
    ]
    result = {}
    for k in keys:
        v = get_config(k)
        if v is None:
            continue
        if k.endswith("_api_key"):
            # 脱敏:只显示前 4 后 4
            if len(v) > 12:
                result[k] = v[:4] + "*" * (len(v) - 8) + v[-4:]
            else:
                result[k] = "****"
        else:
            result[k] = v
    return result


def update_batch(payload: dict[str, Any]):
    """批量更新配置。"""
    for k, v in payload.items():
        if v is None or v == "":
            continue
        set_config(k, str(v))