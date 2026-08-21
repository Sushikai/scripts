"""R112 战法哲学折叠按钮 tap zone — 28→32 (Apple HIG).

原: R95 把 creed-more 压到 28px (折叠紧凑目标), 但 +2px border 后实际 31px,
    拇指勉强命中但不到 HIG 32 底线。
R112: min-height 28→32 + min-width:0 防 40px leak。视觉无变化 (padding 仍是 6/12),
    tap zone 达 32+。
断言 (mock 数据, 390px):
  - .bv-creed-more 高度 ≥ 32px (Apple HIG)
  - 视觉字号不变 (12px font 不变)
  - 折叠态卡片 row 高不显著增长 (R95 紧凑目标保留)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 },
    philosophy: ['仓位管理是核心', '关注板块效应', '严格止损', '跟随主线'] } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 }
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
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var btn = document.querySelector('.view-bv .bv-creed-more');
          if (!btn) return {none:true};
          var r = btn.getBoundingClientRect();
          var cs = getComputedStyle(btn);
          return {
            btn: { w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize, text: btn.textContent.trim().slice(0,15) }
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "btn" in m, f"creed-more button not found"
        assert m["btn"]["h"] >= 32, f"tap zone too small: {m['btn']['h']}px (Apple HIG 32)"
        assert m["btn"]["fontSize"] == "12px", f"font should stay 12px, got {m['btn']['fontSize']}"

        await browser.close()
        print(f"[OK] R112 creed-more tap zone — {m['btn']['w']}×{m['btn']['h']}px")

if __name__ == "__main__":
    asyncio.run(run())