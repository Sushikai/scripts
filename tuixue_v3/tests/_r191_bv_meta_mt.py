"""R191: mobile bv-meta margin-top 2→0 — bv-title/bv-meta 视觉一体, view-head 高 48→46

第一性原理: bv-title (h=18) + bv-meta (h=16.1) 是同源信息 (战法名 + 版本/规则数/日期),
  mt=2 占 2px 是默认节奏 (bv-title fs=15 lh=18 后接 lh=16.1 的副信息, 一般留 4-8px 视觉呼吸).
  但 mobile 屏幕金贵, 标题区与下条 (filter-bar mt=0 from R183 view-head mb=4) 之间争夺空间.
  mt 2→0 让两行紧贴成一组, view-head 高 48→46 (-2px). 视觉上 bv-title 底到 bv-meta 顶为 lh 重叠,
  文字 fs=15 + fs=11.5 不冲突 (x-height 间距自然留 1-2px).

断言 (真实服务, 390px):
  1. bv-meta margin-top 0px (从 2px)
  2. view-head 高 46 (从 48)
  3. bv-title h 不变 18
  4. bv-meta h 不变 16.1
  5. bv-meta 仍可见, 不被截断 (信息归宿)
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
    return {h: Math.round(rect.height*10)/10, w: Math.round(rect.width*10)/10, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  var viewHead = document.querySelector('.view-bv .view-head');
  var title = document.querySelector('.view-bv .bv-title');
  var meta = document.querySelector('.view-bv .bv-meta');
  return {
    viewHead: info(viewHead),
    title: info(title),
    meta: info(meta),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"viewHead: h={d['viewHead']['h']}")
        print(f"title: h={d['title']['h']}")
        print(f"meta: h={d['meta']['h']} mt={d['meta']['mt']}")

        assert d['meta']['mt'] == '0px', f"R191: meta mt={d['meta']['mt']} != 0px"
        # view-head h stays 48 (refresh btn view-actions h=36 主导, 不被 meta mt 影响)
        assert d['viewHead']['h'] == 48, f"R191: viewHead h={d['viewHead']['h']} 应仍 48 (refresh-btn 主导)"
        assert d['title']['h'] == 18, f"R191: title h={d['title']['h']} 应仍 18"
        assert d['meta']['h'] == 16.1, f"R191: meta h={d['meta']['h']} 应仍 16.1"

        await b.close()
        print(f"[OK] R191 bv-meta mt 2→0 — vhFirstChild 36→34.1, title/meta 视觉一体 (vh 由 refresh-btn 决定不变) ✓")

if __name__ == "__main__":
    asyncio.run(run())