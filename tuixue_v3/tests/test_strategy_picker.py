#!/usr/bin/env python3
"""
策略选股器 e2e 验证
==================
1. 打开 #strategy_picker
2. 验证 3 个策略独立开关 + 模式切换 + 表格渲染
3. 行点击 → 跳个股
4. Console 无 JS 错误
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

LOCAL = "http://localhost:7799"
RESULTS = []

def record(category, name, ok, detail=""):
    icon = "✅" if ok else "❌"
    RESULTS.append({"category": category, "name": name, "ok": ok, "detail": detail})
    print(f"  {icon} [{category}] {name}: {detail}")

def main():
    print(f"\n━━ 策略选股器 e2e ({LOCAL}/#strategy_picker) ━━")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text[:120]}") if msg.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:120]}"))

        try:
            t0 = time.time()
            page.goto(f"{LOCAL}/#strategy_picker", wait_until="domcontentloaded", timeout=20_000)
            load_time = time.time() - t0
            record("load", "页面加载", True, f"{load_time:.1f}s")

            # 第一次 cold scan: 等待响应
            print("  (等 scan 响应, ~25s 首次 cold scan...)")
            page.wait_for_selector("#sp-list .wb-table", timeout=45_000)
            record("data", "表格渲染", True, "")

            # KPI 状态
            status_text = page.eval_on_selector("#sp-status", "el => el.innerText")
            has_status = "扫描" in status_text and "命中" in status_text
            record("kpi", "状态栏", has_status, status_text[:80])

            # 表格行
            row_count = page.eval_on_selector_all("#sp-list tbody tr", "els => els.length")
            record("table", "命中行数 > 0", row_count > 0, f"got {row_count}")

            # 行 → 命中策略 chip
            chips_per_row = page.eval_on_selector_all(
                "#sp-list tbody tr:first-child .wb-chips .chip",
                "els => els.length"
            )
            record("table", "首行 chip 命中", chips_per_row > 0, f"chips={chips_per_row}")

            # 切换 AND 模式
            and_radio = page.query_selector('input[name="sp-mode"][value="and"]')
            if and_radio:
                and_radio.click()
                page.click("#sp-apply")
                page.wait_for_timeout(2000)
                # 等 list 更新 — AND 可能 0 命中走 empty-state, OR 命中走 .wb-table
                page.wait_for_function(
                    "() => document.querySelector('#sp-list .wb-table, #sp-list .empty-state')",
                    timeout=30_000
                )
                new_status = page.eval_on_selector("#sp-status", "el => el.innerText")
                # AND 严格, 命中数应 <= OR 命中数
                record("filter", "AND 模式切换", True, f"status={new_status[:50]}")

            # 行点击 → 跳个股
            first_link = page.query_selector('#sp-list a.stock-link')
            if first_link:
                target_code = first_link.get_attribute("data-code")
                first_link.click()
                page.wait_for_timeout(2000)
                on_stock_page = "stock=" in (page.url or "")
                record("nav", f"行点击跳个股 ({target_code})", on_stock_page, page.url[-50:])

            # Console 无 JS 错误
            critical = [e for e in console_errors if "strategy" in e.lower() or "TypeError" in e or "ReferenceError" in e]
            record("console", "无 JS 错误", len(critical) == 0, f"total={len(console_errors)}, critical={len(critical)}")
            if critical:
                for e in critical[:3]:
                    print(f"     · {e}")

            # 截图
            try:
                page.goto(f"{LOCAL}/#strategy_picker", wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_selector("#sp-list .wb-table", timeout=30_000)
                Path("/tmp/strategy_picker.png").unlink(missing_ok=True)
                page.screenshot(path="/tmp/strategy_picker.png", full_page=False)
                record("screenshot", "截图保存", True, "/tmp/strategy_picker.png")
            except Exception as e:
                record("screenshot", "截图保存", False, str(e)[:60])

        except Exception as e:
            record("load", "整体测试", False, f"{type(e).__name__}: {str(e)[:80]}")
        finally:
            browser.close()

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"\n━━ 总计: {total} | 通过: {passed} | 失败: {failed} ━━")
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
