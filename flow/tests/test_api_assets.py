"""/api/assets 端点测试。"""

import json
import urllib.request


def test_assets_shape(client):
    """GET /api/assets 返回 items + by_source + roots。"""
    r = client.get("/api/assets")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("items", "count", "by_source", "roots"):
        assert k in data


def test_assets_item_shape(client):
    """每个 item 有 source/path/name/title/kind/ext/size_bytes/size_human/mtime。"""
    body = client.get("/api/assets").json()
    if body["data"]["items"]:
        first = body["data"]["items"][0]
        for k in ("source", "path", "name", "kind", "size_bytes", "size_human", "mtime"):
            assert k in first
        assert first["kind"] in ("video", "audio", "image")


def test_assets_filter_source(client):
    """?source=info_gap 过滤。"""
    body = client.get("/api/assets?source=info_gap").json()
    assert body["ok"] is True
    for it in body["data"]["items"]:
        assert it["source"] == "info_gap"


def test_assets_filter_kind(client):
    """?kind=audio 只留音频。"""
    body = client.get("/api/assets?kind=audio").json()
    assert body["ok"] is True
    for it in body["data"]["items"]:
        assert it["kind"] == "audio"


def test_assets_limit_param(client):
    """?limit=5 限制条数。"""
    body = client.get("/api/assets?limit=5").json()
    assert body["ok"] is True
    assert len(body["data"]["items"]) <= 5


def test_assets_roots_metadata(client):
    """roots 字段标注每个根的存在性。"""
    body = client.get("/api/assets").json()
    roots = body["data"]["roots"]
    assert len(roots) >= 3
    for r in roots:
        assert "source" in r
        assert "path" in r
        assert "exists" in r


def test_assets_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/assets").read())
    assert body["ok"] is True
    assert "items" in body["data"]