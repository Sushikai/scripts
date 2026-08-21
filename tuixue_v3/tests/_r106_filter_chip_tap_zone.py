"""R106 筛选 chip 触控热区 — 30→32px (Apple HIG).

原: audit 实测 .bv-filter-chip 67×30px, 仅差 2px 不到 Apple HIG 32px 标准。
    用户连续切筛选条件 (R48/R85), 拇指密集操作, 命中率累积差距显著。
R106: padding-top/bottom 5→6 + min-height:32px + inline-flex align 居中, 视觉高度 ≈30 不变,
    tap zone 撑到 32+。
    同时加 min-width:0 覆盖全局 button 40px (短 chip 不被强制拉宽, 横向布局不变)。
断言 (mock 数据, 390px):
  - 所有 filter chip 高度 ≥ 32px
  - filter-bar 高度不应突变 (R103 保持 38px±)
  - chip 宽度依然弹性 (不被强制拉到 40px+)
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
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 }
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
          var chips = document.querySelectorAll('.view-bv .bv-filter-chip');
          var bar = document.querySelector('.view-bv .bv-filter-bar');
          var items = [];
          chips.forEach(function(c){
            var r = c.getBoundingClientRect();
            items.push({ txt: c.textContent.trim().slice(0,12), w: Math.round(r.width), h: Math.round(r.height) });
          });
          var br = bar.getBoundingClientRect();
          return { chips: items, barH: Math.round(br.height) };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m["chips"]) >= 5, f"need ≥5 chips, got {len(m['chips'])}"
        # Apple HIG 32 minimum
        for c in m["chips"]:
            assert c["h"] >= 32, f"chip '{c['txt']}' tap zone too small: {c['h']}px (need ≥32 Apple HIG)"
        # bar height should not blow up (R103 was 38, R106 may add ~2-4 for padding)
        assert m["barH"] <= 42, f"filter bar should stay compact (R103=38px, R106 ≤42px), got {m['barH']}"
        # chip width should remain tight (not forced to 40+ by global button min-width)
        for c in m["chips"]:
            assert c["w"] < 120, f"chip '{c['txt']}' too wide (global min-width leak), got {c['w']}px"

        await browser.close()
        print(f"[OK] R106 filter chip tap zone — all {len(m['chips'])} chips ≥32px, bar {m['barH']}px")

if __name__ == "__main__":
    asyncio.run(run())