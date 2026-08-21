"""R166 mobile 聚合条 pill 涨跌幅 chip 浅色主题对比度 AA — 板块温度计信号.

第一性原理: 聚合条每个板块 pill 的涨跌幅是"这个板块现在多热"的温度计读数
  (R152 已升字号 9→10.5), 决定用户优先看哪个板块。light 主题下 pill bg =
  hsla(shue 180~319,45%,45%,0.14) 叠白卡:
    · .bv-pos 原 hsl(0,70%,60%)=#e05252 全 hue 范围 worst 3.01:1
    · .bv-neg 原 hsl(120,60%,55%)=#47d147 worst 1.58:1 — 负值近不可见
  修复 (light 只覆盖, dark 原色 3.37/6.43 更好不动): pos→red-700 #ba1c1c
  worst 5.10:1, neg→green-800 #166534 worst 5.62:1, 全 hue 范围 AA。

断言 (mock, 390px):
  1. 源 mock .bv-pos pill 合成对比度 ≥ 4.5 (was 3.01)
  2. 注入 .bv-neg pill 合成对比度 ≥ 4.5 (was 1.58)
  3. 颜色 = #ba1c1c / #166534
  4. dark 主题下保持原 hsl(0,70%,60%) / hsl(120,60%,55%) 不被覆盖
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

async def measure_el(page, sel):
    return await page.evaluate(r"""() => {
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
      return { color: cs.color, chain: chain, text: el.textContent.trim() };
    }""".replace('SEL', "'" + sel + "'"))

def contrast_of(m):
    base = composite_bg(m['chain'])
    fg = parse_rgba(m['color'])
    fg_c = blend(fg[:3]+[fg[3]], base)
    return ratio(fg_c, base), base

async def run():
    async with async_playwright() as p:
        # ── light ──
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
        await load(page)

        # measure source .bv-pos pill
        pos = await page.evaluate(r"""() => {
          var el = document.querySelector('.bv-sector-pill .bv-pos');
          if (!el) return {note:'no pos'};
          var cs = getComputedStyle(el);
          var chain = []; var n = el, lim = 0;
          while (n && lim < 12) {
            var st = getComputedStyle(n);
            var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
            if (bm) { chain.push({c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4])}); if (bm[4]===undefined) break; }
            n = n.parentElement; lim++;
          }
          return { color: cs.color, chain: chain, text: el.textContent.trim() };
        }""")
        assert 'note' not in pos, f"no bv-pos pill: {pos}"
        cr_pos, b_pos = contrast_of(pos)
        print(f"light pos pill: color={pos['color']:<14} bg=rgb({b_pos[0]},{b_pos[1]},{b_pos[2]}) {cr_pos:.2f}:1")
        assert cr_pos >= 4.5, f"R166: pos pill {cr_pos:.2f} < 4.5 (was 3.01)"
        assert pos['color'] == "rgb(186, 28, 28)", f"R166: pos should be #ba1c1c, got {pos['color']}"

        # inject a neg pill and measure
        neg = await page.evaluate(r"""() => {
          var pill = document.querySelector('.bv-sector-pill');
          if (!pill) return {note:'no pill'};
          var s = document.createElement('span');
          s.className = 'bv-sector-pill-chg bv-neg';
          s.textContent = '-2.5';
          pill.appendChild(s);
          var cs = getComputedStyle(s);
          var chain = []; var n = s, lim = 0;
          while (n && lim < 12) {
            var st = getComputedStyle(n);
            var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
            if (bm) { chain.push({c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4])}); if (bm[4]===undefined) break; }
            n = n.parentElement; lim++;
          }
          return { color: cs.color, chain: chain, text: s.textContent.trim() };
        }""")
        assert 'note' not in neg, f"neg inject failed: {neg}"
        cr_neg, b_neg = contrast_of(neg)
        print(f"light neg pill: color={neg['color']:<14} bg=rgb({b_neg[0]},{b_neg[1]},{b_neg[2]}) {cr_neg:.2f}:1")
        assert cr_neg >= 4.5, f"R166: neg pill {cr_neg:.2f} < 4.5 (was 1.58)"
        assert neg['color'] == "rgb(22, 101, 52)", f"R166: neg should be #166534, got {neg['color']}"
        await browser.close()

        # ── dark: 保持原色 ──
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        await ctx2.add_init_script(MOCKJS)
        page2 = await ctx2.new_page()
        await load(page2)
        d = await page2.evaluate(r"""() => {
          var el = document.querySelector('.bv-sector-pill .bv-pos');
          if (!el) return {theme: document.documentElement.getAttribute('data-theme'), color: null};
          return { theme: document.documentElement.getAttribute('data-theme'), color: getComputedStyle(el).color };
        }""")
        print("dark pos pill:", d)
        assert d['theme'] == 'dark', f"R166: expected dark theme, got {d['theme']}"
        assert d['color'] and d['color'] != "rgb(186, 28, 28)", \
            f"R166: dark should keep hsl(0,70%,60%), got {d['color']}"
        await browser2.close()

        print(f"[OK] R166 pill-chg — light pos {cr_pos:.2f}:1 / neg {cr_neg:.2f}:1 全 ≥4.5 AA (was 3.01/1.58), dark 原色保持 ✓")

if __name__ == "__main__":
    asyncio.run(run())
