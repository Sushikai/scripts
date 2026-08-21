"""R100 首屏不自动滚 — view-head/banner/creed 不被自动顶出屏外.

原: renderPicks 末尾的 R25 滚动恢复无条件执行, 首次加载时 _scrollY=0 但 _focusRow 在
    ~428px, 算法把它 "放回 20% 视口位置" → scrollTo(269), 用户上来看到的就是已经
    滚下去 270px 的页面, view-head(84)+banner(28)+creed(136)=248 全在屏外,
    R91-R99 整个首屏压缩成果看不见。
R100: 仅在 _scrollY>0(用户已滚动)时执行滚动恢复, 首屏/初次加载保持顶部。
断言 (mock 数据, 390px):
  - 加载后 scrollY === 0 (视图没被自动滚下去)
  - view-head 顶部 ≥ 0 (在视口内可见)
  - phase-banner 在视口内
  - creed-card 顶部 ≥ 0 (在视口内可见)
  - 推票卡 t 在 [60, 720] (在首屏合理位置)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:20,
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
        # wait a beat extra so any auto-scroll completes
        await page.wait_for_timeout(500)

        m = await page.evaluate("""() => {
          var R = function(el){ if(!el) return null; var r=el.getBoundingClientRect();
            return { t: Math.round(r.top), b: Math.round(r.bottom) } };
          return {
            vh: window.innerHeight,
            scrollY: Math.round(window.scrollY),
            head: R(document.querySelector('.view-bv .view-head')),
            banner: R(document.querySelector('.view-bv .bv-phase-banner, .bv-phase-banner')),
            creed: R(document.querySelector('.bv-creed-card')),
            firstCard: R(document.querySelector('#bv-pick-tbody tr.bv-row'))
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["scrollY"] == 0, f"page must not auto-scroll on load, got scrollY={m['scrollY']}"
        assert m["head"] and m["head"]["t"] >= 0, f"view-head must be in viewport (top>=0), got {m['head']}"
        assert m["banner"] and m["banner"]["t"] >= 0, f"banner must be in viewport (top>=0), got {m['banner']}"
        assert m["creed"] and m["creed"]["t"] >= 0, f"creed must be in viewport (top>=0), got {m['creed']}"
        assert m["firstCard"] and 60 <= m["firstCard"]["t"] <= 720, f"first card should be in fold, got {m['firstCard']}"

        await browser.close()
        print("[OK] R100 no auto-scroll on load — view-head/banner/creed all in fold")

if __name__ == "__main__":
    asyncio.run(run())
