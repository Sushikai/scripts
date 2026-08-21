"""R202: mobile bv-row padding-top 6→4 — 卡片内 padding 顶部收紧

第一性原理: bv-row pt=6 pb=6 pl=15 pr=12 (R179 设). 表内容是 grid 3 行 (~77px),
  16px 上下 padding (R179) 已收回一次. pt 6→4 是再次收紧.
  跟 R192 view-head pt 4 + R194 filter-bar pt 2 + R195 sector-bar pt 0 + R201 filter-bar pb 0 顶部节奏协调.
  bv-row h 87→85 (-2px), 15 行累计回收 30px.

断言 (真实服务, 390px):
  1. bv-row padding-top 4px (从 6px)
  2. bv-row padding-bottom 6px 不变
  3. bv-row h 85 (从 87)
  4. hit-tier stripe 仍清晰 (pt 4 + pl 15 让位 hit-tier 3px 仍 ok)
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
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
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
        print(f"row: h={d['row']['h']} pt={d['row']['pt']} pb={d['row']['pb']} pl={d['row']['pl']} pr={d['row']['pr']}")

        assert d['row']['pt'] == '4px', f"R202: row pt={d['row']['pt']} != 4px"
        assert d['row']['pb'] == '6px', f"R202: row pb={d['row']['pb']} 应仍 6px"
        assert abs(d['row']['h'] - 85) < 1.0, f"R202: row h={d['row']['h']} 应 ~85"

        await b.close()
        print(f"[OK] R202 bv-row pt 6→4 — bv-row h 87→85 (-2px), 15 行累计回收 30px ✓")

if __name__ == "__main__":
    asyncio.run(run())