"""R245 prep: 探针 creed-card 位置 vs pick-card — R176 沉底是否生效

第一性原理: R176 让战法哲学卡"沉底" (移动端优先级 — 推票优先).
  但 topHeight=203px 里 pick-card 是第一个卡, creed 若在 DOM 上仍占中间位置
  就会被滚动/压缩逻辑漏掉. 本探针量 creed 卡与 pick-card 的实际文档流相对位置.
"""
import asyncio, json
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
  var creed = document.querySelector('.bv-creed-card');
  var pick = document.querySelector('.bv-pick-card');
  var head = document.querySelector('header');
  if (!creed || !pick || !head) return {err: 'missing'};
  var cr = creed.getBoundingClientRect(), pr = pick.getBoundingClientRect(), hr = head.getBoundingClientRect();
  return {
    head: {top: Math.round(hr.top), bottom: Math.round(hr.bottom), h: Math.round(hr.height)},
    creed: {top: Math.round(cr.top), bottom: Math.round(cr.bottom), h: Math.round(cr.height),
            visible: getComputedStyle(creed).display !== 'none'},
    pick: {top: Math.round(pr.top), bottom: Math.round(pr.bottom), h: Math.round(pr.height)},
    creedAfterPick: Boolean(creed.compareDocumentPosition(pick) & Node.DOCUMENT_POSITION_FOLLOWING),
    creedFirstChildOfView: creed.parentElement === document.querySelector('.view-bv') &&
                           Array.prototype.indexOf.call(document.querySelector('.view-bv').children, creed) === 0
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
