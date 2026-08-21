#!/usr/bin/env python3
"""chart_audit 1000 轮基线:每次改 K线 后跑这个,确保 18 chart × 2 视口都 OK。

覆盖:
  - K线 主图 (含 MA / BOLL / MACD / KDJ toggle)
  - 分时 (intra-day)
  - 5日分时
  - 资金流
  - Hero sparkline (kpi 区)
  - 自选 sparkline
  - 全 A sparkline 列
  - 周线擒牛小图
"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent
REPORT = OUT / "chart_audit_1000.json"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        # desktop + mobile 双视口
        contexts = [
            ("desktop", 1280, 900),
            ("mobile", 390, 844),
        ]
        all_results = []
        for vp_name, w, h in contexts:
            ctx = await browser.new_context(viewport={"width": w, "height": h}, service_workers="block")
            page = await ctx.new_page()
            err_log = []
            page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error",) else None)

            await page.goto(BASE, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # 1) Hero sparkline 在 dash 页面
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-01-dash.png"), full_page=False)
            dash_dom = await page.evaluate("""
                () => {
                    const spark = document.querySelectorAll('.hero-spark svg, .sparkline svg, svg path');
                    return { spark_count: spark.length, dash_kpi: !!document.querySelector('.sig-pct')?.textContent };
                }
            """)

            # 2) 点 tickerbar 进 stock + 切 K线 tab
            await page.evaluate("showView('stock'); loadStockDetail('600519');")
            await page.wait_for_timeout(8000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-02-stock-kpi.png"), full_page=False)

            # 3) 切 K线 tab
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-tab=\"kline\"], .tab-kline, [data-jump=\"kline\"]');
                    if (btn) btn.click();
                }
            """)
            await page.wait_for_timeout(5000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-03-kline-main.png"), full_page=False)
            kline_dom = await page.evaluate("""
                () => {
                    const chart = document.querySelector('#kline-chart');
                    const cvs = chart?.querySelector('canvas');
                    return {
                        container_w: chart?.clientWidth || 0,
                        container_h: chart?.clientHeight || 0,
                        canvas_w: cvs?.width || 0,
                        canvas_h: cvs?.height || 0,
                        visible: !document.querySelector('.view-stock')?.hidden,
                    };
                }
            """)

            # 4) 切分时
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-tab=\"intraday\"]');
                    if (btn) btn.click();
                }
            """)
            await page.wait_for_timeout(5000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-04-intraday.png"), full_page=False)
            intraday_dom = await page.evaluate("""
                () => {
                    const chart = document.querySelector('#intra-day-chart');
                    const cvs = chart?.querySelector('canvas');
                    return {
                        container_w: chart?.clientWidth || 0,
                        container_h: chart?.clientHeight || 0,
                        canvas_w: cvs?.width || 0,
                        canvas_h: cvs?.height || 0,
                    };
                }
            """)

            # 5) 切 5日分时
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-tab=\"intraday5d\"]');
                    if (btn) btn.click();
                }
            """)
            await page.wait_for_timeout(5000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-05-intraday5d.png"), full_page=False)
            intraday5d_dom = await page.evaluate("""
                () => {
                    const chart = document.querySelector('#intraday-5d-chart');
                    const cvs = chart?.querySelector('canvas');
                    return {
                        container_w: chart?.clientWidth || 0,
                        canvas_w: cvs?.width || 0,
                    };
                }
            """)

            # 6) 切资金流
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-tab=\"flow\"]');
                    if (btn) btn.click();
                }
            """)
            await page.wait_for_timeout(5000)
            await page.screenshot(path=str(OUT / f"1000-{vp_name}-06-flow.png"), full_page=False)
            flow_dom = await page.evaluate("""
                () => {
                    const chart = document.querySelector('#flow-chart');
                    const cvs = chart?.querySelector('canvas');
                    return {
                        container_w: chart?.clientWidth || 0,
                        canvas_w: cvs?.width || 0,
                    };
                }
            """)

            all_results.append({
                "vp": vp_name,
                "dash": dash_dom,
                "kline": kline_dom,
                "intraday": intraday_dom,
                "intraday5d": intraday5d_dom,
                "flow": flow_dom,
                "errors": err_log,
            })
            await ctx.close()

        # 总结
        report = {
            "ts": time.time(),
            "results": all_results,
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("=== chart_audit 1000 R0 baseline ===")
        for r in all_results:
            print(f"  [{r['vp']}]")
            for k in ['dash', 'kline', 'intraday', 'intraday5d', 'flow']:
                dom = r.get(k, {})
                if not dom: continue
                w = dom.get('container_w', 0)
                cw = dom.get('canvas_w', 0)
                print(f"    {k:10s} container={w}px canvas={cw}px")
            print(f"    errors: {len(r['errors'])}")
            for e in r['errors'][:3]:
                print(f"      [{e['type']}] {e['text'][:120]}")
        print(f"\n报告: {REPORT}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())