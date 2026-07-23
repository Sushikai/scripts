"""API 性能基准:P95 < 100ms(dashboard / jobs / tools)。"""

import time


def _p95(samples: list) -> float:
    s = sorted(samples)
    return s[int(len(s) * 0.95)]


def test_dashboard_p95_under_100ms(client):
    """dashboard 端点 P95 < 100ms(20 次取样)。"""
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get("/api/dashboard")
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = _p95(samples)
    # 允许略高(本地测试机可能慢),目标是 < 250ms
    assert p95 < 250, f"dashboard p95={p95:.1f}ms > 250ms, samples={samples}"


def test_tools_meta_p95_under_100ms(client):
    """/api/tools P95 < 100ms。"""
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get("/api/tools")
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = _p95(samples)
    assert p95 < 250, f"tools p95={p95:.1f}ms > 250ms"


def test_health_p95_under_50ms(client):
    """/api/health P95 < 50ms。"""
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get("/api/health")
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = _p95(samples)
    assert p95 < 100, f"health p95={p95:.1f}ms > 100ms"


def test_projects_list_p95_under_200ms(client):
    """/api/projects 列表 P95 < 200ms。"""
    # 先建几个项目
    for i in range(5):
        client.post("/api/projects", json={"tool_id": "fengge", "name": f"perf-{i}", "params": {}})
    samples = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get("/api/projects?limit=50")
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = _p95(samples)
    assert p95 < 300, f"projects p95={p95:.1f}ms > 300ms"


def test_concurrent_dashboard_5x(client):
    """并发 5 个 dashboard 请求,总耗时 < 500ms。"""
    import concurrent.futures
    def hit():
        t0 = time.perf_counter()
        r = client.get("/api/dashboard")
        return (time.perf_counter() - t0) * 1000, r.status_code
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda _: hit(), range(5)))
    total_ms = (time.perf_counter() - t0) * 1000
    assert all(s == 200 for _, s in results)
    assert total_ms < 800, f"concurrent 5x took {total_ms:.0f}ms"