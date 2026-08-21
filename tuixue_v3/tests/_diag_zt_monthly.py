"""诊断涨停溢价页回测结果: 按月折叠交易明细表格"""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/zt_monthly_diag")
OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = ctx.new_page()
        console = []
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

        print("=== 直接打开 #screener ===")
        page.goto(f"{BASE}/#screener", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        # 等 zt-mount 出现
        try:
            page.wait_for_selector("#zt-mount", timeout=10000)
            print("zt-mount 已挂载")
        except Exception as e:
            print("!! zt-mount 未出现:", e)

        # 训练权重卡
        print("=== 训练权重卡 ===")
        page.wait_for_timeout(1500)
        wts = page.query_selector_all(".card:has-text('打分维度权重')")
        if wts:
            print("权重卡存在, 内容:")
            print("  ", wts[0].inner_text().replace("\n", " | ")[:200])
        else:
            print("!! 未找到训练权重卡")

        bt = page.query_selector("#zt-bt-run")
        if not bt:
            print("!! 找不到 #zt-bt-run")
            page.screenshot(path=str(OUT / "00_no_button.png"))
            browser.close()
            return
        start = page.query_selector("#zt-bt-start")
        end = page.query_selector("#zt-bt-end")
        print("默认日期:", start.get_attribute("value") if start else None,
              "→", end.get_attribute("value") if end else None)

        print("=== 点击 ▶ 运行 (等待回测, 长任务) ===")
        bt.click()
        t0 = time.time()
        deadline = t0 + 240
        done = False
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            el = page.query_selector("#zt-trades-table")
            if el:
                print(f"[{time.time()-t0:.0f}s] 检测到交易明细表 #zt-trades-table")
                done = True
                break
            err = page.query_selector(".card:has-text('错误')")
            if err:
                print(f"[{time.time()-t0:.0f}s] 错误卡片:", err.inner_text()[:200])
                break
            if page.query_selector("kpi-group"):
                print(f"[{time.time()-t0:.0f}s] 检测到 KPI 卡片 (回测完成)")
        if not done:
            print("!! 240s 内未出现交易明细表")
            page.screenshot(path=str(OUT / "01_timeout.png"), full_page=True)
            browser.close()
            return

        page.screenshot(path=str(OUT / "01_loaded.png"), full_page=False)

        # 验证按月折叠结构
        print("=== 按月折叠结构 ===")
        month_heads = page.query_selector_all("#zt-trades-table .zt-month-head")
        print("月份头行数量:", len(month_heads))
        for i, mh in enumerate(month_heads):
            txt = mh.inner_text().replace("\n", " ").strip()
            print(f"  月[{i}]: {txt[:120]}")
        open_rows = page.query_selector_all("#zt-trades-table .zt-month-row:not([hidden])")
        hidden_rows = page.query_selector_all("#zt-trades-table .zt-month-row[hidden]")
        print("展开中的逐笔行:", len(open_rows), "| 收起:", len(hidden_rows))

        # 点第二个月 → 展开
        if len(month_heads) >= 2:
            print("=== 点击第 2 个月 → 展开 ===")
            m2 = month_heads[1]
            m2.click()
            page.wait_for_timeout(400)
            hidden_rows2 = page.query_selector_all("#zt-trades-table .zt-month-row[hidden]")
            print("点击后收起行数:", len(hidden_rows2), "(应比之前少)")
            page.screenshot(path=str(OUT / "02_month2_expanded.png"), full_page=False)

        # 点第一个月 → 收起
        if month_heads:
            print("=== 点击第 1 个月 → 收起 ===")
            month_heads[0].click()
            page.wait_for_timeout(400)
            hidden_rows3 = page.query_selector_all("#zt-trades-table .zt-month-row[hidden]")
            print("点击后收起行数:", len(hidden_rows3), "(应比之前多)")
            page.screenshot(path=str(OUT / "03_month1_collapsed.png"), full_page=False)

        # 表头排序
        print("=== 表头排序 (点 主退场%) ===")
        th = page.query_selector('#zt-trades-table th[data-key="return_pct"]')
        if th:
            th.click()
            page.wait_for_timeout(400)
            # 读首笔 return
            first = page.query_selector("#zt-trades-table .zt-month-row:not([hidden]) td:nth-child(4)")
            print("排序后首笔 return:", first.inner_text() if first else "none")
            page.screenshot(path=str(OUT / "04_sorted.png"), full_page=False)

        print("=== console 错误 ===")
        errs = [t for typ, t in console if typ in ("error", "pageerror")]
        if errs:
            for t in errs[:10]:
                print("  ", t[:300])
        else:
            print("  无")

        browser.close()
        print("=== DONE ===")


if __name__ == "__main__":
    main()
