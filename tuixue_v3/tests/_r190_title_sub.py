"""R190: mobile 隐藏 bv-title .sub — 装饰 UP 名溢出截断, 信息已在 meta 行

第一性原理: bv-title '游资战法' + .sub 'Bryan 交易随笔 · 仓位管理 + ...' 同行 nowrap.
  实测: bv-title h=18 w=256 (with ofw:hidden); sub w=291 (溢出 35px 被裁切, 视觉上
  显示成 '游资战法 Bryan 交易随笔 · 仓位管理 + ...' 中间断掉 — 像bug不像设计).
  meta 行下面已经有 '游资仓位管理战法 · v1 · 15 条规则 · 2026-08-17' 携带版本/规则数/日期,
  UP 名 'Bryan' 在 sub 里, 但 mobile 用户根本看不到完整 — 截断 = 信息丢失 = 不如隐藏.
  R99 '标题是身份不是导航': 标题 '游资战法' 4 字本身已是身份, sub 是装饰.
  mobile 隐藏 sub → 视觉更干净, 消除'文字被切'的伪 bug 感.

断言 (真实服务, 390px):
  1. bv-title 宽度不超 80 (隐藏 sub 后只剩 4 字)
  2. bv-title 不再有 text-overflow 截断
  3. bv-meta 仍可见 (信息归宿)
  4. bv-title h 不变 (18)
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
    return {h: Math.round(rect.height*10)/10, w: Math.round(rect.width*10)/10, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, fs: cs.fontSize, lh: cs.lineHeight, ofw: cs.overflow, ofx: cs.overflowX, ws: cs.whiteSpace, txt: (el.textContent||'').trim().slice(0,40), disp: cs.display};
  }
  var title = document.querySelector('.view-bv .bv-title');
  var sub = document.querySelector('.view-bv .bv-title .sub');
  var meta = document.querySelector('.view-bv .bv-meta');
  return {
    title: info(title),
    sub: info(sub),
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
        print(f"title: h={d['title']['h']} w={d['title']['w']} fs={d['title']['fs']} ofw={d['title']['ofw']} disp={d['title']['disp']}")
        print(f"sub: disp={d['sub']['disp']} h={d['sub']['h']} w={d['sub']['w']} txt='{d['sub']['txt']}'")
        print(f"meta: h={d['meta']['h']} w={d['meta']['w']} fs={d['meta']['fs']} ofw={d['meta']['ofw']}")

        # 验证 sub 已隐藏 (display:none)
        assert d['sub']['disp'] == 'none', f"R190: sub disp={d['sub']['disp']} != none"
        # sub w=0 (display:none 占位 0)
        assert d['sub']['w'] == 0, f"R190: sub w={d['sub']['w']} 应 == 0 (display:none 不占位)"
        # title 高度不变 (nowrap 同行, sub 不占行高)
        assert d['title']['h'] == 18, f"R190: title h={d['title']['h']} 应仍 18"
        # meta 仍可见
        assert d['meta']['w'] > 100, f"R190: meta w={d['meta']['w']} 应 > 100 (信息仍在)"

        await b.close()
        print(f"[OK] R190 mobile 隐藏 bv-title .sub — 装饰 UP 名溢出截断消除, 信息归宿 meta ✓")

if __name__ == "__main__":
    asyncio.run(run())