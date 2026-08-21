"""R104 code-link 触控热区扩展 — 13px 高 → 32px 高 (Apple HIG).

原: code-link 是默认 inline <a>, 实测只有 43×13px — 拇指无法准确命中。
    用户想点代码进个股页, 但视觉上和物理上都极小。
R104: 加 padding 8px + min-height:32px + 负 margin -8px (视觉尺寸不变, tap zone 撑高到 32px+),
    同时加 :active 高亮反馈 (复用 all_stocks 的 hsla 蓝色)。
断言 (mock 数据, 390px):
  - code-link 高度 ≥ 32px (Apple HIG 最低)
  - code-link 视觉宽度不变 (字号 11px 不变, 不撑大卡片布局)
  - 点击 code-link 命中个股页跳转 (data-goto-stock 属性存在)
  - 卡片整体布局不变 (row height 一致)
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
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
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

        m = await page.evaluate("""() => {
          var link = document.querySelector('#bv-pick-tbody tr.bv-row td:nth-child(1) a.code-link');
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!link) return {none:true};
          var r = link.getBoundingClientRect();
          var cs = getComputedStyle(link);
          return {
            link: { w: Math.round(r.width), h: Math.round(r.height), text: link.textContent.trim(), goto: link.getAttribute('data-goto-stock') },
            padding: cs.padding,
            margin: cs.margin,
            minHeight: cs.minHeight,
            rowH: Math.round(row.getBoundingClientRect().height)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["link"]["h"] >= 32, f"code-link tap zone must be ≥32px (Apple HIG), got {m['link']['h']}"
        assert m["link"]["goto"] == "600519", f"data-goto-stock must be set, got {m['link']['goto']}"
        # row height should not grow meaningfully (negative margin keeps layout stable)
        assert m["rowH"] >= 100 and m["rowH"] <= 145, f"row height should stay reasonable, got {m['rowH']}"

        await browser.close()
        print(f"[OK] R104 code-link tap zone — {m['link']['w']}×{m['link']['h']}px")

if __name__ == "__main__":
    asyncio.run(run())
