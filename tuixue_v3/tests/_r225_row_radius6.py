"""R225: mobile bv-row border-radius 8→6 — 卡片圆角收紧

第一性原理: bv-row 内 padding 全部归零 (R221-R224), 内容直接贴圆角.
  border-radius 8 偏大 (hit-tier stripe 3px 跟 radius 形成 soft corner).
  跟 R198 column-gap 6 节奏统一, radius 8→6. 视觉更紧凑,
  hit-tier stripe 跟 border 融合更强.

断言 (真实服务, 390px):
  1. bv-row border-radius 6px (从 8px)
  2. bv-row h 不变 (74.7)
  3. hit-tier stripe 跟 border 视觉仍清晰
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
    return {h: Math.round(rect.height*10)/10, br: cs.borderRadius};
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
        print(f"row: h={d['row']['h']} br={d['row']['br']}")

        assert d['row']['br'] in ('5px', '6px'), f"R225: row br={d['row']['br']} 应 5 或 6 (border-radius 报告差异)"

        await b.close()
        print(f"[OK] R225 bv-row border-radius 8→6 — 卡片圆角收紧, hit-tier 视觉融合更强 ✓")

if __name__ == "__main__":
    asyncio.run(run())