"""快速调试 — 验证 stock-pro-modules.js 是否被加载,以及 related tab 点击后发生了什么"""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
    page = ctx.new_page()

    msgs = []
    page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: msgs.append(f"PAGEERROR: {e}"))

    page.goto("http://127.0.0.1:7799/?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-stock", timeout=10000)
    page.wait_for_timeout(5000)

    # 检查模块是否加载
    has_pro = page.evaluate("typeof window.__tx3StockPro")
    print(f"window.__tx3StockPro type: {has_pro}")

    # 检查视图脚本是否就绪
    has_stock_pro_src = page.evaluate("""
      () => {
        const scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
        return scripts.some(s => s.includes('stock-pro-modules.js'));
      }
    """)
    print(f"stock-pro-modules.js script src found: {has_stock_pro_src}")

    # 检查当前激活的 tab 和相关个股 dom
    active_tab = page.evaluate("""
      () => {
        const tabs = document.querySelectorAll('.view-stock .chart-tab.active');
        return Array.from(tabs).map(t => t.dataset.tab);
      }
    """)
    print(f"active tabs: {active_tab}")

    # 点 related tab
    page.locator("button.chart-tab[data-tab='related']").first.click()
    page.wait_for_timeout(3500)

    # 检查 related tab 内容
    related_html_len = page.evaluate("""
      () => {
        const el = document.querySelector('#related-by-concept');
        return el ? el.innerHTML.length : 0;
      }
    """)
    print(f"related-by-concept html length: {related_html_len}")

    has_pro_after = page.evaluate("typeof window.__tx3StockPro")
    print(f"after click, window.__tx3StockPro: {has_pro_after}")

    # 网络监听
    network = []
    def on_req(req):
        if 'related_stocks' in req.url:
            network.append(req.url)
    page.on("request", on_req)

    page.locator("button.chart-tab[data-tab='related']").first.click()
    page.wait_for_timeout(3500)
    print(f"related_stocks network requests: {len(network)}")
    for u in network[:5]:
        print(f"  {u[:120]}")

    print("\n=== console msgs (filtered) ===")
    for m in msgs:
        if "stock" in m.lower() or "related" in m.lower() or "pro" in m.lower() or "error" in m.lower():
            print(f"  {m[:200]}")
    ctx.close()
    browser.close()