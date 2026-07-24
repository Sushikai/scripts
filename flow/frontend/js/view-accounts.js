/* 账号管理视图:cookie 健康 + 文件信息 + 状态灯 + 实时活动数 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-accounts' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '👤 账号管理' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'B 站多账号 cookie 健康 + 文件信息 + fan_hunter 互动数。' }));

    // 账号 KPI(总数/新鲜/即将过期/已过期)
    var kpiCard = flow.el('div', { class: 'flow-card' });
    var kpiGrid = flow.el('div', { class: 'kpi-grid', 'data-kpi-grid': '' });
    kpiCard.appendChild(kpiGrid);
    root.appendChild(kpiCard);

    // 账号卡片网格
    var grid = flow.el('div', { class: 'accounts-grid', 'data-accounts-grid': '' });
    grid.innerHTML = '<p class="muted">加载中…</p>';
    root.appendChild(grid);

    host.appendChild(root);

    loadAccounts();
    loadConversion();

    function loadAccounts() {
      flow.api('GET', '/api/accounts').then(function (res) {
        var kg = document.querySelector('[data-kpi-grid]');
        var ag = document.querySelector('[data-accounts-grid]');
        if (!res.ok) return;
        var items = res.data.items || [];
        // KPI
        var fresh = items.filter(function (a) { return a.cookie && a.cookie.freshness === 'fresh'; }).length;
        var stale = items.filter(function (a) { return a.cookie && a.cookie.freshness === 'stale'; }).length;
        var expired = items.filter(function (a) { return a.cookie && a.cookie.freshness === 'expired'; }).length;
        var missing = items.filter(function (a) { return !a.cookie || a.cookie.freshness === 'missing'; }).length;
        kg.innerHTML = '';
        [{ label: '总账号', value: items.length }, { label: 'Cookie 新鲜', value: fresh },
         { label: '即将过期', value: stale }, { label: '已过期', value: expired }].forEach(function (k) {
          var c = flow.el('div', { class: 'kpi-card' });
          c.appendChild(flow.el('div', { class: 'kpi-label', text: k.label }));
          c.appendChild(flow.el('div', { class: 'kpi-value', text: k.value }));
          kg.appendChild(c);
        });
        // 卡片
        ag.innerHTML = '';
        items.forEach(function (a) {
          var card = flow.el('div', { class: 'account-card status-' + a.status });
          var head = flow.el('div', { class: 'account-head' });
          head.appendChild(flow.el('span', { class: 'status-light status-' + a.status, text: '' }));
          head.appendChild(flow.el('span', { class: 'account-name', text: a.name }));
          head.appendChild(flow.el('span', { class: 'chip', text: a.role === 'primary' ? '主' : '备' }));
          card.appendChild(head);
          card.appendChild(flow.el('div', { class: 'account-platform muted', text: a.platform + ' · ' + a.id }));
          var cookie = a.cookie || {};
          var rows = [
            ['状态', cookie.freshness || '—'],
            ['Cookie 数', cookie.cookie_count || 0],
            ['文件大小', cookie.size_bytes ? (cookie.size_bytes + ' B') : '—'],
            ['更新于', cookie.age_days != null ? (cookie.age_days + ' 天前') : '—'],
          ];
          rows.forEach(function (r) {
            var row = flow.el('div', { class: 'kv-row' });
            row.appendChild(flow.el('span', { class: 'kv-key', text: r[0] }));
            row.appendChild(flow.el('span', { class: 'kv-val', text: String(r[1]) }));
            card.appendChild(row);
          });
          card.appendChild(flow.el('div', { class: 'account-path muted', text: a.cookie_path }));
          ag.appendChild(card);
        });
      });
    }

    function loadConversion() {
      flow.api('GET', '/api/comments/conversion').then(function (res) {
        if (!res.ok) return;
        var s = res.data.summary || {};
        var convCard = flow.el('div', { class: 'flow-card' });
        convCard.appendChild(flow.el('h2', { class: 'card-title', text: '📈 账号转化数据' }));
        var grid = flow.el('div', { class: 'kv-grid' });
        var rows = [
          ['日期', s.date || '—'],
          ['总互动', s.total_actions || 0],
          ['总转化', s.total_converted || 0],
          ['转化率', ((s.overall_conversion_rate || 0) * 100).toFixed(2) + '%'],
          ['当前粉丝', s.current_followers || 0],
          ['新增粉丝', s.new_followers || 0],
        ];
        rows.forEach(function (r) {
          var row = flow.el('div', { class: 'kv-row' });
          row.appendChild(flow.el('span', { class: 'kv-key', text: r[0] }));
          row.appendChild(flow.el('span', { class: 'kv-val', text: String(r[1]) }));
          grid.appendChild(row);
        });
        convCard.appendChild(grid);
        // 插入到账号卡片之后
        var accountsGrid = document.querySelector('[data-accounts-grid]');
        if (accountsGrid && accountsGrid.parentNode) {
          accountsGrid.parentNode.insertBefore(convCard, accountsGrid.nextSibling);
        }
      });
    }

    return { name: 'accounts', leave: function () {}, enter: function () { loadAccounts(); loadConversion(); } };
  }

  flow.route(/^accounts$/, view);
})();