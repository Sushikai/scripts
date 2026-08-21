"""诊断涨停溢价页: 渲染状态 + 回测按钮行为 + console 错误"""
import sys, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/zt_diag")
OUT.mkdir(parents=True, exist_ok=True)


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        console = []
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

        print("=== 打开首页 ===")
        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        # 点 sidebar 涨停溢价
        print("=== 点击 sidebar 涨停溢价 (screener) ===")
        try:
            page.click("#sidebar [data-jump='screener']", timeout=8000)
        except Exception as e:
            print("sidebar click failed:", e)
            page.click("[data-jump='screener']", timeout=5000)
        page.wait_for_timeout(2000)

        # zt-mount 内容
        mount_html = page.eval_on_selector("#zt-mount", "el => el.innerHTML.slice(0, 2000)")
        print("=== zt-mount HTML (前 2000 字符) ===")
        print(mount_html)
        page.screenshot(path=str(OUT / "01_zt_view.png"), full_page=False)

        # 找回测按钮
        print("=== 回测按钮状态 ===")
        bt = page.query_selector("#zt-bt-run")
        if bt:
            print("button exists, disabled:", bt.get_attribute("disabled"))
        else:
            print("!! 找不到 #zt-bt-run 按钮")

        # 检查日期 input 值
        for sel in ["#zt-bt-start", "#zt-bt-end"]:
            el = page.query_selector(sel)
            print(sel, "value=", el.get_attribute("value") if el else None)

        # 点击回测按钮
        print("=== 点击回测按钮 ===")
        if bt:
            bt.click()
            page.wait_for_timeout(4000)
            status = page.query_selector("#zt-bt-status")
            print("status text:", status.inner_text() if status else "none")
            # 等待最多 40s 看结果
            deadline = time.time() + 40
            last = ""
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                status = page.query_selector("#zt-bt-status")
                if status:
                    t = status.inner_text()
                    if t != last:
                        last = t
                        print("status ->", t)
                # 有回测结果卡片?
                if page.query_selector(".card .card-head:has-text('回测结果')") or page.query_selector("kpi-group"):
                    print("== 检测到回测结果卡片 ==")
                    break
                if page.query_selector(".card:has-text('错误')"):
                    print("== 检测到错误卡片 ==")
                    break
            page.screenshot(path=str(OUT / "02_after_backtest.png"), full_page=False)
            # 检查是否有 交易明细
            print("has 交易明细:", "交易明细" in page.content())

        print("=== console 错误 ===")
        for typ, txt in console:
            if typ in ("error", "pageerror"):
                print(f"  [{typ}] {txt[:300]}")

        # 网络请求概览
        print("=== 网络: /api/zt/ 请求 ===")
        reqs = page.query_selector_all("body") and []
        browser.close()


if __name__ == "__main__":
    main()
