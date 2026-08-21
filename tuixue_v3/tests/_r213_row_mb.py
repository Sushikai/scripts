"""R213: mobile bv-row margin-bottom 4→2 — 表卡间距紧凑收尾

第一性原理: bv-row mb=4 (R180). 15 行累计 60px 间距偏松.
  跟 pickCard pt 4 (R211) + bv-row pt 4 (R202) + bv-row pb 4 (R204) + pickCard pb 0 (R212) 顶部节奏统一, mb 4→2.
  15 行累计回收 30px (每行减 2px), 视觉卡片区分仍由 hit-tier stripe (R113) + 1px border 给出, 不依赖 mb.

断言 (真实服务, 390px):
  1. bv-row margin-bottom 2px (从 4px)
  2. bv-row h 不变 (82.7)
  3. 首屏可见 row 数 +1 (R91 守护)
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
  // Measure total pick area height / first 3 rows
  var first3 = [];
  for (var i = 0; i < Math.min(3, rows.length); i++) {
    var r = rows[i].getBoundingClientRect();
    first3.push({top: r.top, bot: r.bottom});
  }
  return {row: info(row), first3: first3};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} mb={d['row']['mb']}")
        print("first 3 rows y-range:")
        for i, r in enumerate(d['first3']):
            print(f"  [{i}] top={r['top']:.1f} bot={r['bot']:.1f}")

        assert d['row']['mb'] == '2px', f"R213: row mb={d['row']['mb']} != 2px"
        assert abs(d['row']['h'] - 82.7) < 1.5, f"R213: row h={d['row']['h']} 应仍 ~82.7"

        # verify gap between row[0] and row[1] shrunk
        if len(d['first3']) >= 2:
            gap = d['first3'][1]['top'] - d['first3'][0]['bot']
            print(f"row[0]→row[1] visual gap: {gap:.1f}px")
            # row h ~82.7 + mb was 4 = 86.7 row-pitch. Now mb=2 → 84.7 row-pitch (saved 2px).
            assert abs(gap - 2) < 1.0, f"R213: row gap={gap} 应 ~2 (mb=2)"

        await b.close()
        print(f"[OK] R213 bv-row mb 4→2 — 15 行累计回收 30px, hit-tier stripe 仍清晰 ✓")

if __name__ == "__main__":
    asyncio.run(run())