"""调试 fetch 错误"""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
    page = ctx.new_page()
    page.goto("http://127.0.0.1:7799/?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-stock", timeout=10000)
    page.wait_for_timeout(5000)

    # 直接 fetch 测试
    result = page.evaluate("""
      async () => {
        try {
          const r = await fetch('/api/stock/600519/related_stocks?limit=5');
          const j = await r.json();
          return { ok: r.ok, status: r.status, hasData: !!j.data, count: j.data?.count };
        } catch (e) {
          return { error: e.message };
        }
      }
    """)
    print(f"plain fetch result: {result}")

    # 测试 fetchApi (module 内部)
    result2 = page.evaluate("""
      async () => {
        if (!window.__tx3StockPro) return { error: 'no module' };
        // 直接调用 endpoint 测试
        try {
          const r = await fetch('/api/stock/600519/related_stocks?limit=5');
          const txt = await r.text();
          return { status: r.status, len: txt.length, preview: txt.slice(0, 200) };
        } catch (e) {
          return { error: e.message };
        }
      }
    """)
    print(f"fetch with text: {result2}")

    ctx.close()
    browser.close()