"""R245 prep: 探针首屏卡片密度 — 844px 视口内完整可见多少张卡

第一性原理: R224-R243 连续 21 轮压缩 bv-row 后, 唯一衡量密度收益的指标是
  "首屏能看到几张完整卡片". R101 目标是 1→3 张. 顶部区 (view-head/filter/
  sector/pick-head) 自 R214 后没再动, 若 top 区仍吃走大半屏, 卡片压缩白做.
  本探针量: top 区占用高度 + 首屏完整卡片数 + 首卡是否被裁剪.
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
  var vh = window.innerHeight;
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  // top 区: 从页面顶到第一张卡顶
  var first = rows[0];
  if (!first) return {err: 'no rows'};
  var fRect = first.getBoundingClientRect();
  var topHeight = fRect.top;   // 相对视口顶 = 首卡前所有 UI (含 sticky/滚动)
  // 数首屏完整卡: 卡的 top >= 0 且 bottom <= vh
  var full = 0, partial = 0;
  for (var i=0; i<rows.length; i++) {
    var r = rows[i].getBoundingClientRect();
    if (r.top < 0) continue;             // 顶部已出屏
    if (r.bottom <= vh) full++;          // 完整
    else { if (r.top < vh) partial++; break; }
  }
  return {vh: vh, topHeight: Math.round(topHeight), rowH: Math.round(fRect.height),
          fullCards: full, partialCards: partial, totalRows: rows.length};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(600)
        d = await page.evaluate(PROBE)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
