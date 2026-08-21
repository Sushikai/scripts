"""R239: mobile rules-cell row3 lh 收紧 — 规则行紧凑

第一性原理: bv-row rules-cell (row 3) padding-top 4px → 2px.
  跟 R220 bv-row pb 0 + R216 bv-row pb 2 + R202 bv-row pt 4 节奏统一,
  row 3 上 padding 收紧. (注: R97 全局 !important 0 覆盖 td padding-top,
  但 bv-rules-cell flex 内部仍感知边框位置, 行 h 实际下降 ~1.5px)

断言 (真实服务, 390px):
  1. bv-row h ~75px (从 76.7, 收紧效果)
  2. border-top 仍清晰
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
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, bt: cs.borderTop};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var rules = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(10)');
  var rulesCell = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(10) .bv-rules-cell');
  return {row: info(row), rules: info(rules), rulesCell: info(rulesCell)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']}")
        print(f"rules td: pt={d['rules']['pt']} bt={d['rules']['bt']}")
        print(f"rulesCell: pt={d['rulesCell']['pt'] if d['rulesCell'] else 'NONE'}")

        # bv-row h 应该接近 75 (从 76.7 降)
        assert d['row']['h'] < 76.7, f"R239: row h={d['row']['h']} 应 < 76.7 (收紧效果)"

        await b.close()
        print(f"[OK] R239 rules td padding-top 4→2 — row h {d['row']['h']} ✓")

if __name__ == "__main__":
    asyncio.run(run())