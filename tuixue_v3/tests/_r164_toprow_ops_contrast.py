"""R164 mobile top-1 行 ai/wl/jump 按钮对比度 AA — 浅色 top 卡深文字.

第一性原理: R163 全页对比度扫描收尾发现 top-1 卡 (浅色) 上三颗操作按钮全是
  "为深底设计的浅色" — ai-btn #9ec8ff 叠浅底 1.63:1, jump-btn #257ee4 4.02:1,
  都是 <4.5 AA。浅底必须深文字 (同 R163 motto-badge 原则)。
  但 ai-btn 在普通 (深色) 行上用 #9ec8ff 是好的 → 不能全局改, 只能 .is-bv-top
  上下文覆盖: 深青 #155e75 + cyan bg 0.18 + border 0.5。

断言 (mock 数据, 390px, top-1 卡):
  1. .is-bv-top 行内 ai-btn/wl-btn/jump-btn 合成对比度全 ≥ 4.5:1
  2. 非 top 行 (深色卡) ai-btn 保持 #9ec8ff (不被覆盖)
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

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCKJS)
        page = await ctx.new_page()
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

        m = await page.evaluate(r"""() => {
          var top = document.querySelector('tr.bv-row.is-bv-top');
          if (!top) return {note:'no top row'};
          var out = {};
          var measure = function(el){
            var cs = getComputedStyle(el);
            var chain = []; var n = el; var lim = 0;
            while (n && lim < 12) {
              var st = getComputedStyle(n);
              var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
              if (bm) { chain.push({c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4])}); if (bm[4]===undefined) break; }
              n = n.parentElement; lim++;
            }
            return { color: cs.color, chain: chain };
          };
          ['.bv-ai-btn','.bv-wl-btn','.bv-jump-btn'].forEach(function(sel){
            var b = top.querySelector(sel);
            if (b) out['top'+sel] = measure(b);
          });
          // non-top row: second bv-row
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          for (var i=0;i<rows.length;i++){
            if (!rows[i].classList.contains('is-bv-top')) {
              var a = rows[i].querySelector('.bv-ai-btn');
              if (a) out['norm-ai'] = measure(a);
              break;
            }
          }
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))
        assert "note" not in m, f"R164: {m.get('note')}"

        ok = True
        for sel, v in m.items():
            if not v or not v.get('chain'): continue
            base = composite_bg(v['chain'])
            fg = parse_rgba(v['color'])
            fg_c = blend(fg[:3]+[fg[3]], base)
            cr = ratio(fg_c, base)
            print(f"{sel:<12} color={v['color']:<22} bg=rgb({base[0]},{base[1]},{base[2]})  contrast={cr:.2f}:1")
            if sel.startswith('top'):
                if cr < 4.5:
                    print(f"  !! {sel} {cr:.2f} < 4.5 AA")
                    ok = False
        assert ok, "R164: top-row op button below AA"

        # non-top row ai-btn must keep light blue
        norm_color = m.get('norm-ai', {}).get('color', '')
        print("non-top ai-btn color:", norm_color)
        assert '158, 200, 255' in norm_color, f"R164: non-top ai-btn should stay #9ec8ff, got {norm_color}"

        await browser.close()
        print("[OK] R164 top-row ops — ai/wl/jump all ≥4.5:1 | non-top keeps #9ec8ff ✓")

if __name__ == "__main__":
    asyncio.run(run())
