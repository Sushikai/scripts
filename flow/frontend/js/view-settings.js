/* 设置视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-settings' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '⚙️ 设置' }));

    var card1 = flow.el('div', { class: 'flow-card' });
    card1.appendChild(flow.el('h2', { class: 'card-title', text: '🌐 隧道' }));
    var t1 = flow.el('div', { class: 'kv-row' });
    t1.innerHTML = '<span class="kv-key">tunnel URL</span><span class="kv-val mono" data-tunnel>—</span>';
    card1.appendChild(t1);
    root.appendChild(card1);

    var card2 = flow.el('div', { class: 'flow-card' });
    card2.appendChild(flow.el('h2', { class: 'card-title', text: '🤖 AI 模型' }));
    var t2 = flow.el('div', { class: 'kv-row' });
    t2.innerHTML = '<span class="kv-key">默认</span><span class="kv-val mono" data-ai-model>—</span>';
    card2.appendChild(t2);
    root.appendChild(card2);

    var card3 = flow.el('div', { class: 'flow-card' });
    card3.appendChild(flow.el('h2', { class: 'card-title', text: '🩺 健康' }));
    var h = flow.el('div', { class: 'kv-row' });
    h.innerHTML = '<span class="kv-key">cache stats</span><span class="kv-val mono" data-cache>—</span>';
    card3.appendChild(h);
    root.appendChild(card3);

    // 视频脚本清单
    var scriptsCard = flow.el('div', { class: 'flow-card' });
    scriptsCard.appendChild(flow.el('h2', { class: 'card-title', text: '📜 视频脚本清单 (点击查看详情)' }));
    var scriptsTbl = flow.el('table', { class: 'flow-table', 'data-scripts-tbl': '' });
    scriptsTbl.innerHTML = '<thead><tr><th>脚本</th><th>类型</th><th>路径</th><th>状态</th><th>大小</th><th>mtime</th></tr></thead><tbody><tr><td colspan="6" class="muted">加载中…</td></tr></tbody>';
    scriptsCard.appendChild(scriptsTbl);
    root.appendChild(scriptsCard);

    // 详情面板
    var detailCard = flow.el('div', { class: 'flow-card hidden', 'data-script-detail': '' });
    detailCard.appendChild(flow.el('h2', { class: 'card-title', 'data-detail-title': '', text: '' }));
    var detailBody = flow.el('div', { 'data-detail-body': '' });
    detailCard.appendChild(detailBody);
    root.appendChild(detailCard);

    host.appendChild(root);

    flow.api('GET', '/api/health').then(function (res) {
      var v = res.data || {};
      var ai = document.querySelector('[data-ai-model]');
      if (ai) ai.textContent = v.ai_default || '(default)';
      var cache = document.querySelector('[data-cache]');
      if (cache) cache.textContent = JSON.stringify(v.cache || {});
      var tunnel = document.querySelector('[data-tunnel]');
      if (tunnel) tunnel.textContent = v.tunnel || '未配置';
    });

    loadScripts();

    function loadScripts() {
      flow.api('GET', '/api/scripts').then(function (res) {
        var tbl = document.querySelector('[data-scripts-tbl]');
        if (!tbl || !res.ok) return;
        var tbody = tbl.querySelector('tbody');
        var items = res.data.items || [];
        if (!items.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无</td></tr>';
          return;
        }
        tbody.innerHTML = '';
        items.forEach(function (s) {
          var tr = document.createElement('tr');
          tr.style.cursor = 'pointer';
          tr.dataset.scriptName = s.name;
          var statusChip = '<span class="chip status-' + (s.exists ? 'ok' : 'bad') + '">'
            + (s.exists ? '✓ ' + s.kind : '✗ 缺失') + '</span>';
          tr.innerHTML = '<td><strong>' + flow.escapeHtml(s.name) + '</strong></td>'
            + '<td><span class="chip">' + flow.escapeHtml(s.category || '?') + '</span></td>'
            + '<td class="mono path">' + flow.escapeHtml(s.path) + '</td>'
            + '<td>' + statusChip + '</td>'
            + '<td class="mono muted">' + (s.size_human || '—') + '</td>'
            + '<td class="muted">' + flow.fmtTime(s.mtime) + '</td>';
          tr.addEventListener('click', function () { showScriptDetail(s.name); });
          tbody.appendChild(tr);
        });
      });
    }

    function showScriptDetail(name) {
      var card = document.querySelector('[data-script-detail]');
      var titleEl = document.querySelector('[data-detail-title]');
      var body = document.querySelector('[data-detail-body]');
      if (!card || !body) return;
      card.classList.remove('hidden');
      body.innerHTML = '<div class="muted">加载中…</div>';
      flow.api('GET', '/api/scripts/' + encodeURIComponent(name)).then(function (res) {
        if (!res.ok) {
          body.innerHTML = '<div class="muted">读取失败</div>';
          return;
        }
        var d = res.data;
        titleEl.textContent = '🔍 ' + d.name + ' (' + (d.category || '?') + ')';
        var html = '<div class="kv-row"><span class="kv-key">路径</span><span class="kv-val mono path">' + flow.escapeHtml(d.path) + '</span></div>';
        html += '<div class="kv-row"><span class="kv-key">存在</span><span class="kv-val">' + (d.exists ? '✓' : '✗') + '</span></div>';
        if (d.size_bytes != null) {
          html += '<div class="kv-row"><span class="kv-key">大小</span><span class="kv-val mono">' + d.size_bytes + ' B</span></div>';
        }
        if (d.imports && d.imports.length) {
          html += '<div class="kv-row"><span class="kv-key">依赖</span><span class="kv-val mono">' + d.imports.map(flow.escapeHtml).join(' · ') + '</span></div>';
        }
        if (d.git_log && d.git_log.length) {
          html += '<div class="detail-section">📜 Git 最近 5 次提交</div>';
          d.git_log.forEach(function (g) {
            html += '<div class="detail-row"><span class="mono detail-sha">' + flow.escapeHtml(g.sha) + '</span>'
              + '<span class="muted mono">' + flow.escapeHtml(g.ts) + '</span>'
              + '<span>' + flow.escapeHtml(g.message) + '</span></div>';
          });
        } else {
          html += '<div class="muted">无 git 历史</div>';
        }
        if (d.last_log_line) {
          html += '<div class="detail-section">📄 末行日志</div>';
          html += '<div class="detail-log mono">' + flow.escapeHtml(d.last_log_line) + '</div>';
        }
        body.innerHTML = html;
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }

    return { name: 'settings', leave: function () {}, enter: function () {} };
  }

  flow.route(/^settings$/, view);
})();