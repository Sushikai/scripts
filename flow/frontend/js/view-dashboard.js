/* Dashboard 视图:今日 KPI + 工具状态 + 最近项目 */

(function () {
  'use strict';

  var STAT_TEMPLATES = [
    { id: 'projects_total', label: '项目总数', kind: 'num' },
    { id: 'jobs_today', label: '今日 Job', kind: 'num' },
    { id: 'uploads_today', label: '今日上传', kind: 'num' },
    { id: 'success_rate', label: '成功率', kind: 'pct' },
    { id: 'cookie_fresh', label: 'Cookie 新鲜', kind: 'count' },
    { id: 'cookie_stale', label: 'Cookie 过期', kind: 'count' },
    { id: 'interactions_today', label: '今日互动', kind: 'count' },
    { id: 'total_interactions', label: '总互动', kind: 'count' },
  ];

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-dashboard' });

    var greeting = flow.el('h1', { class: 'view-title', text: '📊 Dashboard' });
    root.appendChild(greeting);

    var sub = flow.el('p', { class: 'view-sub', text: 'flow 平台总览 — KPI、工具状态、最近项目。' });
    root.appendChild(sub);

    // === 粘贴链接一键剪上传 ===
    var pasteCard = flow.el('div', { class: 'flow-card paste-card' });
    pasteCard.appendChild(flow.el('div', { class: 'paste-title', text: '🔗 粘贴视频链接一键剪切上传' }));
    pasteCard.appendChild(flow.el('div', { class: 'paste-desc', text: '支持 B站 / 抖音 / YouTube 等任意视频 URL。自动下载 → 80% 裁剪 → 上传。' }));
    var pasteRow = flow.el('div', { class: 'paste-row' });
    var pasteInput = flow.el('input', {
      type: 'text',
      class: 'form-input paste-input',
      placeholder: 'https://www.bilibili.com/video/BV1xxx 或 https://youtu.be/xxx',
      'data-paste-input': '',
    });
    var pasteBtn = flow.el('button', {
      class: 'btn btn-primary paste-btn',
      text: '去剪切 →',
      'data-paste-btn': '',
    });
    pasteRow.appendChild(pasteInput);
    pasteRow.appendChild(pasteBtn);
    pasteCard.appendChild(pasteRow);
    var pasteErr = flow.el('div', { class: 'paste-err muted', 'data-paste-err': '', text: '' });
    pasteCard.appendChild(pasteErr);
    pasteBtn.addEventListener('click', submitPaste);
    pasteInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') submitPaste();
    });
    function submitPaste() {
      var url = (pasteInput.value || '').trim();
      if (!/^https?:\/\//i.test(url)) {
        pasteErr.textContent = '请输入 http(s):// 开头的链接';
        pasteErr.classList.remove('muted');
        pasteErr.classList.add('err');
        return;
      }
      flow.navigate('new?tool=fengge_url&source_url=' + encodeURIComponent(url));
    }
    root.appendChild(pasteCard);

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
    loadStatsExtra();
    loadTools();
    loadRecent();
  }

  function loadStatsExtra() {
    // 异步加载 accounts + comments stats,合并到 KPI 网格
    Promise.all([
      flow.api('GET', '/api/accounts').catch(function () { return { ok: false }; }),
      flow.api('GET', '/api/comments/stats').catch(function () { return { ok: false }; }),
    ]).then(function (results) {
      var accounts = results[0].ok ? results[0].data.items : [];
      var cstats = results[1].ok ? results[1].data : {};
      var fresh = 0, stale = 0, missing = 0;
      accounts.forEach(function (a) {
        var f = a.cookie && a.cookie.freshness;
        if (f === 'fresh') fresh++;
        else if (f === 'stale' || f === 'expired') stale++;
        else if (f === 'missing') missing++;
      });
      var setVal = function (id, v) {
        var n = document.querySelector('[data-stat="' + id + '"]');
        if (n) n.textContent = String(v);
      };
      setVal('cookie_fresh', fresh);
      setVal('cookie_stale', stale);
      setVal('interactions_today', cstats.today_count || 0);
      setVal('total_interactions', cstats.total || 0);
    });
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
      fengge_url: '🔗',
      tiktok_story: '🎵',
      material_collector: '🎞️',
    }[toolId] || '⚙️';
  }

  flow.route(/^dashboard$/, view);
})();