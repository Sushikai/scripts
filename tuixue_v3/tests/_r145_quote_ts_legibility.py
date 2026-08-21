"""R145 mobile .bv-quote-ts font 10→11px — tap-zone chip typography 收尾.

原: R114 已修 bv-quote-ts tap zone 14→32px (padding 10 + min-height 32), 但字号仍 10px。
    quote-ts 是"原话时间戳"chip (R65 证据可追溯), 用户点它跳视频, 跟 R125 σ (10.5) /
    R131 hit-badge (10.5) 同档 tap-zone chip typography。
R145: quote-ts 10→11px (跟 typography 体系一致)。
第一性原理: quote-ts 是可点击的音频锚点 (点跳视频片段), R114 已给 tap zone,
    字号 10px 让"MM:SS"时间看不清 — 用户要读到具体时间戳才决定点不点。
    11px 跟体系一致, weight 700 + 橙色底保持视觉。
R114 守护: min-height 32 / padding 10 7 / radius 5 / nowrap / inline-flex 不动。
R65 守护: 橙色 color #fb923c + border rgba(251,146,60,.4) 不动。
断言 (mock 数据, 390px, 注入 quote-ts chip):
  - quote-ts font = 11px (was 10)
  - font-weight 700 保留
  - min-height 32 (R114 守护)
  - color #fb923c 保留 (R65 守护)
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
          var q = document.createElement('span');
          q.className = 'bv-quote-ts';
          q.textContent = '00:35';
          (vb || document.body).appendChild(q);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-quote-ts');
          if (!el) return {none: true};
          var cs = getComputedStyle(el);
          var rect = el.getBoundingClientRect();
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            minHeight: cs.minHeight,
            color: cs.color,
            display: cs.display,
            height: Math.round(rect.height)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("fontSize") == "11px", f"quote-ts should be 11px (was 10), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "700", f"quote-ts weight regression: {m.get('fontWeight')} (must stay 700)"
        # R114 守护 tap zone (computed min-height, 注入元素在裸容器中可能 display 塌陷但 computedStyle 仍真)
        assert m.get("minHeight") == "32px", f"quote-ts min-height regression: {m.get('minHeight')} (R114 must stay 32)"
        # R65 守护橙色
        assert m.get("color") == "rgb(251, 146, 60)", f"quote-ts color regression: {m.get('color')} (R65 must stay #fb923c)"

        await browser.close()
        print(f"[OK] R145 quote-ts — 11px (was 10) | minH 32 (R114 ✓) | #fb923c (R65 ✓)")

if __name__ == "__main__":
    asyncio.run(run())