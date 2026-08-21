"""R176: mobile 推票优先 — 战法哲学卡沉底 (flex order 重排).

第一性原理: BV mobile 页里推票是唯一"可操作/高频变化"内容 (筛选/排序/点开/跳转),
  战法哲学 (哲思/原话/UP主) 是纯展示性 context — context 永远不该挡在主内容前面.
  DOM 顺序里 creed 是第一个 card (142..281, 139px), 推票表卡 281 起, 首行 419 —
  用户每次进页都要先滚过一个 139px 的静止哲学卡才能看到推票. 前面 R91-R95 只是
  "折叠 creed 内容", 没动"卡在首屏占位"这件事. R176 从源头解决: mobile 下
  .view-bv 改 flex column, pick-card order:1 紧跟 view-head, creed order:99 沉底.
  推票首行 419 → ~280, 回收 139px 给主内容.

断言 (真实服务, 390px):
  1. pick-card 紧跟 view-head (pickTop - headBottom < 10px)
  2. 首行 top <= 300 (419 → ~280, 回收 >= 119px)
  3. creed-card top > pick-card bottom (沉底, 不再挡推票)
  4. view-bv 是 flex column (order 生效的机制)
  5. pinned banner 不回归 (R175 仍生效)
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for a in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for i in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(500)

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect();
    return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var view = document.querySelector('.view-bv');
  var head = document.querySelector('.view-bv .view-head');
  var creed = document.querySelector('.view-bv .bv-creed-card');
  var pick = document.querySelector('.view-bv .bv-pick-card');
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var pinned = document.body.querySelector('.bv-phase-banner.is-pinned');
  return {
    viewDisp: getComputedStyle(view).display,
    viewFd: getComputedStyle(view).flexDirection,
    head: r(head),
    pick: r(pick),
    creed: r(creed),
    firstRow: r(rows[0]),
    hasPinned: document.body.classList.contains('has-pinned-banner'),
    pinnedTop: pinned ? Math.round(pinned.getBoundingClientRect().top) : null
  };
}"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)

        print(f"viewDisp={d['viewDisp']} fd={d['viewFd']}")
        print(f"head   {d['head']['t']}..{d['head']['b']}")
        print(f"pick   {d['pick']['t']}..{d['pick']['b']}")
        print(f"creed  {d['creed']['t']}..{d['creed']['b']}")
        print(f"first  {d['firstRow']['t']}..{d['firstRow']['b']}")
        print(f"hasPinned={d['hasPinned']} pinnedTop={d['pinnedTop']}")

        # 1) view-bv 是 flex column (order 生效机制)
        assert d['viewDisp'] == 'flex', f"R176: .view-bv not flex ({d['viewDisp']})"
        assert d['viewFd'] == 'column', f"R176: .view-bv flex-dir not column ({d['viewFd']})"

        # 2) pick-card 紧跟 view-head (gap < 10px)
        gap = d['pick']['t'] - d['head']['b']
        assert gap < 10, f"R176: pick card not right after head (gap {gap}px)"

        # 3) 首行 top <= 300 (从 419 上移)
        assert d['firstRow']['t'] <= 300, \
            f"R176: first row top {d['firstRow']['t']} not reclaimed (want <=300)"

        # 4) creed 沉底: creed top > pick bottom
        assert d['creed']['t'] > d['pick']['b'], \
            f"R176: creed ({d['creed']['t']}) not below pick ({d['pick']['b']})"

        # 5) pinned banner 不回归 (R175 仍生效)
        assert d['hasPinned'], "R176: R175 pin regressed (no has-pinned-banner)"
        assert d['pinnedTop'] and d['pinnedTop'] >= 40, \
            f"R176: pinned top {d['pinnedTop']} not below topbar"

        reclaimed = 419 - d['firstRow']['t']
        await browser.close()
        print(f"[OK] R176 pick-first — 首行 {419}→{d['firstRow']['t']} (回收 {reclaimed}px), "
              f"pick 紧跟 head (gap {gap}px), creed 沉底 (t={d['creed']['t']} > pick b={d['pick']['b']}), "
              f"pin 不回归 (top={d['pinnedTop']}) ✓")

if __name__ == "__main__":
    asyncio.run(run())
