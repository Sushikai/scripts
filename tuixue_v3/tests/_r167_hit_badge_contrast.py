"""R167 mobile hit-badge 弱/强态对比度 AA — light 浅卡上冷灰/热红可读.

第一性原理: 命中数 badge 告诉用户"这行几条规则命中" — 弱(=1)/强(≥3)态
  是快扫时的强度分级 (R80 内嵌规则行首格)。light 浅卡上:
    · cold #ddd 叠 rgba(120,120,120,0.7) 合成底 ≈rgb(160,160,160) → 1.93:1
    · hot #ff5757 白字 → 3.11:1
  全 <4.5 AA。修复 light 只覆盖 (dark 下 #ddd 4.58-5.07 本就 AA, 深字反而破):
    cold→#111827 (6.78:1), hot bg→red-600 #dc2626 白字 4.83:1。

断言 (mock, 390px, 注入 cold/hot badge):
  1. cold 合成对比度 ≥ 4.5 (was 1.93)
  2. hot 合成对比度 ≥ 4.5 (was 3.11)
  3. cold 文字 #111827, hot bg #dc2626
  4. dark 下 cold 保持 #ddd (不被覆盖)
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

INJECT_MEASURE = r"""() => {
  var cell = document.querySelector('.bv-rules-cell');
  if (!cell) return {note:'no rules-cell'};
  var cold = document.createElement('span');
  cold.className = 'bv-hit-badge cold'; cold.textContent = '1';
  var hot = document.createElement('span');
  hot.className = 'bv-hit-badge hot'; hot.textContent = '5';
  cell.appendChild(cold); cell.appendChild(hot);
  function meas(b){
    var cs = getComputedStyle(b);
    var chain=[]; var n=b,lim=0;
    while(n&&lim<12){
      var st=getComputedStyle(n);
      var bm=st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if(bm){chain.push({c:[+bm[1],+bm[2],+bm[3]],a:bm[4]===undefined?1:parseFloat(bm[4])}); if(bm[4]===undefined)break;}
      n=n.parentElement;lim++;
    }
    return {color:cs.color,bg:cs.backgroundColor,chain:chain};
  }
  return {cold:meas(cold), hot:meas(hot)};
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
        m = await page.evaluate(INJECT_MEASURE)
        assert 'note' not in m, f"inject failed: {m}"
        cr_cold, b_cold = contrast_of(m['cold'])
        cr_hot, b_hot = contrast_of(m['hot'])
        print(f"light cold: color={m['cold']['color']:<14} bg=rgb({b_cold[0]},{b_cold[1]},{b_cold[2]}) {cr_cold:.2f}:1")
        print(f"light hot:  color={m['hot']['color']:<14} bg=rgb({b_hot[0]},{b_hot[1]},{b_hot[2]}) {cr_hot:.2f}:1")
        assert cr_cold >= 4.5, f"R167: cold {cr_cold:.2f} < 4.5 (was 1.93)"
        assert cr_hot >= 4.5, f"R167: hot {cr_hot:.2f} < 4.5 (was 3.11)"
        assert m['cold']['color'] == "rgb(17, 24, 39)", f"R167: cold should be #111827, got {m['cold']['color']}"
        assert m['hot']['bg'] == "rgb(220, 38, 38)", f"R167: hot should be #dc2626, got {m['hot']['bg']}"
        await browser.close()

        # ── dark: cold 保持 #ddd ──
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        await ctx2.add_init_script(MOCKJS)
        page2 = await ctx2.new_page()
        await load(page2)
        d = await page2.evaluate(r"""() => {
          var cell = document.querySelector('.bv-rules-cell');
          if (!cell) return {theme: document.documentElement.getAttribute('data-theme'), color: null};
          var cold = document.createElement('span');
          cold.className = 'bv-hit-badge cold'; cold.textContent = '1';
          cell.appendChild(cold);
          return { theme: document.documentElement.getAttribute('data-theme'), color: getComputedStyle(cold).color };
        }""")
        print("dark cold:", d)
        assert d['theme'] == 'dark', f"R167: expected dark theme, got {d['theme']}"
        assert d['color'] == "rgb(221, 221, 221)", f"R167: dark cold should keep #ddd, got {d['color']}"
        await browser2.close()

        print(f"[OK] R167 hit-badge — cold {cr_cold:.2f}:1 / hot {cr_hot:.2f}:1 全 ≥4.5 AA (was 1.93/3.11), dark cold #ddd 保持 ✓")

if __name__ == "__main__":
    asyncio.run(run())
