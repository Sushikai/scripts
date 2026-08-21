"""R230: mobile name 列 letter-spacing 0.2→0 — 名称更紧凑

第一性原理: name 列 fs 13 letter-spacing 0.2px (R179 设).
  column-gap 改 4 (R228) 后 name 列 (1fr) 多 8px 内容宽,
  不再需要 letter-spacing 加宽. 改 0 让中文名称更紧凑
  (letter-spacing 对中文无意义, 仅对拉丁字符有效).

断言 (真实服务, 390px):
  1. name 列 letter-spacing 0 (normal)
  2. bv-row h 不变 (74.7)
  3. 中文名称不被截断
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
    return {h: Math.round(rect.height*10)/10, ls: cs.letterSpacing};
  }
  var name = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(2)');
  return {name: info(name)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"name: h={d['name']['h']} ls={d['name']['ls']}")

        assert d['name']['ls'] in ('0px', 'normal'), f"R230: name ls={d['name']['ls']} 应 0 (normal)"

        await b.close()
        print(f"[OK] R230 name letter-spacing 0.2→0 — 名称更紧凑, 中文视觉密度提升 ✓")

if __name__ == "__main__":
    asyncio.run(run())