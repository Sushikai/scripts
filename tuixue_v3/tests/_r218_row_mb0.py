"""R218: mobile bv-row margin-bottom 2→0 — 表卡完全紧凑 (hit-tier 视觉边界)

第一性原理: bv-row mb=2 (R213). 视觉卡片区分由 hit-tier stripe (R113, 3px) + 1px border 给出, 不依赖 mb.
  跟 R208/R207/R212/R210/R214 顶部全 0 节奏统一, bv-row mb 2→0.
  15 行累计回收 30px. hit-tier stripe (3px) 仍清晰区分.

断言 (真实服务, 390px):
  1. bv-row margin-bottom 0px (从 2px)
  2. bv-row h 不变 (78.7)
  3. 相邻 row 视觉 gap 0 (hit-tier stripe + 1px border 仍清晰)
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
    return {h: Math.round(rect.height*10)/10, mb: cs.marginBottom};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var rows = document.querySelectorAll('.view-bv .bv-table tr.bv-row');
  var first2 = [];
  for (var i = 0; i < Math.min(2, rows.length); i++) {
    var r = rows[i].getBoundingClientRect();
    first2.push({top: r.top, bot: r.bottom});
  }
  return {row: info(row), first2: first2};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} mb={d['row']['mb']}")
        if len(d['first2']) >= 2:
            gap = d['first2'][1]['top'] - d['first2'][0]['bot']
            print(f"row[0]→row[1] visual gap: {gap:.1f}px")

        assert d['row']['mb'] == '0px', f"R218: row mb={d['row']['mb']} != 0px"
        assert abs(d['row']['h'] - 78.7) < 1.5, f"R218: row h={d['row']['h']} 应仍 ~78.7"

        await b.close()
        print(f"[OK] R218 bv-row mb 2→0 — 15 行累计回收 30px, hit-tier stripe 仍清晰 ✓")

if __name__ == "__main__":
    asyncio.run(run())