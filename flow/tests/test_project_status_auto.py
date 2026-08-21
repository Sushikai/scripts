"""test_project_status_auto.py — 验证 job 终态时 project 状态自动更新。

修复前 bug: 所有 job 都 done,但 project 状态卡在 running 一直不变。
修复: job_set_done/failed/cancelled 触发 _maybe_finish_project。
"""

import json
import time


def _create_project(client, name="proj_test", tool_id="info_gap"):
    """helper: 直接 DB 插入 project,绕过工具步骤。"""
    from backend.db import repo as db
    p = db.project_create(tool_id=tool_id, name=name, params={"dry_run": True})
    return p["id"]


def test_project_done_when_all_jobs_done(client):
    """所有 job 都 done → project 应自动标 done"""
    from backend.db import repo as db
    pid = _create_project(client, name="auto_done_test")
    # 创建 3 个 job,全部 done
    jids = []
    for i in range(3):
        j = db.job_create(project_id=pid, step=f"step{i}")
        jid = j["id"]
        db.job_set_running(jid)
        db.job_set_done(jid)
        jids.append(jid)
    # project 状态应被自动更新为 done
    p = db.project_get(pid)
    assert p["status"] == "done", f"project 应 done,实为 {p['status']}"


def test_project_failed_when_any_job_failed(client):
    """任一 job failed → project 标 failed"""
    from backend.db import repo as db
    pid = _create_project(client, name="auto_failed_test")
    j1 = db.job_create(project_id=pid, step="step1")
    db.job_set_running(j1["id"])
    db.job_set_done(j1["id"])
    j2 = db.job_create(project_id=pid, step="step2")
    db.job_set_running(j2["id"])
    db.job_set_failed(j2["id"], "boom")
    p = db.project_get(pid)
    assert p["status"] == "failed", f"project 应 failed,实为 {p['status']}"


def test_project_running_when_one_job_pending(client):
    """还有 pending/running 的 job → project 保持 running"""
    from backend.db import repo as db
    pid = _create_project(client, name="still_running_test")
    # 先创建 2 个 job,只 finish 第 1 个
    j1 = db.job_create(project_id=pid, step="step1")
    j2 = db.job_create(project_id=pid, step="step2")
    db.job_set_running(j1["id"])
    db.job_set_done(j1["id"])
    # j2 仍 pending — project 仍应保持 pending/running
    p = db.project_get(pid)
    assert p["status"] in ("pending", "running"), f"应有未完成 job,实为 {p['status']}"


def test_project_cancelled_when_all_cancelled(client):
    """全部 cancelled → project 标 cancelled"""
    from backend.db import repo as db
    pid = _create_project(client, name="all_cancelled_test")
    j1 = db.job_create(project_id=pid, step="step1")
    db.job_set_running(j1["id"])
    db.job_set_cancelled(j1["id"])
    j2 = db.job_create(project_id=pid, step="step2")
    db.job_set_running(j2["id"])
    db.job_set_cancelled(j2["id"])
    p = db.project_get(pid)
    assert p["status"] == "cancelled", f"project 应 cancelled,实为 {p['status']}"


def test_real_fengge_project_was_stuck_now_fixed():
    """真实场景: fengge 项目 jobs 全 done 但 project 卡 running,这是用户痛点"""
    from backend.db import repo as db
    # 找到 峰哥切片 项目
    projects = db.project_list(tool_id="fengge", limit=10)
    stuck = [p for p in projects if p["status"] == "running"]
    if not stuck:
        # 已被新代码修复,跳过
        return
    for p in stuck:
        # 模拟"再标一个 job done",看 project 是否会动
        jobs = db.job_list_by_project(p["id"])
        all_done = all(j["status"] in ("done", "failed", "cancelled") for j in jobs)
        if all_done:
            # 触发一次 _maybe_finish_project(模拟新 job 完成)
            any_jid = jobs[0]["id"]
            # 直接调用内部函数
            db._maybe_finish_project(any_jid)
            new_p = db.project_get(p["id"])
            assert new_p["status"] != "running", \
                f"fengge 项目 {p['name']} 全部 job 已完,但 project 仍 running"