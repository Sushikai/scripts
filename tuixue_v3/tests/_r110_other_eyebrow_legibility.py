"""R110 战法页非 creed 卡 eyebrow — 10→11px (R108/R109 一致性).

原: audit 实测 3 个 .card-eyebrow 都是 10px (UP主/BV号/时间、近180日、点击展开原话出处),
    其中 2 个跟 creed-card 无关 (其他卡), 同样"凑近看"问题。
R110: 用 .view-bv .card:not(.bv-creed-card) > .card-head > .card-eyebrow 选择器
    把非 creed 卡的 eyebrow 全部提到 11px, !important 防止基线 10px 覆盖。
    R109 (creed) 保持 11px !important 单独走。
断言 (mock 数据, 390px):
  - 战法页至少 1 个非 creed eyebrow 字号 ≥ 11px
  - creed eyebrow 保持 R109 11px !important (不回归)
  - 规则卡 eyebrow 字号 ≥ 11px (rule-list 卡的 "近 180 日" 等)
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
          var creed = document.querySelector('.view-bv .bv-creed-card .card-head .card-eyebrow');
          var others = document.querySelectorAll('.view-bv .card:not(.bv-creed-card) > .card-head > .card-eyebrow');
          var ruleListEb = document.querySelector('#bv-rule-list .card-eyebrow, .view-bv .bv-rule-card .card-eyebrow');
          var backtestEb = document.querySelector('.view-bv .bv-backtest-card .card-eyebrow, #bv-backtest .card-eyebrow');
          var items = [];
          function dump(el, label) {
            if (!el) return null;
            var cs = getComputedStyle(el);
            return { label: label, fontSize: cs.fontSize, text: el.textContent.trim().slice(0,20) };
          }
          others.forEach(function(e){
            var cs = getComputedStyle(e);
            items.push({ label: 'other', fontSize: cs.fontSize, text: e.textContent.trim().slice(0,20) });
          });
          return {
            creed: dump(creed, 'creed'),
            others: items,
            ruleListEb: dump(ruleListEb, 'rule-list'),
            backtestEb: dump(backtestEb, 'backtest')
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        # creed must stay 11px !important (R109 not regressed)
        if m["creed"]:
            assert m["creed"]["fontSize"] == "11px", f"creed must stay 11px, got {m['creed']['fontSize']}"

        # at least one non-creed eyebrow should be 11px (R110 scope)
        non_creed_11 = [x for x in m["others"] if x["fontSize"] == "11px"]
        if m["others"]:
            assert len(non_creed_11) >= 1, f"R110 should bump non-creed eyebrow to 11px, got {[x['fontSize'] for x in m['others']]}"

        await browser.close()
        print(f"[OK] R110 non-creed eyebrow — {len(non_creed_11)}/{len(m['others'])} cards 11px")

if __name__ == "__main__":
    asyncio.run(run())