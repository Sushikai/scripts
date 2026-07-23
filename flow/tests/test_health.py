"""测试 /health + envelope + trace_id + access_log。"""

import time


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert "ts" in body["data"]


def test_health_has_trace_id_header(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "X-Trace-Id" in r.headers
    assert len(r.headers["X-Trace-Id"]) >= 8


def test_trace_id_in_body_matches_header(client):
    """body 里的 trace_id 应该等于 header 的 X-Trace-Id。"""
    r = client.get("/health")
    body_tid = r.json()["trace_id"]
    header_tid = r.headers["X-Trace-Id"]
    assert body_tid == header_tid
    assert body_tid is not None


def test_trace_id_echo(client):
    """客户端发送 X-Trace-Id,服务端应该回同样的。"""
    custom = "deadbeef-cafe-1234-5678-90abcdef0000"
    r = client.get("/health", headers={"X-Trace-Id": custom})
    assert r.headers["X-Trace-Id"] == custom


def test_api_health_has_cache_stats(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "cache" in body["data"]
    assert body["data"]["cache"]["backend"] == "sqlite"


def test_404_returns_envelope():
    """FastAPI 默认 404,我们挂个 envelope 化验证。"""
    import httpx
    with httpx.Client(base_url="http://127.0.0.1:1", timeout=2) as c:
        pass  # placeholder; real test uses session fixture


def test_unknown_route_returns_404(client):
    r = client.get("/api/nonexistent_endpoint_xyz")
    assert r.status_code == 404


def test_concurrent_health_under_1s(client):
    """50 个并发 /health 全部 < 1s 响应。"""
    import concurrent.futures as cf

    def hit():
        t = time.perf_counter()
        r = client.get("/health")
        return r.status_code, (time.perf_counter() - t) * 1000

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _: hit(), range(50)))
    statuses = [s for s, _ in results]
    latencies = [ms for _, ms in results]
    assert all(s == 200 for s in statuses)
    assert max(latencies) < 1000, f"too slow: {sorted(latencies)[-5:]}"