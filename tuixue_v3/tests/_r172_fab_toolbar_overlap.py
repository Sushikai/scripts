"""R172 多选模式隐藏 top-FAB — 底部工具栏与 FAB 重叠消除.

第一性原理: 两个 fixed 底部控件在 multi-mode 同时可见时必然碰撞 —
  .bv-top-fab (bottom:18, z-index 9000) 与 .bv-multi-toolbar (全宽, z-index 9500)
  实测 390px 重叠 47px (fab y782-826 vs toolbar y779-844), FAB 被盖且盖住全选按钮。
  multi-mode 是聚焦态, 工具栏已提供"取消"退出, FAB 的"回顶部"被取代 →
  隐藏 FAB (body.bv-multi-active .bv-top-fab { display:none }) 消除冲突。

断言 (mock, 390px):
  1. multi-active + FAB.is-visible → FAB 0×0 不渲染 (display:none), overlap=False
  2. 移除 bv-multi-active → FAB 恢复 (display:flex)
"""
import asyncio, re
from playwright.async_api import async_playwright
_TEMPLATE = open('/Users/kaikai/scripts/tuixue_v3/tests/_r159_sector_pill_cnt_legibility.py').read()
MOCKJS = re.search(r'MOCK = r"""\n(.*?)"""\n', _TEMPLATE, re.S).group(1)
async def load(page):
    for a in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000); break
        except Exception: await page.wait_for_timeout(2000)
    for i in range(15):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length")>=1: break
    await page.wait_for_timeout(400)
CHECK = """() => {
  function showFab(){
    var f=document.querySelector('.bv-top-fab');
    if(!f){ f=document.createElement('button'); f.className='bv-top-fab';  document.body.appendChild(f); }
    f.classList.add('is-visible'); return f;
  }
  function showToolbar(){
    var tb=document.getElementById('bv-multi-toolbar');
    if(!tb){ tb=document.createElement('div'); tb.id='bv-multi-toolbar'; tb.className='bv-multi-toolbar'; tb.innerHTML='<span class="bv-multi-count">已选 <b>3</b> 只</span><button class="bv-multi-btn" id="bv-multi-all">全选</button><button class="bv-multi-btn" id="bv-multi-add">＋加自选</button><button class="bv-multi-btn bv-multi-cancel" id="bv-multi-cancel">取消</button>'; document.body.appendChild(tb); }
    document.body.classList.add('bv-multi-active'); return tb;
  }
  var f=showFab(), tb=showToolbar();
  var fr=f.getBoundingClientRect(), tr=tb.getBoundingClientRect();
  var overlap = !(fr.right<tr.left || fr.left>tr.right || fr.bottom<tr.top || fr.top>tr.bottom);
  var fabDisplay = getComputedStyle(f).display;
  // exit multi
  document.body.classList.remove('bv-multi-active');
  var fabAfter = getComputedStyle(f).display;
  return {overlap: overlap, fabDisplay: fabDisplay, fabAfter: fabAfter,
          fabRect: {w:fr.width, h:fr.height}, toolbarRect: {y:tr.y, h:tr.height}};
}"""
async def run():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        ctx=await b.new_context(viewport={"width":390,"height":844})
        await ctx.add_init_script(MOCKJS)
        page=await ctx.new_page(); await load(page)
        r=await page.evaluate(CHECK)
        print("R172 check:", r)
        assert r['overlap'] == False, f"R172: FAB overlaps toolbar ({r})"
        assert r['fabDisplay'] == 'none', f"R172: FAB should be display:none in multi, got {r['fabDisplay']}"
        assert r['fabAfter'] == 'flex', f"R172: FAB should restore after exiting multi, got {r['fabAfter']}"
        print("[OK] R172 FAB hidden in multi-mode, restored after exit ✓")
        await b.close()
if __name__ == "__main__":
    asyncio.run(run())
