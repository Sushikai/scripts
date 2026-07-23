"""/api/jobs + /api/projects + /api/job/{id} 端到端契约测试。"""

import asyncio
import json
import time


def test_create_project(client):
    r = client.post("/api/projects", json={
        "tool_id": "info_gap",
        "name": "测试项目",
        "params": {"topic": "AI"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["tool_id"] == "info_gap"
    pid = body["data"]["id"]
    assert pid


def test_create_project_requires_tool_id(client):
    r = client.post("/api/projects", json={"name": "x"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["code"] == "BAD_REQUEST"


def test_list_projects(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "items" in body["data"]


def test_get_project_404(client):
    r = client.get("/api/projects/nonexistent_xyz")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


def test_create_job_unknown_tool(client):
    """run_fn tool_id 不存在 → 404。"""
    # 先建 project
    p = client.post("/api/projects", json={"tool_id": "nonexistent_tool", "name": "x", "params": {}}).json()
    r = client.post("/api/jobs", json={
        "tool_id": "nonexistent_tool",
        "project_id": p["data"]["id"],
        "step": "research",
        "params": {},
    })
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "TOOL_NOT_FOUND"


def test_create_job_requires_project_id_and_step(client):
    r = client.post("/api/jobs", json={})
    assert r.status_code == 400


def test_job_lifecycle_e2e(client):
    """建 project → submit job → 轮询等 done → 查结果。"""
    # 1. project
    p = client.post("/api/projects", json={
        "tool_id": "info_gap",
        "name": "lifecycle_test",
        "params": {"topic": "AI"},
    }).json()["data"]
    pid = p["id"]
    # 2. submit job
    j = client.post("/api/jobs", json={
        "tool_id": "info_gap",
        "project_id": pid,
        "step": "research",
        "params": {},
    })
    assert j.status_code == 202
    jid = j.json()["data"]["job_id"]
    # 3. 轮询
    final = None
    for _ in range(40):
        time.sleep(0.1)
        g = client.get(f"/api/job/{jid}").json()["data"]
        if g["status"] in ("done", "failed", "cancelled"):
            final = g
            break
    assert final is not None, "job didn't finish in time"
    assert final["status"] == "done", f"unexpected status {final['status']} err={final.get('error')}"
    assert final["progress"] == 1.0


def test_get_job_404(client):
    r = client.get("/api/job/nonexistent_job_xyz")
    assert r.status_code == 404


def test_cancel_job(client):
    p = client.post("/api/projects", json={
        "tool_id": "fengge",
        "name": "cancel_test",
        "params": {},
    }).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "fengge",
        "project_id": p["id"],
        "step": "search",
        "params": {},
    }).json()["data"]
    r = client.post(f"/api/job/{j['job_id']}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


def test_list_jobs_filter(client):
    p = client.post("/api/projects", json={"tool_id": "fengge", "name": "list_test", "params": {}}).json()["data"]
    client.post("/api/jobs", json={"tool_id": "fengge", "project_id": p["id"], "step": "download", "params": {}})
    r = client.get(f"/api/jobs?project_id={p['id']}")
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 1
    assert all(j["project_id"] == p["id"] for j in items)


def test_envelope_has_trace_id_e2e(client):
    r = client.get("/api/projects", headers={"X-Trace-Id": "abcdef1234"})
    body = r.json()
    assert body["trace_id"] == "abcdef1234"


def test_error_envelope(client):
    """未知 endpoint 返 FastAPI 默认 404(后续 batch 包 envelope)。"""
    r = client.get("/api/totally_unknown_route")
    assert r.status_code == 404


def test_sse_snapshot_immediate(client):
    """建立 job 后立即访问 /api/job/{id}/stream,首事件是 snapshot。"""
    p = client.post("/api/projects", json={"tool_id": "info_gap", "name": "sse_test", "params": {}}).json()["data"]
    j = client.post("/api/jobs", json={"tool_id": "info_gap", "project_id": p["id"], "step": "research", "params": {}}).json()["data"]
    # 用 httpx 走流式
    import httpx
    snap_seen = False
    snap_event_seen = False
    with httpx.stream("GET", client.base_url.join(f"/api/job/{j['job_id']}/stream"), timeout=4.0) as r:
        for line in r.iter_lines():
            if not line:
                continue
            if line.startswith("event: snapshot"):
                snap_event_seen = True
                continue
            if line.startswith("data:"):
                d = json.loads(line[5:].strip())
                if d.get("type") == "snapshot" or "id" in d:
                    snap_seen = True
                    break
            if line.startswith("event: end"):
                break
    assert snap_event_seen or snap_seen, "no snapshot event"