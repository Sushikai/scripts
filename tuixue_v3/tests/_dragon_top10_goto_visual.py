"""验证龙头页 STEP 3 Top 10 卡片跳转个股页 (2026-08-08 修复)"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text[:150]}") if m.type == "error" else None)

        page.goto(BASE + "#dragons", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#dragons-top10 .dragon-card", timeout=20000)
        page.wait_for_timeout(3000)

        n_cards = page.locator("#dragons-top10 .dragon-card").count()
        print(f"Top10 卡片数: {n_cards}")
        if n_cards == 0:
            print("无 Top10 数据,跳过跳转验证")
            browser.close()
            return

        # 1. 卡片上 stock-link 数量与首个 code
        links = page.locator("#dragons-top10 .stock-link[data-code]")
        n_links = links.count()
        print(f"卡片内 stock-link 数量: {n_links} (期望 2/卡)")
        first_code = page.evaluate(
            "() => document.querySelector('#dragons-top10 .stock-link[data-code]')?.dataset.code"
        )
        print(f"首个可跳转 code: {first_code}")

        # 2. 点击名称 → 应进入个股页
        page.locator("#dragons-top10 .dragon-card").first.locator(".dragon-name.stock-link").click()
        page.wait_for_selector(".view-stock", timeout=10000)
        page.wait_for_timeout(3000)
        hash_after = page.evaluate("() => location.hash")
        stock_code = page.evaluate("() => window._currentStockCode || document.querySelector('.stock-title-code')?.textContent")
        print(f"点击名称后 hash: {hash_after} | 个股 code: {stock_code}")

        # 3. 截图个股页
        page.screenshot(path="/tmp/dragon_top10_goto_stock.png", full_page=False)

        # 4. 返回龙头页,验证 bd 展开未受影响
        page.goto(BASE + "#dragons", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#dragons-top10 .dragon-card", timeout=20000)
        page.wait_for_timeout(1500)
        card = page.locator("#dragons-top10 .dragon-card").first
        card.click(position={"x": 200, "y": 70})  # 点击卡片空白处 (head 以下区域)
        page.wait_for_timeout(300)
        bd_visible = card.evaluate(
            "() => !document.querySelector('#dragons-top10 .dragon-card .dragon-bd')?.hidden"
        )
        print(f"点击卡片空白处 → bd 展开: {bd_visible}")

        # 5. 点击 bd 内的"查看完整个股分析"按钮 → 跳个股页
        page.locator("#dragons-top10 .dragon-card button[data-goto]").first.click()
        page.wait_for_selector(".view-stock", timeout=10000)
        page.wait_for_timeout(2000)
        hash2 = page.evaluate("() => location.hash")
        print(f"点击 bd 按钮后 hash: {hash2}")

        real_errors = [e for e in errors if "ERR_CONNECTION" not in e]
        print(f"\nconsole/page errors (excl conn): {len(real_errors)}")
        for e in real_errors[:5]:
            print(f"   - {e[:180]}")

        browser.close()


if __name__ == "__main__":
    main()
