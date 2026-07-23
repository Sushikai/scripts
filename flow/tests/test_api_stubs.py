"""新增 API stub 端点测试。"""

def test_dashboard_stats(client):
    """GET /api/dashboard 返回 4 项 KPI。"""
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    stats = body["data"]["stats"]
    assert "projects_total" in stats
    assert "jobs_today" in stats
    assert "uploads_today" in stats
    assert "success_rate" in stats


def test_assets_empty(client):
    """GET /api/assets 返回空列表(stub)。"""
    r = client.get("/api/assets")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 0


def test_accounts_empty(client):
    """GET /api/accounts 返回空列表(stub)。"""
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0


def test_uploads_empty(client):
    """GET /api/uploads 返回空列表(stub)。"""
    r = client.get("/api/uploads")
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0


def test_log_recent(client):
    """GET /api/log/recent 返回 access.log 末尾。"""
    r = client.get("/api/log/recent?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "lines" in body["data"]