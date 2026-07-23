/* 账号管理视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-accounts' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '👤 账号管理' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'B 站 / 抖音 cookie 健康灯 + 最后活跃。' }));

    var grid = flow.el('div', { class: 'accounts-grid', 'data-accounts-grid': '' });
    grid.innerHTML = '<p class="muted">加载中…</p>';
    root.appendChild(grid);

    host.appendChild(root);

    flow.api('GET', '/api/accounts').then(function (res) {
      grid.innerHTML = '';
      if (!res.ok) {
        grid.innerHTML = '<p class="muted">账号 API 暂未实装(/api/accounts)。</p>';
        return;
      }
      var items = res.data.items || [];
      if (!items.length) {
        grid.innerHTML = '<p class="muted">暂无账号。</p>';
        return;
      }
      items.forEach(function (a) {
        var card = flow.el('div', { class: 'account-card' });
        card.appendChild(flow.el('div', { class: 'account-name', text: a.name || a.id }));
        card.appendChild(flow.el('div', { class: 'account-platform muted', text: a.platform }));
        var light = flow.el('span', { class: 'account-light ' + (a.status || 'unknown'), text: '' });
        card.appendChild(light);
        card.appendChild(flow.el('div', { class: 'muted', text: '最后: ' + flow.fmtTime(a.last_check_at) }));
        grid.appendChild(card);
      });
    });

    return { name: 'accounts', leave: function () {}, enter: function () {} };
  }

  flow.route(/^accounts$/, view);
})();