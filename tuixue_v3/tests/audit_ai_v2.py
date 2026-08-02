"""AI 1000-round stress test — optimized for speed.

Hits 4 AI endpoints in parallel per stock, 6 stocks (skip 830799 北证 AI not trained).
"""
import json
import sys
import time
import urllib.request
import urllib.error
import concurrent.futures
from pathlib import Path

BASE = "http://127.0.0.1:7799"
CODES = ["605179", "000001", "000428", "002659", "300750", "600519"]
ENDPOINTS = [
    ("ai_analysis", "/api/stock/{code}/ai_analysis", 60),
    ("ai_crash",    "/api/stock/{code}/ai_crash_risk", 45),
    ("news",        "/api/news/live?code={code}", 30),
    ("meta_recommend", "/api/meta/recommend?top_n=5", 60),
]

HISTORY_FILE = Path("/tmp/ai_stress_history.json")


def fetch(url, timeout):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            elapsed = (time.time() - t0) * 1000
            try:
                data = json.loads(body)
                ok = data.get("ok", True)
                return (r.status, elapsed, ok, None)
            except Exception as e:
                return (r.status, elapsed, False, f"parse-err:{e}")
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - t0) * 1000
        return (e.code, elapsed, False, f"http-{e.code}")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        return (0, elapsed, False, f"err:{type(e).__name__}:{str(e)[:50]}")


def run_round(round_num):
    issues = []
    tasks = []
    for code in CODES:
        for name, tmpl, timeout in ENDPOINTS:
            url = f"{BASE}{tmpl.format(code=code)}"
            tasks.append((code, name, url, timeout))
    
    # Run all in parallel (max 8 — keep below worker count)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch, url, timeout): (code, name) for code, name, url, timeout in tasks}
        for fut in concurrent.futures.as_completed(futures):
            code, name = futures[fut]
            try:
                status, elapsed, ok, err = fut.result()
                if status != 200:
                    issues.append({"code": code, "endpoint": name, "issue": f"status-{status}", "latency_ms": elapsed})
                elif not ok:
                    issues.append({"code": code, "endpoint": name, "issue": "ok-false", "latency_ms": elapsed})
                elif err:
                    issues.append({"code": code, "endpoint": name, "issue": err, "latency_ms": elapsed})
            except Exception as e:
                issues.append({"code": code, "endpoint": name, "issue": str(e)[:60]})
    return issues


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    clear_streak = 0
    for i in range(rounds):
        issues = run_round(i + 1)
        # Log
        if issues:
            if not HISTORY_FILE.exists():
                HISTORY_FILE.write_text("[]")
            try:
                history = json.loads(HISTORY_FILE.read_text())
            except Exception:
                history = []
            for iss in issues:
                history.append({"ts": time.time(), "round": i + 1, "category": iss["endpoint"], "msg": iss["issue"]})
            HISTORY_FILE.write_text(json.dumps(history[-1000:], indent=2, ensure_ascii=False))
        
        n = len(issues)
        print(f"R{i+1}: {n} issues" + (f" — {issues[:3]}" if issues else " ✓ ALL CLEAR"))
        if n == 0:
            clear_streak += 1
        else:
            clear_streak = 0
        sys.stdout.flush()
        time.sleep(delay)


if __name__ == "__main__":
    main()
