"""R171 全交互态对比度扫描 — 5 态 × 双主题 AA 里程碑.

第一性原理: R163-R170 的对比度扫描只覆盖默认折叠卡列表 (collapsed), 交互态
  (expanded 详情行 / sort sheet / multi 工具栏 / pinned banner) 是独立渲染
  上下文, 元素完全不同 — 折叠态干净不代表交互态干净。逐态 × 双主题扫,
  alpha 合成到最底不透明, 揪出 4 处 light 漏网 (dark 全程 0 sub-AA 不动):
  1) .bv-quote-ts 详情时间戳 dark 橙 #fb923c 叠 cream #F2E6DD = 1.85:1 → orange-800 #9A3412 5.96
  2) .bv-detail-sector-link.bv-detail-op 浅青 #7dd3fc 叠浅青 pill = 1.45:1 → 深青 #155E75 6.32
     (R168 只修 ai/jump, 漏 sector-op — 同款浅底深文字)
  3) .bv-multi-cancel 浅粉 hsl(0,75%,75%) 叠浅粉 toolbar = 1.83:1 → red-800 #B91C1C 5.06
  4) .bv-multi-count b --accent #257ee4 叠 toolbar light = 3.87:1 → blue-700 #1D4ED8 6.41
  断言: 5 态 × 双主题 全 0 sub-AA (fs>=18 → 3.0, else 4.5).
"""
import asyncio, re
from playwright.async_api import async_playwright
_TEMPLATE = open('/Users/kaikai/scripts/tuixue_v3/tests/_r159_sector_pill_cnt_legibility.py').read()
MOCKJS = re.search(r'MOCK = r"""\n(.*?)"""\n', _TEMPLATE, re.S).group(1)
def blend(fg,bg):
    a=fg[3]; return [round(fg[i]*a+bg[i]*(1-a)) for i in range(3)]
def lum(c):
    c=c/255.0
    return c/12.92 if c<=0.03928 else ((c+0.055)/1.055)**2.4
def ratio(a,b):
    la=0.2126*lum(a[0])+0.7152*lum(a[1])+0.0722*lum(a[2])
    lb=0.2126*lum(b[0])+0.7152*lum(b[1])+0.0722*lum(b[2])
    return (max(la,lb)+0.05)/(min(la,lb)+0.05)
SCAN = r"""() => {
  var out = [];
  var scope = '.view-bv, .view-bv *, #bv-sort-sheet, #bv-sort-sheet *, ' +
              '#bv-multi-toolbar, #bv-multi-toolbar *, ' +
              '.bv-phase-banner, .bv-phase-banner *, .bv-top-fab, .bv-top-fab *';
  document.querySelectorAll(scope).forEach(function(el){
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
    out.push({cls:String(el.className||'').slice(0,32), text:txt.slice(0,12),
      fg:[+cm[1],+cm[2],+cm[3],cm[4]===undefined?1:parseFloat(cm[4])], chain:chain,
      fs:parseFloat(cs.fontSize)});
  });
  return out;
}"""
def scan_bad(els):
    bad=[]
    for el in els:
        chain=el['chain']; base=list(chain[-1]['c'])
        for layer in reversed(chain[:-1]):
            base=blend([layer['c'][0],layer['c'][1],layer['c'][2],layer['a']],base)
        fg=el['fg']; fg_c=blend(fg[:3]+[fg[3]],base)
        cr=ratio(fg_c,base); thr=3.0 if el['fs']>=18 else 4.5
        if cr<thr: bad.append({**el,'cr':round(cr,2),'bg':'rgb(%d,%d,%d)'%tuple(base)})
    return bad
STATES = [
    ("collapsed",    "() => 0"),
    ("expanded",     "() => { var rows=document.querySelectorAll('#bv-pick-tbody tr.bv-row'); if(rows[1]) rows[1].click(); return 1; }"),
    ("sort-sheet",   "() => { var b=document.querySelector('.bv-sort-btn'); if(b) b.click(); return document.getElementById('bv-sort-sheet') ? !document.getElementById('bv-sort-sheet').hidden : 0; }"),
    ("multi",        "() => { var tb=document.getElementById('bv-multi-toolbar'); if(!tb){ tb=document.createElement('div'); tb.id='bv-multi-toolbar'; tb.className='bv-multi-toolbar'; tb.innerHTML='<span class=\"bv-multi-count\">已选 <b>3</b> 只</span><button class=\"bv-multi-btn\" id=\"bv-multi-all\">全选</button><button class=\"bv-multi-btn\" id=\"bv-multi-add\">＋加自选</button><button class=\"bv-multi-btn bv-multi-cancel\" id=\"bv-multi-cancel\">取消</button>'; document.body.appendChild(tb); document.body.classList.add('bv-multi-active'); } return !!tb; }"),
    ("pinned",       "() => { var pb=document.body.querySelector('.bv-phase-banner.is-pinned'); if(!pb){ var p=document.querySelector('.view-bv .bv-phase-banner'); if(p){ var c=p.cloneNode(true); c.className='bv-phase-banner is-pinned'; document.body.appendChild(c); } } return !!document.body.querySelector('.bv-phase-banner.is-pinned'); }"),
]
async def load(page):
    for a in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000); break
        except Exception: await page.wait_for_timeout(2000)
    for i in range(15):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length")>=1: break
    await page.wait_for_timeout(400)
async def run():
    async with async_playwright() as p:
        for theme, scheme in [("dark","dark"), ("light",None)]:
            bads=[]
            print(f"\n===== {theme} =====")
            for sname, sj in STATES:
                kw={"viewport":{"width":390,"height":844}}
                if scheme: kw["color_scheme"]=scheme
                b=await p.chromium.launch(headless=True)
                ctx=await b.new_context(**kw); await ctx.add_init_script(MOCKJS)
                page=await ctx.new_page(); await load(page)
                engaged=0
                try: engaged=await page.evaluate(sj); await page.wait_for_timeout(500)
                except Exception as e: print(f"  {sname}: state-enter failed {e}")
                els=await page.evaluate(SCAN)
                bad=scan_bad(els)
                bads.extend(bad)
                print(f"  {sname:<11} engaged={engaged} {len(els):>3} nodes, {len(bad)} sub-AA")
                for x in bad[:6]:
                    print(f"      {x['cr']:.2f}  {x['cls']:<26} '{x['text']}' bg={x['bg']} fs={x['fs']}")
                await b.close()
            print(f"  => {theme}: {len(bads)} total sub-AA across states")
            assert not bads, f"R171: {theme} has {len(bads)} sub-AA items"
        print("[OK] R171 all-states AA — 5 态 × 双主题 全 0 sub-AA ✓")
if __name__ == "__main__":
    asyncio.run(run())
