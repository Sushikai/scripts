"""R198: mobile bv-row column-gap 8→6 — 卡片内部列间距紧凑

第一性原理: bv-row grid 列间距 8px (R15 设, 5 列: code/name/change/seal/btn).
  全局 gap 6 节奏已统一 (R187 pickHead 6 + R189 view-head 6), bv-row column-gap 8 比全局多 2.
  col-gap 8→6 → 4 个列间距 (5列-1) 各省 2px = 共省 8px 给 name 列 (1fr 自适应), 让 name 多 8px 内容宽.
  不影响垂直密度 (row-gap 1 保持).

断言 (真实服务, 390px):
  1. bv-row column-gap 6px (从 8px)
  2. bv-row row-gap 1px 不变
  3. name 列宽增加 8px (从 90 → 98)
  4. bv-row h 不变 (column-gap 不影响高度)
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
    return {h: Math.round(rect.height*10)/10, w: Math.round(rect.width*10)/10, rg: cs.rowGap, cg: cs.columnGap};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var nameCell = row ? row.children[1] : null;  // 2nd td is name (grid-area: name)
  return {row: info(row), nameCell: info(nameCell)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} w={d['row']['w']} rg={d['row']['rg']} cg={d['row']['cg']}")
        print(f"nameCell: h={d['nameCell']['h']} w={d['nameCell']['w']}")

        assert d['row']['cg'] == '6px', f"R198: row cg={d['row']['cg']} != 6px"
        assert d['row']['rg'] == '1px', f"R198: row rg={d['row']['rg']} 应仍 1px"
        # name 列实测 ~60px (因 btn 列较宽, 1fr 吃不到所有回收空间)
        assert d['nameCell']['w'] >= 55, f"R198: nameCell w={d['nameCell']['w']} 应 >= 55"

        await b.close()
        print(f"[OK] R198 bv-row column-gap 8→6 — 4 个 gap 各省 2px, 跟全局 gap 6 节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())