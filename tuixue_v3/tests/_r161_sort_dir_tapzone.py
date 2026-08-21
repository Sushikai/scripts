"""R161 mobile sort-sheet dir-opt 32→44px — 聚焦工具 modal 决策点用 tool-class 地板.

第一性原理: 触控目标分级 — 44px (tool-class: 工具/主操作) vs 32px (卡内次级快捷)。
  排序 sheet (R17) 是聚焦工具弹层: 用户进 sheet 后无法点别的, 每个决策点
  (选字段 / 选方向 / 确定) 都必须 ≥44px。R139 已把 sort-opt 提到 44,
  R158 已把 sheet re-parent 到 body。但 R117 给 dir-opt 用了 32px — 这是
  "紧凑 in-card 次级" 的地板, 归类错误: 工具 modal 里没有"次级", 全是主操作。
  实测 (390px) dir-opt 实际 40px (padding 10 + 字号 13 + min-height 32) — 也 <44。
  同理 apply 64px 已达标。

R161 修复: dir-opt min-height 32→44 (与 sort-opt 同 floor)。

断言 (mock 数据, 390px, 打开 sheet):
  1. sheet 仍在 BODY (R158 守护)
  2. bv-sort-opt / bv-sort-dir-opt / bv-sort-apply 全部 ≥ 44px
  3. 点升序 + 涨幅 + 确定 → sheet 关闭, 排序生效
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

        # open the sheet
        await page.evaluate("() => { var b = document.querySelector('.bv-sort-btn'); if (b) b.click(); }")
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var sheet = document.querySelector('#bv-sort-sheet');
          var out = { parent: sheet.parentElement.tagName };
          sheet.querySelectorAll('.bv-sort-opt, .bv-sort-dir-opt, .bv-sort-apply').forEach(function(el){
            var cls = String(el.className).split(' ')[0];
            var r = el.getBoundingClientRect();
            out[cls] = Math.round(r.height);
          });
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["parent"] == "BODY", f"R161: sheet must be BODY child, got {m['parent']}"
        for k in ("bv-sort-opt", "bv-sort-dir-opt", "bv-sort-apply"):
            assert m.get(k, 0) >= 44, f"R161: {k} height {m.get(k)} < 44 tool floor"

        # dir-opt actually changed: 升序 → desc toggle
        await page.evaluate("""() => {
          var opt = document.querySelector('.bv-sort-opt[data-sort-key="change_pct"]');
          if (opt) opt.click();
          var dir = document.querySelector('.bv-sort-dir-opt[data-dir="desc"]');
          if (dir) dir.click();
        }""")
        await page.wait_for_timeout(200)
        await page.evaluate("() => { var a = document.querySelector('.bv-sort-apply'); if (a) a.click(); }")
        await page.wait_for_timeout(400)
        closed = await page.evaluate("() => document.querySelector('#bv-sort-sheet').hidden")
        firstCode = await page.evaluate("""() => {
          var r = document.querySelector('#bv-pick-tbody tr.bv-row .code-link');
          return r ? r.textContent.trim() : '';
        }""")
        print(f"closed={closed} firstCode={firstCode}")
        assert closed, "R161: sheet did not close on apply"
        assert firstCode == "600519", f"R161: sort not applied, first code {firstCode}"

        await browser.close()
        print(f"[OK] R161 sort-dir — {m['bv-sort-dir-opt']}px (was 40) | opt {m['bv-sort-opt']}/dir {m['bv-sort-dir-opt']}/apply {m['bv-sort-apply']} 全 ≥44 | 排序生效 ✓")

if __name__ == "__main__":
    asyncio.run(run())
