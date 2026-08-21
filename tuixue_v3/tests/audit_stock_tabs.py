"""Deep audit v3: check actual data presence in each tab via DOM inspection."""
import asyncio
import json
import sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
CODE = "605179"


# Per-tab data selectors (from view-stock.js getElementById and selector inspection)
TAB_DATA = {
    "intraday": {
        "elements": ["#q-price", "#q-change", "#q-chg-pct",
                     "[data-tab-pane=intraday] canvas, [data-tab-pane=intraday] svg",
                     "#stock-intraday"],
        "min_size": 30,
    },
    "kline": {
        "elements": ["[data-tab-pane=kline] canvas, [data-tab-pane=kline] svg",
                     "#kline-chart"],
        "min_size": 30,
    },
    "flow": {
        "elements": ["[data-tab-pane=flow] canvas",
                     "#flow-chart",
                     "[data-tab-pane=flow] table"],
        "min_size": 30,
    },
    "seats": {
        "elements": ["[data-tab-pane=seats] table",
                     "[data-tab-pane=seats] .seat-card",
                     "#seats-table"],
        "min_size": 50,
    },
    "holders": {
        "elements": ["[data-tab-pane=holders] table",
                     "[data-tab-pane=holders] canvas",
                     "#holders-table"],
        "min_size": 50,
    },
    "crash": {
        "elements": ["[data-tab-pane=crash] .crash-score",
                     "[data-tab-pane=crash] table",
                     "[data-tab-pane=crash] [class*=score]"],
        "min_size": 50,
    },
    "ai": {
        "elements": ["#ai-verdict", "#ai-summary", "[data-tab-pane=ai] .ai-layer"],
        "min_size": 100,
    },
    "news": {
        "elements": ["[data-tab-pane=news] .news-card",
                     "[data-tab-pane=news] .news-item",
                     "[data-tab-pane=news] .news-list",
                     "[data-tab-pane=news] article"],
        "min_size": 200,
    },
    "sectors": {
        "elements": ["[data-tab-pane=sectors] .sector-card",
                     "[data-tab-pane=sectors] .sector-item",
                     "[data-tab-pane=sectors] .sector"],
        "min_size": 100,
    },
    "related": {
        "elements": ["[data-tab-pane=related] .related-card",
                     "[data-tab-pane=related] .related-item",
                     "[data-tab-pane=related] a[data-stock-code]",
                     "[data-tab-pane=related] .stock-link"],
        "min_size": 100,
    },
}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # Network log
        network_log = []
        all_apis = []
        def on_response(r):
            if "/api/" in r.url and "/api/_meta/" not in r.url:
                rec = {"url": r.url.replace(BASE, ""), "status": r.status, "method": r.request.method}
                network_log.append(rec)
                all_apis.append(rec)
        page.on("response", on_response)

        console_errors = []
        page.on("console", lambda m: console_errors.append((m.type, m.text[:200]))
                if m.type == "error" else None)

        # Load app fresh (no SW)
        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
        await page.evaluate("""() => {
          if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistrations().then(regs => {
              regs.forEach(r => r.unregister());
            });
          }
        }""")
        await asyncio.sleep(1)

        # Go to stock page
        await page.goto(f"{BASE}/#stock?code={CODE}", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(6)

        print("="*100)
        print(f"STOCK PAGE TAB AUDIT V3 — {CODE}")
        print("="*100)

        all_issues = []
        score_count = 0
        pass_count = 0

        # === HERO check ===
        print(f"\n⭐ HERO")
        hero = await page.evaluate("""() => {
          const get = (sel) => {
            const el = document.querySelector(sel);
            if (!el) return {sel, exists: false};
            const text = (el.textContent || '').trim();
            return {sel, exists: true, text: text.slice(0, 50), empty: !text || text === '—'};
          };
          return [
            get('#stock-title'),
            get('#stock-code'),
            get('#q-price'),
            get('#q-change'),
            get('#q-chg-pct'),
            get('#q-arrow'),
          ];
        }""")
        for r in hero:
            mark = "✓" if r.get("exists") and not r.get("empty") else "✗"
            print(f"  {mark} {r['sel']:20s} {r.get('text', '')}")
            if not r.get("exists") or r.get("empty"):
                all_issues.append(f"hero-empty: {r['sel']}")

        # === Per tab ===
        for tab_id, tab_label in [
            ("intraday", "分时"),
            ("kline", "K 线"),
            ("flow", "资金流向"),
            ("seats", "游资席位"),
            ("holders", "散户/主力"),
            ("crash", "砸盘风险"),
            ("ai", "AI 铁律"),
            ("news", "新闻"),
            ("sectors", "板块"),
            ("related", "相关个股"),
        ]:
            network_log.clear()
            console_errors.clear()

            # Click tab
            try:
                tab_btn = page.locator(f'button[data-tab="{tab_id}"]').first
                if await tab_btn.count() == 0:
                    print(f"\n✗ {tab_label} ({tab_id}): NO TAB BUTTON")
                    all_issues.append(f"no-tab: {tab_id}")
                    continue
                await tab_btn.click()
                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"\n✗ {tab_label} ({tab_id}): CLICK FAILED: {str(e)[:80]}")
                all_issues.append(f"click-fail: {tab_id}")
                continue

            # Check pane state
            pane_info = await page.evaluate(f"""() => {{
              const p = document.querySelector('[data-tab-pane="{tab_id}"]');
              if (!p) return {{exists: false}};
              return {{
                exists: true,
                hidden: p.hasAttribute('hidden'),
                text: (p.textContent || '').trim(),
                textLen: (p.textContent || '').length,
                childCount: p.children.length,
                canvasCount: p.querySelectorAll('canvas').length,
                tableCount: p.querySelectorAll('table').length,
                imgCount: p.querySelectorAll('img').length,
                svgCount: p.querySelectorAll('svg').length,
              }};
            }}""")

            # Check data element presence
            selectors = TAB_DATA.get(tab_id, {}).get("elements", [])
            elem_check = await page.evaluate(f"""() => {{
              const sels = {json.dumps(selectors)};
              const out = {{}};
              for (const sel of sels) {{
                try {{
                  const els = document.querySelectorAll(sel);
                  out[sel] = {{
                    count: els.length,
                    sizes: Array.from(els).slice(0,3).map(e => ({{
                      tag: e.tagName,
                      w: e.offsetWidth || 0,
                      h: e.offsetHeight || 0,
                      text: (e.textContent || '').trim().slice(0, 80),
                    }}))
                  }};
                }} catch (e) {{
                  out[sel] = {{error: e.message}};
                }}
              }}
              return out;
            }}""")

            # API errors in this tab
            api_errors = [r for r in network_log if r["status"] >= 500 or r["status"] == 404]

            # Compute tab score
            total_chars = max((pane_info or {}).get("textLen", 0), 0)
            data_min = TAB_DATA.get(tab_id, {}).get("min_size", 50)
            canvas_count = (pane_info or {}).get("canvasCount", 0)
            table_count = (pane_info or {}).get("tableCount", 0)
            svg_count = (pane_info or {}).get("svgCount", 0)

            # Has content if text > min OR has canvas/table/svg
            has_data = (total_chars > data_min) or canvas_count > 0 or table_count > 0 or svg_count > 0

            mark = "✓" if has_data else "✗"
            score_count += 1
            if has_data:
                pass_count += 1

            print(f"\n📑 {tab_label} ({tab_id})  {mark}")
            print(f"  pane: hidden={pane_info.get('hidden')} textLen={total_chars} canvas={canvas_count} table={table_count} svg={svg_count}")

            # Show data elements
            for sel, info in elem_check.items():
                if info.get("count", 0) > 0:
                    sizes = info.get("sizes", [])
                    if sizes:
                        first = sizes[0]
                        print(f"  ✓ {sel:50s} count={info['count']} first_size={first['w']}x{first['h']} text='{first['text'][:60]}'")
                else:
                    print(f"  ✗ {sel:50s} NOT FOUND")

            if not has_data:
                all_issues.append(f"tab-no-data: {tab_id}")

            if api_errors:
                print(f"  ✗ API ERRORS: {len(api_errors)}")
                for r in api_errors[:5]:
                    print(f"    {r['status']} {r['method']} {r['url'][:120]}")
                all_issues.append(f"tab-api-errors: {tab_id}")

            # Print API calls
            if api_errors or "flow" in tab_id or "sector" in tab_id or "news" in tab_id:
                print(f"  📡 API calls ({len(network_log)}):")
                for r in network_log:
                    if r["status"] >= 500 or r["status"] == 404:
                        print(f"    ✗ {r['status']} {r['method']:4s} {r['url'][:120]}")
                    elif r["status"] >= 400:
                        print(f"    ⚠ {r['status']} {r['method']:4s} {r['url'][:120]}")
                    else:
                        print(f"    ✓ {r['status']} {r['method']:4s} {r['url'][:120]}")

        # Summary
        print("\n" + "="*100)
        print(f"📊 TAB SCORE: {pass_count}/{score_count} tabs have data")
        print(f"📛 TOTAL ISSUES: {len(all_issues)}")
        for i in all_issues:
            print(f"  - {i}")

        await browser.close()
        return 0 if len(all_issues) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
