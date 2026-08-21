"""R2003.2 验证: 截 BV 战法页 + 检查 console error + 抓 picks 数量"""
import sys
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:7799/?view=bv"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errors = []
    failed_reqs = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("response", lambda r: failed_reqs.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    page.goto(URL, wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector(".view-bv", state="visible", timeout=15_000)
    # 等待 globals 加载
    page.wait_for_function("typeof window._api !== 'undefined' || typeof window.api !== 'undefined'", timeout=10_000)
    # 确保 bv-frontend.js 也加载完
    page.wait_for_timeout(3_000)
    # 触发 view-enter (URL hash 路由不派发, bv-frontend.js 监听 document)
    page.evaluate("document.dispatchEvent(new CustomEvent('view-enter', {detail: {name: 'bv', prev: 'dash'}}))")
    page.wait_for_timeout(5_000)  # let JS render picks

    # 抓 picks 数量
    n_picks = page.evaluate("document.querySelectorAll('#bv-picks-body tr').length")
    # 抓 phase banner
    phase = page.evaluate("document.querySelector('.bv-phase-banner')?.textContent?.trim()")
    # 抓 meta 行
    meta = page.evaluate("document.querySelector('.bv-meta')?.textContent?.trim()")
    # 抓 picks 第一行
    first_pick = page.evaluate("document.querySelector('#bv-picks-body tr')?.textContent?.trim()")
    # 抓 picks 状态行 (空态显示)
    picks_status = page.evaluate("document.querySelector('.bv-picks-status')?.textContent?.trim()")
    # 抓空态文案
    empty_msg = page.evaluate("document.querySelector('.bv-empty')?.textContent?.trim()")
    # 抓 raw _picks state
    raw_picks_len = page.evaluate("(window.__bv && window.__bv._picks) ? window.__bv._picks.length : -1")
    # 直接 fetch 验证
    fetch_result = page.evaluate("""
        async () => {
            try {
                const r = await fetch('/api/bv/live_pick?refresh=1');
                const j = await r.json();
                return {ok: j.ok, picks: j.data?.picks?.length, scanned: j.data?.scanned, matched: j.data?.matched};
            } catch(e) { return {error: e.toString()}; }
        }
    """)

    page.screenshot(path="/tmp/bv_r2003_2_screenshot.png", full_page=True)
    print(f"OK picks={n_picks} phase={phase} meta={meta}")
    print(f"first_pick={first_pick}")
    print(f"picks_status={picks_status}")
    print(f"empty_msg={empty_msg}")
    print(f"raw_picks_len={raw_picks_len}")
    print(f"fetch_result={fetch_result}")
    print(f"failed_reqs ({len(failed_reqs)}):")
    for r in failed_reqs[:5]:
        print(f"  - {r}")
    print(f"errors ({len(errors)}):")
    for e in errors[:5]:
        print(f"  - {e}")
    browser.close()
