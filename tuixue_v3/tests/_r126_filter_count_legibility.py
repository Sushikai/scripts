"""R126 .bv-filter-count 字号 10→11px — filter chip 数量徽章可读性.

原: .bv-filter-count 是 filter chip 旁的数字徽章 (e.g. "板块 42" / "全部 100+"),
    font 10px, 跟 chip 主标签 (12px+) 比例悬殊 → 用户看不清数字。
R126: font 10→11px (跟 R108/R119/R123 typography 一致档位)。
    chip 触控 (R106 32px) 跟视觉字号 (11px) 双轨补完。
第一性原理: count 数字是用户判断"该筛选项命中多少"的关键信号,
    数字看不清 → 用户盲切 filter 浪费时间。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-filter-count 渲染
  - font-size = 11px
  - font-weight = 700 (数字加粗保留 — 信号靠粗体 + 背景对比)
  - chip 整体 padding (R106 守护不动)
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

        # Inject a filter chip with count badge
        m = await page.evaluate(r"""() => {
          // Remove old injected test
          var old = document.querySelectorAll('.bv-filter-bar.test-r126');
          old.forEach(function(n){ n.remove(); });
          var bar = document.createElement('div');
          bar.className = 'bv-filter-bar test-r126';
          bar.innerHTML =
            '<button class="bv-filter-chip is-active">全部 <span class="bv-filter-count">42</span></button>' +
            '<button class="bv-filter-chip">白酒 <span class="bv-filter-count">12</span></button>' +
            '<button class="bv-filter-chip">银行 <span class="bv-filter-count">3</span></button>';
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          (vb || document.body).appendChild(bar);
          void bar.offsetHeight;
          var cnt = document.querySelector('.bv-filter-count');
          var chip = document.querySelector('.bv-filter-chip');
          var cs = cnt ? getComputedStyle(cnt) : null;
          var chipCs = chip ? getComputedStyle(chip) : null;
          var rect = chip ? chip.getBoundingClientRect() : null;
          return {
            chipCount: document.querySelectorAll('.bv-filter-chip').length,
            cntCount: document.querySelectorAll('.bv-filter-count').length,
            cntFont: cs ? cs.fontSize : null,
            cntWeight: cs ? cs.fontWeight : null,
            chipRect: rect ? {w: Math.round(rect.width), h: Math.round(rect.height)} : null,
            chipMinH: chipCs ? chipCs.minHeight : null,
            firstCntText: cnt ? cnt.textContent : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("cntCount", 0) >= 1, f"expected ≥1 filter-count, got {m.get('cntCount')}"
        assert m.get("cntFont") == "11px", f"filter-count should be 11px (was 10), got {m.get('cntFont')}"
        assert m.get("cntWeight") in ("700", "bold"), f"filter-count weight should be 700, got {m.get('cntWeight')}"
        # R106 chip tap zone regression check
        if m.get("chipRect"):
            assert m["chipRect"]["h"] >= 32, f"chip tap zone regression: {m['chipRect']['h']} (R106 requires ≥32)"

        await browser.close()
        print(f"[OK] R126 filter-count typography — {m['cntFont']} {m['cntWeight']} ({m['cntCount']} badges) | chip {m['chipRect']['w']}×{m['chipRect']['h']} (R106 ≥32 ✓)")

if __name__ == "__main__":
    asyncio.run(run())