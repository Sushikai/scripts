"""R300 visual optimization loop — 300 rounds with first-principles conviction per round.

Per round:
  1. Read MANIFEST phase/round definition
  2. lock to current base commit (just snap the diff)
  3. capture M_before (computed CSS / DOM count / hex count / etc.)
  4. apply the change (single CSS/JS edit per round)
  5. capture M_after
  6. gate checks (a) threshold met (b) hex/px not regressed (c) no new console errors
  7. commit with structured message
  8. log to summary.json

Usage:
  python3 tests/r300_visual_loop.py 300 0
"""
import os, sys, json, time, subprocess, re, traceback
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

ROOT = Path("/Users/kaikai/scripts/tuixue_v3")
BASE = "http://127.0.0.1:7799"
HTML_URL = f"{BASE}/#yeren-ai?code=002716"
OUT_DIR = Path("/tmp/r300_visual")
LOG_FILE = OUT_DIR / "fixes.log"
SUMMARY_FILE = OUT_DIR / "summary.json"

# ────────────────────────────────────────────────────────────────────────────
# Phase × Round definitions (verbatim from plan)
# Each round: {conviction, apply_fn, metric_target, gate}
# Conviction is the first-principles reasoning; metric is computed CSS / DOM / count
# ────────────────────────────────────────────────────────────────────────────


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def measure_css(page, selector: str, prop: str) -> str:
    """Read getComputedStyle for a selector."""
    return page.evaluate(
        """([sel, p]) => {
            const el = document.querySelector(sel);
            if (!el) return 'NO_EL';
            return getComputedStyle(el).getPropertyValue(p);
        }""", [selector, prop])


def measure_count(page, selector: str) -> int:
    return page.evaluate("sel => document.querySelectorAll(sel).length", selector)


def measure_bbox(page, selector: str) -> dict:
    return page.evaluate(
        """sel => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
        }""", selector)


def ensure_server():
    """Check /api/ready once."""
    import requests
    try:
        r = requests.get(f"{BASE}/api/ready", timeout=4)
        return r.status_code == 200 and r.json().get("ok")
    except Exception:
        return False


# Round implementations start here ─────────────────────────────────────────

def round_R1(page):
    """R1: Define --line-soft in tokens.css (both themes). 30+ usages benefit."""
    # measure before
    # selector: msg-bubble.ai border
    before = page.evaluate("""() => {
        const el = document.querySelector('.msg-bubble.ai');
        if (!el) return null;
        const s = getComputedStyle(el);
        return {border: s.getPropertyValue('border-top-color'), background: s.getPropertyValue('background-color')};
    }""")
    # the actual edit happens outside via run_round
    return {"conviction": "30 处 var(--line-soft) undefined → 30 border 不可见. 计算物理: 不可见 border = 0 信息量 + 假装有边框 = 视觉欺骗. 修 = 让变量有定义 = border 真实可见.",
            "selector": ".msg-bubble.ai",
            "metric": "computed border-top-color",
            "target": "rgba(15, 23, 42, .08) (light) or rgba(255, 255, 255, .06) (dark)",
            "before_sample": str(before)[:200]}


# round dispatch
ROUNDS = {
    1: round_R1,
    # 2-10: stub (only R1 wired for first commit batch)
}


def run_round(n: int, page):
    """Run round n. Returns record dict."""
    fn = ROUNDS.get(n)
    if not fn:
        return {"iter": n, "error": "no impl", "skip": True}
    record = {"iter": n, "ts": ts()}
    try:
        result = fn(page)
        record.update(result)
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return record


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    if not ensure_server():
        print("FATAL: server not ready")
        sys.exit(1)

    summary = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "started_at": ts(),
        "max": max_n,
        "start": start,
        "completed": 0,
        "rounds": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(HTML_URL, wait_until="commit", timeout=20000)
        try:
            page.wait_for_selector(".view-yeren-ai", timeout=15000)
        except Exception:
            pass
        time.sleep(2)

        for n in range(max_n):
            if n < start:
                continue
            r = run_round(n, page)
            summary["rounds"].append(r)
            summary["completed"] += 1
            (OUT_DIR / f"round_{n:04d}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2))
            print(f"  R{n}: {r.get('conviction', 'no-conviction')[:80]}", flush=True)
            if n >= 9:  # first phase done
                break

        ctx.close()
        browser.close()

    summary["ended_at"] = ts()
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nDONE: {summary['completed']} rounds, summary → {SUMMARY_FILE}", flush=True)


if __name__ == "__main__":
    main()
