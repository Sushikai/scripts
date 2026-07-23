"""SSE Job 流测试:推送实时进度 / 状态变化。"""

import json
import time


def test_sse_immediate_snapshot(client):
    """连接 SSE 立即收到当前 snapshot。"""
    # 建项目 + 提交一个会跑几秒的 job
    p = client.post("/api/projects", json={"tool_id": "fengge", "name": "sse_test", "params": {}}).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "fengge", "project_id": p["id"], "step": "fetch_candidates", "params": {},
    }).json()["data"]
    job_id = j["job_id"]

    with client.stream("GET", f"/api/job/{job_id}/stream") as r:
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/event-stream" in ct
        # 读第一个 chunk 应该有 snapshot 或 progress 事件
        deadline = time.time() + 5
        seen_event = False
        for line in r.iter_lines():
            if time.time() > deadline:
                break
            if not line:
                continue
            if line.startswith("event:"):
                seen_event = True
                break
            if line.startswith("data:"):
                seen_event = True
                break
        assert seen_event, "expected SSE event within 5s"


def test_sse_terminates_when_done(client):
    """done 状态后,SSE 推 done 事件并允许客户端断开。"""
    p = client.post("/api/projects", json={"tool_id": "fengge", "name": "sse_done", "params": {}}).json()["data"]
    j = client.post("/api/jobs", json={
        "tool_id": "fengge", "project_id": p["id"], "step": "fetch_candidates", "params": {},
    }).json()["data"]
    job_id = j["job_id"]

    seen_done = False
    deadline = time.time() + 10
    with client.stream("GET", f"/api/job/{job_id}/stream") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if time.time() > deadline:
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except Exception:
                continue
            if payload.get("type") == "done" or payload.get("status") == "done":
                seen_done = True
                break
    assert seen_done, "expected SSE done event within 10s"


def test_sse_404_unknown_job(client):
    """未知 job_id 返回 404。"""
    r = client.get("/api/job/nonexistent_job_id/stream")
    assert r.status_code == 404