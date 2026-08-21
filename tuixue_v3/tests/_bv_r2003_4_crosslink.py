"""R2003.4 验证: 跨页串联 — yeren 页 → 跳 BV, window.__bv 暴露"""
import sys
from playwright.sync_api import sync_playwright

YEREN_URL = "http://127.0.0.1:7799/?view=yeren"
BV_URL = "http://127.0.0.1:7799/?view=bv"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

    # 1. 打开 yeren 页
    page.goto(YEREN_URL, wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector(".view-yeren", state="visible", timeout=15_000)
    page.wait_for_timeout(2_000)

    # 2. 找 BV 跨链 link
    bv_link = page.query_selector('a[data-jump="bv"]')
    print(f"bv_link found: {bv_link is not None}")
    if bv_link:
        text = bv_link.text_content()
        print(f"bv_link text: {text.strip()}")

    # 3. 跳到 BV
    page.goto(BV_URL, wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    page.wait_for_timeout(3_000)
    page.evaluate("document.dispatchEvent(new CustomEvent('view-enter', {detail: {name: 'bv', prev: 'yeren'}}))")
    page.wait_for_timeout(3_000)

    # 4. 验证 window.__bv 暴露
    bv_picks = page.evaluate("(window.__bv && window.__bv.picks) ? window.__bv.picks.length : -1")
    bv_rules = page.evaluate("(window.__bv && window.__bv.rules) ? window.__bv.rules.length : -1")
    print(f"window.__bv.picks: {bv_picks}")
    print(f"window.__bv.rules: {bv_rules}")

    # 5. 试 scrollToRule
    scrolled = page.evaluate("""
        (() => {
            if (window.__bv && window.__bv.scrollToRule) {
                window.__bv.scrollToRule('BV03');
                return true;
            }
            return false;
        })()
    """)
    print(f"scrollToRule: {scrolled}")

    page.screenshot(path="/tmp/bv_r2003_4_crosslink.png", full_page=True)
    print(f"errors ({len(errors)}):")
    for e in errors[:5]:
        print(f"  - {e}")
    browser.close()
