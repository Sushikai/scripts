"""缩略图 LRU 缓存(内存版,生产可加 Redis 共享层)。"""

from __future__ import annotations

import threading
from collections import OrderedDict


class ThumbCache:
    """线程安全 LRU,固定容量,evict 最久未访问。"""

    def __init__(self, max_items: int = 1024):
        self.max_items = max(1, int(max_items))
        self._d: "OrderedDict[str, object]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            if key not in self._d:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return self._d[key]

    def set(self, key: str, value) -> None:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                self._d[key] = value
                return
            self._d[key] = value
            if len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._d),
                "max": self.max_items,
                "hits": self.hits,
                "misses": self.misses,
            }


# 全局单例(预留 L1)
_global_cache: ThumbCache | None = None


def global_cache() -> ThumbCache:
    global _global_cache
    if _global_cache is None:
        _global_cache = ThumbCache(max_items=1024)
    return _global_cache