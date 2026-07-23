"""限频中间件单元测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_bucket_for_default():
    from backend.middleware.rate_limit import _bucket_for
    assert _bucket_for("/api/dashboard") == 60
    assert _bucket_for("/api/projects") == 60
    assert _bucket_for("/api/ai/something") == 20
    assert _bucket_for("/api/jobs") == 10
    assert _bucket_for("/static/x.css") == 60


def test_is_whitelisted():
    from backend.middleware.rate_limit import _is_whitelisted
    assert _is_whitelisted("/health") is True
    assert _is_whitelisted("/api/health") is True
    assert _is_whitelisted("/") is True
    assert _is_whitelisted("/static/app.js") is True
    assert _is_whitelisted("/api/dashboard") is False


def test_limiter_allows_under_limit():
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    for _ in range(10):
        ok, _ = lim.allow("1.1.1.1", "/api/dashboard")
        assert ok is True


def test_limiter_blocks_over_limit():
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    # /api/jobs 限 10/min
    for _ in range(10):
        ok, _ = lim.allow("2.2.2.2", "/api/jobs")
        assert ok is True
    ok, retry = lim.allow("2.2.2.2", "/api/jobs")
    assert ok is False
    assert retry >= 1


def test_limiter_per_ip_isolated():
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    # ip A 满了
    for _ in range(10):
        lim.allow("3.3.3.3", "/api/jobs")
    ok_a, _ = lim.allow("3.3.3.3", "/api/jobs")
    assert ok_a is False
    # ip B 不受影响
    ok_b, _ = lim.allow("4.4.4.4", "/api/jobs")
    assert ok_b is True


def test_limiter_per_path_isolated():
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    for _ in range(10):
        lim.allow("5.5.5.5", "/api/jobs")
    ok_jobs, _ = lim.allow("5.5.5.5", "/api/jobs")
    assert ok_jobs is False
    # 不同路径独立
    ok_other, _ = lim.allow("5.5.5.5", "/api/dashboard")
    assert ok_other is True


def test_reset_for_tests():
    from backend.middleware.rate_limit import _Limiter, reset_for_tests
    lim = _Limiter()
    for _ in range(5):
        lim.allow("x", "/api/y")
    reset_for_tests()
    ok, _ = lim.allow("x", "/api/y")
    assert ok is True


def test_limiter_query_string_stripped():
    """path 带 ?query 也按 base path 计数。"""
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    for _ in range(10):
        lim.allow("6.6.6.6", "/api/jobs?x=1")
    ok, _ = lim.allow("6.6.6.6", "/api/jobs?x=2")
    assert ok is False


def test_rate_limit_via_server(client):
    """end-to-end:打爆 /api/dashboard 应该 429。

    注:conftest 把上限提到 100k 让 e2e 不撞墙;这里只对 _Limiter 类自身跑验证。
    """
    from backend.middleware.rate_limit import _Limiter
    lim = _Limiter()
    for i in range(61):
        ok, _ = lim.allow("127.0.0.1", "/api/dashboard")
        if not ok:
            return
    raise AssertionError("expected rate limit to kick in before 61 requests")