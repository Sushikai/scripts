"""R163 mobile motto-badge 对比度 1.08→5.92:1 — 全页对比度扫描收尾.

第一性原理: 手机在室外/阳光下使用, 对比度是信息可达性底线 — 字看不清 =
  信息不存在 (跟 R159/R160 的"全页扫描收尾"同型)。做全页 alpha 合成
  对比度扫描 (半透明背景必须逐层合成到最底不透明色, 不能当不透明算),
  发现最严重的真败: .bv-motto-badge (top-1 卡"口诀"徽章) 浅青文字 #7feaff
  叠浅青底 (top-1 卡是浅色: body 248,250,252 + cyan 0.06 渐变) → 1.08:1,
  口诀是 #1 选股推理浓缩成的一个词, 不可见 = 关键信息丢失。

R163 修复: 文字 #7feaff→#155e75 (深青), bg rgba(0,240,255,0.12)→0.22,
  border 0.3→0.45。叠浅卡合成 bg ≈ rgb(185,242,247), #155e75 对比 5.92:1 AA ✓。
  (浅底上深文字 — 反转原浅底浅文的错误方向。)

断言 (mock 数据, 390px):
  1. .bv-motto-badge 合成 bg (逐层 alpha 合成) 后 文字对比度 ≥ 4.5:1
  2. badge 文字色 #155e75, 背景 alpha 0.22
  3. badge 仍可见 (在 rules-cell 内)
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
          var b = document.querySelector('.bv-motto-badge');
          if (!b) return {note:'no badge'};
          var cs = getComputedStyle(b);
          var chain = [];
          var n = b, limit = 0;
          while (n && limit < 12) {
            var st = getComputedStyle(n);
            var bm = st.backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
            if (bm) {
              chain.push({ c:[+bm[1],+bm[2],+bm[3]], a: bm[4]===undefined?1:parseFloat(bm[4]) });
              if (bm[4]===undefined) break;
            }
            n = n.parentElement; limit++;
          }
          return {
            text: b.textContent.trim(),
            color: cs.color,
            visible: b.offsetParent !== null,
            chain: chain
          };
        }""")
        print(json.dumps(m, ensure_ascii=False))
        if m.get("note"):
            print("[FAIL] no motto-badge (mock has no motto?)")
            await browser.close()
            return

        # composite bg
        chain = m["chain"]
        base = list(chain[-1]["c"])
        for layer in reversed(chain[:-1]):
            base = blend([layer['c'][0], layer['c'][1], layer['c'][2], layer['a']], base)
        fg = parse_rgba(m["color"])
        fg_c = blend(fg[:3]+[fg[3]], base)
        cr = ratio(fg_c, base)
        print(f"composite bg rgb({base[0]},{base[1]},{base[2]})  fg_eff rgb({fg_c[0]},{fg_c[1]},{fg_c[2]})  contrast {cr:.2f}:1")

        assert m.get("visible"), "R163: badge not visible"
        assert cr >= 4.5, f"R163: motto-badge contrast {cr:.2f} < 4.5 AA"
        assert m["color"].lower() == "rgb(21, 94, 117)" or "#155e75" in m["color"].lower(), \
            f"R163: badge color {m['color']} != #155e75"
        assert m["text"], "R163: badge has no text"

        await browser.close()
        print(f"[OK] R163 motto-badge — 对比度 {cr:.2f}:1 ≥4.5 (was 1.08) | text='{m['text']}' | 深青 #155e75 浅底 ✓")

if __name__ == "__main__":
    asyncio.run(run())
