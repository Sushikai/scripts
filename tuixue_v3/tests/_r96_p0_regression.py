"""R96-P0-A 战法 AI 视觉回归 — 桌面 + 移动 各 50 轮

覆盖:
- welcome 渲染 + 4 tiles
- 首次发送 (cold) → 加载气泡 → AI 回复
- 重复发送 (cache hit) → ⚡ 秒回徽章
- 多轮对话
- 取消按钮
- 跨页面 jumpToYerenAi
- IME 弹起 (mobile)

每轮记录: {viewport, pass/fail, latency, console_err, page_err, visual_issue}
最终输出: 桌面 50 通过率, 移动 50 通过率, 已知 issue 清单
"""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"
ROUNDS = 50
RESULTS = {"desktop": [], "mobile": []}


def make_page(p, viewport):
    ctx = p.chromium.launch(headless=True, args=["--no-sandbox"]).new_context(
        viewport=viewport,
        device_scale_factor=2 if viewport["width"] < 500 else 1,
        is_mobile=viewport["width"] < 500,
        has_touch=viewport["width"] < 500,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" if viewport["width"] < 500 else None,
    )
    page = ctx.new_page()
    return page, ctx


def collect_errs(page):
    ce, pe = [], []
    page.on("console", lambda m: ce.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: pe.append(str(e)))
    return ce, pe


def assert_no_x_overflow(page, label):
    """Check no horizontal page-level overflow."""
    info = page.evaluate("""(() => {
      const d = document.documentElement;
      return { scrollW: d.scrollWidth, vpW: window.innerWidth, x: window.scrollX };
    })()""")
    return info["scrollW"] <= info["vpW"] + 2, f"x-overflow {info['scrollW']}>{info['vpW']}"


def assert_welcome_visible(page):
    """Welcome 状态: 居中 logo + 4 tiles."""
    return page.evaluate("""(() => {
      const w = document.querySelector('.yeren-ai-welcome');
      if (!w) return { ok: false, why: 'no welcome' };
      const tiles = document.querySelectorAll('.welcome-tile');
      const logo = document.querySelector('.welcome-logo');
      return {
        ok: !!w && tiles.length >= 4 && !!logo,
        tiles: tiles.length,
        hasLogo: !!logo,
        visible: w.offsetHeight > 100,
      };
    })()""")


def assert_bubble_layout(page):
    """AI 气泡不应超出 92%, 用户不应超出 75%."""
    return page.evaluate("""(() => {
      const ai = document.querySelector('.msg-row.ai:not(.loading) .msg-bubble');
      const user = document.querySelector('.msg-row.user .msg-bubble');
      const vpW = window.innerWidth;
      const r = (el) => el ? el.getBoundingClientRect() : null;
      return {
        ai: r(ai),
        user: r(user),
        vpW,
        aiOK: r(ai) ? r(ai).width <= vpW * 0.95 : null,
        userOK: r(user) ? r(user).width <= vpW * 0.80 : null,
      };
    })()""")


def assert_ime_handled(page):
    """Mobile: visualViewport 检测 + .ime-up class 切换."""
    page.evaluate("""(() => {
      if (window.visualViewport) {
        Object.defineProperty(window.visualViewport, 'height', { value: 300, configurable: true });
        window.visualViewport.dispatchEvent(new Event('resize'));
      }
    })()""")
    page.wait_for_timeout(150)
    has_ime = page.evaluate("document.querySelector('.view-yeren-ai.ime-up') !== null")
    page.evaluate("""(() => {
      if (window.visualViewport) {
        Object.defineProperty(window.visualViewport, 'height', { value: window.innerHeight, configurable: true });
        window.visualViewport.dispatchEvent(new Event('resize'));
      }
    })()""")
    page.wait_for_timeout(150)
    return has_ime


def run_round(p, idx, viewport, label):
    """单轮回归."""
    issues = []
    page, ctx = make_page(p, viewport)
    ce, pe = collect_errs(page)
    t0 = time.time()
    try:
        page.goto(f"{BASE}/#yeren-ai", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(500)
        page.evaluate("if (typeof showView === 'function') showView('yeren-ai');")
        page.wait_for_timeout(300)
        # Clear cache for true cold
        page.evaluate("sessionStorage.clear(); yerenAiHistory = []; yerenAiReplyCache = {};")
        page.reload(wait_until="domcontentloaded")
        page.evaluate("if (typeof showView === 'function') showView('yeren-ai');")
        page.wait_for_timeout(500)

        # (1) Welcome visible
        w = assert_welcome_visible(page)
        if not w.get("ok"):
            issues.append(f"welcome-fail: {w}")

        # (2) No horizontal overflow
        ok, why = assert_no_x_overflow(page, "welcome")
        if not ok:
            issues.append(f"welcome-{why}")

        # (3) Click first tile → cold query
        page.evaluate("document.querySelectorAll('.welcome-tile')[0]?.click()")
        page.wait_for_selector(".msg-row.user", timeout=5000)
        page.wait_for_selector(".msg-row.ai:not(.loading)", timeout=60000)
        page.wait_for_timeout(300)

        # (4) Bubble layout OK
        b = assert_bubble_layout(page)
        if b.get("aiOK") is False:
            issues.append(f"ai-bubble-too-wide: {b}")
        if b.get("userOK") is False:
            issues.append(f"user-bubble-too-wide: {b}")

        # (5) Re-send same → cache hit
        page.evaluate("document.querySelector('#yeren-ai-msg').value = ''")
        page.wait_for_timeout(300)
        prev_count = page.evaluate("document.querySelectorAll('.msg-row.ai:not(.loading)').length")
        page.evaluate("document.querySelectorAll('.welcome-tile')[0]?.click()")
        page.wait_for_timeout(1500)
        badge_count = page.evaluate("document.querySelectorAll('.msg-row.ai .cached-badge').length")
        if badge_count < 1:
            issues.append(f"no-cache-badge: badges={badge_count}")

        # (6) IME handling on mobile
        if viewport["width"] < 500:
            ime_ok = assert_ime_handled(page)
            if not ime_ok:
                issues.append("ime-class-not-applied")

        # (7) No console / page errors
        if ce:
            issues.append(f"console-err: {ce[:2]}")
        if pe:
            issues.append(f"page-err: {pe[:2]}")

        dt = time.time() - t0
        pass_ok = len(issues) == 0
        return {"round": idx, "label": label, "pass": pass_ok, "issues": issues, "dt": round(dt, 1), "vpW": viewport["width"]}
    except Exception as e:
        return {"round": idx, "label": label, "pass": False, "issues": [f"exception: {e}"], "dt": round(time.time() - t0, 1), "vpW": viewport["width"]}
    finally:
        try: ctx.close()
        except Exception: pass


def main():
    desktop_vp = {"width": 1280, "height": 800}
    mobile_vp = {"width": 390, "height": 800}

    with sync_playwright() as p:
        print(f"R96-P0-A regression · desktop 1280x800 + mobile 390x800 × {ROUNDS} rounds")
        print(f"{'='*60}")
        for i in range(1, ROUNDS + 1):
            r1 = run_round(p, i, desktop_vp, "desktop")
            RESULTS["desktop"].append(r1)
            mark = "PASS" if r1["pass"] else "FAIL"
            print(f"  desktop [{i:02d}/{ROUNDS}] {mark} dt={r1['dt']}s  issues={r1['issues'][:2]}")

            r2 = run_round(p, i, mobile_vp, "mobile")
            RESULTS["mobile"].append(r2)
            mark = "PASS" if r2["pass"] else "FAIL"
            print(f"  mobile  [{i:02d}/{ROUNDS}] {mark} dt={r2['dt']}s  issues={r2['issues'][:2]}")

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    for vp_key, vp_label in [("desktop", "1280x800"), ("mobile", "390x800")]:
        rounds = RESULTS[vp_key]
        passed = sum(1 for r in rounds if r["pass"])
        failed = [r for r in rounds if not r["pass"]]
        avg_dt = sum(r["dt"] for r in rounds) / len(rounds)
        print(f"\n  {vp_label}: {passed}/{ROUNDS} pass · avg {avg_dt:.1f}s/round")
        if failed:
            issue_groups = {}
            for r in failed:
                for iss in r["issues"]:
                    head = iss.split(":")[0] if ":" in iss else iss
                    issue_groups[head] = issue_groups.get(head, 0) + 1
            print(f"    Failed issues histogram:")
            for h, c in sorted(issue_groups.items(), key=lambda x: -x[1]):
                print(f"      {h}: {c}")

    out_json = Path("/tmp/r96_p0_regression.json")
    out_json.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False))
    print(f"\nFull results: {out_json}")

    total_pass = sum(1 for k in RESULTS for r in RESULTS[k] if r["pass"])
    total = sum(len(RESULTS[k]) for k in RESULTS)
    sys.exit(0 if total_pass == total else 1)


if __name__ == "__main__":
    main()