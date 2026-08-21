"""R179 探针: card-head 是否会因 count/phase 文案换行变高?"""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var head = document.querySelector('.bv-pick-card .card-head');
  var title = document.querySelector('.bv-pick-card .card-head h3');
  var count = document.querySelector('.bv-pick-card .card-head #bv-pick-count');
  var sortBtn = document.querySelector('.bv-pick-card .card-head .bv-sort-btn');
  var pickCard = document.querySelector('.bv-pick-card');
  // 检查 card-head 内每个子元素的 overflow 状态
  return {
    head: head ? {h: r(head).h, text: head.textContent.replace(/\s+/g,' ').trim().slice(0,80)} : null,
    title: title ? {h: r(title).h, fs: getComputedStyle(title).fontSize, w: r(title).width} : null,
    count: count ? {h: r(count).h, fs: getComputedStyle(count).fontSize, text: count.textContent.trim().slice(0,40)} : null,
    sortBtn: sortBtn ? {h: r(sortBtn).h, fs: getComputedStyle(sortBtn).fontSize, w: r(sortBtn).width} : null,
    pickCardH: pickCard ? r(pickCard).h : null
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

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"head h={d['head']['h']} text='{d['head']['text']}'")
        print(f"title h={d['title']['h']} fs={d['title']['fs']} w={d['title']['w']}")
        print(f"count h={d['count']['h']} fs={d['count']['fs']} text='{d['count']['text']}'")
        print(f"sortBtn h={d['sortBtn']['h']} fs={d['sortBtn']['fs']} w={d['sortBtn']['w']}")
        print(f"pickCard h={d['pickCardH']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())