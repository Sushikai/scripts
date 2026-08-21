"""R20 跨模块 dexin 集成端到端验证 — 桌面 1280×800 + 移动 390×844
覆盖:
  1) sidebar 第 6 项 = 得鑫量变 (data-jump=dexin)
  2) 点 sidebar 得鑫 → showView(dexin)
  3) 全 A 表格「得鑫」列存在 + 多行 cell 有 .dxin-row-badge
  4) 点击得鑫 cell → 弹 modal (有 phase + advice 或 error)
  5) 个股页搜索 600519 → pill 验按钮存在
  6) 移动端 sidebar 也能点开得鑫
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "http://localhost:7799"
OUT = Path("/tmp/dexin_e2e")
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸 {p}")


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        page = ctx.new_page()
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("=== 1) 桌面打开首页, 验证 sidebar 第 6 项 = 得鑫量变 ===")
        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#sidebar [data-jump='dexin']", timeout=10000)
        dexin_item = page.query_selector("#sidebar [data-jump='dexin']")
        if not dexin_item:
            errors.append("sidebar 找不到 data-jump=dexin")
        else:
            txt = dexin_item.inner_text().strip()
            print(f"  sidebar item: {txt!r}")
            if "得鑫" not in txt:
                errors.append(f"sidebar item 文字不是得鑫: {txt!r}")
            nav_items = [el for el in page.query_selector_all("#sidebar .sidebar-item") if el.get_attribute("data-jump")]
            if len(nav_items) >= 6:
                sixth = nav_items[5].get_attribute("data-jump")
                if sixth != "dexin":
                    errors.append(f"sidebar 第 6 项应是 dexin, 实际是 {sixth!r}")
            else:
                errors.append(f"sidebar 导航项只有 {len(nav_items)} 个, < 6")
        shot(page, "01-desktop-home")

        print("=== 2) 点击 sidebar 得鑫 → showView(dexin) ===")
        page.click("#sidebar [data-jump='dexin']")
        page.wait_for_timeout(800)
        dexin_view = page.query_selector(".view-dexin")
        if dexin_view and dexin_view.get_attribute("hidden") is not None:
            errors.append("点击 sidebar 得鑫量变后, view-dexin 仍 hidden")
        shot(page, "02-desktop-dexin")

        print("=== 3) 全 A 表格「得鑫」列存在 + 多行 ===")
        page.goto(BASE + "#all_stocks", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#as-stocks-tbody", timeout=10000)
        # 等数据 — board 接口可慢
        page.wait_for_timeout(25000)
        col_hdr = page.query_selector("#as-stocks-table thead th[data-col='得鑫']")
        if not col_hdr:
            errors.append("全 A 表格 thead 缺少「得鑫」列")
        else:
            print(f"  thead 得鑫: {col_hdr.inner_text().strip()!r}")
        cnt = page.evaluate("document.querySelectorAll('#as-stocks-tbody td[data-col=\"得鑫\"]').length")
        print(f"  得鑫 cells: {cnt}")
        if cnt == 0:
            errors.append("全 A 表格无任何得鑫 cell")
        else:
            first_badge = page.query_selector("#as-stocks-tbody td[data-col='得鑫'] .dxin-row-badge")
            if not first_badge:
                errors.append("得鑫 cell 内无 .dxin-row-badge")
            else:
                print(f"  first badge: {first_badge.inner_text().strip()!r}")
        shot(page, "03-desktop-all-stocks")

        print("=== 4) 用 dexin-check API 已知有数据的股 600519 → 弹 modal ===")
        try:
            # 直接调用 dexinCheckOne 跨过表格 row 选股 (避免按到 data_short 的股)
            page.evaluate("dexinCheckOne('600519')")
            page.wait_for_selector(".dexin-check-modal", timeout=8000)
            page.wait_for_function(
                "(() => { const b = document.querySelector('.dexin-modal-body'); return b && (b.querySelector('.dexin-modal-advice') || b.querySelector('.dexin-modal-error')); })()",
                timeout=15000,
            )
            phase = page.query_selector(".dexin-modal-phase")
            advice = page.query_selector(".dexin-modal-advice")
            modal_error = page.query_selector(".dexin-modal-error")
            if not phase:
                errors.append("modal 无 .dexin-modal-phase")
            else:
                ptext = phase.inner_text().strip()
                print(f"  modal phase: {ptext!r}")
                if ptext == "·":
                    errors.append("modal phase 仍占位 ·, API 没回来")
            if advice:
                print(f"  modal advice: {advice.inner_text().strip()[:80]!r}")
            elif modal_error:
                emsg = modal_error.inner_text().strip()
                print(f"  modal error: {emsg[:80]!r}")
                errors.append(f"已知有数据股 600519 仍报错: {emsg}")
            # 验证 phase_dates chips
            chips = page.query_selector_all(".dexin-check-modal .dx-phase-chip")
            print(f"  phase_dates chips: {len(chips)}")
            if len(chips) == 0:
                errors.append("modal 无 phase_dates chips (5 阶段链条未渲染)")
            # 验证 footer 按钮可见
            footer = page.query_selector(".dexin-check-modal .dexin-modal-footer")
            if footer and footer.is_visible():
                print("  footer 按钮可见 ✓")
            else:
                errors.append("modal footer 按钮未显示")
            shot(page, "04-desktop-dexin-modal")
            close_btn = page.query_selector(".dexin-modal-close")
            if close_btn:
                close_btn.click()
        except PWTimeout as e:
            errors.append(f"弹 modal 超时: {e}")

        print("=== 5) 个股页搜索 600519 → pill 验按钮存在 ===")
        page.goto(BASE + "#stock", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#stock-search", timeout=10000)
        page.fill("#stock-search", "600519")
        page.wait_for_timeout(3000)
        pills = page.query_selector_all(".result-pill")
        if not pills:
            errors.append("搜索 600519 没有 .result-pill")
        else:
            first_pill = pills[0]
            rp_dxin = first_pill.query_selector(".rp-dxin")
            if not rp_dxin:
                errors.append("搜索 pill 无 .rp-dxin 验按钮")
            else:
                action = rp_dxin.get_attribute("data-action")
                print(f"  pill 验按钮: {rp_dxin.inner_text().strip()!r}, action={action!r}")
                if not action or "dexin-check" not in action:
                    errors.append(f"pill 验按钮 data-action 不是 dexin-check: {action!r}")
        shot(page, "05-desktop-search-pill")

        ctx.close()

        print("=== 6) 移动端 viewport sidebar ===")
        mobile_ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, ignore_https_errors=True)
        mobile_page = mobile_ctx.new_page()
        mobile_page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        mobile_page.wait_for_timeout(1500)
        mobile_page.click("#menu-btn")
        mobile_page.wait_for_selector("body.sidebar-open", timeout=5000)
        mobile_page.wait_for_timeout(500)
        dexin_mobile = mobile_page.query_selector("#sidebar [data-jump='dexin']")
        if not dexin_mobile:
            errors.append("移动端 sidebar 找不到 data-jump=dexin")
        else:
            print(f"  移动端 sidebar: {dexin_mobile.inner_text().strip()!r}")
        shot(mobile_page, "06-mobile-sidebar")
        mobile_ctx.close()

        browser.close()

    critical = [e for e in console_errors if "favicon" not in e.lower() and "manifest" not in e.lower()]
    if critical:
        print(f"\n⚠ console errors ({len(critical)}):")
        for e in critical[:10]:
            print(f"  - {e[:200]}")

    print("\n=== RESULT ===")
    if errors:
        print(f"❌ {len(errors)} 个错误:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ 全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
