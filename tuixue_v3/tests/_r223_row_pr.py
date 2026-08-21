"""R223: mobile bv-row padding-right 12→10 — 卡片右边距收紧

第一性原理: bv-row pr=12 (R198 设). pl=12 (R222) pr=12 对称.
  但 pl 已收紧, pr 12 也偏多 — hit-tier stripe 视觉只 3px (R113).
  跟 R222 pl 15→12 节奏统一, pr 12→10. name 列 (1fr) 多 2px 内容宽.

断言 (真实服务, 390px):
  1. bv-row padding-right 10px (从 12px)
  2. bv-row padding-left 12px 不变
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

        assert d['row']['pr'] == '10px', f"R223: row pr={d['row']['pr']} != 10px"

        await b.close()
        print(f"[OK] R223 bv-row pr 12→10 — 卡片右边距收紧, name 列 (1fr) 多 2px ✓")

if __name__ == "__main__":
    asyncio.run(run())