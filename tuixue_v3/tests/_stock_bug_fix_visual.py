"""个股页 日K + 新闻 bug 修复前后视觉验证"""
import sys
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/stock_bug_visual")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, viewport, mobile in [
            ("desktop", {"width": 1280, "height": 800}, False),
            ("mobile", {"width": 390, "height": 844}, True),
        ]:
            ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, ignore_https_errors=True, service_workers="block")
            page = ctx.new_page()
            console_msgs = []
            page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))

            # 1. 直接访问 600519 个股页
            page.goto(BASE + "?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-stock", timeout=10000)
            page.wait_for_timeout(8000)  # 等数据回来
            page.evaluate("document.body.classList.remove('sidebar-open')")

            # 2. 等日K 数据 ready
            kline_len = 0
            try:
                page.wait_for_function("window.klineState && window.klineState.data && window.klineState.data.length > 0", timeout=15000)
                kline_len = page.evaluate("window.klineState.data.length")
            except Exception as e:
                kline_len = page.evaluate("(window.klineState && window.klineState.data && window.klineState.data.length) || 0")
            print(f"[{label}] 600519 kline len: {kline_len}")

            # 3. 检查 K 线图是否实际渲染 (canvas/svg)
            kline_canvas = page.evaluate("document.querySelectorAll('#kline-chart canvas, #kline-chart svg').length")
            kline_data = page.evaluate("window.klineState ? window.klineState.data.length : 0")
            print(f"[{label}] K-line canvas/svg: {kline_canvas}, klineState.data: {kline_data}")

            # 强制切到 K 线 tab 触发 drawKlineChart
            try:
                page.click("[data-tab='kline']")
                page.wait_for_timeout(2000)
                kline_canvas_after = page.evaluate("document.querySelectorAll('#kline-chart canvas, #kline-chart svg').length")
                print(f"[{label}] After kline-tab click, canvas/svg: {kline_canvas_after}")
            except Exception as e:
                print(f"kline tab click err: {e}")

            # 截图: 全页
            p = OUT / f"{label}-stock-600519-full.png"
            page.screenshot(path=str(p), full_page=False)
            print(f"📸 {p}")

            # 4. 切到 K 线 tab (如果不在)
            try:
                page.click("[data-tab='kline']")
                page.wait_for_timeout(1500)
                p = OUT / f"{label}-stock-600519-kline-tab.png"
                page.screenshot(path=str(p), full_page=False)
                print(f"📸 {p}")
            except Exception as e:
                print(f"kline tab click err: {e}")

            # 5. 检查新闻
            news_count = page.evaluate("document.querySelectorAll('#news-list *').length")
            news_titles = page.evaluate("""
                Array.from(document.querySelectorAll('#news-list a, #news-list .news-title, #news-list li')).slice(0,5).map(e => (e.textContent || '').slice(0, 100))
            """)
            print(f"[{label}] news DOM items: {news_count}")
            for t in news_titles:
                print(f"  news: {t}")

            p = OUT / f"{label}-stock-600519-news.png"
            try:
                # 切到 news tab
                news_tab = page.query_selector("[data-tab='news'], [data-tab='tab-news'], .tab-news")
                if news_tab:
                    news_tab.click()
                    page.wait_for_timeout(2000)
                page.evaluate("document.querySelector('#news-list')?.scrollIntoView({block: 'center'})")
                page.wait_for_timeout(500)
                page.screenshot(path=str(p), full_page=False)
                print(f"📸 {p}")
            except Exception as e:
                print(f"news shot err: {e}")

            # 6. 网络请求检查 - K-line 与 news 端点
            api_calls = []
            page2 = ctx.new_page()
            page2.on("response", lambda r: api_calls.append((r.url, r.status)))
            page2.goto(BASE + "?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
            page2.wait_for_timeout(8000)
            print(f"\n[{label}] API calls:")
            for url, st in api_calls:
                if "stock/" in url and ("kline" in url or "news" in url or "related" in url):
                    print(f"  {st} {url[:120]}")

            ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()