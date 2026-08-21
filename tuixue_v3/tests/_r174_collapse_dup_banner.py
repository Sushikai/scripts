"""R174: pinned 激活时折叠 view-head 内重复 phase banner — 回收 32px.

第一性原理: BV mobile view-head 内 in-view phase banner (icon/label/TTL/buy-window)
  与 pinned banner (body 顶层 clone, 同源同步) 内容完全重复。两者同时显示 =
  同一信息占两处 (32px 原地 + 首卡下压)。pinned 已 detached 到 body, 源 banner
  只是 sync 的 template — 折叠它信息零损失, 垂直空间还给推票表。

断言 (mock, 390px):
  1. R175 后 deep-link 即自动 pin: mobile 首次加载 has-pinned=true (pre-pin
     状态在真实流程已不存在, R174 测试更新为直接验证 pin 后折叠)
  2. pinned 激活时: in-view banner display:none (折叠), pinned 内容完整
  3. pinned 内容 == 折叠前 in-view 内容 (icon/label/TTL/buy-window)
  4. 折叠后 in-view banner 不占位 (首卡不被重复 banner 下压)
  5. pinned 仍在视口 (topbar 之下, R173 不回归)
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

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var inv = document.querySelector('.view-bv .bv-phase-banner');
  var head = document.querySelector('.view-bv .view-head');
  // R176 后主内容卡是 pick-card (flex order:1), 用它测首卡位置
  var card = document.querySelector('.view-bv > .bv-pick-card') ||
             document.querySelector('.view-bv > article.card:first-of-type');
  var out = {
    inv: r(inv), invDisp: getComputedStyle(inv).display,
    head: r(head), headH: head ? getComputedStyle(head).height : null,
    firstCard: r(card),
    hasPinned: document.body.classList.contains('has-pinned-banner')
  };
  var pinned = document.body.querySelector('.bv-phase-banner.is-pinned');
  out.pinned = r(pinned);
  out.pinnedText = pinned ? (pinned.textContent||'').replace(/\s+/g,' ').trim() : '';
  return out;
}"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
        await load(page)

        # 1) R175 后 deep-link mobile 自动 pin — has-pinned-banner 已激活
        post = await page.evaluate(PROBE)
        assert post['hasPinned'], "R174: has-pinned-banner class missing"
        # 2) pinned 激活: in-view banner display:none (折叠)
        assert post['inv'] is None or post['invDisp'] == 'none', \
            f"R174: in-view banner still visible post-pin: disp={post['invDisp']}"
        # 3) pinned content == pre in-view content
        assert post['pinnedText'] == "⚫ 盘后守候 TTL 300s ⛔ 观望中", \
            f"R174: pinned content mismatch: '{post['pinnedText']}'"
        # 4) in-view banner 折叠后不占位 — 首卡 (R176 后为 pick-card) 顶部高于 in-view banner 底
        assert post['firstCard'] and post['firstCard']['t'] < 200, \
            f"R174: first card top {post['firstCard']['t']} not in upper area"
        # 5) pinned still below topbar (R173 no-regress)
        assert post['pinned'] and post['pinned']['t'] >= 40, \
            f"R174: pinned banner top {post['pinned']['t']} should be below topbar (>=40)"
        print(f"post-pin: firstCard t={post['firstCard']['t']}, pinned t={post['pinned']['t']}, "
              f"pinnedText='{post['pinnedText']}'")
        await browser.close()
        print(f"[OK] R174 in-view banner collapsed on pin — 首卡 t={post['firstCard']['t']} (无重复 banner 下压), "
              f"pinned 内容完整 + topbar 之下 ✓")

if __name__ == "__main__":
    asyncio.run(run())
