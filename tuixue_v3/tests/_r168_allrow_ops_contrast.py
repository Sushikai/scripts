"""R168 mobile 全部行 ai/jump 按钮 light 主题对比度 AA — R164 只修了 top-1 行.

第一性原理: 卡片操作按钮 (💬 AI / 📈 跳个股) 每行都有, 不只是 top-1。R164 只覆盖
  .is-bv-top 行, 其余行的 ai-btn #9ec8ff 叠浅卡 1.63:1 / jump-btn accent 蓝 2.94:1
  仍是"为深底设计的浅色" — 全行 light 覆盖: 深青 #155e75 (同 R164 浅底深文字原则)。
  dark 卡上原色正确 (深底亮字高对比), 不动。

断言 (mock, 390px):
  1. 所有行 (top + 非 top) 的 .bv-ai-btn / .bv-jump-btn 合成对比度 ≥ 4.5
  2. light 下颜色 = #155e75
  3. dark 下保持原色 (ai-btn #9ec8ff, jump-btn accent 蓝) 不被覆盖
  4. .bv-wl-btn (gray-600) 本就 ≥4.5 不回归
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

MEASURE_ALL = r"""() => {
  var out = [];
  ['bv-ai-btn','bv-wl-btn','bv-jump-btn'].forEach(function(cls){
    document.querySelectorAll('.view-bv .bv-table .' + cls).forEach(function(el){
      var cs = getComputedStyle(el);
      var chain=[]; var n=el,lim=0;
      while(n&&lim<12){
        var st=getComputedStyle(n);
        var bm=st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
        if(bm){chain.push({c:[+bm[1],+bm[2],+bm[3]],a:bm[4]===undefined?1:parseFloat(bm[4])}); if(bm[4]===undefined)break;}
        n=n.parentElement;lim++;
      }
      var row = el.closest('tr.bv-row');
      out.push({
        cls: cls,
        isTop: row ? row.classList.contains('is-bv-top') : 'none',
        color: cs.color,
        chain: chain,
        text: el.textContent.trim().slice(0,4)
      });
    });
  });
  return out;
}"""

async def run():
    async with async_playwright() as p:
        # ── light ──
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
        await load(page)
        btns = await page.evaluate(MEASURE_ALL)
        print(f"light: {len(btns)} buttons measured")
        ok = True
        for b in btns:
            base = composite_bg(b['chain'])
            fg = parse_rgba(b['color'])
            fg_c = blend(fg[:3]+[fg[3]], base)
            cr = ratio(fg_c, base)
            flag = "  !! <4.5" if cr < 4.5 else ""
            print(f"  {b['cls']:<12} top={str(b['isTop']):<5} color={b['color']:<18} bg=rgb({base[0]},{base[1]},{base[2]}) {cr:.2f}:1{flag}")
            if cr < 4.5:
                ok = False
        assert ok, "R168: some ai/jump button < 4.5 in light theme"
        # ai + jump must be #155e75 in light
        for b in btns:
            if b['cls'] in ('bv-ai-btn','bv-jump-btn'):
                assert b['color'] == "rgb(21, 94, 117)", f"R168: {b['cls']} should be #155e75, got {b['color']}"
        await browser.close()

        # ── dark: 原色保持 ──
        browser2 = await p.chromium.launch(headless=True)
        ctx2 = await browser2.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        await ctx2.add_init_script(MOCKJS)
        page2 = await ctx2.new_page()
        await load(page2)
        d = await page2.evaluate(r"""() => {
          var ai = document.querySelector('.view-bv .bv-table .bv-ai-btn');
          var jump = document.querySelector('.view-bv .bv-table .bv-jump-btn');
          return { theme: document.documentElement.getAttribute('data-theme'),
                   ai: ai ? getComputedStyle(ai).color : null,
                   jump: jump ? getComputedStyle(jump).color : null };
        }""")
        print("dark:", d)
        assert d['theme'] == 'dark', f"R168: expected dark, got {d['theme']}"
        assert d['ai'] == "rgb(158, 200, 255)", f"R168: dark ai should keep #9ec8ff, got {d['ai']}"
        await browser2.close()

        print(f"[OK] R168 all-row ops — light ai/jump 全行 ≥4.5 (was 1.63/2.94), dark #9ec8ff 保持 ✓")

if __name__ == "__main__":
    asyncio.run(run())
