"""cache store 单元测试。"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fresh_cache():
    tmp = Path(tempfile.mkdtemp(prefix="flow_cache_"))
    os.environ["FLOW_CACHE_DB"] = str(tmp / "cache.db")
    import importlib
    import backend.cache.store as s
    importlib.reload(s)
    return s


def test_set_and_get():
    s = _fresh_cache()
    s.set_("k1", {"hello": "world"}, ttl=60)
    assert s.get("k1") == {"hello": "world"}


def test_get_missing_returns_none():
    s = _fresh_cache()
    assert s.get("missing") is None


def test_expired_returns_none():
    s = _fresh_cache()
    s.set_("k2", {"x": 1}, ttl=0)
    # ttl=0 表示立刻过期
    time.sleep(0.05)
    assert s.get("k2") is None


def test_delete():
    s = _fresh_cache()
    s.set_("k3", "v", ttl=60)
    s.delete("k3")
    assert s.get("k3") is None


def test_clear_expired():
    s = _fresh_cache()
    s.set_("k4", "v", ttl=0)
    s.set_("k5", "v", ttl=60)
    time.sleep(0.05)
    cleared = s.clear_expired()
    assert cleared >= 1
    assert s.get("k5") == "v"


def test_cache_or_compute_hit():
    s = _fresh_cache()
    s.set_("hit", 42, ttl=60)
    val, was_hit = s.cache_or_compute("hit", lambda: 999, ttl=60)
    assert val == 42
    assert was_hit is True


def test_cache_or_compute_miss():
    s = _fresh_cache()
    val, was_hit = s.cache_or_compute("miss", lambda: 123, ttl=60)
    assert val == 123
    assert was_hit is False
    assert s.get("miss") == 123


def test_stats():
    s = _fresh_cache()
    s.set_("a", 1, ttl=60)
    s.set_("b", 2, ttl=60)
    st = s.stats()
    assert st["total"] == 2
    assert st["backend"] == "sqlite"


def test_complex_value():
    s = _fresh_cache()
    payload = {"list": [1, 2, 3], "nested": {"a": [4, 5]}, "unicode": "中文 🎬"}
    s.set_("cplx", payload, ttl=60)
    assert s.get("cplx") == payload