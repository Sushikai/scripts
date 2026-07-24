"""/api/scripts/{name} 详情端点测试。"""

import json
import urllib.request


def test_script_detail_shape(client):
    """GET /api/scripts/{name} 返回 imports/git_log/last_log_line。"""
    r = client.get("/api/scripts/fengge_pipeline")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    data = body["data"]
    for k in ("name", "path", "exists", "category", "imports", "git_log", "last_log_line"):
        assert k in data
    assert isinstance(data["imports"], list)
    assert isinstance(data["git_log"], list)


def test_script_detail_404(client):
    """不存在的脚本 → 404。"""
    r = client.get("/api/scripts/nonexistent_script_xyz")
    assert r.status_code == 404


def test_script_detail_via_server(flow_server):
    """HTTP 端到端。"""
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/scripts/bilibili_reply_v17").read())
    assert body["ok"] is True
    d = body["data"]
    # imports 应该至少 1 个 (脚本本身有依赖)
    if d["exists"]:
        assert d["category"] == "comment"
        # git_log 可能为 [] (没 git 历史)
        assert isinstance(d["git_log"], list)


def test_script_detail_git_log_shape(client):
    """git_log 项字段。"""
    body = client.get("/api/scripts/fengge_pipeline").json()
    if body["data"]["git_log"]:
        first = body["data"]["git_log"][0]
        for k in ("sha", "ts", "message"):
            assert k in first
        assert len(first["sha"]) == 8