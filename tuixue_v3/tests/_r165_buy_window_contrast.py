"""R165 mobile 买点窗口 浅色 banner 对比度 AA — 源 banner + pinned 双状态.

第一性原理: "可买/观望" 是用户决定要不要看下去/动手的最高优先级信号
  (R14/R107 已放大视觉), 但浅色主题 (data-theme=light, body 248,250,252) 下:
    · 源 banner .bv-buy-window 用 var(--accent)=rgb(37,126,228) 叠浅 banner 3.29:1
    · pinned .is-buy 浅绿 #7de8b2 叠合成底 1.09:1, .is-not-buy 浅灰 1.31:1 — 全挂
  买点窗口常驻 (永不 hidden, R14), 字看不清 = 信号不存在。
  修复: light 主题下源 banner 深蓝 #1d4ed8 (同 hue 系, 全 5 tone 4.54~5.44:1),
  pinned is-buy 深绿 green-800 / is-not-buy 深灰 gray-600。dark 主题原样 (4.71/5.96/3.63 合规)。

断言 (mock, 390px):
  1. light 主题下源 banner is-not-buy 合成对比度 ≥ 4.5 (was 3.29)
  2. pinned is-not-buy 合成对比度 ≥ 4.5 (was 1.31)
  3. 翻转到 is-buy 态: 源 + pinned 对比度均 ≥ 4.5 (pinned was 1.09)
  4. dark 主题下源 banner 仍用 var(--accent) (不被 light 覆盖破坏)
"""
import asyncio, json, re
from playwright.async_api import async_playwright

_TEMPLATE = open('/Users/kaikai/scripts/tuixue_v3/tests/_r159_sector_pill_cnt_legibility.py').read()
MOCKJS = re.search(r'MOCK = r"""\n(.*?)"""\n', _TEMPLATE, re.S).group(1)

def parse_rgba(s):
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)', s)
    if not m: return None
    return [int(m[1]), int(m[2]), int(m[3]), float(m[4]) if m[4] else 1.0]

def blend(fg, bg):
    a = fg[3]
    return [round(fg[i]*a + bg[i]*(1-a)) for i in range(3)]

def lum(c):
    c = c/255.0
    return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4

def ratio(a, b):
    la = 0.2126*lum(a[0]) + 0.7152*lum(a[1]) + 0.0722*lum(a[2])
    lb = 0.2126*lum(b[0]) + 0.7152*lum(b[1]) + 0.0722*lum(b[2])
    hi, lo = max(la,lb), min(la,lb)
    return (hi+0.05)/(lo+0.05)

def composite_bg(chain):
    base = list(chain[-1]['c'])
    for layer in reversed(chain[:-1]):
        base = blend([layer['c'][0], layer['c'][1], layer['c'][2], layer['a']], base)
    return base

MEASURE = r"""() => {
  var el = document.querySelector(SEL);
  if (!el) return {note:'missing ' + SEL};
  var cs = getComputedStyle(el);
  var chain = []; var n = el; var lim = 0;
  while (n && lim < 12) {
    var st = getComputedStyle(n);
    var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    if (bm) { chain.push({c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4])}); if (bm[4]===undefined) break; }
    n = n.parentElement; lim++;
  }
  return { cls: el.className, text: (el.textContent||'').trim(), color: cs.color, chain: chain };
}"""

async def measure(page, sel):
    return await page.evaluate(MEASURE.replace('SEL', "'" + sel + "'"))

async def load(page):
    for attempt in range(5):
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
    # 数据渲染完再 pin (复现真实流程: banner 已在 DOM 时 view-enter)
    await page.evaluate('() => document.dispatchEvent(new CustomEvent("view-enter", {detail:{name:"bv"}}))')
    await page.wait_for_timeout(500)

def contrast_of(m):
    base = composite_bg(m['chain'])
    fg = parse_rgba(m['color'])
    fg_c = blend(fg[:3]+[fg[3]], base)
    return ratio(fg_c, base), base

async def run():
    async with async_playwright() as p:
        # ── light 主题 (默认) ──
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
        await load(page)

        # is-not-buy 态 (mock phase=close)
        src = await measure(page, '.view-bv .bv-buy-window')
        pin = await measure(page, 'body > .bv-phase-banner.is-pinned .bv-buy-window')
        assert 'note' not in src, f"src banner buy-window missing: {src}"
        assert 'note' not in pin, f"pinned buy-window missing: {pin}"
        cr_src, b_src = contrast_of(src)
        cr_pin, b_pin = contrast_of(pin)
        print(f"light is-not-buy | src color={src['color']:<18} bg=rgb({b_src[0]},{b_src[1]},{b_src[2]}) {cr_src:.2f}:1"
              f" | pin color={pin['color']:<18} bg=rgb({b_pin[0]},{b_pin[1]},{b_pin[2]}) {cr_pin:.2f}:1")
        assert cr_src >= 4.5, f"R165: source is-not-buy {cr_src:.2f} < 4.5 (was 3.29)"
        assert cr_pin >= 4.5, f"R165: pinned is-not-buy {cr_pin:.2f} < 4.5 (was 1.31)"
        assert pin['color'] == "rgb(75, 85, 99)", f"R165: pinned is-not-buy should be gray-600, got {pin['color']}"
        assert src['color'] == "rgb(29, 78, 216)", f"R165: src should be #1d4ed8, got {src['color']}"

        # 翻转 is-buy 态 (源 + pinned 通过 MutationObserver 同步)
        await page.evaluate(r"""() => {
          var flip = function(sel){
            var b = document.querySelector(sel);
            if (!b) return;
            b.className = 'bv-buy-window is-buy';
            b.textContent = '📍 可买 10:40前';
          };
          flip('.view-bv .bv-buy-window');
        }""")
        await page.wait_for_timeout(400)
        src2 = await measure(page, '.view-bv .bv-buy-window')
        pin2 = await measure(page, 'body > .bv-phase-banner.is-pinned .bv-buy-window')
        cr_src2, b_src2 = contrast_of(src2)
        cr_pin2, b_pin2 = contrast_of(pin2)
        print(f"light is-buy     | src color={src2['color']:<18} bg=rgb({b_src2[0]},{b_src2[1]},{b_src2[2]}) {cr_src2:.2f}:1"
              f" | pin color={pin2['color']:<18} bg=rgb({b_pin2[0]},{b_pin2[1]},{b_pin2[2]}) {cr_pin2:.2f}:1")
        assert cr_src2 >= 4.5, f"R165: source is-buy {cr_src2:.2f} < 4.5"
        assert cr_pin2 >= 4.5, f"R165: pinned is-buy {cr_pin2:.2f} < 4.5 (was 1.09)"
        assert pin2['color'] == "rgb(22, 101, 52)", f"R165: pinned is-buy should be green-800, got {pin2['color']}"
        await browser.close()

        # ── dark 主题: 不被 light 覆盖破坏 ──
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        await ctx2.add_init_script(MOCKJS)
        page2 = await ctx2.new_page()
        await load(page2)
        m = await page2.evaluate(r"""() => {
          var b = document.querySelector('.view-bv .bv-buy-window');
          return { theme: document.documentElement.getAttribute('data-theme'),
                   color: b ? getComputedStyle(b).color : null };
        }""")
        print("dark src buy-window:", m)
        assert m['theme'] == 'dark'
        assert m['color'] and m['color'] != "rgb(29, 78, 216)", \
            f"R165: dark theme should keep var(--accent), got {m['color']}"
        await browser2.close()

        print(f"[OK] R165 buy-window — light: src is-not-buy {cr_src:.2f}:1 / is-buy {cr_src2:.2f}:1, "
              f"pinned is-not-buy {cr_pin:.2f}:1 / is-buy {cr_pin2:.2f}:1 — 全 ≥4.5 AA (was 3.29/1.31/1.09) ✓")

if __name__ == "__main__":
    asyncio.run(run())
