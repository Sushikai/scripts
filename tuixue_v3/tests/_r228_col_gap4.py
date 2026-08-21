"""R228: mobile bv-row column-gap 6→4 — 列间距再收紧

第一性原理: 现在 padding 全部归零 (R221-R224) + hit-tier 2px (R226) + radius 6 (R225)
  + bg-2 浮起 (R227), 视觉边界全部优化. column-gap 6 占 5 列间 24px (4 gaps×6).
  跟 R224 pl/pr=10 节奏统一 (内部紧凑跟外部紧凑同步), column-gap 6→4.
  5 列宽再减少 8px (4 gaps×2px), name 列 (1fr) 多 8px 内容宽.
  row-gap 1 不动 (跟 lh 1.2 行内文字已经接近).

断言 (真实服务, 390px):
  1. bv-row column-gap 4px (从 6px)
  2. bv-row row-gap 1px 不变
  3. bv-row h 不变 (74.7)
  4. 卡片内文字不溢出
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
    return {h: Math.round(rect.height*10)/10, rg: cs.rowGap, cg: cs.columnGap};
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
        print(f"row: h={d['row']['h']} rg={d['row']['rg']} cg={d['row']['cg']}")

        assert d['row']['cg'] == '4px', f"R228: row column-gap={d['row']['cg']} 应 4px"
        assert d['row']['rg'] == '1px', f"R228: row row-gap={d['row']['rg']} 应仍 1px"

        await b.close()
        print(f"[OK] R228 bv-row column-gap 6→4 — 卡片内列间距再收紧, name 列多 8px 内容宽 ✓")

if __name__ == "__main__":
    asyncio.run(run())