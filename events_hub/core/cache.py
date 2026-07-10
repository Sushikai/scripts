"""
core/cache.py - SQLite 缓存层
避免重复请求 akshare，TTL 机制
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Any

DB_PATH = Path(__file__).parent.parent / "data" / "events_cache.db"


class Cache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT,
                expired_at INTEGER
            )
        """)
        self.conn.commit()

    def get(self, key: str) -> Optional[Any]:
        cur = self.conn.execute(
            "SELECT value, expired_at FROM cache WHERE key=?",
            (key,)
        )
        row = cur.fetchone()
        if not row:
            return None
        value_str, expired_at = row
        if int(datetime.now().timestamp()) > expired_at:
            return None
        return json.loads(value_str)

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        ttl = ttl or self.ttl
        expired_at = int((datetime.now() + timedelta(seconds=ttl)).timestamp())
        self.conn.execute(
            "REPLACE INTO cache (key, value, expired_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False, default=str), expired_at)
        )
        self.conn.commit()


# 全局缓存实例
_default_cache: Optional[Cache] = None


def cache() -> Cache:
    global _default_cache
    if _default_cache is None:
        _default_cache = Cache()
    return _default_cache


def cached(key: str, ttl: int = 3600):
    """装饰器：缓存函数结果（akshare 慢查询必备）"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            k = f"{key}:{args}:{tuple(sorted(kwargs.items()))}"
            c = cache()
            hit = c.get(k)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            c.set(k, result, ttl=ttl)
            return result
        return wrapper
    return decorator
