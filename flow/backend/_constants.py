"""flow 平台常量(单一来源,避免魔法数字散落)。

所有 TTL/限频/超时/阈值都在这里。env 通过函数动态读取,便于测试覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _project_root() -> str:
    return os.environ.get("FLOW_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# === 服务端口(每次访问读 env) ===
def PORT() -> int: return _env_int("FLOW_PORT", 8810)


def HOST() -> str: return _env("FLOW_HOST", "0.0.0.0")


# === 路径 ===
def PROJECT_ROOT() -> str: return _project_root()


def DB_PATH() -> str: return _env("FLOW_DB") or str(Path(PROJECT_ROOT()) / "flow.db")


def CACHE_DB_PATH() -> str: return _env("FLOW_CACHE_DB") or str(Path(PROJECT_ROOT()) / "cache.db")


def ACCESS_LOG_PATH() -> str: return _env("FLOW_ACCESS_LOG") or str(Path(PROJECT_ROOT()) / "access.log")


ACCESS_LOG_MAX_BYTES: int = 10 * 1024 * 1024
ACCESS_LOG_BACKUP_COUNT: int = 3

# === 静态资源 ===
def FRONTEND_DIR() -> str: return str(Path(PROJECT_ROOT()) / "frontend")


# === 超时(秒) ===
def TIMEOUT_DEFAULT() -> float: return _env_float("FLOW_TIMEOUT", 8.0)
TIMEOUT_HEALTH: float = 2.0
TIMEOUT_AI: float = 30.0
TIMEOUT_JOB_STREAM: float = 7200.0

# === 缓存 TTL(秒) ===
CACHE_TTL_DEFAULT: int = 60
CACHE_TTL_DASHBOARD: int = 30
CACHE_TTL_TOOLS_META: int = 600
CACHE_TTL_ACCOUNTS: int = 300
CACHE_TTL_THUMB: int = 3600

# === 限频(每 IP) ===
RATE_LIMIT_DEFAULT_PER_MIN: int = _env_int("FLOW_RATE_LIMIT_DEFAULT", 60)
RATE_LIMIT_AI_PER_MIN: int = _env_int("FLOW_RATE_LIMIT_AI", 20)
RATE_LIMIT_JOB_CREATE_PER_MIN: int = _env_int("FLOW_RATE_LIMIT_JOB", 10)
RATE_LIMIT_WINDOW_SEC: int = 60

# === Job 调度 ===
JOB_MAX_CONCURRENT: int = _env_int("FLOW_JOB_MAX", 2)
JOB_PROGRESS_DEBOUNCE_MS: int = 200
SSE_HEARTBEAT_SEC: int = 15
SSE_MAX_QUEUE: int = 128

# === 文件代理白名单根(防 path traversal)===
def ALLOWED_FS_ROOTS() -> tuple:
    return (
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/ai_video_project"),
        os.path.expanduser("~/ai_video_upload"),
        os.path.expanduser("~/tiktok_automation"),
        str(Path(PROJECT_ROOT()) / "outputs"),
        str(Path(PROJECT_ROOT()) / "data"),
    )


# === 缩略图 ===
THUMB_MAX_WIDTH: int = 480
THUMB_QUALITY: int = 78
THUMB_CACHE_MAX_ITEMS: int = 1024

# === 鉴权 ===
LOCAL_TOKEN: str = _env("FLOW_LOCAL_TOKEN", "flow-local-dev-token")

# === 重试 / 熔断 ===
AI_RETRY_MAX: int = 3
AI_RETRY_BASE_MS: int = 600
AI_CB_FAIL_THRESHOLD: int = 5
AI_CB_RESET_SEC: int = 60

# === Redis ===
USE_REDIS: bool = _env("FLOW_USE_REDIS", "0") == "1"
REDIS_URL: str = _env("FLOW_REDIS_URL", "redis://localhost:6379/0")
REDIS_KEY_PREFIX: str = "flow:"