/* 项目列表视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-projects' });

    var head = flow.el('div', { class: 'view-head' });
    head.appendChild(flow.el('h1', { class: 'view-title', text: '📦 项目列表' }));
    var newBtn = flow.el('a', { class: 'btn-primary', href: '#new', text: '✨ 新建项目' });
    head.appendChild(newBtn);
    root.appendChild(head);

    // 工具分组统计
    var statsCard = flow.el('div', { class: 'flow-card' });
    statsCard.appendChild(flow.el('h2', { class: 'card-title', text: '📊 按工具分布' }));
    var statsGrid = flow.el('div', { class: 'tool-stats-grid', 'data-stats-grid': '' });
    statsCard.appendChild(statsGrid);
    root.appendChild(statsCard);

    var filter = flow.el('div', { class: 'filter-bar' });
    var toolSel = flow.el('select', { class: 'form-input', 'data-filter-tool': '' });
    toolSel.innerHTML = '<option value="">全部工具</option>';
    filter.appendChild(toolSel);
    var statusSel = flow.el('select', { class: 'form-input', 'data-filter-status': '' });
    statusSel.innerHTML = '<option value="">全部状态</option><option>pending</option><option>running</option><option>done</option><option>failed</option><option>cancelled</option>';
    filter.appendChild(statusSel);
    var refresh = flow.el('button', { class: 'btn-mini', text: '🔄 刷新', on: { click: function () { load(); } } });
    filter.appendChild(refresh);
    root.appendChild(filter);

    var tbl = flow.el('table', { class: 'flow-table', 'data-tbl': '' });
    tbl.innerHTML = '<thead><tr><th>名称</th><th>工具</th><th>状态</th><th>创建</th><th>更新</th><th></th></tr></thead><tbody><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(tbl);

    host.appendChild(root);

    function load() {
      var tool = toolSel.value;
      var status = statusSel.value;
      var qs = [];
      if (tool) qs.push('tool_id=' + encodeURIComponent(tool));
      if (status) qs.push('status=' + encodeURIComponent(status));
      flow.api('GET', '/api/projects?' + qs.join('&')).then(function (res) {
        if (!res.ok) return;
        render(res.data.items || []);
      });
    }

    function render(items) {
      var tbody = tbl.querySelector('tbody');
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无项目。</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach(function (p) {
        var tr = document.createElement('tr');
        tr.innerHTML = '<td>' + flow.escapeHtml(p.name) + '</td>'
          + '<td><span class="chip">' + flow.escapeHtml(p.tool_id) + '</span></td>'
          + '<td><span class="status status-' + flow.escapeHtml(p.status) + '">' + flow.escapeHtml(p.status) + '</span></td>'
          + '<td class="muted">' + flow.fmtTime(p.created_at) + '</td>'
          + '<td class="muted">' + flow.fmtTime(p.updated_at) + '</td>'
          + '<td><a class="btn-mini" href="#projects/' + encodeURIComponent(p.id) + '">查看</a></td>';
        tbody.appendChild(tr);
      });
      renderStats(items);
    }

    function renderStats(items) {
      var grid = document.querySelector('[data-stats-grid]');
      if (!grid) return;
      var byTool = {};
      items.forEach(function (p) {
        var t = p.tool_id || '?';
        if (!byTool[t]) byTool[t] = { total: 0, done: 0, running: 0, failed: 0 };
        byTool[t].total++;
        if (p.status === 'done') byTool[t].done++;
        else if (p.status === 'running') byTool[t].running++;
        else if (p.status === 'failed' || p.status === 'cancelled') byTool[t].failed++;
      });
      grid.innerHTML = '';
      var tools = Object.keys(byTool).sort(function (a, b) { return byTool[b].total - byTool[a].total; });
      tools.forEach(function (t) {
        var s = byTool[t];
        var card = flow.el('div', { class: 'tool-stat-card' });
        card.appendChild(flow.el('div', { class: 'tool-stat-name', text: t }));
        card.appendChild(flow.el('div', { class: 'tool-stat-total', text: s.total + ' 个项目' }));
        var successRate = s.total > 0 ? Math.round((s.done / s.total) * 100) : 0;
        card.appendChild(flow.el('div', { class: 'tool-stat-rate', text: '成功率 ' + successRate + '%' }));
        var bar = flow.el('div', { class: 'tool-stat-bar' });
        bar.appendChild(flow.el('div', { class: 'seg seg-done', style: 'flex:' + s.done }));
        bar.appendChild(flow.el('div', { class: 'seg seg-running', style: 'flex:' + s.running }));
        bar.appendChild(flow.el('div', { class: 'seg seg-failed', style: 'flex:' + s.failed }));
        bar.appendChild(flow.el('div', { class: 'seg seg-other', style: 'flex:' + (s.total - s.done - s.running - s.failed) }));
        card.appendChild(bar);
        grid.appendChild(card);
      });
      if (!tools.length) grid.innerHTML = '<div class="muted">尚无项目数据</div>';
    }

    // 加载工具下拉
    flow.api('GET', '/api/tools').then(function (res) {
      if (!res.ok) return;
      (res.data.tools || []).forEach(function (t) {
        var opt = document.createElement('option');
        opt.value = t.tool_id;
        opt.textContent = t.name;
        toolSel.appendChild(opt);
      });
    });

    toolSel.addEventListener('change', load);
    statusSel.addEventListener('change', load);
    load();

    return { name: 'projects', leave: function () {}, enter: function () { load(); } };
  }

  flow.route(/^projects$/, view);
})();