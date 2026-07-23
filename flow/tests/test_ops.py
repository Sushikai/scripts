"""运维测试:launchd plist + tunnel + access.log + health。"""

from pathlib import Path


def test_run_sh_exists():
    """run.sh 启动脚本存在。"""
    p = Path("/Users/kaikai/scripts/flow/run.sh")
    assert p.exists()


def test_run_tunnel_sh_exists():
    """run_tunnel.sh 隧道脚本存在。"""
    p = Path("/Users/kaikai/scripts/flow/run_tunnel.sh")
    assert p.exists()


def test_launchd_plist_exists():
    """launchd plist 守护配置存在。"""
    p = Path("/Users/kaikai/scripts/flow/launchd/com.kaikai.flow.plist")
    assert p.exists()
    text = p.read_text()
    assert "KeepAlive" in text
    assert "8810" in text


def test_env_example_exists():
    p = Path("/Users/kaikai/scripts/flow/.env.example")
    assert p.exists()


def test_readme_exists():
    p = Path("/Users/kaikai/scripts/flow/README.md")
    assert p.exists()
    text = p.read_text()
    assert "8810" in text
    assert "flow" in text.lower()


def test_requirements_txt_exists():
    p = Path("/Users/kaikai/scripts/flow/requirements.txt")
    assert p.exists()


def test_pyproject_toml_exists():
    p = Path("/Users/kaikai/scripts/flow/pyproject.toml")
    assert p.exists()


def test_gitignore_exists():
    p = Path("/Users/kaikai/scripts/flow/.gitignore")
    assert p.exists()
    text = p.read_text()
    assert "*.db" in text or "*.sqlite" in text


def test_access_log_written(client, flow_server):
    """请求后 access.log 有 JSON 行。"""
    client.get("/api/health")
    import time
    time.sleep(0.2)
    # conftest 已经把 access log 重定向到 ROOT/data/test_access.log
    log_path = Path(flow_server["base"]).parent  # 不准
    log_path = Path(__file__).resolve().parent.parent / "data" / "test_access.log"
    assert log_path.exists()
    text = log_path.read_text(errors="ignore")
    # 至少有一行 JSON
    assert '"path"' in text
    assert '"status"' in text


def test_tunnel_url_env_var():
    """tunnel_url.txt 存在(可选内容)。"""
    p = Path("/Users/kaikai/scripts/tunnel_url.txt")
    # 不强制存在,但若存在应是 https://
    if p.exists():
        text = p.read_text().strip()
        if text:
            assert text.startswith("http"), f"tunnel URL should start with http, got {text}"


def test_health_endpoint_full(client):
    """/api/health 返回 status=ok + cache stats。"""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert "cache" in body["data"]


def test_metrics_summary(client):
    """concurrent 请求下 server 不挂。"""
    import concurrent.futures
    def hit():
        r = client.get("/api/health")
        return r.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        statuses = list(ex.map(lambda _: hit(), range(20)))
    assert all(s == 200 for s in statuses), f"some failed: {statuses}"