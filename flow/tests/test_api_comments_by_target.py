"""/api/comments/by-target 端点测试。"""

import json
import urllib.request


def test_by_target_shape(client):
    """GET /api/comments/by-target 返回 items。"""
    r = client.get("/api/comments/by-target")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("items", "count", "limit"):
        assert k in data


def test_by_target_item_shape(client):
    """每个 item 有 uid/uname/total/likes/replies/follows/dms/first_seen/last_seen/video_count。"""
    body = client.get("/api/comments/by-target").json()
    items = body["data"]["items"]
    if items:
        first = items[0]
        for k in ("uid", "uname", "total", "likes", "replies", "follows", "dms", "first_seen", "last_seen", "video_count"):
            assert k in first
        assert isinstance(first["total"], int)


def test_by_target_sorted(client):
    """items 按 total 倒序。"""
    body = client.get("/api/comments/by-target").json()
    items = body["data"]["items"]
    if items:
        totals = [x["total"] for x in items]
        assert totals == sorted(totals, reverse=True)


def test_by_target_limit_param(client):
    """?limit=N 限制返回数。"""
    body = client.get("/api/comments/by-target?limit=5").json()
    assert len(body["data"]["items"]) <= 5
    assert body["data"]["limit"] == 5


def test_by_target_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/comments/by-target?limit=10").read())
    assert body["ok"] is True
    assert len(body["data"]["items"]) <= 10


def test_target_detail(client):
    """单 uid 详情返 by_action + videos + records。"""
    body = client.get("/api/comments/by-target?limit=20").json()
    items = body["data"]["items"]
    if items:
        uid = items[0]["uid"]
        detail = client.get("/api/comments/by-target/" + uid).json()
        assert detail["ok"] is True
        d = detail["data"]
        for k in ("uid", "uname", "total", "by_action", "videos", "records"):
            assert k in d