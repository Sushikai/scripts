"""R120 bv-title .sub 紧贴标题的副标签 — 10.5→11.5px (跟 R108/R119 typography 模式).

原: .bv-title .sub font 10.5px, 紧贴 15px title 旁边 (如 "v1" / "盘后守候")。
    副标签是版本/阶段的元信息, 跟 R108 meta 一样是身份信息, 但 10.5px 跟 15px 强对比
    → 视觉水印感, 用户要凑近才看清。
R120: 10.5→11.5px (跟 R108 meta 11.5px 完全一致), 跟 title 比例仍清晰 (15 vs 11.5),
    不抢主标题戏。
断言 (mock 数据, 390px):
  - .bv-title .sub 字号 = 11.5px
  - 字号 = .bv-meta 同 (11.5px, R108 一致)
  - title 主字号保留 15px
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大脑环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var title = document.querySelector('.view-bv .bv-title');
          var sub = document.querySelector('.view-bv .bv-title .sub');
          var meta = document.querySelector('.view-bv .bv-meta');
          if (!title || !sub) return {none:true};
          return {
            titleFont: getComputedStyle(title).fontSize,
            subFont: getComputedStyle(sub).fontSize,
            metaFont: meta ? getComputedStyle(meta).fontSize : null,
            subText: sub.textContent.trim().slice(0,20)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("subFont") == "11.5px", f"sub should be 11.5px (was 10.5), got {m.get('subFont')}"
        assert m.get("titleFont") == "15px", f"title regression: {m.get('titleFont')} (must stay 15)"
        # sub should match meta (both are version/phase metadata)
        assert m.get("metaFont") == "11.5px", f"meta should be 11.5px (R108), got {m.get('metaFont')}"

        await browser.close()
        print(f"[OK] R120 title sub typography — {m['subFont']} (was 10.5) | title {m['titleFont']} | meta {m['metaFont']}")

if __name__ == "__main__":
    asyncio.run(run())