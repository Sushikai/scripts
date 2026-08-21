"""R133b 诊断: 实测 loadStockDetail 后 inflight key 生命周期."""
from playwright.sync_api import sync_playwright
import time, sys

BASE = "http://127.0.0.1:7799"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_function("typeof showView === 'function'", timeout=15000)
    page.wait_for_timeout(2000)
    print(f"[setup] typeof loadStockDetail = {page.evaluate('typeof loadStockDetail')}")
    print(f"[setup] window._stockDetailInflightKey = {page.evaluate('window._stockDetailInflightKey')!r}")
    page.evaluate("location.hash = 'stock=600519'")
    # 给 200ms 起步
    for i in range(20):
        time.sleep(0.2)
        v = page.evaluate("window._stockDetailInflightKey")
        inflight = page.evaluate("!!window._stockDetailInflight")
        # 同时检查渲染是否完成 (stock-title 有内容)
        title = page.evaluate("document.querySelector('#stock-title')?.textContent || ''")
        print(f"[{i*0.2:.1f}s] key={v!r} inflight={inflight} title='{title[:30]}'")
        if not v and not inflight:
            print(f"[done] inflight cleared at {i*0.2:.1f}s")
            break
    else:
        print("[done] NEVER CLEARED after 4s")
    ctx.close()
    browser.close()