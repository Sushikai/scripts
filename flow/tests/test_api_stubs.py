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


def test_assets_real(client):
    """GET /api/assets 扫描多个根,真实返回 items。"""
    r = client.get("/api/assets")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # 真实环境有素材 → count >= 1
    assert body["data"]["count"] >= 1
    assert "by_source" in body["data"]
    assert "roots" in body["data"]


def test_accounts_real_4_bilibili(client):
    """GET /api/accounts 返回 4 个 B 站账号(实装,读 cookie 文件)。"""
    r = client.get("/api/accounts")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 4
    platforms = {a["platform"] for a in body["data"]["items"]}
    assert "bilibili" in platforms


def test_uploads_aggregates_jobs(client):
    """GET /api/uploads 现在从 job artifacts 聚合,可能为空但格式正确。"""
    r = client.get("/api/uploads")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "count" in body["data"]
    assert "items" in body["data"]


def test_log_recent(client):
    """GET /api/log/recent 返回 access.log 末尾。"""
    r = client.get("/api/log/recent?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "lines" in body["data"]