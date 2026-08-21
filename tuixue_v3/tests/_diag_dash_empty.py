"""诊断首页 dashboard 卡片空白 + 热力图缺失"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/dash_diag")
OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1800})
        page = ctx.new_page()
        console = []
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

        print("=== 打开首页 #dash ===")
        # 用 ?v= bust cache,避免 SW/浏览器复用旧 view-dash.js
        page.goto(f"{BASE}/?v={int(time.time())}#dash", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(10000)  # 等数据加载

        # 取所有 card
        cards = page.query_selector_all(".card, [class*='card']")
        print(f"=== 找到 {len(cards)} 个 card-like 元素 ===")
        for i, c in enumerate(cards):
            try:
                txt = c.inner_text().strip()
                if not txt: continue
                head = txt[:80].replace("\n", " | ")
                cls = c.get_attribute("class") or ""
                print(f"  [{i}] .{cls[:40]} | {head}")
            except Exception as e:
                print(f"  [{i}] err: {e}")

        page.screenshot(path=str(OUT / "01_dash.png"), full_page=True)

        # 看 index_trend 容器
        print("\n=== index_trend 容器 ===")
        trend_grid = page.query_selector("#index-trend-grid")
        if trend_grid:
            inner_html = trend_grid.inner_html()[:600]
            print("innerHTML:", inner_html)
            print("子元素数:", len(page.query_selector_all("#index-trend-grid > *")))

        # 看 heat-map 容器
        print("\n=== heat-map 容器 ===")
        hm = page.query_selector(".treemap, #treemap, [class*='treemap']")
        print("heatmap el:", hm)

        print("\n=== console 错误 ===")
        errs = [t for typ, t in console if typ in ("error", "pageerror")]
        for e in errs[:20]:
            print("  ", e[:300])

        browser.close()


if __name__ == "__main__":
    main()