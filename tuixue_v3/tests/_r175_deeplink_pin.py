"""R175: deep-link 首次进 BV 补 _maybePinBanner — 真实加载 pinned 生效.

第一性原理: BV mobile pinned banner 只在 view-enter listener (bv-frontend.js:1991)
  创建。但 showView('bv') 异步加载脚本时, view-enter 在脚本就绪前 dispatch
  (R2003.7 注释), listener 注册太晚 → 首次 deep-link 进页面 pinned banner 不出现,
  R174 的折叠 + margin 回收全部不生效 (实测 creedTop 175, hasPinned=false)。
  深链 init 路径 (2039-2045) 只 loadLivePick, 漏了 _maybePinBanner。

修复: deep-link init 路径补 _maybePinBanner()。

断言 (真实服务, 390px, 首次 deep-link 加载):
  1. hasPinned-banner class 存在 (pin 生效)
  2. pinned banner 元素存在
  3. in-view banner display:none (R174 折叠触发)
  4. 首卡 top <= 200 (pin 回收后主内容进入首屏; R176 后首卡是 pick-card)
  5. pinned banner 内容完整 + topbar 之下 (R173/R174 不回归)
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
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom)}; }
  // R176 后首卡是 pick-card (flex order:1), 用首张 card 拿主内容位置
  var card = document.querySelector('.view-bv > .bv-pick-card') ||
             document.querySelector('.view-bv > article.card:first-of-type');
  var pinned = document.body.querySelector('.bv-phase-banner.is-pinned');
  var inv = document.querySelector('.view-bv .bv-phase-banner');
  return {
    hasPinned: document.body.classList.contains('has-pinned-banner'),
    pinnedExists: !!pinned,
    pinnedText: pinned ? (pinned.textContent||'').replace(/\s+/g,' ').trim() : '',
    pinnedTop: r(pinned) ? r(pinned).t : null,
    invDisp: getComputedStyle(inv).display,
    creedTop: card ? Math.round(card.getBoundingClientRect().top) : null,
    creedMT: card ? getComputedStyle(card).marginTop : null
  };
}"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"hasPinned={d['hasPinned']} pinnedExists={d['pinnedExists']} invDisp={d['invDisp']} "
              f"creedTop={d['creedTop']} creedMT={d['creedMT']} pinnedTop={d['pinnedTop']}")
        assert d['hasPinned'], "R175: pin not active on deep-link"
        assert d['pinnedExists'], "R175: pinned banner missing"
        assert d['invDisp'] == 'none', f"R175: in-view banner not collapsed ({d['invDisp']})"
        assert d['creedTop'] is not None and d['creedTop'] <= 200, \
            f"R175: first card top {d['creedTop']} not reclaimed (want <=200)"
        assert d['pinnedTop'] and d['pinnedTop'] >= 40, f"R175: pinned top {d['pinnedTop']} below topbar"
        assert '盘后守候' in (d['pinnedText'] or ''), f"R175: pinned content lost: '{d['pinnedText']}'"
        await browser.close()
        print(f"[OK] R175 deep-link pin active — firstCardTop {d['creedTop']} (pin 回收生效), "
              f"pinned content ok + topbar 之下 ✓")

if __name__ == "__main__":
    asyncio.run(run())
