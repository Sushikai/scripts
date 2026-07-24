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

    // 跨工具活动流
    var activityTitle = flow.el('h2', { class: 'view-section-title', text: '🌐 跨工具活动流 (最近 20)' });
    root.appendChild(activityTitle);
    var activityFeed = flow.el('div', { class: 'activity-feed', 'data-activity-feed': '' });
    activityFeed.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(activityFeed);

    // wrapper 运行统计
    var wrapperTitle = flow.el('h2', { class: 'view-section-title', text: '🛠 Wrapper 运行统计' });
    root.appendChild(wrapperTitle);
    var wrapperTbl = flow.el('table', { class: 'flow-table wrapper-table', 'data-wrapper-tbl': '' });
    wrapperTbl.innerHTML = '<thead><tr><th>工具</th><th>总数</th><th>成功</th><th>失败</th><th>取消</th><th>成功率</th><th>平均耗时</th></tr></thead><tbody><tr><td colspan="7" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(wrapperTbl);

    // 公网 / 局域网访问入口 (R9)
    var accessTitle = flow.el('h2', { class: 'view-section-title', text: '🌐 访问入口' });
    root.appendChild(accessTitle);
    var accessCard = flow.el('div', { class: 'access-card', 'data-access-card': '' });
    accessCard.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(accessCard);

    // 后台 cron 健康 (R10)
    var cronTitle = flow.el('h2', { class: 'view-section-title', text: '⏰ 后台 Cron 健康' });
    root.appendChild(cronTitle);
    var cronKpis = flow.el('div', { class: 'kpi-grid', 'data-cron-kpis': '' });
    cronKpis.innerHTML = '<div class="kpi-card"><div class="kpi-label">总数</div><div class="kpi-value">—</div></div>'
      + '<div class="kpi-card"><div class="kpi-label">运行中</div><div class="kpi-value">—</div></div>'
      + '<div class="kpi-card"><div class="kpi-label">已停止</div><div class="kpi-value">—</div></div>'
      + '<div class="kpi-card"><div class="kpi-label">非零退出</div><div class="kpi-value">—</div></div>';
    root.appendChild(cronKpis);
    var cronTbl = flow.el('table', { class: 'flow-table cron-table', 'data-cron-tbl': '' });
    cronTbl.innerHTML = '<thead><tr><th>任务</th><th>调度</th><th>状态</th><th>PID</th><th>退出码</th><th>最近日志</th></tr></thead><tbody><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(cronTbl);

    // Inbox — 跨子系统告警 (R11)
    var inboxTitle = flow.el('h2', { class: 'view-section-title', text: '📥 Inbox (待办告警)' });
    root.appendChild(inboxTitle);
    var inboxHost = flow.el('div', { class: 'inbox-list', 'data-inbox': '' });
    inboxHost.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(inboxHost);

    // 实时 JobRunner 队列 (R13)
    var queueTitle = flow.el('h2', { class: 'view-section-title', text: '⚡ 实时 Job 队列' });
    root.appendChild(queueTitle);
    var queueHost = flow.el('div', { class: 'queue-host', 'data-queue': '' });
    queueHost.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(queueHost);

    // 今日时间线 (R15)
    var todayTitle = flow.el('h2', { class: 'view-section-title', text: '📅 今日时间线 (按小时)' });
    root.appendChild(todayTitle);
    var todayHost = flow.el('div', { class: 'today-host', 'data-today': '' });
    todayHost.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(todayHost);

    // 磁盘用量 (R16)
    var storageTitle = flow.el('h2', { class: 'view-section-title', text: '💾 磁盘用量' });
    root.appendChild(storageTitle);
    var storageHost = flow.el('div', { class: 'storage-host', 'data-storage': '' });
    storageHost.innerHTML = '<div class="muted">加载中…</div>';
    root.appendChild(storageHost);

    // 自动刷新开关 + 状态指示 (R14)
    var refreshBar = flow.el('div', { class: 'refresh-bar', 'data-refresh-bar': '' });
    var refreshBtn = flow.el('button', {
      class: 'btn-mini refresh-btn',
      'data-refresh-btn': '',
      text: '⏸ 自动刷新已开启 (10s)',
    });
    var refreshStamp = flow.el('span', { class: 'muted mono refresh-stamp', 'data-refresh-stamp': '', text: '' });
    refreshBar.appendChild(refreshBtn);
    refreshBar.appendChild(refreshStamp);
    root.appendChild(refreshBar);

    host.appendChild(root);

    refreshDashboard();

    var autoTimer = null;
    function startAuto() {
      if (autoTimer) return;
      autoTimer = setInterval(function () { refreshDashboard(); }, 10000);
      refreshBtn.textContent = '⏸ 自动刷新已开启 (10s)';
    }
    function stopAuto() {
      if (autoTimer) clearInterval(autoTimer);
      autoTimer = null;
      refreshBtn.textContent = '▶ 自动刷新已暂停';
    }
    refreshBtn.addEventListener('click', function () {
      if (autoTimer) stopAuto(); else startAuto();
    });
    startAuto();

    return {
      name: 'dashboard',
      leave: function () { stopAuto(); },
      enter: function () { refreshDashboard(); startAuto(); },
    };
  }

  function loadToday() {
    flow.api('GET', '/api/today').then(function (res) {
      var host = document.querySelector('[data-today]');
      if (!host || !res.ok) return;
      var d = res.data;
      var hours = d.hours || [];
      var maxAct = Math.max(1, ...hours.map(function (h) { return h.jobs + h.uploads; }));
      var html = '<div class="today-summary">'
        + '<span>今日 jobs <strong>' + d.total_jobs + '</strong></span>'
        + '<span class="muted">·</span>'
        + '<span>上传 <strong>' + d.total_uploads + '</strong></span>'
        + '<span class="muted">·</span>'
        + '<span>cron 跑 <strong>' + d.cron_running + '</strong></span>'
        + (d.peak_hour ? '<span class="muted">· 高峰 ' + d.peak_hour + '</span>' : '')
        + '</div>';
      html += '<div class="today-grid">';
      hours.forEach(function (h) {
        var act = h.jobs + h.uploads;
        var pct = act > 0 ? Math.max(8, Math.round(act / maxAct * 100)) : 0;
        var sample = h.samples.slice(0, 1).map(function (s) { return s.name; }).join(' · ');
        html += '<div class="today-cell" style="height:' + pct + 'px" title="' + h.label + ' · ' + act + ' 个活动 — ' + flow.escapeHtml(sample) + '">'
          + '<span class="today-cell-lbl">' + h.label.split(':')[0] + '</span>'
          + '<span class="today-cell-val">' + act + '</span>'
          + '</div>';
      });
      html += '</div>';
      host.innerHTML = html;
    });
  }

  function refreshDashboard() {
    var stamp = document.querySelector('[data-refresh-stamp]');
    if (stamp) stamp.textContent = '更新于 ' + new Date().toLocaleTimeString();
    loadStats();
    loadStatsExtra();
    loadTools();
    loadRecent();
    loadActivityFeed();
    loadWrapperStats();
    loadTunnelStatus();
    loadCrons();
    loadInbox();
    loadQueue();
    loadToday();
    loadStorage();
  }

  function loadStorage() {
    Promise.all([
      flow.api('GET', '/api/storage/disk').catch(function () { return { ok: false }; }),
      flow.api('GET', '/api/storage').catch(function () { return { ok: false }; }),
    ]).then(function (res) {
      var host = document.querySelector('[data-storage]');
      if (!host) return;
      var html = '';
      if (res[0].ok && res[0].data && !res[0].data.error) {
        var d = res[0].data;
        var pctCls = d.pct >= 90 ? 'status-bad' : d.pct >= 80 ? 'status-warn' : '';
        html += '<div class="storage-disk">'
          + '<span>根分区</span>'
          + '<strong class="' + pctCls + '">' + d.pct + '%</strong>'
          + '<span class="muted">已用 ' + d.used_human + ' / 剩 ' + d.avail_human + '</span>'
          + '</div>';
      }
      if (res[1].ok) {
        var items = res[1].data.items || [];
        if (items.length) {
          html += '<div class="storage-summary muted">监控路径 共 ' + res[1].data.total_human + '</div>';
          items.slice(0, 8).forEach(function (it) {
            var top = (it.top_files || [])[0];
            html += '<div class="storage-row">'
              + '<span class="storage-name">' + flow.escapeHtml(it.name) + '</span>'
              + '<span class="mono">' + flow.escapeHtml(it.size_human) + '</span>'
              + (top ? '<span class="muted mono storage-top" title="' + flow.escapeHtml(top.path) + '">' + flow.escapeHtml(top.path.split('/').slice(-2).join('/')) + ' (' + flow.escapeHtml(top.size_human) + ')</span>' : '<span class="muted">' + (it.exists ? '' : '(不存在)') + '</span>')
              + '</div>';
          });
        }
      }
      host.innerHTML = html || '<div class="muted">磁盘信息读取失败</div>';
    });
  }

  function loadInbox() {
    flow.api('GET', '/api/inbox').then(function (res) {
      var host = document.querySelector('[data-inbox]');
      if (!host || !res.ok) return;
      var items = res.data.items || [];
      if (!items.length) {
        host.innerHTML = '<div class="muted">无待办 — 系统健康 ✨</div>';
        return;
      }
      host.innerHTML = '';
      items.forEach(function (it) {
        var row = flow.el('div', { class: 'inbox-row sev-' + it.severity + ' cat-' + it.category });
        row.appendChild(flow.el('span', { class: 'inbox-icon', text: it.severity === 'error' ? '⛔' : '⚠️' }));
        row.appendChild(flow.el('span', { class: 'inbox-cat', text: it.category }));
        var txt = flow.el('span', { class: 'inbox-text' });
        var title = flow.el('strong', { text: it.title });
        txt.appendChild(title);
        if (it.detail) txt.appendChild(flow.el('span', { class: 'inbox-detail muted', text: ' — ' + it.detail }));
        row.appendChild(txt);
        if (it.href) {
          var a = flow.el('a', { href: it.href, class: 'inbox-link mono', text: '→' });
          row.appendChild(a);
        }
        host.appendChild(row);
      });
    });
  }

  function loadQueue() {
    flow.api('GET', '/api/queue').then(function (res) {
      var host = document.querySelector('[data-queue]');
      if (!host || !res.ok) return;
      var d = res.data;
      var utilPct = Math.round(d.utilization * 100);
      var html = '<div class="queue-bar"><span class="muted">Worker 利用率</span>'
        + '<strong>' + d.inflight_count + '</strong> / ' + d.max_concurrent
        + ' <span class="muted">(' + utilPct + '%)</span></div>'
        + '<div class="queue-bar"><span class="muted">DB 待处理</span> <strong>' + d.pending_count + '</strong>'
        + ' <span class="muted">· 内存队列深度</span> <strong>' + d.queue_depth + '</strong></div>';
      if (d.inflight.length) {
        html += '<div class="queue-section-title">运行中</div>';
        d.inflight.forEach(function (j) {
          var pctTxt = Math.round((j.progress || 0) * 100) + '%';
          html += '<div class="queue-row"><span class="mono">' + flow.escapeHtml(j.step || '?') + '</span>'
            + '<a href="#projects/' + flow.escapeHtml(j.project_id || '') + '" class="queue-proj">' + flow.escapeHtml(j.project_name || '?') + '</a>'
            + '<div class="queue-bar-mini"><div class="queue-bar-fill" style="width:' + pctTxt + '"></div></div>'
            + '<span class="mono">' + pctTxt + '</span></div>';
        });
      }
      if (d.pending.length) {
        html += '<div class="queue-section-title">待启动</div>';
        d.pending.slice(0, 8).forEach(function (j) {
          html += '<div class="queue-row"><span class="mono">' + flow.escapeHtml(j.step || '?') + '</span>'
            + '<a href="#projects/' + flow.escapeHtml(j.project_id || '') + '" class="queue-proj">' + flow.escapeHtml(j.project_name || '?') + '</a>'
            + '<span class="muted mono">queued</span></div>';
        });
      }
      if (!d.inflight.length && !d.pending.length) {
        html += '<div class="muted">队列空闲</div>';
      }
      host.innerHTML = html;
    });
  }

  function loadCrons() {
    Promise.all([
      flow.api('GET', '/api/crons/summary').catch(function () { return { ok: false }; }),
      flow.api('GET', '/api/crons').catch(function () { return { ok: false }; }),
    ]).then(function (res) {
      // KPI
      var kpiHost = document.querySelector('[data-cron-kpis]');
      if (kpiHost && res[0].ok) {
        var d = res[0].data;
        var cards = kpiHost.querySelectorAll('.kpi-value');
        if (cards.length >= 4) {
          cards[0].textContent = d.total;
          cards[1].textContent = d.running;
          cards[2].textContent = d.stopped;
          cards[3].textContent = d.failed_exit;
          cards[3].className = 'kpi-value ' + (d.failed_exit > 0 ? 'status-bad' : '');
        }
      }
      // 表格
      var tbl = document.querySelector('[data-cron-tbl]');
      if (!tbl || !res[1].ok) return;
      var tbody = tbl.querySelector('tbody');
      var items = res[1].data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">无可监控 cron</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach(function (c) {
        var tr = document.createElement('tr');
        var statusCls = c.running ? 'status-ok' : (c.last_status !== '-' && c.last_status !== '0' ? 'status-bad' : 'status-warn');
        var statusTxt = c.running ? 'running' : (c.last_status !== '-' ? 'exit ' + c.last_status : 'stopped');
        var tail = (c.stdout_tail || c.stderr_tail || '').split('\n').pop();
        tr.innerHTML = '<td><strong>' + flow.escapeHtml(c.label) + '</strong>'
          + (c.keep_alive ? ' <span class="chip">KA</span>' : '')
          + (c.run_at_load ? ' <span class="chip">RAL</span>' : '')
          + '</td>'
          + '<td class="mono">' + flow.escapeHtml(c.schedule) + '</td>'
          + '<td class="' + statusCls + '">' + statusTxt + '</td>'
          + '<td class="mono">' + flow.escapeHtml(c.pid || '-') + '</td>'
          + '<td class="mono">' + flow.escapeHtml(c.last_status || '-') + '</td>'
          + '<td class="muted mono path">' + flow.escapeHtml(tail.slice(0, 80)) + '</td>';
        tbody.appendChild(tr);
      });
    });
  }

  function loadTunnelStatus() {
    flow.api('GET', '/api/tunnel-status').then(function (res) {
      var card = document.querySelector('[data-access-card]');
      if (!card || !res.ok) return;
      var d = res.data || {};
      var stateCls = d.state === 'online' ? 'status-ok' : 'status-bad';
      var html = '<div class="access-row">'
        + '<span class="status-light ' + stateCls + '"></span>'
        + '<span class="access-key">公网隧道</span>'
        + '<span class="access-val mono">' + (d.url ? '<a href="' + flow.escapeHtml(d.url) + '" target="_blank" rel="noopener">' + flow.escapeHtml(d.url) + '</a>' : '<span class="muted">离线</span>') + '</span>'
        + '<span class="muted mono">' + flow.escapeHtml(d.method || '') + '</span>'
        + '</div>';
      html += '<div class="access-row">'
        + '<span class="status-light ' + (d.lan_ip ? 'status-ok' : 'status-bad') + '"></span>'
        + '<span class="access-key">局域网</span>'
        + '<span class="access-val mono">' + (d.lan_url ? '<a href="' + flow.escapeHtml(d.lan_url) + '" target="_blank" rel="noopener">' + flow.escapeHtml(d.lan_url) + '</a>' : '<span class="muted">未知</span>') + '</span>'
        + '<span class="muted mono">' + flow.escapeHtml(d.hostname || '') + '</span>'
        + '</div>';
      if (d.process_patterns && d.process_patterns.length) {
        html += '<div class="access-row"><span class="access-key muted">后台进程</span><span class="access-val mono muted">' + d.process_patterns.map(flow.escapeHtml).join(' / ') + '</span></div>';
      }
      card.innerHTML = html;
    }).catch(function () {
      var card = document.querySelector('[data-access-card]');
      if (card) card.innerHTML = '<div class="muted">访问入口读取失败</div>';
    });
  }

  function loadWrapperStats() {
    flow.api('GET', '/api/wrapper-stats?days=30').then(function (res) {
      var tbl = document.querySelector('[data-wrapper-tbl]');
      if (!tbl || !res.ok) return;
      var tbody = tbl.querySelector('tbody');
      var items = res.data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="muted">尚无运行数据</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach(function (s) {
        var tr = document.createElement('tr');
        var rate = (s.success_rate * 100).toFixed(1) + '%';
        var rateClass = s.success_rate >= 0.9 ? 'status-ok' : s.success_rate >= 0.5 ? 'status-warn' : 'status-bad';
        tr.innerHTML = '<td><strong>' + flow.escapeHtml(s.tool_id) + '</strong></td>'
          + '<td>' + s.total + '</td>'
          + '<td>' + s.done + '</td>'
          + '<td>' + (s.failed || 0) + '</td>'
          + '<td>' + (s.cancelled || 0) + '</td>'
          + '<td class="' + rateClass + '">' + rate + '</td>'
          + '<td class="muted mono">' + (s.avg_duration_ms ? (s.avg_duration_ms + 'ms') : '—') + '</td>';
        tbody.appendChild(tr);
      });
    });
  }

  function loadActivityFeed() {
    // 并发拉 3 个数据源,合并按时间倒序
    Promise.all([
      flow.api('GET', '/api/projects?limit=8').catch(function () { return { ok: false }; }),
      flow.api('GET', '/api/comments/actions?limit=12').catch(function () { return { ok: false }; }),
      flow.api('GET', '/api/jobs?limit=8').catch(function () { return { ok: false }; }),
    ]).then(function (r) {
      var feed = [];
      if (r[0].ok) (r[0].data.items || []).forEach(function (p) {
        feed.push({ ts: p.updated_at || p.created_at, kind: 'project', icon: '📦', text: p.name + ' · ' + p.status, href: '#projects/' + p.id });
      });
      if (r[1].ok) (r[1].data.items || []).forEach(function (a) {
        feed.push({ ts: a.timestamp ? new Date(a.timestamp).getTime() : Date.now(), kind: 'action', icon: a.action === 'like' ? '👍' : '💬', text: (a.uname || '?') + ' · ' + (a.video_title || '').slice(0, 30), href: '#comments' });
      });
      if (r[2].ok) (r[2].data.items || []).forEach(function (j) {
        feed.push({ ts: j.started_at || j.finished_at || Date.now(), kind: 'job', icon: '⚙️', text: j.step + ' · ' + j.status, href: '#projects/' + j.project_id });
      });
      feed.sort(function (a, b) { return b.ts - a.ts; });
      feed = feed.slice(0, 20);

      var host = document.querySelector('[data-activity-feed]');
      if (!host) return;
      host.innerHTML = '';
      if (!feed.length) {
        host.innerHTML = '<div class="muted">暂无活动</div>';
        return;
      }
      feed.forEach(function (f) {
        var row = flow.el('div', { class: 'activity-row' });
        row.appendChild(flow.el('span', { class: 'activity-icon', text: f.icon }));
        var text = flow.el('span', { class: 'activity-text' });
        if (f.href) {
          var a = flow.el('a', { href: f.href, text: f.text });
          text.appendChild(a);
        } else {
          text.textContent = f.text;
        }
        row.appendChild(text);
        row.appendChild(flow.el('span', { class: 'activity-ts muted', text: flow.fmtTime(f.ts) }));
        host.appendChild(row);
      });
    });
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