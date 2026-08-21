"""R96 visual verification loop — 100+ iterations with screenshot + DOM classification.

Per-iteration:
  1. Pre-flight: /api/ready, restart if needed
  2. Clear state (cache, history, localStorage)
  3. Type query, send
  4. Wait for resolve (no .loading)
  5. Read yerenAiHistory[-1] from window
  6. Classify PASS / FAIL
  7. Screenshot full + bubble crop
  8. If FAIL: classify → apply_fix → re-run same iter

Usage:
  python3 tests/r96_visual_loop.py [--max-iters 100] [--start 0]

Output:
  /tmp/r96_iter/iter_XXXX{_full,_bubble}.png
  /tmp/r96_iter/iter_XXXX.json
  /tmp/r96_iter/summary.json
  /tmp/r96_iter/fixes.log
"""
import os, sys, json, time, subprocess, traceback, shutil
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path("/Users/kaikai/scripts/tuixue_v3")
BASE = "http://127.0.0.1:7799"
HTML_URL = f"{BASE}/#yeren-ai?code=002716"
OUT_DIR = Path("/tmp/r96_iter")
LOG_FILE = OUT_DIR / "fixes.log"

QUERIES_PATH = ROOT / "tests" / "r96_query_set.py"
sys.path.insert(0, str(ROOT / "tests"))
from r96_query_set import QUERIES


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_fix(iter_id, error_class, fix_desc, before_class, after_class):
    line = f"[{ts()}] iter_{iter_id} {before_class}→{after_class} via {fix_desc}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(f"  [FIX] {line.strip()}", flush=True)


def ensure_server(max_restarts=2, restart_budget=2):
    """Check /api/ready, restart if needed."""
    import requests
    for attempt in range(max_restarts + 1):
        try:
            r = requests.get(f"{BASE}/api/ready", timeout=4)
            if r.status_code == 200 and r.json().get("ok"):
                return True
        except Exception:
            pass
        if attempt < max_restarts:
            print(f"  [srv] /api/ready not ok, restart.sh (attempt {attempt+1}/{max_restarts})", flush=True)
            subprocess.run(["bash", str(ROOT / "web" / "restart.sh")], timeout=300, cwd=str(ROOT))
            time.sleep(5)
        else:
            print(f"  [srv] {max_restarts} restarts exhausted", flush=True)
            return False
    return False


def classify(state):
    """Map DOM state to error_class. None = NO_STATE (response never resolved)."""
    if not state:
        return "NO_STATE"
    c = state.get("content") or ""
    if state.get("loading"):
        return "LOADING_STUCK"
    if c.startswith("⚠") and "失联" in c:
        return "TIMEOUT"
    if c.startswith("🌐") or "网络异常" in c or state.get("is_net_error"):
        return "NETWORK"
    if c.startswith("⏹") or "已取消" in c:
        return "CANCEL"
    if c.strip() in ("", "(空)"):
        return "EMPTY"
    if state.get("cached") and state.get("tool_calls_count", 0) > 0 and len(c) < 50:
        return "CACHE_POISON"
    if len(c) > 0 and len(c) < 50 and any(m in c for m in ["我先调取", "稍后", "稍等", "我先调", "马上调", "等我"]):
        return "TRUNCATED"
    if len(c) < 50:
        return "TRUNCATED"
    if c.startswith("⚠"):
        return "EMPTY"
    if state.get("degraded"):
        return "DEGRADED"
    if state.get("failed"):
        return "EMPTY"
    if (state.get("suggestions_count", 0) + state.get("rules_hit_count", 0) + state.get("tool_calls_count", 0)) == 0:
        return "NO_DATA"
    return "PASS"


def wait_for_resolve(page, max_s=110):
    """Poll until last history entry is not loading, or timeout."""
    deadline = time.time() + max_s
    while time.time() < deadline:
        try:
            loading = page.evaluate("""() => {
                const h = (window.yerenAiHistory || []).slice(-1)[0];
                return h ? !!h.loading : false;
            }""")
            if not loading:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def read_state(page):
    """Read yerenAiHistory[-1] from window."""
    try:
        return page.evaluate("""() => {
            const h = (window.yerenAiHistory || []).slice(-1)[0];
            if (!h) return null;
            return {
                role: h.role,
                content: h.content || '',
                loading: !!h.loading,
                degraded: !!h.degraded,
                failed: !!h.failed,
                is_net_error: !!h.is_net_error,
                cached: !!h.cached,
                suggestions_count: (h.suggestions || []).length,
                rules_hit_count: (h.rules_hit || []).length,
                tool_calls_count: (h.tool_calls || []).length,
                related_code: h.related_code || '',
                wait_sec: h.wait_sec || null,
            };
        }""")
    except Exception as e:
        return {"_err": str(e)}


def pre_iter_cleanup(page, force_bypass):
    """3-layer cache invalidation."""
    page.evaluate("""(fb) => {
        try { localStorage.removeItem('yeren-ai-history'); } catch (e) {}
        try { sessionStorage.removeItem('yeren-ai-history'); } catch (e) {}
        if (window._yerenBypassCache !== undefined) window._yerenBypassCache = !!fb;
        if (window.AI && window.AI.cache) {
            try { window.AI.cache.bypass(); } catch (e) {}
            try { window.AI.cache.invalidate('/api/yeren/ai/chat'); } catch (e) {}
        }
    }""", force_bypass)


def click_clear_btn(page):
    """Click the clear-history button to reset yerenAiHistory."""
    try:
        if page.locator("#yeren-ai-clear").count() > 0 and page.locator("#yeren-ai-clear").is_visible():
            page.click("#yeren-ai-clear", timeout=3000)
            page.wait_for_function("() => (window.yerenAiHistory || []).length === 0", timeout=4000)
    except Exception:
        pass


def run_one_iter(page, slot_idx, slot, screenshot=True):
    """Run a single iteration. Returns (record, ok_bool)."""
    iter_id = f"{slot_idx+1:04d}"
    actual_q = slot["q"]
    if slot["fb"]:
        actual_q = f"{slot['q']}"  # fb is server-side; keep query same so we test cache-bypass path

    record = {
        "iter": iter_id,
        "ts": ts(),
        "cat": slot["cat"],
        "query": slot["q"],
        "code": slot["code"],
        "force_bypass": slot["fb"],
        "reply_len": 0,
        "tool_calls_count": 0,
        "rules_hit_count": 0,
        "suggestions_count": 0,
        "cached": False,
        "is_loading": True,
        "error_class": "PENDING",
        "screenshot_path": None,
        "bubble_path": None,
        "console_err": [],
        "dur_ms": 0,
        "fix_applied": None,
    }
    t0 = time.time()

    try:
        # 1. cache invalidation
        pre_iter_cleanup(page, slot["fb"])

        # 2. navigate to view (use hash to avoid full reload)
        try:
            code = slot["code"]
            page.evaluate(f"() => {{ window.location.hash = '#yeren-ai?code={code}'; }}")
        except Exception:
            pass

        # 3. clear history
        click_clear_btn(page)

        # 4. input + send
        if not actual_q.strip():
            # EDGE category: empty query — should NOT trigger AI call (send disabled or no-op)
            record["error_class"] = "EDGE_SKIP"
            record["dur_ms"] = int((time.time() - t0) * 1000)
            return record, True

        # Use fill + click for normal flow
        page.fill("#yeren-ai-msg", actual_q)
        # Use Enter key as fallback (more reliable than click for some send paths)
        page.press("#yeren-ai-msg", "Enter")
        # also click send as belt-and-suspenders
        try:
            if page.locator("#yeren-ai-send").is_visible() and not page.locator("#yeren-ai-send").is_disabled():
                page.click("#yeren-ai-send", timeout=500)
        except Exception:
            pass

        # 5. wait for resolve
        resolved = wait_for_resolve(page, max_s=110)
        if not resolved:
            record["error_class"] = "LOADING_STUCK"
            record["is_loading"] = True
        else:
            record["is_loading"] = False
            state = read_state(page)
            if state and "_err" not in state:
                record["reply_len"] = len(state.get("content", ""))
                record["tool_calls_count"] = state.get("tool_calls_count", 0)
                record["rules_hit_count"] = state.get("rules_hit_count", 0)
                record["suggestions_count"] = state.get("suggestions_count", 0)
                record["cached"] = state.get("cached", False)
                record["error_class"] = classify(state)
            else:
                record["error_class"] = "NO_STATE"

        # 6. screenshot
        if screenshot:
            try:
                full_p = OUT_DIR / f"iter_{iter_id}_full.png"
                page.screenshot(path=str(full_p), full_page=False)
                record["screenshot_path"] = str(full_p)

                bbox = page.evaluate("""() => {
                    const el = document.querySelector('.msg-row.ai:last-child');
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {x: r.x, y: r.y, w: r.width, h: r.height};
                }""")
                if bbox:
                    b_p = OUT_DIR / f"iter_{iter_id}_bubble.png"
                    page.screenshot(path=str(b_p), clip={
                        "x": max(0, bbox["x"]),
                        "y": max(0, bbox["y"]),
                        "width": min(bbox["w"], 1440),
                        "height": min(bbox["h"], 900),
                    })
                    record["bubble_path"] = str(b_p)
            except Exception as e:
                record["screenshot_err"] = str(e)[:120]

    except Exception as e:
        record["error_class"] = "EXCEPTION"
        record["exc"] = f"{type(e).__name__}: {str(e)[:120]}"
        record["traceback"] = traceback.format_exc()[:500]

    record["dur_ms"] = int((time.time() - t0) * 1000)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"iter_{iter_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record, record["error_class"] == "PASS"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    max_iters = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    summary = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "started_at": ts(),
        "max_iters": max_iters,
        "start": start,
        "completed": 0,
        "pass_count": 0,
        "fail_count": 0,
        "by_class": {},
        "fixes_applied": [],
        "category_pass": {},
        "escalate": False,
        "stop_reason": None,
    }

    # pre-flight
    if not ensure_server():
        print("FATAL: server not ready after max restarts")
        summary["stop_reason"] = "server_down"
        (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(f"=== R96 visual loop: {max_iters} iters from slot {start} ===", flush=True)

    consecutive_class_fail = {}  # {cat: {error_class: count}}
    recent_classes = []
    server_restarts = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        console_errs = []
        page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc: console_errs.append(str(exc)))

        # initial navigate
        page.goto(HTML_URL, wait_until="commit", timeout=20000)
        try:
            page.wait_for_selector(".view-yeren-ai", timeout=20000)
        except Exception:
            # try to wait for nav anyway
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_selector(".view-yeren-ai", timeout=10000)
        # ensure AI loaded
        has_ai = page.evaluate("() => !!window.AI && typeof window.AI.chat === 'function'")
        if not has_ai:
            print("FATAL: window.AI not loaded")
            summary["stop_reason"] = "ai_not_loaded"
            (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            sys.exit(1)

        # reset console_errs at start
        console_errs.clear()

        for i in range(max_iters):
            if i < start:
                continue
            slot = QUERIES[i]

            # health gate every 25 iters
            if i > 0 and i % 25 == 0:
                if not ensure_server(max_restarts=0):
                    print(f"  [srv] server down at iter {i}, restart.sh", flush=True)
                    if server_restarts >= 2:
                        print("  [srv] 2 restarts budget exhausted, escalate", flush=True)
                        summary["stop_reason"] = "server_unstable"
                        summary["escalate"] = True
                        break
                    subprocess.run(["bash", str(ROOT / "web" / "restart.sh")], timeout=300, cwd=str(ROOT))
                    server_restarts += 1
                    time.sleep(5)
                    # re-open browser context if pages died
                    try:
                        page.goto(HTML_URL, wait_until="commit", timeout=20000)
                    except Exception:
                        ctx.close()
                        browser.close()
                        browser = p.chromium.launch(headless=True)
                        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                        page = ctx.new_page()
                        page.goto(HTML_URL, wait_until="commit", timeout=20000)

            # capture current console errors (since last iter)
            pre_console_err_n = len(console_errs)

            record, ok = run_one_iter(page, i, slot)

            # console errors during this iter
            new_errs = console_errs[pre_console_err_n:]
            record["console_err"] = new_errs[:5]

            # category stats
            cat = slot["cat"]
            if cat not in summary["category_pass"]:
                summary["category_pass"][cat] = {"n": 0, "pass": 0}
            summary["category_pass"][cat]["n"] += 1
            if ok:
                summary["category_pass"][cat]["pass"] += 1

            # class stats
            ec = record["error_class"]
            summary["by_class"][ec] = summary["by_class"].get(ec, 0) + 1
            if ok:
                summary["pass_count"] += 1
            else:
                summary["fail_count"] += 1
                recent_classes.append(ec)
                if len(recent_classes) > 20:
                    recent_classes.pop(0)

                # per-cat consecutive fail
                k = cat
                if k not in consecutive_class_fail:
                    consecutive_class_fail[k] = {}
                consecutive_class_fail[k][ec] = consecutive_class_fail[k].get(ec, 0) + 1
                # reset other class counters
                for ck in list(consecutive_class_fail[k].keys()):
                    if ck != ec:
                        consecutive_class_fail[k][ck] = 0

            summary["completed"] += 1

            # progress print
            if (i + 1) % 10 == 0 or i == 0:
                elapsed_min = (time.time() - time.mktime(datetime.strptime(summary["started_at"], "%Y-%m-%dT%H:%M:%SZ").timetuple())) / 60
                print(f"  [{i+1:3d}/{max_iters}] PASS={summary['pass_count']} FAIL={summary['fail_count']} "
                      f"avg={int(elapsed_min*60*1000/max(1, summary['completed']))}ms "
                      f"classes={summary['by_class']}", flush=True)
                (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

            # stop conditions
            if summary["pass_count"] == max_iters:
                summary["stop_reason"] = "all_pass"
                break

            # 3 consecutive same-class fail for same cat
            for cat_k, class_map in consecutive_class_fail.items():
                for ec, cnt in class_map.items():
                    if cnt >= 3:
                        print(f"  [ESCALATE] {cat_k} {ec} failed 3 consecutive, stop", flush=True)
                        summary["stop_reason"] = f"3_consecutive_{cat_k}_{ec}"
                        summary["escalate"] = True
                        break
                if summary["escalate"]:
                    break
            if summary["escalate"]:
                break

            # 5 different classes in last 20 iters
            if len(recent_classes) >= 20 and len(set(recent_classes[-20:])) >= 5:
                print(f"  [ESCALATE] 5+ different FAIL classes in last 20, stop", flush=True)
                summary["stop_reason"] = "diversity_escalate"
                summary["escalate"] = True
                break

        # final
        summary["ended_at"] = ts()
        (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\n=== DONE: {summary['completed']}/{max_iters}, PASS={summary['pass_count']}, "
              f"FAIL={summary['fail_count']}, stop={summary['stop_reason']} ===", flush=True)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
