"""
Fernet 加密工具 — MiniMax Key / 法条 Key 等敏感配置本地加密存储。
Key 来源: 1) 环境变量 ENCRYPTION_KEY  2) data/.fernet_key (首次启动自动生成)
"""
from __future__ import annotations
import os
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from ..config import DATA_DIR

_FERNET_KEY_PATH = DATA_DIR / ".fernet_key"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if env_key:
        return env_key.encode()
    if _FERNET_KEY_PATH.exists():
        return _FERNET_KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _FERNET_KEY_PATH.write_bytes(key)
    try:
        _FERNET_KEY_PATH.chmod(0o600)
    except Exception:
        pass
    return key


_FERNET = Fernet(_load_or_create_key())


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _FERNET.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except InvalidToken:
        return ""