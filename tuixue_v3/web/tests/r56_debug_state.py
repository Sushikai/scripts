#!/usr/bin/env python3
"""Debug: 检查 _btMaybeAutoCompare 是否触发 + WR1000 实际写入 _BT_RESULTS"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:7799/#screener"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        await page.evaluate("""() => {
            typeof showView === 'function' && showView('screener', {push: false});
            const p = document.getElementById('backtest-panel');
            if (p) p.classList.remove('collapsed');
        }""")
        await page.wait_for_timeout(1500)

        # 跑 baseline
        body = {"periods":["半年"],"hold_days":1,"top_n":2,"sample":50,
                "breadth_min":0,"breadth_min_soft":0,"sector_hot_topn":0,
                "sector_inflow_topn":0,"require_surge_label":False,
                "enable_actual_10":False,"index_late_up":False,
                "sector_late_up":False,"tail_vol_ratio_min":0,
                "strategy_id":"baseline"}
        r = await page.evaluate("""async (b) => {
            const r = await fetch('/api/screener/backtest', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify(b)});
            return await r.json();
        }""", body)
        rid = r["data"]["run_id"]
        print(f"  baseline rid: {rid}")

        for i in range(30):
            await page.wait_for_timeout(2000)
            s = await page.evaluate("""async (rid) => {
                const r = await fetch('/api/screener/backtest?run_id=' + rid);
                return await r.json();
            }""", rid)
            st = s.get("data", {}).get("status")
            if st == "done":
                print(f"  baseline done at {i*2}s")
                break

        # 直接调 btFinishRun 看是否触发 auto-compare
        result = s["data"]["result"]
        # 模拟 btFinishRun hook
        await page.evaluate("""(r) => {
            window._BT_RESULTS = window._BT_RESULTS || {};
            window._BT_RESULTS[r.config.strategy_id] = r;
            btRenderV4(r);
            if (typeof _btMaybeAutoCompare === 'function') {
                console.log('[debug] auto-compare 函数存在,准备调用');
                window._btMaybeAutoCompare(r).then(() => {
                    console.log('[debug] auto-compare 完成');
                }).catch(e => console.error('[debug] auto-compare err:', e));
            } else {
                console.log('[debug] auto-compare 函数不存在');
            }
        }""", result)

        await page.wait_for_timeout(15000)

        # 检查 _BT_RESULTS
        keys = await page.evaluate("() => Object.keys(window._BT_RESULTS || {})")
        print(f"  _BT_RESULTS keys: {keys}")

        # 检查 #bt-compare-host 是否有内容
        host_html = await page.evaluate("""() => {
            const h = document.getElementById('bt-compare-host');
            return h ? h.outerHTML.slice(0, 200) : '(no host)';
        }""")
        print(f"  compare-host HTML: {host_html}")

        # 检查 _bt_auto_compare_running
        flag = await page.evaluate("""() => ({
            auto_running: window._BT_AUTO_COMPARE_RUNNING,
            last_body: !!window._BT_LAST_BODY,
            baseline: !!(window._BT_RESULTS && window._BT_RESULTS.baseline),
            wr1000: !!(window._BT_RESULTS && window._BT_RESULTS.WIN_RATE_1000)
        })""")
        print(f"  state: {flag}")

        print("\n--- console msgs ---")
        for m in console_msgs:
            print(m)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())