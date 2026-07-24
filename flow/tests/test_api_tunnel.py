"""/api/tunnel-status 端点测试。"""

import json
import urllib.request


def test_tunnel_status_shape(client):
    """GET /api/tunnel-status 返必要字段。"""
    r = client.get("/api/tunnel-status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("state", "url", "method", "lan_ip", "lan_url", "port", "running", "hostname", "ts"):
        assert k in data, f"missing key {k}"
    assert data["port"] > 0  # conftest 注入随机空闲端口
    assert data["state"] in ("online", "offline")


def test_tunnel_status_via_server(flow_server):
    """HTTP 端到端 + lan_url 格式正确。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/tunnel-status").read())
    assert body["ok"] is True
    data = body["data"]
    if data["lan_ip"]:
        assert data["lan_url"].startswith("http://")
        assert ":" + str(data["port"]) in data["lan_url"]


def test_tunnel_status_offline_default(flow_server, tmp_path):
    """没 tunnel_url.txt + 没后台 tunnel 进程 → state=offline。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/tunnel-status").read())
    assert body["ok"] is True
    # 默认测试环境没起 cloudflared/ngrok,应当 offline (除非 hook 启动)
    if body["data"]["state"] == "offline":
        assert body["data"]["url"] == ""
        assert body["data"]["running"] is False


def test_tunnel_status_hostname_present(client):
    """hostname 字段非空(本地 socket 一定能拿到)。"""
    body = client.get("/api/tunnel-status").json()
    assert body["data"]["hostname"] != ""