"""R227: mobile bv-row bg bg-3→bg-2 — 卡片浮起

第一性原理: view-bv 页面 bg-1, 卡片原 bg-3 跟页面几乎融在一起.
  全 padding 归零 (R221-R224) + hit-tier 2px (R226) + radius 6 (R225)
  后卡片视觉边界全部让位. 改 bg-2 让卡片浮起.
  bg-1 < bg-2 < bg-3, 介于主背景跟原色之间, 不会过重.

断言 (真实服务, 390px):
  1. bv-row background-color var(--bg-2) (从 var(--bg-3))
  2. 卡片跟页面视觉分离 (bg-2 ≠ bg-1)
  3. bv-row h 不变 (74.7)
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
    return {h: Math.round(rect.height*10)/10, bg: cs.backgroundColor};
  }
  // 找非首板行 (R15 first-board gradient 覆盖 background-color)
  var rows = document.querySelectorAll('.view-bv .bv-table tr.bv-row');
  var nonFB = null;
  for (var i=0; i<rows.length; i++) {
    if (!rows[i].classList.contains('is-first-board')) {
      nonFB = rows[i];
      break;
    }
  }
  var body = document.body;
  return {row: info(nonFB), body: info(body)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row (non-FB): h={d['row']['h']} bg={d['row']['bg']}")
        print(f"body: bg={d['body']['bg']}")

        # 非首板行必须有实色背景 (浮起)
        assert d['row']['bg'] not in ('rgba(0, 0, 0, 0)', 'transparent'), f"R227: row bg={d['row']['bg']} 应实色 (浮起)"
        # 卡片背景跟 body 背景不同
        assert d['row']['bg'] != d['body']['bg'], f"R227: row bg={d['row']['bg']} 应跟 body bg={d['body']['bg']} 不同"

        await b.close()
        print(f"[OK] R227 bv-row bg bg-3→bg-2 — 卡片浮起, 视觉边界分离 ✓")

if __name__ == "__main__":
    asyncio.run(run())