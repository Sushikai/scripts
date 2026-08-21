"""R169 mobile 剩余 light 主题对比度 AA — 首板/连板/排序按钮/涨幅 收尾.

第一性原理: R163 扫描 6 项 sub-AA 文本, R164-R168 修了按钮/banner/pill/hit-badge,
  剩下的 4 处全是 light 浅卡上的"为深底设计的浅色/中性色":
    1) 首板 streak badge hsl(0,80%,65%)#ed5e5e 叠粉卡 (bg #F8D6D7) = 2.45:1
    2) 2板/连板 streak td var(--accent)#257ee4 叠灰卡 (bg #E2E8F0) = 3.29:1
    3) .bv-sort-btn "↓ 分数" var(--accent) 同灰卡 = 3.29:1
    4) td.bv-pos change_pct red-600#DC2626 叠灰卡 3.92 / 叠 strong 粉卡 3.70
  修复 (全 [data-theme=light] 限定, dark 深底亮字天然高对比不动):
    1) 首板 → red-700 #BA1C1C (4.77:1)
    2) 连板 → blue-700 #1D4ED8 (5.44:1)
    3) sort-btn → blue-700 #1D4ED8 (5.44:1)
    4) bv-pos → red-700 #BA1C1C (5.21 normal / 4.92 strong)

断言 (mock, 390px):
  1. 首板行 streak badge 合成对比度 ≥ 4.5 (was 2.45) + 颜色 #BA1C1C
  2. 非首板连板 td (2板) 合成对比度 ≥ 4.5 (was 3.29) + 颜色 #1D4ED8
  3. .bv-sort-btn "↓ 分数" 合成对比度 ≥ 4.5 (was 3.29) + 颜色 #1D4ED8
  4. td.bv-pos change_pct 合成对比度 ≥ 4.5 (was 3.92) + 颜色 #BA1C1C
  5. dark 主题下 4 项保持原色不被覆盖 (回归守护)
"""
import asyncio, re
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

MEASURE = r"""() => {
  function meas(el){
    if (!el) return null;
    var cs = getComputedStyle(el);
    var chain = []; var n = el, lim = 0;
    while (n && lim < 12) {
      var st = getComputedStyle(n);
      var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if (bm) { chain.push({c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4])}); if (bm[4]===undefined) break; }
      n = n.parentElement; lim++;
    }
    return {color: cs.color, chain: chain, text: (el.textContent||'').trim().slice(0,12)};
  }
  var out = {};
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  for (var i=0;i<rows.length;i++){
    var tds = rows[i].querySelectorAll('td');
    if (tds.length >= 6) {
      if (rows[i].classList.contains('is-first-board')) {
        out.streakFirst = meas(tds[5]);
      } else if (!out.streak2) {
        out.streak2 = meas(tds[5]);
      }
    }
    var ch = rows[i].querySelector('td.bv-pos');
    if (ch && !out.pos) out.pos = meas(ch);
  }
  out.sortBtn = meas(document.querySelector('.bv-sort-btn'));
  return out;
}"""

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
        d = await page.evaluate(MEASURE)
        assert d.get('streakFirst'), f"R169: no first-board streak: {d}"
        assert d.get('streak2'), f"R169: no 2-board streak: {d}"
        assert d.get('sortBtn'), f"R169: no sort-btn: {d}"
        assert d.get('pos'), f"R169: no bv-pos: {d}"

        # 1) 首板 streak badge
        cr_sf, b_sf = contrast_of(d['streakFirst'])
        print(f"light 首板: color={d['streakFirst']['color']:<16} bg=rgb({b_sf[0]},{b_sf[1]},{b_sf[2]}) {cr_sf:.2f}:1")
        assert cr_sf >= 4.5, f"R169: first-board streak {cr_sf:.2f} < 4.5 (was 2.45)"
        assert d['streakFirst']['color'] == "rgb(186, 28, 28)", f"R169: streak first should be #BA1C1C, got {d['streakFirst']['color']}"
        # 2) 2板 streak td
        cr_s2, b_s2 = contrast_of(d['streak2'])
        print(f"light 2板: color={d['streak2']['color']:<16} bg=rgb({b_s2[0]},{b_s2[1]},{b_s2[2]}) {cr_s2:.2f}:1")
        assert cr_s2 >= 4.5, f"R169: 2板 streak {cr_s2:.2f} < 4.5 (was 3.29)"
        assert d['streak2']['color'] == "rgb(29, 78, 216)", f"R169: streak 2板 should be #1D4ED8, got {d['streak2']['color']}"
        # 3) sort-btn
        cr_sb, b_sb = contrast_of(d['sortBtn'])
        print(f"light sort: color={d['sortBtn']['color']:<16} bg=rgb({b_sb[0]},{b_sb[1]},{b_sb[2]}) {cr_sb:.2f}:1")
        assert cr_sb >= 4.5, f"R169: sort-btn {cr_sb:.2f} < 4.5 (was 3.29)"
        assert d['sortBtn']['color'] == "rgb(29, 78, 216)", f"R169: sort-btn should be #1D4ED8, got {d['sortBtn']['color']}"
        # 4) bv-pos change
        cr_p, b_p = contrast_of(d['pos'])
        print(f"light bv-pos: color={d['pos']['color']:<16} bg=rgb({b_p[0]},{b_p[1]},{b_p[2]}) {cr_p:.2f}:1")
        assert cr_p >= 4.5, f"R169: bv-pos {cr_p:.2f} < 4.5 (was 3.92)"
        assert d['pos']['color'] == "rgb(186, 28, 28)", f"R169: bv-pos should be #BA1C1C, got {d['pos']['color']}"
        await browser.close()

        # ── dark: 原色保持 ──
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        await ctx2.add_init_script(MOCKJS)
        page2 = await ctx2.new_page()
        await load(page2)
        dk = await page2.evaluate(MEASURE)
        theme = await page2.evaluate("() => document.documentElement.getAttribute('data-theme')")
        print("dark:", theme, "streakFirst:", dk.get('streakFirst',{}).get('color'),
              "streak2:", dk.get('streak2',{}).get('color'),
              "sortBtn:", dk.get('sortBtn',{}).get('color'),
              "pos:", dk.get('pos',{}).get('color'))
        assert theme == 'dark', f"R169: expected dark, got {theme}"
        # dark 首板保持 hsl(0,80%,65%) 非 #BA1C1C
        assert dk['streakFirst']['color'] and dk['streakFirst']['color'] != "rgb(186, 28, 28)", \
            f"R169: dark first-board streak should keep hsl(0,80%,65%), got {dk['streakFirst'].get('color')}"
        # dark 2板保持 accent (rgb(73,148,233)) 非 #1D4ED8
        assert dk['streak2']['color'] and dk['streak2']['color'] != "rgb(29, 78, 216)", \
            f"R169: dark 2板 streak should keep accent, got {dk['streak2'].get('color')}"
        # dark sort-btn 保持 accent
        assert dk['sortBtn']['color'] and dk['sortBtn']['color'] != "rgb(29, 78, 216)", \
            f"R169: dark sort-btn should keep accent, got {dk['sortBtn'].get('color')}"
        # dark bv-pos 保持 #E5404A
        assert dk['pos']['color'] and dk['pos']['color'] != "rgb(186, 28, 28)", \
            f"R169: dark bv-pos should keep #E5404A, got {dk['pos'].get('color')}"
        await browser2.close()

        print(f"[OK] R169 remaining light contrast — 首板 {cr_sf:.2f} / 2板 {cr_s2:.2f} / sort {cr_sb:.2f} / bv-pos {cr_p:.2f} 全 ≥4.5 AA (was 2.45/3.29/3.29/3.92), dark 原色保持 ✓")

if __name__ == "__main__":
    asyncio.run(run())
