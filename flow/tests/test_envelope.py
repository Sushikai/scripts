"""envelope 单元测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.envelope import ok, err, degraded, paginated, Code


def test_ok_basic():
    r = ok({"x": 1}, trace_id="t1")
    assert r["ok"] is True
    assert r["data"] == {"x": 1}
    assert r["error"] is None
    assert r["trace_id"] == "t1"
    assert r["code"] == 0
    assert r["ts"] > 0


def test_err_returns_tuple():
    status, body = err(Code.NOT_FOUND, "missing", status=404)
    assert status == 404
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "missing"


def test_degraded_marks_data():
    r = degraded({"hello": "world"}, "rate limited")
    assert r["ok"] is True
    assert r["data"]["_degraded"] is True
    assert r["data"]["_degraded_reason"] == "rate limited"
    assert r["data"]["hello"] == "world"


def test_paginated():
    r = paginated([1, 2, 3], total=10, page=1, page_size=3)
    assert r["ok"] is True
    assert r["data"]["has_more"] is True
    assert r["data"]["items"] == [1, 2, 3]


def test_ok_empty():
    r = ok()
    assert r["ok"] is True
    assert r["data"] is None