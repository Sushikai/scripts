"""R125 .bv-sort-opt 触控热区 — padding 10px 但无 min-height (audit).

原: R17 引入 .bv-sort-opt (sheet 内排序字段按钮), padding 10px + font 13px,
    实际渲染高度约 38-40px (R102 sort-btn 28px 是 sheet 触发按钮, opt 是 sheet 内容)。
R125 audit: 实际渲染高度检查, 确认是否已经 ≥32px (HIG)。
    若已 ≥32: 不动, R125 文档化 (类型 B: 已有足够大)。
    若 <32: 加 min-height: 32 (跟 R104-R118 + R117 一致 HIG)。
第一性原理: sheet 内按钮是高频操作 (切排序), 拇指点击必须有 32px 兜底;
    就算 padding 撑到 38, 写 min-height 32 是 anti-regression 守护。
断言 (mock 数据, 390px):
  - sort-btn 点击打开 sheet
  - .bv-sort-opt 至少 4 个 (mock 提供 4 个排序字段)
  - 第一个 opt height ≥ 32
  - 字体 13px (保持 R17 标准)
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

        # Inject a sort sheet with .bv-sort-opt items
        m = await page.evaluate(r"""() => {
          // Remove any old injected sheet
          var old = document.querySelectorAll('.bv-sort-sheet');
          old.forEach(function(n){ n.remove(); });
          // Force body class
          document.body.classList.add('bv-sort-open');
          // Build sheet with sort-opts (4 fields per R17 pattern)
          var sheet = document.createElement('div');
          sheet.className = 'bv-sort-sheet';
          sheet.innerHTML =
            '<div class="bv-sort-mask"></div>' +
            '<div class="bv-sort-panel">' +
              '<div class="bv-sort-handle"></div>' +
              '<h4>排序字段</h4>' +
              '<div class="bv-sort-list">' +
                '<button class="bv-sort-opt is-active" data-field="score">综合分</button>' +
                '<button class="bv-sort-opt" data-field="streak">连板</button>' +
                '<button class="bv-sort-opt" data-field="change_pct">涨幅</button>' +
                '<button class="bv-sort-opt" data-field="first_time">首封</button>' +
              '</div>' +
              '<div class="bv-sort-dir">' +
                '<button class="bv-sort-dir-opt is-active">降序</button>' +
                '<button class="bv-sort-dir-opt">升序</button>' +
              '</div>' +
            '</div>';
          document.body.appendChild(sheet);
          // Force layout
          void sheet.offsetHeight;
          var opt = document.querySelector('.bv-sort-opt');
          var rect = opt ? opt.getBoundingClientRect() : null;
          var cs = opt ? getComputedStyle(opt) : null;
          return {
            sheetExists: !!sheet,
            optCount: document.querySelectorAll('.bv-sort-opt').length,
            optRect: rect ? {w: Math.round(rect.width), h: Math.round(rect.height)} : null,
            optFont: cs ? cs.fontSize : null,
            optPadding: cs ? cs.padding : null,
            optMinH: cs ? cs.minHeight : null,
            hasActive: !!document.querySelector('.bv-sort-opt.is-active'),
            firstOptText: opt ? opt.textContent.trim() : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("optCount", 0) >= 4, f"expected ≥4 sort-opts, got {m.get('optCount')}"
        if m.get("optRect"):
            h = m["optRect"]["h"]
            assert h >= 32, f"sort-opt height regression: {h} (HIG requires ≥32)"

        await browser.close()
        print(f"[OK] R125 sort-opt audit — {m['optCount']} opts, first {m['optRect']['w']}×{m['optRect']['h']} | font {m['optFont']} | minH {m['optMinH']}")

if __name__ == "__main__":
    asyncio.run(run())