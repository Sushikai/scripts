"""R107 买点窗口徽章 — 18→24px (第一性原理: 决策信号不弱化).

原: audit 实测 .bv-buy-window 58×18px, font-size 10px padding 1×6,
    banner 里 6 个元素挤在一起, "可买/观望"信号几乎看不到。
    R14 设计时为压扁 banner 把所有元素都缩到 10px, 但决策信号反而应该放大。
R107: padding 1→3 + min-height:24 + font-size 10→11 + 行高 1.3 + inline-flex 居中,
    视觉从 18→24px 高, 体积 +33%, 在 banner 内视觉占比从 30% 提到 40%,
    用户第一眼就能识别"可买"vs"观望"。
断言 (mock 数据, 390px):
  - bv-buy-window 高度 ≥ 22px (R107 目标 24, 允许 ±2 误差)
  - bv-phase-banner 总高度不应显著增长 (R107 局部放大, 不应撑高整个 banner)
  - bv-buy-window 文字可读 (font-size ≥ 11px)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
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
          var buy = document.querySelector('.view-bv .bv-phase-banner .bv-buy-window');
          var banner = document.querySelector('.view-bv .bv-phase-banner');
          if (!buy) return {none:true};
          var r = buy.getBoundingClientRect();
          var cs = getComputedStyle(buy);
          var br = banner.getBoundingClientRect();
          return {
            buy: { w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize, padding: cs.padding, text: buy.textContent.trim() },
            bannerH: Math.round(br.height)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "buy" in m, f"buy-window not found"
        assert m["buy"]["h"] >= 22, f"buy-window too small: {m['buy']['h']}px (R107 目标 24)"
        assert m["buy"]["fontSize"] == "11px", f"font should be 11px, got {m['buy']['fontSize']}"
        # banner shouldn't bloat
        assert m["bannerH"] <= 60, f"banner shouldn't grow much, got {m['bannerH']}px"

        await browser.close()
        print(f"[OK] R107 buy-window visibility — {m['buy']['w']}×{m['buy']['h']}px, banner {m['bannerH']}px")

if __name__ == "__main__":
    asyncio.run(run())