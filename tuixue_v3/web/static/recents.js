// recents.js · 个股最近栈 + 页面标题图标注入
// ─────────────────────────────────────────────────────────────
// 数据来源:监听 view-enter 事件 + 抓 #stock-title / #stock-code
// 存储:localStorage 'tuixue_recent_stocks_v1' · deque(去重) · max 5
// 触发:popover 长按/单击 tabbar-stock 打开,点击外部关闭
// ─────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────
// 数据来源:监听 view-enter 事件 + 抓 #stock-title / #stock-code
// 存储:localStorage 'tuixue_recent_stocks_v1' · deque(去重) · max 5
// 触发:popover 长按/单击 tabbar-stock 打开,点击外部关闭
// ─────────────────────────────────────────────────────────────
(function () {
  const KEY = 'tuixue_recent_stocks_v1';
  const MAX = 5;

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.slice(0, MAX) : [];
    } catch (e) { return []; }
  }
  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); } catch (e) {}
  }
  function push(code, name) {
    if (!code || !/^\d{6}$/.test(code)) return;
    const cur = load().filter(x => x.code !== code);
    cur.unshift({ code, name: name || code, ts: Date.now() });
    save(cur);
  }

  // ─── popover 渲染 ───
  function renderList() {
    const ul = document.getElementById('sr-list');
    if (!ul) return;
    const list = load();
    if (list.length === 0) {
      ul.innerHTML = '<li class="sr-empty">暂无最近个股 · 从「龙头/策略/妖股」点入个股后会自动记录</li>';
      return;
    }
    ul.innerHTML = list.map(x => `
      <li class="sr-item" data-code="${x.code}">
        <span class="sr-code">${x.code}</span>
        <span class="sr-name">${escapeHtml(x.name || x.code)}</span>
        <span class="sr-change">—</span>
      </li>
    `).join('');
    // 点击 → 关弹层 + 切到 stock view 加载
    ul.querySelectorAll('.sr-item').forEach(li => {
      li.addEventListener('click', () => {
        const code = li.dataset.code;
        closePopover();
        if (typeof window.showView === 'function') window.showView('stock');
        if (typeof window.loadStockDetail === 'function') window.loadStockDetail(code);
      });
    });
  }
  // (R-fix: tabbar 红点 badge 已删除,这里留个空函数避免 init 报错)
  function renderBadge() {}

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // ─── popover 开关 ───
  let _popoverOpen = false;
  function openPopover() {
    const pop = document.getElementById('stock-recents-popover');
    if (!pop) return;
    renderList();
    pop.hidden = false;
    _popoverOpen = true;
    // 下一帧绑定外部点击关闭(避免本次 click 立即关闭)
    requestAnimationFrame(() => {
      document.addEventListener('click', _onDocClick, { capture: true });
    });
  }
  function closePopover() {
    const pop = document.getElementById('stock-recents-popover');
    if (pop) pop.hidden = true;
    _popoverOpen = false;
    document.removeEventListener('click', _onDocClick, { capture: true });
  }
  function _onDocClick(e) {
    const pop = document.getElementById('stock-recents-popover');
    const tab = document.getElementById('tabbar-stock');
    if (!pop || !tab) return;
    if (pop.contains(e.target) || tab.contains(e.target)) return;
    closePopover();
  }

  // ─── 监听 view-enter, 个股进入时入栈 ───
  document.addEventListener('view-enter', (e) => {
    const { name } = (e && e.detail) || {};
    if (name !== 'stock') return;
    // 等下一帧让 view-stock.js 渲染完
    setTimeout(() => {
      const codeEl = document.getElementById('stock-code');
      const titleEl = document.getElementById('stock-title');
      const code = (codeEl && codeEl.textContent || '').trim();
      const name = (titleEl && titleEl.textContent || '').trim();
      if (code && code !== '—') push(code, name);
    }, 50);
  });

  // ─── 长按个股 Tab 弹 popover;短按正常切到 stock ───
  // 用 touch 长按 + mouse 长按双触发
  function bindTab() {
    const tab = document.getElementById('tabbar-stock');
    if (!tab || tab.dataset.recentBound) return;
    tab.dataset.recentBound = '1';
    let pressTimer = null;
    let longPressed = false;
    const start = (e) => {
      longPressed = false;
      pressTimer = setTimeout(() => {
        longPressed = true;
        openPopover();
        // 震动反馈 (iOS Safari 不支持,Android Chrome 支持)
        if (navigator.vibrate) navigator.vibrate(10);
      }, 420);
    };
    const cancel = () => { if (pressTimer) clearTimeout(pressTimer); };
    tab.addEventListener('touchstart', start, { passive: true });
    tab.addEventListener('touchend',   cancel);
    tab.addEventListener('touchmove',  cancel);
    tab.addEventListener('mousedown',  start);
    tab.addEventListener('mouseup',    cancel);
    tab.addEventListener('mouseleave', cancel);
    // 如果长按触发后跳到个股页会立刻关弹层,挡一下
    tab.addEventListener('click', (e) => {
      if (longPressed) { e.stopImmediatePropagation(); e.preventDefault(); longPressed = false; }
    }, true);
  }

  // ─── 清空按钮 ───
  function bindClear() {
    const btn = document.getElementById('sr-clear');
    if (!btn || btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      try { localStorage.removeItem(KEY); } catch (e) {}
      renderList();
    });
  }

  // ─── 初始化 ───
  function init() {
    bindTab();
    bindClear();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();