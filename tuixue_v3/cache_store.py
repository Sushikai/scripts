"""
tuixue_v3/cache_store.py
统一 K/V 存储层 — Redis 主用 + SQLite 兜底,内置探活 + 自动恢复。

设计目标:
- 毫秒级响应 (Redis):所有 hot path(实时行情/日线缓存/资金流)走 Redis
- 自动降级:Redis ping 失败 ×3 → 自动走 SQLite kv_store 表,服务不中断
- 后台探活:daemon thread 每 30s PING,恢复后无缝切回 Redis
- TTL:所有 set/hset/zadd 强制带 ttl 参数(秒);SQLite fallback 用 expires_at REAL
- 跨进程共享:多个 worker / FastAPI 进程共享同一份 Redis 数据
- 序列化:JSON 默认,可选 msgpack(已安装)
- 统计:hit/miss/fallback_count 暴露给 /api/health

key 命名:所有 key 加 `tx3:` 前缀,SQLite 表 `kv_store` 也用同前缀

v1.0 (2026-07-11):初始版,Redis 8.6 + sqlite 3 fallback
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time as systime
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

try:
    import redis
    from redis.exceptions import ConnectionError as RedisConnError
    from redis.exceptions import TimeoutError as RedisTimeoutError
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    RedisConnError = Exception
    RedisTimeoutError = Exception

log = logging.getLogger("tuixue_v3.store")

DEFAULT_PREFIX = "tx3:"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_TTL = 3600
PING_TIMEOUT = 0.5
PING_BG_INTERVAL = 30.0
PING_FAIL_THRESHOLD = 3
USE_REDIS_ENV = "TUIXUE_USE_REDIS"

# SQLite fallback 路径 (与 cache.db 分离,独立维护)
_FALLBACK_DB = Path(__file__).resolve().parent / "data" / "cache_store_fallback.db"


# ═══════════════════════════════════════════════════
# SQLite fallback 层
# ═══════════════════════════════════════════════════
class _SqliteFallback:
    """kv_store / kv_hash / kv_zset 三张表 — 与 cache.db 分离。

    schema:
      kv_store(key TEXT PRIMARY KEY, value BLOB, expires_at REAL)
      kv_hash(key TEXT, field TEXT, value BLOB, expires_at REAL, PRIMARY KEY(key,field))
      kv_zset(key TEXT, score REAL, value BLOB, expires_at REAL, PRIMARY KEY(key,score,value))
    """
    def __init__(self, db_path: Path = _FALLBACK_DB):
        self.path = db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv_store(expires_at);

                CREATE TABLE IF NOT EXISTS kv_hash (
                    key TEXT NOT NULL,
                    field TEXT NOT NULL,
                    value BLOB,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (key, field)
                );
                CREATE INDEX IF NOT EXISTS idx_hash_expires ON kv_hash(expires_at);

                CREATE TABLE IF NOT EXISTS kv_zset (
                    key TEXT NOT NULL,
                    score REAL NOT NULL,
                    value BLOB,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (key, score, value)
                );
                CREATE INDEX IF NOT EXISTS idx_zset_key ON kv_zset(key, score);
            """)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── 通用 ──
    def _now(self) -> float:
        return systime.time()

    def _expired(self, expires_at: float) -> bool:
        return expires_at > 0 and expires_at < self._now()

    # ── kv_store ──
    def set(self, key: str, value: bytes, ttl: int) -> None:
        expires_at = self._now() + ttl if ttl > 0 else 0
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value, expires_at),
            )

    def get(self, key: str) -> bytes | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value, expires_at FROM kv_store WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if self._expired(row[1]):
            self.delete(key)
            return None
        return row[0]

    def delete(self, key: str) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM kv_store WHERE key=?", (key,))
            return cur.rowcount > 0

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def ttl(self, key: str) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT expires_at FROM kv_store WHERE key=?", (key,)).fetchone()
        if not row:
            return -2
        exp = row[0]
        if exp <= 0:
            return -1
        return max(0, int(exp - self._now()))

    def mget(self, keys: list[str]) -> dict[str, bytes]:
        out = {}
        for k in keys:
            v = self.get(k)
            if v is not None:
                out[k] = v
        return out

    def scan(self, match: str) -> list[str]:
        # match 转 LIKE pattern:% 替换 *
        like = match.replace("*", "%")
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM kv_store WHERE key LIKE ? AND (expires_at=0 OR expires_at>?)",
                (like, self._now()),
            ).fetchall()
        return [r[0] for r in rows]

    # ── kv_hash ──
    def hset(self, key: str, field: str, value: bytes, ttl: int) -> None:
        expires_at = self._now() + ttl if ttl > 0 else 0
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_hash (key, field, value, expires_at) VALUES (?, ?, ?, ?)",
                (key, field, value, expires_at),
            )

    def hget(self, key: str, field: str) -> bytes | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM kv_hash WHERE key=? AND field=?", (key, field)
            ).fetchone()
        if not row:
            return None
        if self._expired(row[1]):
            self.hdel(key, field)
            return None
        return row[0]

    def hgetall(self, key: str) -> dict[str, bytes]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT field, value, expires_at FROM kv_hash WHERE key=? AND (expires_at=0 OR expires_at>?)",
                (key, self._now()),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def hdel(self, key: str, *fields: str) -> int:
        if not fields:
            return 0
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                f"DELETE FROM kv_hash WHERE key=? AND field IN ({','.join('?'*len(fields))})",
                (key, *fields),
            )
            return cur.rowcount

    # ── kv_zset ──
    def zadd(self, key: str, score: float, value: bytes, ttl: int) -> None:
        expires_at = self._now() + ttl if ttl > 0 else 0
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_zset (key, score, value, expires_at) VALUES (?, ?, ?, ?)",
                (key, score, value, expires_at),
            )

    def zrange(self, key: str, start: int = 0, end: int = -1) -> list[tuple[float, bytes]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT score, value, expires_at FROM kv_zset WHERE key=? AND (expires_at=0 OR expires_at>?) ORDER BY score ASC",
                (key, self._now()),
            ).fetchall()
        if end == -1:
            end = len(rows)
        return [(r[0], r[1]) for r in rows[start:end+1]]

    def zremrangebyscore(self, key: str, min_: float, max_: float) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM kv_zset WHERE key=? AND score BETWEEN ? AND ?",
                (key, min_, max_),
            )
            return cur.rowcount

    # ── 维护 ──
    def vacuum(self) -> int:
        """清理过期 key,返回删除条数。"""
        now = self._now()
        with self._lock, self._conn() as conn:
            c1 = conn.execute("DELETE FROM kv_store WHERE expires_at>0 AND expires_at<?", (now,)).rowcount
            c2 = conn.execute("DELETE FROM kv_hash WHERE expires_at>0 AND expires_at<?", (now,)).rowcount
            c3 = conn.execute("DELETE FROM kv_zset WHERE expires_at>0 AND expires_at<?", (now,)).rowcount
        return c1 + c2 + c3


# ═══════════════════════════════════════════════════
# CacheStore 主类
# ═══════════════════════════════════════════════════
class CacheStore:
    """统一 K/V 抽象。Redis 主用,SQLite 兜底。"""

    def __init__(
        self,
        redis_url: str = DEFAULT_REDIS_URL,
        prefix: str = DEFAULT_PREFIX,
        fallback_db: Path | None = _FALLBACK_DB,
    ):
        self.prefix = prefix
        self.fallback = _SqliteFallback(fallback_db) if fallback_db else None
        self._redis: "redis.Redis | None" = None
        self._redis_available = False
        self._ping_fail_count = 0
        self._lock = threading.Lock()

        # 统计
        self._hits = 0
        self._miss = 0
        self._fallback_count = 0
        self._redis_ops = 0
        self._sqlite_ops = 0
        self._latency_ms = 0.0

        # 后台探活
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None

        # 总开关 (回滚用)
        self._enabled = os.environ.get(USE_REDIS_ENV, "1") == "1"

        if self._enabled and _HAS_REDIS:
            try:
                self._redis = redis.Redis.from_url(
                    redis_url,
                    socket_timeout=PING_TIMEOUT,
                    socket_connect_timeout=PING_TIMEOUT,
                    decode_responses=False,
                    retry_on_timeout=False,
                    health_check_interval=0,
                )
                self._try_redis()
            except Exception as e:
                log.warning(f"Redis 初始化失败: {e},启用 SQLite fallback")
                self._redis = None
        else:
            if not self._enabled:
                log.info(f"CacheStore: 环境变量 {USE_REDIS_ENV}!=1,跳过 Redis,纯 SQLite")
            else:
                log.warning("CacheStore: redis 库未安装,纯 SQLite fallback")
            self._redis = None

        if self._redis_available:
            self._start_bg_ping()

    # ── 生命周期 ──
    def _start_bg_ping(self) -> None:
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._bg_thread = threading.Thread(target=self._bg_ping_loop, daemon=True, name="cache-store-ping")
        self._bg_thread.start()

    def _bg_ping_loop(self) -> None:
        while not self._stop_event.wait(PING_BG_INTERVAL):
            self._try_redis()

    def stop(self) -> None:
        self._stop_event.set()
        if self._bg_thread:
            self._bg_thread.join(timeout=2)

    def _try_redis(self) -> bool:
        """Inline ping (cheap, 0.5s timeout)。所有 get/set 入口调用。"""
        if not self._redis:
            return False
        try:
            t0 = systime.monotonic()
            self._redis.ping()
            self._latency_ms = (systime.monotonic() - t0) * 1000
            if not self._redis_available:
                log.info("CacheStore: Redis 已恢复,切回主用")
            self._redis_available = True
            self._ping_fail_count = 0
            return True
        except (RedisConnError, RedisTimeoutError, OSError) as e:
            self._ping_fail_count += 1
            if self._redis_available and self._ping_fail_count >= PING_FAIL_THRESHOLD:
                log.warning(f"CacheStore: Redis 不可用 ({e}),降级 SQLite fallback")
                self._redis_available = False
            elif self._ping_fail_count == 1:
                log.debug(f"CacheStore: Redis ping fail ({e})")
            return False

    # ── 内部 helper ──
    def _k(self, key: str) -> str:
        return key if key.startswith(self.prefix) else self.prefix + key

    def _uk(self, key: str) -> str:
        return key[len(self.prefix):] if key.startswith(self.prefix) else key

    def _encode(self, v: Any) -> bytes:
        try:
            return json.dumps(v, ensure_ascii=False, default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            log.warning(f"_encode 失败: {e},fallback repr")
            return repr(v).encode("utf-8")

    def _decode(self, raw: bytes | None) -> Any:
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return raw.decode("utf-8", errors="ignore")

    def _record_redis(self) -> None:
        with self._lock:
            self._redis_ops += 1

    def _record_sqlite(self) -> None:
        with self._lock:
            self._sqlite_ops += 1
            self._fallback_count += 1

    def _record_hit(self) -> None:
        with self._lock:
            self._hits += 1

    def _record_miss(self) -> None:
        with self._lock:
            self._miss += 1

    # ── K/V ──
    def get(self, key: str) -> Any | None:
        k = self._k(key)
        if self._try_redis():
            try:
                raw = self._redis.get(k)
                self._record_redis()
                if raw is None:
                    self._record_miss()
                    return None
                self._record_hit()
                return self._decode(raw)
            except Exception as e:
                log.debug(f"Redis get {k} 失败: {e}")
        if self.fallback:
            raw = self.fallback.get(k)
            self._record_sqlite()
            if raw is None:
                self._record_miss()
                return None
            self._record_hit()
            return self._decode(raw)
        self._record_miss()
        return None

    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL) -> bool:
        k = self._k(key)
        v = self._encode(value)
        ok = False
        if self._try_redis():
            try:
                if ttl > 0:
                    self._redis.set(k, v, ex=ttl)
                else:
                    self._redis.set(k, v)
                self._record_redis()
                ok = True
            except Exception as e:
                log.debug(f"Redis set {k} 失败: {e}")
        if not ok and self.fallback:
            self.fallback.set(k, v, ttl)
            self._record_sqlite()
            ok = True
        return ok

    def delete(self, key: str) -> bool:
        k = self._k(key)
        ok = False
        if self._try_redis():
            try:
                self._redis.delete(k)
                self._record_redis()
                ok = True
            except Exception as e:
                log.debug(f"Redis delete {k} 失败: {e}")
        if self.fallback:
            self.fallback.delete(k)
            self._record_sqlite()
            ok = True
        return ok

    def exists(self, key: str) -> bool:
        k = self._k(key)
        if self._try_redis():
            try:
                r = self._redis.exists(k) > 0
                self._record_redis()
                return r
            except Exception:
                pass
        if self.fallback:
            r = self.fallback.exists(k)
            self._record_sqlite()
            return r
        return False

    def ttl(self, key: str) -> int:
        k = self._k(key)
        if self._try_redis():
            try:
                r = self._redis.ttl(k)
                self._record_redis()
                return int(r)
            except Exception:
                pass
        if self.fallback:
            r = self.fallback.ttl(k)
            self._record_sqlite()
            return r
        return -2

    def expire(self, key: str, ttl: int) -> bool:
        k = self._k(key)
        ok = False
        if self._try_redis():
            try:
                self._redis.expire(k, ttl)
                self._record_redis()
                ok = True
            except Exception:
                pass
        # SQLite fallback 不单独支持 expire(只能 set 时一起),这里 best-effort
        return ok

    def getset(self, key: str, default: Any, ttl: int) -> Any:
        """GET 命中返值;未命中 → 写入 default 并返回 default。"""
        v = self.get(key)
        if v is not None:
            return v
        self.set(key, default, ttl=ttl)
        return default

    # ── Hash ──
    def hset(self, key: str, field: str, value: Any, ttl: int = DEFAULT_TTL) -> bool:
        """注意:Redis HSET 不带 key 级 TTL;每次 hset 时刷新整个 key 的 TTL。"""
        k = self._k(key)
        v = self._encode(value)
        ok = False
        if self._try_redis():
            try:
                pipe = self._redis.pipeline()
                pipe.hset(k, field, v)
                if ttl > 0:
                    pipe.expire(k, ttl)
                pipe.execute()
                self._record_redis()
                ok = True
            except Exception as e:
                log.debug(f"Redis hset {k}/{field} 失败: {e}")
        if not ok and self.fallback:
            self.fallback.hset(k, field, v, ttl)
            self._record_sqlite()
            ok = True
        return ok

    def hget(self, key: str, field: str) -> Any | None:
        k = self._k(key)
        if self._try_redis():
            try:
                raw = self._redis.hget(k, field)
                self._record_redis()
                if raw is None:
                    self._record_miss()
                    return None
                self._record_hit()
                return self._decode(raw)
            except Exception:
                pass
        if self.fallback:
            raw = self.fallback.hget(k, field)
            self._record_sqlite()
            if raw is None:
                self._record_miss()
                return None
            self._record_hit()
            return self._decode(raw)
        self._record_miss()
        return None

    def hgetall(self, key: str) -> dict[str, Any]:
        k = self._k(key)
        if self._try_redis():
            try:
                raw = self._redis.hgetall(k)
                self._record_redis()
                if not raw:
                    self._record_miss()
                    return {}
                self._record_hit()
                return {f.decode() if isinstance(f, bytes) else f: self._decode(v) for f, v in raw.items()}
            except Exception:
                pass
        if self.fallback:
            raw = self.fallback.hgetall(k)
            self._record_sqlite()
            if not raw:
                self._record_miss()
                return {}
            self._record_hit()
            return {f: self._decode(v) for f, v in raw.items()}
        self._record_miss()
        return {}

    def hdel(self, key: str, *fields: str) -> int:
        k = self._k(key)
        n = 0
        if self._try_redis():
            try:
                n = self._redis.hdel(k, *fields)
                self._record_redis()
                return n
            except Exception:
                pass
        if self.fallback:
            n = self.fallback.hdel(k, *fields)
            self._record_sqlite()
        return n

    # ── SortedSet ──
    def zadd(self, key: str, score: float, value: Any, ttl: int = DEFAULT_TTL) -> bool:
        k = self._k(key)
        v = self._encode(value)
        ok = False
        if self._try_redis():
            try:
                pipe = self._redis.pipeline()
                pipe.zadd(k, {v: score})
                if ttl > 0:
                    pipe.expire(k, ttl)
                pipe.execute()
                self._record_redis()
                ok = True
            except Exception:
                pass
        if not ok and self.fallback:
            self.fallback.zadd(k, score, v, ttl)
            self._record_sqlite()
            ok = True
        return ok

    def zrange(self, key: str, start: int = 0, end: int = -1) -> list[tuple[float, Any]]:
        k = self._k(key)
        if self._try_redis():
            try:
                raw = self._redis.zrange(k, start, end, withscores=True)
                self._record_redis()
                self._record_hit()
                return [(s, self._decode(v)) for v, s in raw]
            except Exception:
                pass
        if self.fallback:
            rows = self.fallback.zrange(k, start, end)
            self._record_sqlite()
            self._record_hit()
            return [(s, self._decode(v)) for s, v in rows]
        self._record_miss()
        return []

    def zremrangebyscore(self, key: str, min_: float, max_: float) -> int:
        k = self._k(key)
        n = 0
        if self._try_redis():
            try:
                n = self._redis.zremrangebyscore(k, min_, max_)
                self._record_redis()
                return n
            except Exception:
                pass
        if self.fallback:
            n = self.fallback.zremrangebyscore(k, min_, max_)
            self._record_sqlite()
        return n

    # ── 批 ──
    def mget(self, keys: list[str]) -> dict[str, Any]:
        out = {}
        for k in keys:
            v = self.get(k)
            if v is not None:
                out[k] = v
        return out

    def scan(self, match: str = "*", count: int = 200) -> list[str]:
        """返回去掉前缀的 key 列表(便于上层使用)。"""
        full = self.prefix + match.lstrip("*")
        if self._try_redis():
            try:
                keys = []
                cursor = 0
                while True:
                    cursor, batch = self._redis.scan(cursor=cursor, match=full, count=count)
                    keys.extend(b.decode() if isinstance(b, bytes) else b for b in batch)
                    if cursor == 0:
                        break
                self._record_redis()
                return [self._uk(k) for k in keys]
            except Exception:
                pass
        if self.fallback:
            keys = self.fallback.scan(match)
            self._record_sqlite()
            return [self._uk(k) for k in keys]
        return []

    def dbsize(self) -> int:
        if self._try_redis():
            try:
                return int(self._redis.dbsize())
            except Exception:
                pass
        if self.fallback:
            return len(self.fallback.scan("*"))
        return 0

    # ── 监控 ──
    def status(self) -> dict:
        """给 /api/health 用。"""
        info = {}
        if self._redis_available:
            try:
                info = self._redis.info("memory")
                dbsize = int(self._redis.dbsize())
            except Exception:
                info = {}
                dbsize = 0
            mem_used = int(info.get("used_memory", 0)) if info else 0
            return {
                "redis": True,
                "latency_ms": round(self._latency_ms, 2),
                "dbsize": dbsize,
                "memory_used_mb": round(mem_used / 1024 / 1024, 2),
                "ping_fail_count": self._ping_fail_count,
                "uptime": "ok",
            }
        return {
            "redis": False,
            "latency_ms": 0,
            "dbsize": 0,
            "memory_used_mb": 0,
            "ping_fail_count": self._ping_fail_count,
            "uptime": "down",
        }

    def stats(self) -> dict:
        """命中率统计。"""
        with self._lock:
            total = self._hits + self._miss
            return {
                "hits": self._hits,
                "miss": self._miss,
                "hit_rate_pct": round(self._hits / total * 100, 1) if total else 0,
                "redis_ops": self._redis_ops,
                "sqlite_ops": self._sqlite_ops,
                "fallback_count": self._fallback_count,
            }

    @property
    def redis_available(self) -> bool:
        return self._redis_available

    @property
    def enabled(self) -> bool:
        return self._enabled


# ═══════════════════════════════════════════════════
# 模块级单例 — 第一次 import 初始化
# ═══════════════════════════════════════════════════
_store: CacheStore | None = None
_store_lock = threading.Lock()


def get_store() -> CacheStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = CacheStore()
    return _store


# ═══════════════════════════════════════════════════
# 便捷命名空间 key (避免散写错字)
# ═══════════════════════════════════════════════════
class K:
    """集中 key 命名,避免散写。"""
    # 日线 (Hash: date → json)
    DAILY = "daily:{code}"                    # TTL 4h
    # 分时
    INTRADAY = "intraday:{code}"              # TTL 30min
    # 主线板块
    MAINLINE = "mainline:{date}"              # TTL 24h
    MAINLINE_LATEST = "mainline:latest"       # TTL 24h
    # 股票池
    STOCKLIST_FILTERED = "stocklist:filtered"  # TTL 24h
    STOCKLIST_ALL = "stocklist:all"           # TTL 24h
    # 实时行情
    QUOTE = "quote:{code}"                    # TTL 5s
    # 大盘指数
    IDX = "idx:{code}"                        # TTL 15s
    # 资金流
    FUND = "fund:{code}:{days}"               # TTL 60s
    # 全市场快照
    SPOT_ALL = "spot:all"                     # TTL 60s
    # K线
    KLINE = "kline:{code}:{days}"             # TTL 300s
    # 全球情绪
    GLOBAL_SENTIMENT = "global:sentiment"     # TTL 60s
    # AI verdict (Hash: model → json)
    AI = "ai:{date}:{code}"                   # TTL 至 23:59
    # 自选股
    WATCHLIST = "watchlist"                   # 永久
    WATCHLIST_AI = "watchlist_ai:{code}"      # TTL 至 23:59
    # 资金结构
    CAPITAL = "capital:{code}"                # TTL 60s
    # 新闻
    NEWS = "news:cache"                       # TTL 30min
    # 板块映射
    SECTOR = "sector:map"                     # TTL 24h
    # 席位
    SEAT_ALIASES = "seat:aliases"             # 永久
    SEAT_KNOWN = "seat:known"                 # 永久
    # 交易 (双写)
    TRADE = "trade:{id}"                      # 永久
    REVIEW = "review:{trade_id}"              # 永久


def ttl_until_midnight() -> int:
    """返回到今天 23:59:59 的秒数。"""
    from datetime import datetime, timedelta
    now = datetime.now()
    midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
    if midnight <= now:
        midnight += timedelta(days=1)
    return int((midnight - now).total_seconds()) + 60  # +60s 缓冲