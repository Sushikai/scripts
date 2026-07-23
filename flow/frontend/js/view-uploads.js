/* 上传记录视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-uploads' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '📤 上传记录' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'B 站 BV id / 抖音视频 id 表格。' }));

    var tbl = flow.el('table', { class: 'flow-table', 'data-tbl': '' });
    tbl.innerHTML = '<thead><tr><th>平台</th><th>账号</th><th>BV/视频 ID</th><th>项目</th><th>状态</th><th>创建</th></tr></thead><tbody><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(tbl);

    host.appendChild(root);

    flow.api('GET', '/api/uploads').then(function (res) {
      var tbody = tbl.querySelector('tbody');
      tbody.innerHTML = '';
      if (!res.ok) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">上传 API 暂未实装(/api/uploads)。</td></tr>';
        return;
      }
      var items = res.data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无上传记录。</td></tr>';
        return;
      }
      items.forEach(function (u) {
        var tr = document.createElement('tr');
        tr.innerHTML = '<td><span class="chip">' + flow.escapeHtml(u.platform) + '</span></td>'
          + '<td>' + flow.escapeHtml(u.account || '') + '</td>'
          + '<td class="mono">' + flow.escapeHtml(u.vid_id || '') + '</td>'
          + '<td>' + flow.escapeHtml(u.project_name || '') + '</td>'
          + '<td><span class="status status-' + flow.escapeHtml(u.status) + '">' + flow.escapeHtml(u.status) + '</span></td>'
          + '<td class="muted">' + flow.fmtTime(u.created_at) + '</td>';
        tbody.appendChild(tr);
      });
    });

    return { name: 'uploads', leave: function () {}, enter: function () {} };
  }

  flow.route(/^uploads$/, view);
})();