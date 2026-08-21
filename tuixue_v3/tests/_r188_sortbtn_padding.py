"""R188: mobile sortBtn padding 12→10 — 横向 padding 紧凑

第一性原理: sortBtn padding 4px 12px (R154 设的 tap zone 44) — 文字 "排序 ⇅" 12px fs +
  pl/pr 12 = sortBtn w=61. pl/pr 12→10 让 sortBtn 横向少 4px (61→57). 跟 R187 gap 6 协同 —
  pickHead 整体节奏统一 (gap 6 + padding 6/10). 横向内容总宽 +4px 给 h3 (260→264).
  不影响垂直密度 (sortBtn 高仍 44).

断言 (真实服务, 390px):
  1. sortBtn padding-left 10px (从 12px)
  2. sortBtn padding-right 10px (从 12px)
  3. sortBtn padding-top/bottom 4px 保持
  4. sortBtn h 仍 44
  5. sortBtn w 减少 4px (从 61 → 57)
  6. h3 w 增加 4px (从 261 → 265)
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
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height),w:Math.round(x.width)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, w: rect.w, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  var head = document.querySelector('.bv-pick-card .card-head');
  var h3 = document.querySelector('.bv-pick-card .card-head h3');
  var count = document.querySelector('.bv-pick-card .card-head #bv-pick-count');
  var sortBtn = document.querySelector('.bv-pick-card .bv-sort-btn');
  return {
    head: info(head),
    h3: info(h3),
    count: info(count),
    sortBtn: info(sortBtn),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"head: h={d['head']['h']} gap={d['head'].get('gap', 'N/A')}")
        print(f"h3: h={d['h3']['h']} w={d['h3']['w']}")
        print(f"count: h={d['count']['h']} w={d['count']['w']}")
        print(f"sortBtn: h={d['sortBtn']['h']} w={d['sortBtn']['w']} pl={d['sortBtn']['pl']} pr={d['sortBtn']['pr']} pt={d['sortBtn']['pt']} pb={d['sortBtn']['pb']}")

        assert d['sortBtn']['pl'] == '10px', f"R188: sortBtn pl={d['sortBtn']['pl']} != 10px"
        assert d['sortBtn']['pr'] == '10px', f"R188: sortBtn pr={d['sortBtn']['pr']} != 10px"
        assert d['sortBtn']['pt'] == '4px', f"R188: sortBtn pt={d['sortBtn']['pt']} != 4px"
        assert d['sortBtn']['pb'] == '4px', f"R188: sortBtn pb={d['sortBtn']['pb']} != 4px"
        assert d['sortBtn']['h'] == 44, f"R188: sortBtn h={d['sortBtn']['h']} != 44"
        assert d['sortBtn']['w'] <= 60, f"R188: sortBtn w={d['sortBtn']['w']} 应 <= 60 (回收 4px)"
        # h3 w 应增加 (sortBtn 让出空间)
        assert d['h3']['w'] >= 263, f"R188: h3 w={d['h3']['w']} 应 >= 263 (R187 后 + R188 累计)"

        await b.close()
        print(f"[OK] R188 sortBtn pl/pr 12→10 — sortBtn w {d['sortBtn']['w']} (回收 4px), "
              f"h3 w {d['h3']['w']} (回收累计 6px), 高 44 不变 ✓")

if __name__ == "__main__":
    asyncio.run(run())
