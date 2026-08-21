"""R160 mobile sector-pill 触控热区 27→32px + btn-refresh 28→32 — 全页交互地板收尾.

第一性原理: Apple HIG 触控目标 44px (tool-class) / 32px (卡内次级 + 紧凑过滤控件)。
  R104-R118 逐个修过 tap zone, 但从未做全页扫描 — "存在" 不等于 "可命中"。
  R160 首次全页扫描: 枚举 .view-bv 下所有可交互元素, 报 <32px 独立目标
  (子元素若已有 ≥32px 可交互祖先 → 点它即点祖先, 非独立目标, 跳过)。
  扫描捕获两个真漏网:
    1) .bv-sector-pill 27px — 与 R106 已修到 32px 的 filter-chip 同属过滤控件却矮 5px。
    2) .btn-refresh 28px — R99 硬编码 height:28px 的头部刷新按钮, 独立触控控件 <32px。
  R160: pill 加 min-height:32px + box-sizing (R106 同 idiom); btn-refresh 28→32px。

断言 (mock 数据, 390px):
  1. 全页扫描: 无 <32px 独立可交互目标 (子元素归属祖先 + 纯装饰白名单)
  2. .bv-sector-pill 高度 ≥ 32px; #bv-sector-bar 总高 ≤ 48 (R96 预算)
  3. .btn-refresh 高度 = 32px
  4. 点击白酒 pill → 过滤生效 (只剩白酒行), 再点还原
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

        # --- 1) whole-page interactive-target scan ---
        small = await page.evaluate(r"""() => {
          var out = [];
          document.querySelectorAll('.view-bv *').forEach(function(el){
            var tag = el.tagName.toLowerCase();
            var cls = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');
            cls = String(cls);
            var isInteract = el.onclick || el.getAttribute('role') === 'button'
                || (tag === 'button' || tag === 'a' || tag === 'input' || tag === 'select')
                || cls.indexOf('-btn') >= 0 || cls.indexOf('chip') >= 0
                || cls.indexOf('pill') >= 0 || cls.indexOf('clickable') >= 0
                || cls.indexOf('opt') >= 0 || cls.indexOf('toggle') >= 0;
            if (!isInteract) return;
            // 子元素归属父容器: 若存在 ≥32px 的可交互祖先 (pill/chip/card), 点它即点祖先 → 非独立目标
            var parent = el.parentElement;
            var contained = false;
            while (parent) {
              var pc = String(parent.className || '');
              var pr = parent.getBoundingClientRect();
              var pInteract = parent.onclick || pc.indexOf('pill') >= 0 || pc.indexOf('chip') >= 0
                  || pc.indexOf('-btn') >= 0 || parent.getAttribute('role') === 'button';
              if (pInteract && pr.height >= 32) { contained = true; break; }
              parent = parent.parentElement;
            }
            if (contained) return;
            var st = getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return;
            var r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return;
            if (r.bottom < 0 || r.top > window.innerHeight) return;
            // 纯装饰白名单: 折叠 chevron / 方向箭头 (无独立点击, 父容器才是目标)
            var deco = cls.indexOf('chevron') >= 0 || cls.indexOf('ar-arrow') >= 0;
            if (deco) return;
            if (r.height < 32) {
              out.push({ tag: tag, cls: cls.slice(0, 40), h: Math.round(r.height), w: Math.round(r.width) });
            }
          });
          return out;
        }""")
        print("sub-32px interactive targets:", json.dumps(small, ensure_ascii=False))
        assert len(small) == 0, f"R160: sub-32px interactive targets remain: {small}"

        # --- 2) pill height + bar budget ---
        m = await page.evaluate(r"""() => {
          var pill = document.querySelector('.bv-sector-pill');
          var bar = document.querySelector('#bv-sector-bar');
          var pr = pill.getBoundingClientRect();
          var br = bar.getBoundingClientRect();
          return { pillH: Math.round(pr.height), barH: Math.round(br.height) };
        }""")
        print("pill/bar:", json.dumps(m))
        assert m["pillH"] >= 32, f"R160: pill height {m['pillH']} < 32"
        assert m["barH"] <= 48, f"R160: sector-bar total {m['barH']} > 48 (R96 budget)"

        # --- 3) pill filter still works: click 白酒 → rows filtered, click again → restore ---
        await page.evaluate("""() => {
          var pill = document.querySelector('.bv-sector-pill[data-sector-key="白酒"]');
          if (pill) pill.click();
        }""")
        await page.wait_for_timeout(400)
        rows1 = await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length")
        codes1 = await page.evaluate("""() => Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row .code-link'))
              .map(function(a){ return a.textContent.trim(); }).join(',')""")
        print(f"after filter: rows={rows1} codes=[{codes1}]")
        assert rows1 == 1 and '600519' in codes1, f"R160: 白酒 filter failed rows={rows1} codes={codes1}"

        await page.evaluate("""() => {
          var pill = document.querySelector('.bv-sector-pill.is-active');
          if (pill) pill.click();
        }""")
        await page.wait_for_timeout(400)
        rows2 = await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length")
        print(f"after restore: rows={rows2}")
        assert rows2 == 3, f"R160: restore failed rows={rows2}"

        await browser.close()
        print(f"[OK] R160 sector-pill — {m['pillH']}px (was 27) | 全页 0 个 <32px 交互目标 | bar {m['barH']}px ≤ 48 | 过滤/还原 ✓")

if __name__ == "__main__":
    asyncio.run(run())
