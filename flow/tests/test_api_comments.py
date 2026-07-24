"""/api/comments + /api/accounts 端点测试。"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def test_list_accounts_returns_4(flow_server):
    """GET /api/accounts 应返回 4 个 B 站账号 + cookie 信息。"""
    import urllib.request
    req = urllib.request.urlopen(flow_server["base"] + "/api/accounts")
    body = json.loads(req.read())
    assert body["ok"] is True
    items = body["data"]["items"]
    assert len(items) == 4
    ids = [a["id"] for a in items]
    assert "100w" in ids and "travel" in ids and "rain" in ids and "leaf" in ids
    # 每条都有 status + cookie 子结构
    for a in items:
        assert a["status"] in ("ok", "warn", "bad")
        assert "cookie" in a
        assert "freshness" in a["cookie"]


def test_accounts_have_platform_role(flow_server):
    """每个账号都有 platform=bilibili + role(主/备)。"""
    import urllib.request
    body = json.loads(urllib.request.urlopen(flow_server["base"] + "/api/accounts").read())
    for a in body["data"]["items"]:
        assert a["platform"] == "bilibili"
        assert a["role"] in ("primary", "secondary")


def test_actions_endpoint(flow_server):
    """GET /api/comments/actions?limit=10 应返回最近动作列表。"""
    import urllib.request
    body = json.loads(urllib.request.urlopen(
        flow_server["base"] + "/api/comments/actions?limit=10"
    ).read())
    assert body["ok"] is True
    assert "items" in body["data"]
    assert "count" in body["data"]


def test_stats_endpoint(flow_server):
    """GET /api/comments/stats 应有 total + today + by_action + by_day + top_videos。"""
    import urllib.request
    body = json.loads(urllib.request.urlopen(
        flow_server["base"] + "/api/comments/stats"
    ).read())
    assert body["ok"] is True
    d = body["data"]
    assert "total" in d
    assert "today_count" in d
    assert "by_action" in d
    assert "by_day" in d
    assert "top_videos" in d


def test_conversion_endpoint(flow_server):
    """GET /api/comments/conversion 至少返回空 envelope(数据文件可能不存在)。"""
    import urllib.request
    body = json.loads(urllib.request.urlopen(
        flow_server["base"] + "/api/comments/conversion"
    ).read())
    assert body["ok"] is True
    assert "summary" in body["data"]
    assert "snapshot" in body["data"]


def test_actions_filter_by_action(flow_server):
    """?action=like 过滤生效。"""
    import urllib.request
    body = json.loads(urllib.request.urlopen(
        flow_server["base"] + "/api/comments/actions?action=like&limit=5"
    ).read())
    assert body["ok"] is True
    for a in body["data"]["items"]:
        assert a["action"] == "like"


def test_account_detail_404(flow_server):
    """不存在的 account_id 返回 404。"""
    import urllib.request
    import urllib.error
    try:
        urllib.request.urlopen(flow_server["base"] + "/api/accounts/nonexistent")
        assert False, "should have raised"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_check_cookie_file_missing(tmp_path):
    """缺失的 cookie 文件返回 bad + missing。"""
    from backend.routers.accounts import _check_cookie_file
    info = _check_cookie_file(str(tmp_path / "missing.txt"))
    assert info["exists"] is False
    assert info["freshness"] == "missing"
    assert info["cookie_count"] == 0


def test_check_cookie_file_fresh(tmp_path):
    """新建 cookie 文件 → fresh。"""
    from backend.routers.accounts import _check_cookie_file
    p = tmp_path / "fresh.txt"
    p.write_text("# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n")
    info = _check_cookie_file(str(p))
    assert info["exists"] is True
    assert info["freshness"] == "fresh"
    assert info["cookie_count"] >= 1
    assert info["age_days"] < 1


def test_check_cookie_file_json_format(tmp_path):
    """JSON 格式 cookie 文件计数。"""
    from backend.routers.accounts import _check_cookie_file
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps({"DedeUserID": "123", "SESSDATA": "abc", "_secret": "x"}))
    info = _check_cookie_file(str(p))
    assert info["exists"] is True
    assert info["cookie_count"] == 2  # 排除 _secret