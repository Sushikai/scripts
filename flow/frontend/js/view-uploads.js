/* 上传记录视图:从 job artifacts 聚合(fengge.upload / tiktok.upload_bili 等) */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-uploads' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '📤 上传记录' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: '从 job artifacts 聚合 B站/抖音 上传记录。' }));

    // KPI
    var kpiCard = flow.el('div', { class: 'flow-card' });
    var kpiGrid = flow.el('div', { class: 'kpi-grid', 'data-kpi-grid': '' });
    kpiCard.appendChild(kpiGrid);
    root.appendChild(kpiCard);

    // 平台过滤
    var filter = flow.el('div', { class: 'filter-bar' });
    var platSel = flow.el('select', { class: 'form-input', 'data-filter-plat': '' });
    platSel.innerHTML = '<option value="">所有平台</option><option value="bilibili">B 站</option><option value="douyin">抖音</option>';
    filter.appendChild(platSel);
    root.appendChild(filter);

    var tbl = flow.el('table', { class: 'flow-table', 'data-tbl': '' });
    tbl.innerHTML = '<thead><tr><th>平台</th><th>工具</th><th>账号</th><th>BV/视频 ID</th><th>项目</th><th>状态</th><th>时间</th></tr></thead><tbody><tr><td colspan="7" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(tbl);

    host.appendChild(root);

    var _allItems = [];
    function load() {
      flow.api('GET', '/api/uploads?limit=200').then(function (res) {
        var tbody = tbl.querySelector('tbody');
        if (!res.ok) {
          tbody.innerHTML = '<tr><td colspan="7" class="muted">上传 API 加载失败</td></tr>';
          return;
        }
        _allItems = res.data.items || [];
        render();
      });
    }

    function render() {
      var tbody = tbl.querySelector('tbody');
      var plat = platSel.value;
      var items = plat ? _allItems.filter(function (i) { return i.platform === plat; }) : _allItems;
      // KPI
      var kg = document.querySelector('[data-kpi-grid]');
      if (kg) {
        var done = items.filter(function (i) { return i.status === 'done'; }).length;
        var failed = items.filter(function (i) { return i.status === 'failed'; }).length;
        var withBvid = items.filter(function (i) { return i.vid_id && !i.vid_id.startsWith('(dry'); }).length;
        kg.innerHTML = '';
        [{ label: '总上传', value: items.length }, { label: '成功', value: done },
         { label: '失败', value: failed }, { label: '真实 BV (非 dry)', value: withBvid }].forEach(function (k) {
          var c = flow.el('div', { class: 'kpi-card' });
          c.appendChild(flow.el('div', { class: 'kpi-label', text: k.label }));
          c.appendChild(flow.el('div', { class: 'kpi-value', text: k.value }));
          kg.appendChild(c);
        });
      }
      // 表格
      tbody.innerHTML = '';
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="muted">暂无上传记录</td></tr>';
        return;
      }
      items.forEach(function (u) {
        var tr = document.createElement('tr');
        var bvid = u.vid_id || '(dry-run)';
        var bvidDisplay = bvid.length > 18 ? bvid.slice(0, 18) + '…' : bvid;
        tr.innerHTML = '<td><span class="chip">' + flow.escapeHtml(u.platform) + '</span></td>'
          + '<td><span class="chip">' + flow.escapeHtml(u.tool_id) + '</span></td>'
          + '<td>' + flow.escapeHtml(u.account || '—') + '</td>'
          + '<td class="mono">' + flow.escapeHtml(bvidDisplay) + '</td>'
          + '<td><a href="#projects/' + encodeURIComponent(u.project_id) + '">' + flow.escapeHtml(u.project_name || '') + '</a></td>'
          + '<td><span class="status status-' + flow.escapeHtml(u.status) + '">' + flow.escapeHtml(u.status) + '</span></td>'
          + '<td class="muted">' + flow.fmtTime(u.created_at) + '</td>';
        tbody.appendChild(tr);
      });
    }

    platSel.addEventListener('change', render);

    load();
    return { name: 'uploads', leave: function () {}, enter: function () { load(); } };
  }

  flow.route(/^uploads$/, view);
})();