"""R190 probe: expanded bv-row 详情区 + view-head 第一性原理扫描."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height), w: Math.round(rect.width), mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, gap: cs.gap, fs: cs.fontSize, tag: el.tagName, cls: el.className.toString().slice(0,40)};
  }
  var row1 = document.querySelector('.view-bv .bv-table tr.bv-row');
  // 尝试展开第一个 row
  row1.click();
  return {
    row1: info(row1),
    caption: info(document.querySelector('.view-bv .view-head .caption')),
    display: info(document.querySelector('.view-bv .view-head .display')),
    h1: info(document.querySelector('.view-bv .view-head h1')),
    vhFirstChild: info(document.querySelector('.view-bv .view-head > div:first-child')),
  };
}"""

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
    await page.wait_for_timeout(300)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            if v:
                print(f"{k}: h={v.get('h')} w={v.get('w')} mb={v.get('mb')} mt={v.get('mt')} pb={v.get('pb')} pt={v.get('pt')} fs={v.get('fs')} gap={v.get('gap')} cls={v.get('cls','')}")
        await page.wait_for_timeout(800)
        # 截图
        await page.screenshot(path="/tmp/r190_expanded.png", full_page=False)
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())