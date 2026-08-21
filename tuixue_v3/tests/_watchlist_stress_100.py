"""自选股页面 100 轮压力测试 — Playwright"""
import asyncio, json, sys, time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
TEST_URL = f"{BASE}/#watchlist"

async def main():
    results = {"pass": 0, "fail": 0, "errors": [], "load_times": [], "data_empty": 0}
    console_errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        # 收集 console 错误
        page.on("console", lambda msg: (
            console_errors.append(f"[{msg.type}] {msg.text}")
            if msg.type == "error" else None
        ))

        for i in range(1, 101):
            t0 = time.time()
            console_errors.clear()

            try:
                # 第 1 轮: goto; 后续轮: reload
                if i == 1:
                    await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
                else:
                    await page.reload(wait_until="networkidle", timeout=30000)

                await asyncio.sleep(0.3)  # 让 JS 渲染完

                # 检查 watchlist 表格是否有数据
                rows = await page.locator("#wl-table tbody tr").count()
                if rows == 0:
                    results["data_empty"] += 1
                    # 尝试从卡片模式检测
                    cards = await page.locator(".view-watchlist .card, .wl-card").count()
                    rows = cards

                # 检查是否有可见 error/toast
                toast_err = await page.locator('.toast.error, .toast.bad, [class*="error"]').count()

                elapsed = round((time.time() - t0) * 1000)
                results["load_times"].append(elapsed)

                if console_errors or toast_err > 0:
                    err_detail = "; ".join(console_errors[:3]) if console_errors else f"toast_err={toast_err}"
                    results["fail"] += 1
                    results["errors"].append(f"R{i}: {err_detail}")
                else:
                    results["pass"] += 1

                if i % 10 == 0:
                    print(f"  R{i:3d} | pass={results['pass']} fail={results['fail']} empty={results['data_empty']} | last={elapsed}ms")

            except Exception as e:
                results["fail"] += 1
                results["errors"].append(f"R{i}: EXCEPTION {e}")
                print(f"  R{i:3d} EXCEPTION: {e}")

        await browser.close()

    # ── 报告 ──
    times = results["load_times"]
    times_sorted = sorted(times)
    n = len(times)
    print(f"\n{'='*60}")
    print(f"自选股 100 轮压力测试报告")
    print(f"{'='*60}")
    print(f"通过: {results['pass']} / 100")
    print(f"失败: {results['fail']} / 100")
    print(f"空数据: {results['data_empty']} / 100")
    print(f"加载时间: P50={times_sorted[n//2]}ms  P95={times_sorted[int(n*0.95)]}ms  P99={times_sorted[int(n*0.99)]}ms  max={max(times)}ms  min={min(times)}ms")
    if results["errors"]:
        print(f"\n错误明细 (前 20):")
        for e in results["errors"][:20]:
            print(f"  {e}")
    else:
        print(f"\n无错误 ✓")

    return 0 if results["fail"] == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
