/* 素材库视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-library' });

    root.appendChild(flow.el('h1', { class: 'view-title', text: '🎬 素材库' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: '采集到的素材 + 标签筛选(后端 API 待补)。' }));

    var grid = flow.el('div', { class: 'library-grid', 'data-library-grid': '' });
    grid.innerHTML = '<p class="muted">加载中…</p>';
    root.appendChild(grid);

    host.appendChild(root);

    flow.api('GET', '/api/assets').then(function (res) {
      grid.innerHTML = '';
      if (!res.ok) {
        grid.innerHTML = '<p class="muted">素材库 API 暂未实装(/api/assets)。请运行 material_collector 后查看。</p>';
        return;
      }
      var items = res.data.items || [];
      if (!items.length) {
        grid.innerHTML = '<p class="muted">尚无素材,先到 <a href="#new">新建项目</a> 跑素材采集。</p>';
        return;
      }
      items.forEach(function (a) {
        var card = flow.el('div', { class: 'asset-card' });
        card.appendChild(flow.el('div', { class: 'asset-thumb', text: '🎞️' }));
        card.appendChild(flow.el('div', { class: 'asset-title', text: a.title || a.id }));
        card.appendChild(flow.el('div', { class: 'asset-meta muted', text: (a.platform || '') + ' · ' + flow.fmtTime(a.created_at) }));
        grid.appendChild(card);
      });
    });

    return { name: 'library', leave: function () {}, enter: function () {} };
  }

  flow.route(/^library$/, view);
})();