"""深度调试 — 等更长时间让网络回来,看渲染了什么"""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
    page = ctx.new_page()

    msgs = []
    page.on("console", lambda m: msgs.append(f"{m.type}: {m.text[:300]}"))
    page.on("pageerror", lambda e: msgs.append(f"PAGEERROR: {e}"))

    page.goto("http://127.0.0.1:7799/?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-stock", timeout=10000)
    page.wait_for_timeout(5000)

    # 点 related tab
    page.locator("button.chart-tab[data-tab='related']").first.click()

    # 等更久
    page.wait_for_timeout(8000)

    related_html = page.evaluate("""
      () => document.querySelector('#related-by-concept')?.innerHTML || ''
    """)
    print(f"related html length: {len(related_html)}")
    print(f"related html preview (first 600):")
    print(related_html[:600])

    # 检查是否有 table
    has_table = page.evaluate("""
      () => document.querySelectorAll('#related-by-concept table').length
    """)
    has_target = page.evaluate("""
      () => document.querySelectorAll('#related-by-concept .related-target').length
    """)
    print(f"\ntable count: {has_table}, target count: {has_target}")

    print("\n=== all console msgs ===")
    for m in msgs[-20:]:
        print(f"  {m}")

    ctx.close()
    browser.close()