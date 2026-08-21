// view-decorate.js · view 标题前注入 SVG 图标 + accent 装饰
// ─────────────────────────────────────────────────────────────
// 监听 view-enter,对每个 view 的 .view-head .display 注入统一风格 SVG
// 在 h1 前插入 24×24 主题图标,accent 后追加装饰元素
// ─────────────────────────────────────────────────────────────
(function () {
  // ─── 图标库(从 sidebar 同源,统一尺寸 24×24,stroke-width 1.6) ───
  const ICONS = {
    dash:        'M3 3h7v7H3zM14 3h7v7h-7M3 14h7v7H3zM14 14h7v7h-7',
    stock:       'M2 18l5-5 4 4 8-9M2 22h20',
    watchlist:   'M5 3h11l3 3v15H5zM9 12l2 2 4-4',
    dragons:     'M2 14l4-5 3 4 3-7 4 8M2 19h18',
    weekly_bull: 'M2 16c2-3 4-3 6 0s4 3 6 0 4-3 6 0M2 20h18',
    strategy_picker: 'M8 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM16 16l5 5',
    yaogu:       'M12 3l2 5 5 1-4 4 1 5-4-2-4 2 1-5-4-4 5-1z',
    optimize:    'M4 12a8 8 0 1 1 16 0 8 8 0 0 1-16 0zM12 6v6l4 2',
    laws:        'M5 3h11l3 3v15H5zM8 8h7M8 12h7M8 16h5',
    yeren:       'M3 6h6v6H3zM11 10h6v10h-6zM15 6h6v6h-6z',
    yeren_ai:    'M12 4a6 6 0 0 0-6 6c0 2.5 1.5 4.5 3.5 5.5L9 20h6l-.5-4.5C16.5 14.5 18 12.5 18 10a6 6 0 0 0-6-6zM9 22h6',
    review:      'M3 4h18M3 10h18M3 16h12M17 18l2 2 4-4',
    sources:     'M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM4 21a8 8 0 0 1 16 0',
    sector:      'M3 21V10M9 21V4M15 21v-8M21 21V8',
    all_stocks:  'M3 19V8M8 19V4M13 19v-9M18 19V6',
    dexin:       'M3 18l5-5 4 4 8-9M3 21h18',
    screener:    'M11 11a6 6 0 1 1 0-12 6 6 0 0 1 0 12zM16 16l5 5',
    ai_review:   'M12 3a9 9 0 1 0 9 9M12 7v5l3 2M9 12h6',
    more:        'M5 12h.01M12 12h.01M19 12h.01',
  };

  // view 名字 → 图标 key 的别名映射(数据驱动名 vs 图标 key)
  const ALIAS = {
    'ai-review': 'ai_review',
  };

  function iconKeyFor(view) {
    if (ICONS[view]) return view;
    return ALIAS[view] || null;
  }

  // 渲染 SVG 字符串
  function svgFor(view) {
    const key = iconKeyFor(view);
    if (!key) return '';
    return `<svg class="display-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[key]}"/></svg>`;
  }

  // 给单个 view-head 注入图标
  function decorate(head) {
    if (!head || head.dataset.decorated) return;
    const view = head.closest('[data-view]');
    if (!view) return;
    const viewName = view.dataset.view;
    const h1 = head.querySelector('.display');
    if (!h1) return;
    const svg = svgFor(viewName);
    if (!svg) return;
    head.classList.add('view-head-decorated');
    h1.insertAdjacentHTML('afterbegin', svg);
    head.dataset.decorated = '1';
  }

  // 监听 view-enter + 立即处理当前显示的 view
  function run(viewName) {
    const sel = viewName ? `[data-view="${viewName}"]` : '.view:not([hidden])';
    const el = document.querySelector(sel);
    if (el) decorate(el.querySelector('.view-head'));
  }

  document.addEventListener('view-enter', (e) => {
    const { name } = (e && e.detail) || {};
    if (name) run(name);
  });

  // 首屏 dash 已经渲染, 直接跑一次
  function init() {
    run('dash');
    // 也兜底所有 visible view
    document.querySelectorAll('.view:not([hidden]) .view-head').forEach(decorate);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();