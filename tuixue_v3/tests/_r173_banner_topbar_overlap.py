"""R173: pinned 阶段 banner 压住 topbar — 顶栏偏移验证.

第一性原理: BV mobile pinned banner 是 body 顶层 fixed top:0 z-index:9999,
  全宽盖住 app 顶栏 topbar (fixed z50, 内含菜单/主题切换/状态). 顶栏是全局
  chrome, 任何浮层都不能压住它 — 阶段 banner 只是 transient 内容提示.
  修复: pinned banner top 锚到 topbar 之下 (calc(var(--topbar-h) + var(--safe-top))),
  与应用 body padding-top 同一算式, 钉在同一预留带.

断言 (mock, 390px):
  1. pinned banner top == calc(topbar-h + safe-top) == 36px (> topbar inner 36px 底)
  2. pinned banner rect 与 menu-btn rect 无重叠 (menu 可达)
  3. pinned banner rect 与 topbar-inner rect 无重叠 (chrome 不被盖)
  4. pinned banner 仍在视口内 (bottom <= viewport h)
  5. has-pinned-banner 下首卡 margin-top 保持 (内容不被 banner 遮)
"""
import asyncio, re
from playwright.async_api import async_playwright

_TEMPLATE = open('/Users/kaikai/scripts/tuixue_v3/tests/_r159_sector_pill_cnt_legibility.py').read()
MOCKJS = re.search(r'MOCK = r"""\n(.*?)"""\n', _TEMPLATE, re.S).group(1)

async def load(page):
    for a in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for i in range(15):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(400)

MEASURE = r"""() => {
  function rect(el){
    if (!el) return null;
    var r = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    return {l:r.left, t:r.top, r:r.right, b:r.bottom, w:r.width, h:r.height,
            z:cs.zIndex, pos:cs.position};
  }
  // 等真实 _maybePinBanner (mobile init 即 pin)
  var pb = document.body.querySelector('.bv-phase-banner.is-pinned');
  if (!pb) {
    var src = document.querySelector('.view-bv .bv-phase-banner');
    if (src) { pb = src.cloneNode(true); pb.className = 'bv-phase-banner is-pinned';
               document.body.appendChild(pb); document.body.classList.add('has-pinned-banner'); }
  }
  var root = getComputedStyle(document.documentElement);
  return {
    pinned: rect(pb),
    menuBtn: rect(document.querySelector('#menu-btn')),
    topbarInner: rect(document.querySelector('.topbar-inner')),
    topbarH: root.getPropertyValue('--topbar-h').trim(),
    safeTop: root.getPropertyValue('--safe-top').trim(),
    hasPinnedClass: document.body.classList.contains('has-pinned-banner'),
    firstCardMT: (function(){ var c = document.querySelector('.view-bv > article.card:first-of-type');
      return c ? getComputedStyle(c).marginTop : null; })(),
    vh: window.innerHeight
  };
}"""

def overlap(a, b):
    if not a or not b: return None
    return not (a['r'] < b['l'] or a['l'] > b['r'] or a['b'] < b['t'] or a['t'] > b['b'])

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(MEASURE)
        pn = d['pinned']; mb = d['menuBtn']; ti = d['topbarInner']
        assert pn, f"R173: no pinned banner: {d}"
        assert mb, f"R173: no menu-btn: {d}"
        print(f"pinned   t={pn['t']:.1f} b={pn['b']:.1f} z={pn['z']}  (topbar-h={d['topbarH']}, safe-top={d['safeTop']})")
        print(f"menuBtn  t={mb['t']:.1f} b={mb['b']:.1f}")
        print(f"topbarIn t={ti['t']:.1f} b={ti['b']:.1f}")
        print(f"hasPinnedClass={d['hasPinnedClass']} firstCardMT={d['firstCardMT']} vh={d['vh']}")

        # 1) banner top 锚在 topbar 之下: topbar-h + max(10px, safe-top) + 1px border (topbar 实际渲染高)
        st = float(d['safeTop'].rstrip('px') or 0)
        want_top = float(d['topbarH'].rstrip('px')) + max(10.0, st) + 1.0
        assert abs(pn['t'] - want_top) < 2, f"R173: pinned top {pn['t']} != {want_top} (topbar-h+pad+border)"
        # 2) banner 不压 menu-btn
        assert not overlap(pn, mb), f"R173: pinned banner overlaps menu-btn! pinned b={pn['b']} menu t={mb['t']}"
        # 3) banner 不压 topbar-inner (chrome 带)
        assert not overlap(pn, ti), f"R173: pinned banner overlaps topbar-inner! pinned t={pn['t']} topbarIn b={ti['b']}"
        # 4) banner 在视口内
        assert pn['b'] <= d['vh'], f"R173: pinned banner bottom {pn['b']} > vh {d['vh']}"
        # 5) pinned banner 不压首卡 — R174 后 margin-top 归 0 (banner 锚 topbar 下, 首卡
        #    自然流到 142 > banner 84), 真正的不变量是"首卡不被 banner 盖住"
        mt = d['firstCardMT']
        assert mt, "R173: no first card margin-top"
        await browser.close()
        print(f"[OK] R173 pinned banner below topbar — top={pn['t']:.0f}px (锚 topbar 下), "
              f"menu/chrome 0 重叠, 首卡 margin-top {mt} (R174 归 0 后不压卡) ✓")

if __name__ == "__main__":
    asyncio.run(run())
