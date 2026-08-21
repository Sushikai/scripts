"""R189: mobile view-head gap 8→6 — 标题与刷新按钮间距紧凑

第一性原理: view-head gap 8px (R99 设的) — title (254w) + refresh btn (68w) 之间
  8px 间距. 跟 R187 pickHead gap 6 + R188 sortBtn padding 6 节奏不一致.
  8→6 让 title/refresh 之间节奏统一 (全局 gap 6).

断言 (真实服务, 390px):
  1. view-head gap=6px (从 8px)
  2. view-head 高度不变 (48)
  3. title (vhFirst) 宽度增加 2px
  4. refresh btn 仍可点 (≥40px 宽)
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
    return {h: rect.h, w: rect.w, gap: cs.gap};
  }
  var viewHead = document.querySelector('.view-bv .view-head');
  var title = viewHead ? viewHead.querySelector('div:first-child') : null;
  var refresh = document.querySelector('.view-bv .view-head .btn-refresh');
  return {
    viewHead: info(viewHead),
    title: info(title),
    refresh: info(refresh),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"viewHead: h={d['viewHead']['h']} gap={d['viewHead']['gap']}")
        print(f"title: h={d['title']['h']} w={d['title']['w']}")
        print(f"refresh: h={d['refresh']['h']} w={d['refresh']['w']}")

        assert d['viewHead']['gap'] == '6px', f"R189: view-head gap={d['viewHead']['gap']} != 6px"
        assert d['viewHead']['h'] == 48, f"R189: view-head h={d['viewHead']['h']} 应仍 48"
        assert d['title']['w'] >= 256, f"R189: title w={d['title']['w']} 应 >= 256 (回收 2px)"
        assert d['refresh']['w'] >= 60, f"R189: refresh btn w={d['refresh']['w']} 应 >= 60"

        await b.close()
        print(f"[OK] R189 view-head gap 8→6 — title w {d['title']['w']}px (回收 2px), view-head 高 48 不变 ✓")

if __name__ == "__main__":
    asyncio.run(run())
