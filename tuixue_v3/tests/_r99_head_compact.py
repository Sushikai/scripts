"""R99 标题是身份不是导航 — 压缩 view-head, 推票卡尽早进首屏.

原: .view-bv .view-head 高 115px — 刷新按钮被全局 button min-height:40px 泄漏
    撑到 46px (声明的 34px 被覆盖), 标题含内联副标题换行成 45px 两行.
R99: 1) 刷新按钮 height:28px + min-height:0 !important (次级操作)
    2) 标题单行省略 (身份是标题本身, 副标题是 meta)
    3) meta 单行省略 + 紧凑
断言 (mock 数据, 390px):
  - 刷新按钮高度 ≤ 30
  - 标题单行 (scrollWidth ≤ clientWidth + 2)
  - meta 单行
  - view-head 高度 < 95 (基线 115)
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

        m = await page.evaluate("""() => {
          var head = document.querySelector('.view-bv .view-head');
          if (!head) return { noHead: true };
          var refresh = head.querySelector('#bv-refresh');
          var title = head.querySelector('.bv-title');
          var meta = head.querySelector('#bv-meta');
          var firstCard = document.querySelector('#bv-pick-tbody tr.bv-row');
          var R = function(el){ var r=el.getBoundingClientRect();
            return { t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height) } };
          return {
            headH: Math.round(head.getBoundingClientRect().height),
            refreshH: refresh ? Math.round(refresh.getBoundingClientRect().height) : null,
            titleH: title ? Math.round(title.getBoundingClientRect().height) : null,
            metaSingle: meta ? (meta.scrollWidth <= meta.clientWidth + 2) : null,
            metaH: meta ? Math.round(meta.getBoundingClientRect().height) : null,
            firstCardTop: firstCard ? Math.round(firstCard.getBoundingClientRect().top) : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("noHead") != True, "view-head missing"
        assert m["refreshH"] is not None and m["refreshH"] <= 30, f"refresh btn {m['refreshH']} should be ≤30"
        # title single line = its height ≈ one line-height (≤20px at 15px font)
        assert m["titleH"] is not None and m["titleH"] <= 20, f"title must be single line, got height {m['titleH']}"
        assert m["metaSingle"] == True, "meta must be single line"
        assert m["headH"] < 95, f"view-head should be < 95px, got {m['headH']}"

        await browser.close()
        print("[OK] R99 view-head compact")

if __name__ == "__main__":
    asyncio.run(run())
