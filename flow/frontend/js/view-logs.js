/* 日志视图:access.log 实时 tail + 解析 JSON + 过滤 + 统计 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-logs' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '📋 日志' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'access.log 实时 tail + 路径过滤 + 状态过滤 + 方法过滤。' }));

    // KPI
    var kpiCard = flow.el('div', { class: 'flow-card' });
    var kpiGrid = flow.el('div', { class: 'kpi-grid', 'data-kpi-grid': '' });
    kpiCard.appendChild(kpiGrid);
    root.appendChild(kpiCard);

    // 过滤栏
    var filterBar = flow.el('div', { class: 'filter-bar' });
    var pathInput = flow.el('input', { class: 'form-input', placeholder: '过滤路径(包含)', 'data-filter-path': '' });
    filterBar.appendChild(pathInput);
    var methodSel = flow.el('select', { class: 'form-input', 'data-filter-method': '' });
    methodSel.innerHTML = '<option value="">所有方法</option><option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>';
    filterBar.appendChild(methodSel);
    var statusSel = flow.el('select', { class: 'form-input', 'data-filter-status': '' });
    statusSel.innerHTML = '<option value="">所有状态</option><option value="2">2xx</option><option value="3">3xx</option><option value="4">4xx</option><option value="5">5xx</option>';
    filterBar.appendChild(statusSel);
    var clearBtn = flow.el('button', { class: 'btn-mini', text: '清屏', on: { click: function () { _buffer.length = 0; render(); } } });
    filterBar.appendChild(clearBtn);
    root.appendChild(filterBar);

    // 日志表
    var tbl = flow.el('table', { class: 'flow-table log-table', 'data-log-tbl': '' });
    tbl.innerHTML = '<thead><tr><th>时间</th><th>方法</th><th>路径</th><th>状态</th><th>耗时</th></tr></thead><tbody><tr><td colspan="5" class="muted">加载中…</td></tr></tbody>';
    root.appendChild(tbl);

    host.appendChild(root);

    var _buffer = [];   // 所有解析后的 log 对象
    var _filter = { path: '', method: '', status: '' };
    var _autoScroll = true;

    function applyFilter() {
      _filter.path = pathInput.value || '';
      _filter.method = methodSel.value || '';
      _filter.status = statusSel.value || '';
      render();
    }
    pathInput.addEventListener('input', applyFilter);
    methodSel.addEventListener('change', applyFilter);
    statusSel.addEventListener('change', applyFilter);

    function render() {
      var tbody = tbl.querySelector('tbody');
      var filtered = _buffer.filter(function (l) {
        if (_filter.path && (l.path || '').indexOf(_filter.path) === -1) return false;
        if (_filter.method && l.method !== _filter.method) return false;
        if (_filter.status && String(l.status || 0).charAt(0) !== _filter.status) return false;
        return true;
      });
      // 显示最近 200 条
      var show = filtered.slice(-200);
      tbody.innerHTML = '';
      if (!show.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted">暂无日志</td></tr>';
        return;
      }
      show.forEach(function (l) {
        var tr = document.createElement('tr');
        var sc = String(l.status || 0);
        var statusClass = sc.charAt(0) === '2' ? 'status-ok' : sc.charAt(0) === '4' || sc.charAt(0) === '5' ? 'status-bad' : '';
        tr.innerHTML = '<td class="muted mono">' + flow.fmtTime(l.ts) + '</td>'
          + '<td><span class="chip">' + flow.escapeHtml(l.method || '?') + '</span></td>'
          + '<td class="mono">' + flow.escapeHtml(l.path || '?') + '</td>'
          + '<td class="' + statusClass + '">' + (l.status || '?') + '</td>'
          + '<td class="muted mono">' + (l.duration_ms || '—') + 'ms</td>';
        tbody.appendChild(tr);
      });
      // KPI 更新
      var kg = document.querySelector('[data-kpi-grid]');
      if (kg) {
        var total = _buffer.length;
        var err = _buffer.filter(function (l) { var s = String(l.status || 0); return s.charAt(0) === '4' || s.charAt(0) === '5'; }).length;
        var avgMs = total > 0 ? Math.round(_buffer.reduce(function (s, l) { return s + (l.duration_ms || 0); }, 0) / total) : 0;
        kg.innerHTML = '';
        [{ label: '总条数', value: total }, { label: '错误数', value: err },
         { label: '平均耗时', value: avgMs + 'ms' }, { label: '显示中', value: show.length }].forEach(function (k) {
          var c = flow.el('div', { class: 'kpi-card' });
          c.appendChild(flow.el('div', { class: 'kpi-label', text: k.label }));
          c.appendChild(flow.el('div', { class: 'kpi-value', text: k.value }));
          kg.appendChild(c);
        });
      }
      // 自动滚到底
      if (_autoScroll) {
        var docTbl = tbl.parentElement;
        if (docTbl) docTbl.scrollTop = docTbl.scrollHeight;
      }
    }

    function parseLine(line) {
      try {
        var j = JSON.parse(line);
        return {
          ts: j.ts || j.start || 0,
          method: j.method || '?',
          path: j.path || '',
          status: j.status || 0,
          duration_ms: j.duration_ms || j.duration || 0,
        };
      } catch (e) {
        return null;
      }
    }

    var sse = null;
    function start() {
      // 先拉 snapshot
      flow.api('GET', '/api/log/recent?limit=500').then(function (res) {
        if (!res.ok) return;
        var raw = (res.data.lines || []);
        var parsed = raw.map(parseLine).filter(Boolean);
        _buffer = parsed;
        render();
      });
      try {
        sse = flow.sse('/api/log/stream', function (m) {
          if (!m || !m.line) return;
          var p = parseLine(m.line);
          if (p) {
            _buffer.push(p);
            // 限制 buffer 大小防止内存爆炸
            if (_buffer.length > 2000) _buffer = _buffer.slice(-1500);
            render();
          }
        });
      } catch (e) { /* ignore */ }
    }
    start();

    return {
      name: 'logs',
      leave: function () { if (sse) sse.close(); },
      enter: function () { start(); },
    };
  }

  flow.route(/^logs$/, view);
})();