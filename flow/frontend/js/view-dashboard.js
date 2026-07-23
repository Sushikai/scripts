/* Dashboard 视图:今日 KPI + 工具状态 + 最近项目 */

(function () {
  'use strict';

  var STAT_TEMPLATES = [
    { id: 'projects_total', label: '项目总数', kind: 'num' },
    { id: 'jobs_today', label: '今日 Job', kind: 'num' },
    { id: 'uploads_today', label: '今日上传', kind: 'num' },
    { id: 'success_rate', label: '成功率', kind: 'pct' },
  ];

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-dashboard' });

    var greeting = flow.el('h1', { class: 'view-title', text: '📊 Dashboard' });
    root.appendChild(greeting);

    var sub = flow.el('p', { class: 'view-sub', text: 'flow 平台总览 — KPI、工具状态、最近项目。' });
    root.appendChild(sub);

    var kpiGrid = flow.el('div', { class: 'kpi-grid' });
    STAT_TEMPLATES.forEach(function (s) {
      var card = flow.el('div', { class: 'kpi-card' });
      card.appendChild(flow.el('div', { class: 'kpi-label', text: s.label }));
      card.appendChild(flow.el('div', { class: 'kpi-value', text: '—', 'data-stat': s.id }));
      card.appendChild(flow.el('div', { class: 'kpi-trend', text: '' }));
      kpiGrid.appendChild(card);
    });
    root.appendChild(kpiGrid);

    var toolsTitle = flow.el('h2', { class: 'view-section-title', text: '🛠 工具状态' });
    root.appendChild(toolsTitle);

    var toolsGrid = flow.el('div', { class: 'tools-grid', 'data-tools-grid': '' });
    root.appendChild(toolsGrid);

    var recentTitle = flow.el('h2', { class: 'view-section-title', text: '🕒 最近项目' });
    root.appendChild(recentTitle);

    var recentTable = flow.el('table', { class: 'flow-table', 'data-recent-table': '' });
    recentTable.innerHTML = '<thead><tr><th>名称</th><th>工具</th><th>状态</th><th>更新时间</th></tr></thead><tbody><tr><td colspan="4" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(recentTable);

    host.appendChild(root);

    refreshDashboard();

    return {
      name: 'dashboard',
      leave: function () { /* noop */ },
      enter: function () { refreshDashboard(); },
    };
  }

  function refreshDashboard() {
    loadStats();
    loadTools();
    loadRecent();
  }

  function loadStats() {
    flow.api('GET', '/api/dashboard').then(function (res) {
      if (!res.ok) return;
      var data = res.data || {};
      var stats = data.stats || {};
      STAT_TEMPLATES.forEach(function (s) {
        var node = document.querySelector('[data-stat="' + s.id + '"]');
        if (!node) return;
        var v = stats[s.id];
        if (v == null) {
          node.textContent = '—';
        } else if (s.kind === 'pct') {
          node.textContent = (v * 100).toFixed(1) + '%';
        } else {
          node.textContent = String(v);
        }
      });
    }).catch(function () {
      // 静默 — 灯会显示离线
    });
  }

  function loadTools() {
    flow.api('GET', '/api/tools').then(function (res) {
      var grid = document.querySelector('[data-tools-grid]');
      if (!grid || !res.ok) return;
      grid.innerHTML = '';
      (res.data.tools || []).forEach(function (t) {
        var card = flow.el('div', { class: 'tool-card' });
        card.appendChild(flow.el('div', { class: 'tool-icon', text: toolIcon(t.tool_id) }));
        card.appendChild(flow.el('div', { class: 'tool-name', text: t.name }));
        card.appendChild(flow.el('div', { class: 'tool-desc', text: t.description || '' }));
        var meta = flow.el('div', { class: 'tool-meta' });
        meta.appendChild(flow.el('span', { class: 'chip', text: (t.steps || []).length + ' 步' }));
        card.appendChild(meta);
        card.addEventListener('click', function () {
          flow.navigate('new?tool=' + encodeURIComponent(t.tool_id));
        });
        grid.appendChild(card);
      });
    });
  }

  function loadRecent() {
    flow.api('GET', '/api/projects?limit=8').then(function (res) {
      var tbl = document.querySelector('[data-recent-table]');
      if (!tbl || !res.ok) return;
      var tbody = tbl.querySelector('tbody');
      var items = res.data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="muted">暂无项目,去 <a href="#new">新建项目</a>。</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach(function (p) {
        var tr = document.createElement('tr');
        tr.innerHTML = '<td>' + flow.escapeHtml(p.name) + '</td>'
          + '<td><span class="chip">' + flow.escapeHtml(p.tool_id) + '</span></td>'
          + '<td><span class="status status-' + flow.escapeHtml(p.status) + '">' + flow.escapeHtml(p.status) + '</span></td>'
          + '<td class="muted">' + flow.fmtTime(p.updated_at) + '</td>';
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', function () {
          flow.navigate('projects/' + encodeURIComponent(p.id));
        });
        tbody.appendChild(tr);
      });
    });
  }

  function toolIcon(toolId) {
    return {
      info_gap: '🎬',
      fengge: '📺',
      tiktok_story: '🎵',
      material_collector: '🎞️',
    }[toolId] || '⚙️';
  }

  flow.route(/^dashboard$/, view);
})();