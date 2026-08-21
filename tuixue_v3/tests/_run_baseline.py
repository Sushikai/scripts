"""方向A baseline runner — per-file pytest with hard timeout (no single test can hang).

Usage: python3 tests/_run_baseline.py [--timeout 120] [--only file1 file2 ...]
Outputs a per-file PASS/FAIL/TIMEOUT/ERROR map to stdout (and writes a JSON copy).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
VENV = ROOT.parent.parent / ".hermes" / "hermes-agent" / "venv" / "bin" / "python3"
PY = str(VENV) if VENV.exists() else sys.executable

# 重型/易 wedged 文件 — 单独人工处理,不进 baseline
# (screener_stability/watchlist_stress/stock_professional_stress/full_acceptance
#  已 rename 为 _*.py 脚本,不再被 pytest 收集)
HEAVY = {
    "test_race_memory.py",
    "test_deep_analysis_contract.py",
    "test_ai_visual_regression.py",
    "test_cross_to_stock_perf.py",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=180, help="per-file timeout seconds")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    files = sorted(TESTS.glob("test_*.py"))
    if args.only:
        files = [f for f in files if f.name in set(args.only)]
    else:
        files = [f for f in files if f.name not in HEAVY]

    results = {}
    start = time.time()
    for i, f in enumerate(files, 1):
        t0 = time.time()
        label = f"{i}/{len(files)} {f.name}"
        try:
            cp = subprocess.run(
                [PY, "-m", "pytest", str(f), "-p", "no:cacheprovider",
                 "--no-header", "-q", "--tb=short", "-rN"],
                capture_output=True, text=True, timeout=args.timeout,
                cwd=ROOT,
                env={**__import__("os").environ, "PYTHONPATH": str(ROOT.parent)},
            )
            dt = time.time() - t0
            status = "PASS" if cp.returncode == 0 else "FAIL"
            tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
            results[f.name] = {"status": status, "time": round(dt, 1),
                               "rc": cp.returncode, "tail": tail}
            print(f"[{status}] {label} {dt:.1f}s rc={cp.returncode}", flush=True)
        except subprocess.TimeoutExpired:
            dt = time.time() - t0
            results[f.name] = {"status": "TIMEOUT", "time": round(dt, 1),
                               "rc": "timeout", "tail": []}
            print(f"[TIMEOUT] {label} >{args.timeout}s", flush=True)

    total = time.time() - start
    n_fail = sum(1 for r in results.values() if r["status"] == "FAIL")
    n_to = sum(1 for r in results.values() if r["status"] == "TIMEOUT")
    n_pass = sum(1 for r in results.values() if r["status"] == "PASS")
    print(f"\n=== SUMMARY === files={len(results)} pass={n_pass} fail={n_fail} timeout={n_to} total={total:.0f}s")
    for name, r in sorted(results.items(), key=lambda kv: kv[1]["status"]):
        if r["status"] != "PASS":
            print(f"  [{r['status']}] {name} {r['time']}s")
    out = ROOT / "_baseline_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
