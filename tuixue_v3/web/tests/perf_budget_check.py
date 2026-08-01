"""
Sprint 10: 性能预算断言 — 拉 /api/_meta/rum_summary vs reports/perf/perf_budget.json,超阈值则 exit 1
设计: 不做主动压测,只验证 RUM log 累积的最近 1h sample 是否合规
    - 启动回归: cron / launchd 每小时跑一次
    - 失败 3 次连续 → 触发 auto_rollback.sh 回滚 SW shell
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://localhost:7799"
ROOT = Path(__file__).resolve().parent.parent.parent
BUDGET_PATH = ROOT / "reports" / "perf" / "perf_budget.json"
HISTORY_PATH = ROOT / "reports" / "perf" / "budget_history.jsonl"

def _get_json(url: str, timeout: int = 5) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_error": str(e)[:200]}

def _check_budget():
    if not BUDGET_PATH.exists():
        return {"ok": False, "error": f"budget file not found: {BUDGET_PATH}", "failures": []}
    budget = json.loads(BUDGET_PATH.read_text())
    routes_budget = budget.get("routes", {})
    min_samples = budget.get("_global_guards", {}).get("min_samples_per_route", 1)
    min_total = budget.get("_global_guards", {}).get("min_total_samples", 30)
    resp = _get_json(f"{BASE}/api/_meta/rum_summary?window_sec=3600&top_n=50")
    if "_error" in resp:
        return {"ok": False, "error": f"rum_summary fetch failed: {resp['_error']}", "failures": []}
    by_route = {r["route"]: r for r in resp.get("data", {}).get("by_route", [])}
    n_total = resp.get("data", {}).get("n", 0)
    failures = []
    if n_total < min_total:
        failures.append({"route": "(global)", "metric": "total_samples", "limit": f">={min_total}", "actual": n_total})
    for route, b in routes_budget.items():
        actual = by_route.get(route)
        if not actual or actual.get("n", 0) < min_samples:
            failures.append({"route": route, "metric": "samples", "limit": f">={min_samples}", "actual": actual.get("n", 0) if actual else 0})
            continue
        for k, lim in b.items():
            if k.startswith("_") or not k.endswith("_max"):
                continue
            metric = k[:-4]  # nav_ms_p95_max → nav_ms_p95
            v = actual.get(metric, 0)
            if v > lim:
                failures.append({"route": route, "metric": metric, "limit": f"<={lim}", "actual": v})
    return {
        "ok": len(failures) == 0,
        "failures": failures,
        "n_total": n_total,
        "ts": time.time(),
    }

def _record_history(result: dict):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")

def _consecutive_fails() -> int:
    if not HISTORY_PATH.exists():
        return 0
    n = 0
    for ln in HISTORY_PATH.read_text().splitlines()[-10:]:
        try:
            it = json.loads(ln)
        except Exception:
            continue
        if not it.get("ok", True):
            n += 1
        else:
            n = 0
    return n

def main():
    result = _check_budget()
    _record_history(result)
    n = result.get("n_total", 0)
    print(f"[perf-budget] total_samples={n} ok={result['ok']} fails={len(result['failures'])}")
    if result["failures"]:
        for f in result["failures"][:10]:
            print(f"  ✗ {f['route']} {f['metric']}: {f['actual']} (limit {f['limit']})")
    consec = _consecutive_fails()
    print(f"[perf-budget] consecutive_fails={consec}")
    # exit 0 = pass, 1 = budget fail, 2 = 3 consecutive (rollback)
    if consec >= 3:
        print("[perf-budget] 3 consecutive fails — triggering auto-rollback")
        sys.exit(2)
    sys.exit(0 if result["ok"] else 1)

if __name__ == "__main__":
    main()
