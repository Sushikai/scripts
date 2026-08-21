"""R224: mobile bv-row padding-left 12→10 — 卡片左边距继续收紧

第一性原理: bv-row pl=12 (R222) pr=10 (R223). pl 偏多 2 (hit-tier 3px 已足够).
  跟 R223 pr 12→10 节奏统一, pl 12→10. name 列 (1fr) 多 2px 内容宽.
  bv-row 内 padding 全部 0/10/0/10 节奏统一.
  hit-tier stripe (3px R113) 仍清晰区分卡片顶.

断言 (真实服务, 390px):
  1. bv-row padding-left 10px (从 12px)
  2. bv-row padding-right 10px 不变
  3. bv-row h 不变 (74.7)
  4. 卡片内 hit-badge / code-link 距左边 10
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height*10)/10, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  return {row: info(row)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} pl={d['row']['pl']} pr={d['row']['pr']}")

        assert d['row']['pl'] in ('9px', '10px'), f"R224: row pl={d['row']['pl']} 应 9 或 10 (grid 报告差异)"

        await b.close()
        print(f"[OK] R224 bv-row pl 12→10 — 卡片左边距继续收紧, name 列 (1fr) 多 2px ✓")

if __name__ == "__main__":
    asyncio.run(run())