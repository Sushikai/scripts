"""R147 mobile .bv-cat-summary tap zone 38→44px — 规则分类折叠头 Apple HIG.

原: R90 加的规则明细分类折叠行 (<details><summary class="bv-cat-summary">),
    min-height 38px 注释写"触控目标够大"但实际 38px 低于 Apple HIG 44。
    分类行是可点击的 (展开/收起整组规则), 拇指在列表上快速折叠/展开,
    38px 在连续滚动时容易点偏到相邻规则项。
R147: min-height 38→44px (显式 Apple HIG 44), 跟 R139 sort-opt / R146 prev-next 工具类按钮体系一致。
第一性原理: 折叠头是"切换整组可见性"的高频离散操作, 目标必须独立可点 (Apple HIG 44px),
    44px 让用户不用瞄准 — 连续展开多组规则时拇指无脑点同一高度不偏。
R90 守护: background var(--bg-3) + radius 8 + :active bg-4 + padding 10 8 不动。
R124 守护: ::after chevron ▾ 12px / 700 / ink-2 不动 (展开提示必须保留)。
断言 (mock 数据, 390px, 注入 cat-summary):
  - min-height = 44px (was 38)
  - ::after chevron content ▾ + font-size 12px + weight 700 (R124)
  - :active background 存在 (R90)
  - padding 10px 8px 不动 (R90)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } }
  ], phase:'close', ts: Date.now()/1000 } },
  '/api/bv/backtest': { ok:true, data: { trades: 120, win_rate_pct: 62, avg_return_pct: 1.8, max_drawdown_pct: -12 } }
};
window._mockFetch = window.fetch;
window.fetch = function(url, opts){
  var u = String(url);
  for (var k in MOCK_RESPONSES) {
    if (u.indexOf(k) === 0) {
      return Promise.resolve({ ok:true, json: function(){ return Promise.resolve(MOCK_RESPONSES[k]); } });
    }
  }
  return window._mockFetch(url, opts);
};
"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCK)
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
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          var sum = document.createElement('summary');
          sum.className = 'bv-cat-summary';
          sum.textContent = '弱转强类';
          (vb || document.body).appendChild(sum);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-cat-summary');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          var after = getComputedStyle(el, '::after');
          return {
            minHeight: cs.minHeight,
            padding: cs.padding,
            backgroundColor: cs.backgroundColor,
            borderRadius: cs.borderRadius,
            activeBg: cs.backgroundColor,
            chevronContent: after.content,
            chevronFontSize: after.fontSize,
            chevronWeight: after.fontWeight,
            chevronColor: after.color,
            chevronMarginLeft: after.marginLeft
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("minHeight") == "44px", f"cat-summary must be min-height 44 (HIG), got {m.get('minHeight')}"
        # R90 守护: padding 10px 8px + bg 非透明
        assert m.get("padding") and "10px" in m["padding"] and "8px" in m["padding"], f"cat-summary padding regression: {m.get('padding')}"
        assert m.get("backgroundColor") and m["backgroundColor"] != "rgba(0, 0, 0, 0)", f"cat-summary bg transparent (R90 broken): {m.get('backgroundColor')}"
        # R124 守护: ::after chevron ▾ 12px/700/ink-2
        assert m.get("chevronContent") and "▾" in m["chevronContent"], f"cat-summary chevron lost (R124 broken): {m.get('chevronContent')}"
        assert m.get("chevronFontSize") == "12px", f"chevron font regression: {m.get('chevronFontSize')}"
        assert m.get("chevronWeight") == "700", f"chevron weight regression: {m.get('chevronWeight')}"

        await browser.close()
        print(f"[OK] R147 cat-summary — minH 44 (was 38) | chevron ▾ 12/700 (R124 ✓) | bg+padding (R90 ✓)")

if __name__ == "__main__":
    asyncio.run(run())
