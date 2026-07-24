/* Comments 视图:账号健康 + 互动统计 + 转化漏斗 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-comments' });

    root.appendChild(flow.el('h1', { class: 'view-title', text: '💬 评论 / 回复' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'B 站多账号 cookie 健康 + fan_hunter 互动数据 + 转化漏斗。' }));

    // === 账号健康 ===
    var accountsCard = flow.el('div', { class: 'flow-card' });
    accountsCard.appendChild(flow.el('h2', { class: 'card-title', text: '👤 账号 cookie 健康' }));
    var accountsGrid = flow.el('div', { class: 'accounts-grid', 'data-accounts-grid': '' });
    accountsGrid.innerHTML = '<div class="muted">加载中…</div>';
    accountsCard.appendChild(accountsGrid);
    root.appendChild(accountsCard);

    // === 互动 KPI ===
    var kpiCard = flow.el('div', { class: 'flow-card' });
    kpiCard.appendChild(flow.el('h2', { class: 'card-title', text: '📊 互动统计' }));
    var kpiGrid = flow.el('div', { class: 'kpi-grid', 'data-kpi-grid': '' });
    kpiCard.appendChild(kpiGrid);
    root.appendChild(kpiCard);

    // === 转化 ===
    var convCard = flow.el('div', { class: 'flow-card' });
    convCard.appendChild(flow.el('h2', { class: 'card-title', text: '🎯 转化漏斗' }));
    var convBody = flow.el('div', { 'data-conv-body': '', class: 'conv-body' });
    convCard.appendChild(convBody);
    root.appendChild(convCard);

    // === Top 视频 ===
    var videosCard = flow.el('div', { class: 'flow-card' });
    videosCard.appendChild(flow.el('h2', { class: 'card-title', text: '🏆 互动 Top 10 视频' }));
    var videosTbl = flow.el('table', { class: 'flow-table', 'data-videos-tbl': '' });
    videosTbl.innerHTML = '<thead><tr><th>#</th><th>BV 号</th><th>标题</th><th>互动数</th></tr></thead><tbody><tr><td colspan="4" class="muted">加载中…</td></tr></tbody>';
    videosCard.appendChild(videosTbl);
    root.appendChild(videosCard);

    // === 最近动作 ===
    var recentCard = flow.el('div', { class: 'flow-card' });
    recentCard.appendChild(flow.el('h2', { class: 'card-title', text: '🕒 最近动作 (最新 50)' }));
    var recentTbl = flow.el('table', { class: 'flow-table', 'data-recent-tbl': '' });
    recentTbl.innerHTML = '<thead><tr><th>时间</th><th>动作</th><th>用户</th><th>视频</th></tr></thead><tbody><tr><td colspan="4" class="muted">加载中…</td></tr></tbody>';
    recentCard.appendChild(recentTbl);
    root.appendChild(recentCard);

    // === Top 互动用户 (R19) ===
    var topUsersCard = flow.el('div', { class: 'flow-card' });
    topUsersCard.appendChild(flow.el('h2', { class: 'card-title', text: '👥 Top 20 互动用户 (按 fan_hunter 目标聚合)' }));
    var topUsersTbl = flow.el('table', { class: 'flow-table', 'data-topusers-tbl': '' });
    topUsersTbl.innerHTML = '<thead><tr><th>#</th><th>用户</th><th>UID</th><th>👍</th><th>💬</th><th>➕</th><th>📩</th><th>视频</th><th>首次</th><th>最近</th></tr></thead><tbody><tr><td colspan="9" class="muted">加载中…</td></tr></tbody>';
    topUsersCard.appendChild(topUsersTbl);
    root.appendChild(topUsersCard);

    host.appendChild(root);

    refreshAll();

    return {
      name: 'comments',
      leave: function () { /* noop */ },
      enter: function () { refreshAll(); },
    };
  }

  function refreshAll() {
    loadAccounts();
    loadStats();
    loadConversion();
    loadTopUsers();
  }

  function loadTopUsers() {
    flow.api('GET', '/api/comments/by-target?limit=20').then(function (res) {
      var tbl = document.querySelector('[data-topusers-tbl]');
      if (!tbl || !res.ok) return;
      var tbody = tbl.querySelector('tbody');
      var items = res.data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="muted">暂无数据</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach(function (u, i) {
        var tr = document.createElement('tr');
        var fs = (u.first_seen || '').slice(5, 16).replace('T', ' ');
        var ls = (u.last_seen || '').slice(5, 16).replace('T', ' ');
        tr.innerHTML = '<td>' + (i + 1) + '</td>'
          + '<td><strong>' + flow.escapeHtml(u.uname) + '</strong></td>'
          + '<td class="mono muted">' + flow.escapeHtml(u.uid) + '</td>'
          + '<td>' + u.likes + '</td>'
          + '<td>' + u.replies + '</td>'
          + '<td>' + u.follows + '</td>'
          + '<td>' + u.dms + '</td>'
          + '<td>' + u.video_count + '</td>'
          + '<td class="muted mono">' + flow.escapeHtml(fs) + '</td>'
          + '<td class="muted mono">' + flow.escapeHtml(ls) + '</td>';
        tbody.appendChild(tr);
      });
    });
  }

  function loadAccounts() {
    flow.api('GET', '/api/accounts').then(function (res) {
      var grid = document.querySelector('[data-accounts-grid]');
      if (!grid || !res.ok) return;
      grid.innerHTML = '';
      (res.data.items || []).forEach(function (a) {
        var card = flow.el('div', { class: 'account-card status-' + a.status });
        var head = flow.el('div', { class: 'account-head' });
        head.appendChild(flow.el('span', { class: 'status-light status-' + a.status, text: '' }));
        head.appendChild(flow.el('span', { class: 'account-name', text: a.name }));
        var roleChip = flow.el('span', { class: 'chip', text: a.role === 'primary' ? '主账号' : '备用' });
        head.appendChild(roleChip);
        card.appendChild(head);
        var cookie = a.cookie || {};
        var rows = [
          ['状态', cookie.freshness === 'fresh' ? '新鲜' : cookie.freshness === 'stale' ? '即将过期' : cookie.freshness === 'expired' ? '已过期' : '缺失'],
          ['Cookie 数', cookie.cookie_count || 0],
          ['文件大小', formatBytes(cookie.size_bytes || 0)],
          ['更新于', cookie.age_days != null ? (cookie.age_days + ' 天前') : '—'],
        ];
        rows.forEach(function (r) {
          var row = flow.el('div', { class: 'kv-row' });
          row.appendChild(flow.el('span', { class: 'kv-key', text: r[0] }));
          row.appendChild(flow.el('span', { class: 'kv-val', text: String(r[1]) }));
          card.appendChild(row);
        });
        card.appendChild(flow.el('div', { class: 'account-path muted', text: a.cookie_path }));
        grid.appendChild(card);
      });
    });
  }

  function loadStats() {
    flow.api('GET', '/api/comments/stats').then(function (res) {
      var grid = document.querySelector('[data-kpi-grid]');
      if (!grid || !res.ok) return;
      grid.innerHTML = '';
      var d = res.data;
      var kpis = [
        { label: '总互动', value: d.total, kind: 'num' },
        { label: '今日互动', value: d.today_count, kind: 'num' },
        { label: 'Top 视频数', value: (d.top_videos || []).length, kind: 'num' },
        { label: '活跃天数', value: (d.by_day || []).length, kind: 'num' },
      ];
      kpis.forEach(function (k) {
        var c = flow.el('div', { class: 'kpi-card' });
        c.appendChild(flow.el('div', { class: 'kpi-label', text: k.label }));
        c.appendChild(flow.el('div', { class: 'kpi-value', text: k.value }));
        grid.appendChild(c);
      });

      // 渲染 by_day mini bar
      var dayCard = flow.el('div', { class: 'kpi-card kpi-wide' });
      dayCard.appendChild(flow.el('div', { class: 'kpi-label', text: '近 7 天' }));
      var dayBar = flow.el('div', { class: 'day-bar' });
      var max = Math.max.apply(null, (d.by_day || []).map(function (x) { return x.count; }).concat([1]));
      (d.by_day || []).forEach(function (x) {
        var bar = flow.el('div', { class: 'day-col' });
        bar.appendChild(flow.el('div', { class: 'day-fill', style: 'height:' + (x.count / max * 80) + 'px' }));
        bar.appendChild(flow.el('div', { class: 'day-num', text: x.count }));
        bar.appendChild(flow.el('div', { class: 'day-lbl muted', text: x.date.slice(5) }));
        dayBar.appendChild(bar);
      });
      dayCard.appendChild(dayBar);
      grid.appendChild(dayCard);

      // Top 视频表格
      var tbl = document.querySelector('[data-videos-tbl]');
      if (tbl) {
        var tbody = tbl.querySelector('tbody');
        if (!(d.top_videos || []).length) {
          tbody.innerHTML = '<tr><td colspan="4" class="muted">暂无数据</td></tr>';
        } else {
          tbody.innerHTML = '';
          d.top_videos.forEach(function (v, i) {
            var tr = document.createElement('tr');
            tr.innerHTML = '<td>' + (i + 1) + '</td>'
              + '<td><span class="chip">' + flow.escapeHtml(v.bvid) + '</span></td>'
              + '<td>' + flow.escapeHtml(v.title || '—') + '</td>'
              + '<td><strong>' + v.count + '</strong></td>';
            tbody.appendChild(tr);
          });
        }
      }
    });

    // 最近动作单独取
    flow.api('GET', '/api/comments/actions?limit=50').then(function (res) {
      var tbl = document.querySelector('[data-recent-tbl]');
      if (!tbl || !res.ok) return;
      var tbody = tbl.querySelector('tbody');
      if (!(res.data.items || []).length) {
        tbody.innerHTML = '<tr><td colspan="4" class="muted">暂无动作记录</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      res.data.items.forEach(function (a) {
        var tr = document.createElement('tr');
        var ts = (a.timestamp || '').slice(0, 19).replace('T', ' ');
        tr.innerHTML = '<td class="muted">' + flow.escapeHtml(ts) + '</td>'
          + '<td><span class="chip chip-action chip-action-' + flow.escapeHtml(a.action || '?') + '">'
          + flow.escapeHtml(a.action || '?') + '</span></td>'
          + '<td>' + flow.escapeHtml(a.uname || '?') + '</td>'
          + '<td>' + flow.escapeHtml((a.video_title || '').slice(0, 40)) + '</td>';
        tbody.appendChild(tr);
      });
    });
  }

  function loadConversion() {
    flow.api('GET', '/api/comments/conversion').then(function (res) {
      var body = document.querySelector('[data-conv-body]');
      if (!body || !res.ok) return;
      var d = res.data;
      var summary = d.summary || {};
      var perScript = summary.per_script || {};
      body.innerHTML = '';
      var total = flow.el('div', { class: 'conv-row' });
      total.appendChild(flow.el('div', { class: 'conv-key', text: '日期' }));
      total.appendChild(flow.el('div', { class: 'conv-val', text: summary.date || '—' }));
      body.appendChild(total);
      var rows = [
        ['总动作数', summary.total_actions || 0],
        ['总转化', summary.total_converted || 0],
        ['总体转化率', (summary.overall_conversion_rate || 0).toFixed(4) + '%'],
        ['当前粉丝', summary.current_followers || 0],
      ];
      rows.forEach(function (r) {
        var row = flow.el('div', { class: 'conv-row' });
        row.appendChild(flow.el('div', { class: 'conv-key', text: r[0] }));
        row.appendChild(flow.el('div', { class: 'conv-val', text: String(r[1]) }));
        body.appendChild(row);
      });
      // per_script 表格
      var tbl = flow.el('table', { class: 'flow-table' });
      tbl.innerHTML = '<thead><tr><th>脚本</th><th>动作数</th><th>转化数</th><th>转化率</th></tr></thead><tbody></tbody>';
      var tbody = tbl.querySelector('tbody');
      Object.keys(perScript).forEach(function (k) {
        var s = perScript[k];
        var tr = document.createElement('tr');
        tr.innerHTML = '<td><span class="chip">' + flow.escapeHtml(k) + '</span></td>'
          + '<td>' + (s.actions || 0) + '</td>'
          + '<td>' + (s.converted || 0) + '</td>'
          + '<td>' + ((s.conversion_rate || 0) * 100).toFixed(2) + '%</td>';
        tbody.appendChild(tr);
      });
      body.appendChild(tbl);
    });
  }

  function formatBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1024 / 1024).toFixed(2) + ' MB';
  }

  flow.route(/^comments$/, view);
})();