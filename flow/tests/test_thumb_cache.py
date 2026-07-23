"""缩略图缓存测试:thumb 服务 + LRU 命中。

注:Batch 4 实装 ffmpeg 缩略图;当前先做接口契约 + 占位缓存。
"""

import io
from pathlib import Path


def test_thumb_cache_module_importable():
    """thumb_cache 模块可 import。"""
    from backend.services import thumb_cache
    assert hasattr(thumb_cache, "ThumbCache")


def test_thumb_cache_lru_basic():
    """LRU 行为:插入满后取旧值。"""
    from backend.services.thumb_cache import ThumbCache
    cache = ThumbCache(max_items=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") == 1  # a 移到末尾
    cache.set("d", 4)  # 满,b 是最旧的,b 被驱逐
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert cache.get("d") == 4


def test_thumb_cache_hit_miss_counter():
    """hit/miss 计数器。"""
    from backend.services.thumb_cache import ThumbCache
    cache = ThumbCache(max_items=10)
    cache.set("a", 1)
    cache.get("a")  # hit
    cache.get("b")  # miss
    assert cache.hits == 1
    assert cache.misses == 1


def test_thumb_cache_clear():
    """clear 重置所有状态。"""
    from backend.services.thumb_cache import ThumbCache
    cache = ThumbCache(max_items=10)
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None
    assert cache.hits == 0
    assert cache.misses == 1


def test_thumb_cache_get_none_safe():
    """get 不存在的 key 不抛错。"""
    from backend.services.thumb_cache import ThumbCache
    cache = ThumbCache(max_items=10)
    assert cache.get("missing") is None