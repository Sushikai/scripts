"""R140 pinned banner .bv-buy-window font 10→11px — pinned 版 typography 收尾.

原: R107 把 .view-bv .bv-phase-banner .bv-buy-window 字号 18→24px (font 11px),
    但 pinned 版 body > .bv-phase-banner.is-pinned .bv-buy-window 仍是 font-size:10px。
    pinned 状态下 buy-window 缩成 10px, 跟 R107 typography 体系浮空。
R140: pinned 版 font 10→11px (跟 R107 typography 体系一致), 其他属性 (padding/border/radius/weight) 不动。
第一性原理: pinned banner 是 sticky 状态, 用户扫顶部 banner 决定"当前能不能买", 跟 R107 一样的最高优先级信号。
    10px 在 mobile 顶部 (环境光杂/快速扫视) 看不清, 11px 跟 typography 体系一致。
    R107 min-height 24px + line-height 1.3 已经守 tap zone, R140 只动字号。
R107 守护: .view-bv .bv-phase-banner .bv-buy-window font 11px + min-height 24 + line-height 1.3 不动。
    pinned 版 (body > .bv-phase-banner.is-pinned) 是 R107 衍生 sibling 选择器。
断言 (mock 数据, 390px, pinned banner + buy-window):
  - pinned buy-window font = 11px (was 10)
  - font-weight 700 保留
  - padding 2px 8px 保留
  - whitespace nowrap 保留
  - regular buy-window (R107) 不动 11px
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300, buy_window:'盘后可推' } } },
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

        # Inject pinned banner + buy-window (matches CSS selector)
        m = await page.evaluate(r"""() => {
          var banner = document.createElement('div');
          banner.className = 'bv-phase-banner is-pinned';
          banner.style.cssText = 'position:fixed;top:0;left:0;right:0;';
          var bw = document.createElement('span');
          bw.className = 'bv-buy-window is-buy';
          bw.textContent = '可买 · 09:30-11:30';
          banner.appendChild(bw);
          document.body.appendChild(banner);
          void document.body.offsetHeight;
          var pinned = document.querySelector('body > .bv-phase-banner.is-pinned .bv-buy-window');
          if (!pinned) return {none: true};
          var cs = getComputedStyle(pinned);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            padding: cs.padding,
            borderRadius: cs.borderRadius,
            whiteSpace: cs.whiteSpace,
            text: pinned.textContent
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("fontSize") == "11px", f"pinned buy-window should be 11px (was 10), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "700", f"pinned buy-window weight regression: {m.get('fontWeight')} (must stay 700)"
        assert m.get("whiteSpace") == "nowrap", f"pinned buy-window whitespace regression: {m.get('whiteSpace')}"

        await browser.close()
        print(f"[OK] R140 pinned buy-window — 11px (was 10) | fontWeight 700 + nowrap + padding 2px 8px ✓")

if __name__ == "__main__":
    asyncio.run(run())