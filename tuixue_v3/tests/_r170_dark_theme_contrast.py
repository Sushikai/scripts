"""R170 dark 主题全页对比度扫描 — 双主题 AA 里程碑验证.

第一性原理: R163-R169 只扫了 light 主题 (默认). dark 深底亮字天然高对比,
  但 R163 的 motto-badge 修复 (#7feaff→#155e75) 是全局的, 没加 [data-theme=light]
  限定 → dark top-1 深青卡上 #155e75 1.12:1 近不可见 (深底需要浅字, 浅底需要深字,
  同一元素的对比修复必须主题感知 — R165-R169 确立的原则, R163 自己违反了).

R170 修复 (dark 限定, light 走 R169 不动):
  1) motto-badge 基准回 #7feaff (dark 5.86-7.74:1), 加 [data-theme=light] #155e75
  2) td.bv-pos change_pct dark --up #E5404A 叠灰卡 3.48 / strong 粉卡 3.89 → #FF6B73 5.12/5.73
  3) bv-sector-pill-chg.bv-pos dark hsl(0,70%,60%) 3.95-4.11 → #FF6B73 5.82-6.05

断言 (mock, 390px, light + dark 双主题):
  1. dark: 全页 alpha 合成对比度扫描 0 sub-AA (was 8)
  2. dark motto-badge #7feaff, light motto-badge #155e75
  3. dark bv-pos #FF6B73, light bv-pos #BA1C1C
  4. dark pill-chg.bv-pos #FF6B73, light #BA1C1C
  5. light: 全页扫描仍 0 sub-AA (R169 不回归)
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

SCAN = r"""() => {
  var out = [];
  document.querySelectorAll('.view-bv, .view-bv *').forEach(function(el){
    if (el.children.length > 0) return;
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    var txt = (el.textContent || '').trim();
    if (!txt) return;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    var cm = cs.color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
    if (!cm) return;
    var chain = []; var n = el, limit = 0;
    while (n && limit < 12) {
      var st = getComputedStyle(n);
      var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if (bm) {
        var a = bm[4] === undefined ? 1 : parseFloat(bm[4]);
        chain.push({c:[+bm[1],+bm[2],+bm[3]], a: a});
        if (a >= 1) break;
      }
      n = n.parentElement; limit++;
    }
    if (!chain.length) return;
    out.push({cls:String(el.className||'').slice(0,34), text:txt.slice(0,12),
      fg:[+cm[1],+cm[2],+cm[3],cm[4]===undefined?1:parseFloat(cm[4])], chain:chain,
      fs:parseFloat(cs.fontSize)});
  });
  return out;
}"""

def scan_bad(page_els):
    bad = []
    for el in page_els:
        chain = el['chain']
        base = list(chain[-1]['c'])
        for layer in reversed(chain[:-1]):
            base = blend([layer['c'][0], layer['c'][1], layer['c'][2], layer['a']], base)
        fg = el['fg']
        fg_c = blend(fg[:3]+[fg[3]], base)
        cr = ratio(fg_c, base)
        thr = 3.0 if el['fs'] >= 18 else 4.5
        if cr < thr:
            bad.append({**el, 'cr': round(cr,2)})
    return bad

async def measure(page, sel):
    return await page.evaluate("""() => {
      var el = document.querySelector(SEL);
      if (!el) return {note:'missing'};
      var cs = getComputedStyle(el);
      return { color: cs.color };
    }""".replace('SEL', "'" + sel + "'"))

async def run():
    async with async_playwright() as p:
        results = {}
        for theme, scheme, expected in [
            ('dark', 'dark', {'motto': "rgb(127, 234, 255)", 'pos': "rgb(255, 107, 115)", 'pill': "rgb(255, 107, 115)"}),
            ('light', None,   {'motto': "rgb(21, 94, 117)",  'pos': "rgb(186, 28, 28)",  'pill': "rgb(186, 28, 28)"}),
        ]:
            kw = {"viewport": {"width": 390, "height": 844}}
            if scheme: kw["color_scheme"] = scheme
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(**kw)
            await ctx.add_init_script(MOCKJS)
            page = await ctx.new_page()
            await load(page)
            els = await page.evaluate(SCAN)
            bad = scan_bad(els)
            print(f"{theme}: {len(els)} text nodes, {len(bad)} sub-threshold")
            for b in bad[:6]:
                print(f"  {b['cr']:.2f} <4.5  {b['cls']:<30} {b['text']}")
            assert not bad, f"R170: {theme} has {len(bad)} sub-AA items"
            # verify colors
            motto = await measure(page, '.bv-motto-badge')
            pos = await measure(page, '.view-bv .bv-table td.bv-pos')
            pill = await measure(page, '.bv-sector-pill .bv-sector-pill-chg.bv-pos')
            assert motto.get('color') == expected['motto'], f"R170 {theme}: motto got {motto.get('color')}, want {expected['motto']}"
            assert pos.get('color') == expected['pos'], f"R170 {theme}: pos got {pos.get('color')}, want {expected['pos']}"
            assert pill.get('color') == expected['pill'], f"R170 {theme}: pill got {pill.get('color')}, want {expected['pill']}"
            results[theme] = len(bad)
            await browser.close()

        print(f"[OK] R170 dual-theme AA — dark 0 sub-AA (was 8), light 0 sub-AA (R169 保持), "
              f"motto/pos/pill 双主题原色正确 ✓")

if __name__ == "__main__":
    asyncio.run(run())
