"""R162 mobile multi-toolbar btn 32→44px — 聚焦模式工具栏用 tool-class 地板.

第一性原理: 触控目标分级 — 44px (tool-class) vs 32px (卡内次级)。
  多选 mode (R8) 是聚焦模式: 长按进入后整页锁进多选, body.bv-multi-active,
  页面主操作切换为 toolbar 的 全选/加自选/取消。这跟 R161 排序 sheet 同归类 —
  用户无法点页面其它东西, 工具栏按钮全是"当前模式的主操作", 必须 44px。
  R116 用 32px in-card 地板是错误归类 — 模式工具栏没有"次级"。
  实测 (390px, body.bv-multi-active): btn 实际渲染 37px (padding 8 + 12px font),
  即便 min-height:32px 也因全局 button 规则被顶到 37 → 32 地板形同虚设。

R162 修复: min-height 32→44px。

断言 (mock 数据, 390px):
  1. body.bv-multi-active 时注入 toolbar → 全选/加自选/取消 全部 ≥ 44px
  2. 无 multi-active 时 toolbar 仍 display:none (不占用正常布局)
"""
import asyncio, json, re
from playwright.async_api import async_playwright

_TEMPLATE = open('/Users/kaikai/scripts/tuixue_v3/tests/_r159_sector_pill_cnt_legibility.py').read()
MOCKJS = re.search(r'MOCK = r"""\n(.*?)"""\n', _TEMPLATE, re.S).group(1)

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

        # --- 1) activate multi mode, inject toolbar, measure ---
        m = await page.evaluate(r"""() => {
          document.body.classList.add('bv-multi-active');
          var tb = document.createElement('div');
          tb.className = 'bv-multi-toolbar';
          tb.innerHTML =
            '<span class="bv-multi-count">已选 <b>1</b> 只</span>' +
            '<button class="bv-multi-btn" id="bv-multi-all">全选</button>' +
            '<button class="bv-multi-btn" id="bv-multi-add">＋加自选</button>' +
            '<button class="bv-multi-btn bv-multi-cancel" id="bv-multi-cancel">取消</button>';
          document.body.appendChild(tb);
          var out = { display: getComputedStyle(tb).display };
          tb.querySelectorAll('.bv-multi-btn').forEach(function(el){
            out[el.id] = Math.round(el.getBoundingClientRect().height);
          });
          tb.remove();
          document.body.classList.remove('bv-multi-active');
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["display"] != "none", "R162: toolbar should be visible in multi-active"
        for k in ("bv-multi-all", "bv-multi-add", "bv-multi-cancel"):
            assert m.get(k, 0) >= 44, f"R162: {k} height {m.get(k)} < 44 tool floor"

        # --- 2) without multi-active, toolbar hidden (not consuming layout) ---
        hidden = await page.evaluate(r"""() => {
          var tb = document.createElement('div');
          tb.className = 'bv-multi-toolbar';
          tb.innerHTML = '<button class="bv-multi-btn" id="bv-multi-add">＋加自选</button>';
          document.body.appendChild(tb);
          var d = getComputedStyle(tb).display;
          tb.remove();
          return d;
        }""")
        print("toolbar display (no active):", hidden)
        assert hidden == "none", f"R162: toolbar should be hidden without multi-active, got {hidden}"

        await browser.close()
        print(f"[OK] R162 multi-btn — all {m.get('bv-multi-add')}px ≥44 | 隐藏态 display:none 不占位 ✓")

if __name__ == "__main__":
    asyncio.run(run())
