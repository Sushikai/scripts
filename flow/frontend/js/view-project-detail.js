/* 项目详情视图:进度条 + 日志 tail + 产物 + 重跑/取消 */

(function () {
  'use strict';

  function view(match, host) {
    var projectId = match[1];
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-detail' });

    var head = flow.el('div', { class: 'view-head' });
    var back = flow.el('a', { class: 'back-link', href: '#projects', text: '← 返回项目列表' });
    head.appendChild(back);
    var title = flow.el('h1', { class: 'view-title', 'data-title': '', text: '加载中…' });
    head.appendChild(title);
    root.appendChild(head);

    var metaCard = flow.el('div', { class: 'flow-card' });
    metaCard.innerHTML = '<div class="meta-grid" data-meta-grid></div>';
    root.appendChild(metaCard);

    var paramsCard = flow.el('div', { class: 'flow-card' });
    paramsCard.appendChild(flow.el('h2', { class: 'card-title', text: '⚙️ 项目参数' }));
    var paramsBox = flow.el('pre', { class: 'json-box', 'data-params-box': '' });
    paramsBox.textContent = '加载中…';
    paramsCard.appendChild(paramsBox);
    root.appendChild(paramsCard);

    var stepsCard = flow.el('div', { class: 'flow-card' });
    stepsCard.appendChild(flow.el('h2', { class: 'card-title', text: '🪜 执行步骤' }));
    var stepsList = flow.el('div', { class: 'steps-list', 'data-steps-list': '' });
    stepsCard.appendChild(stepsList);
    root.appendChild(stepsCard);

    var logCard = flow.el('div', { class: 'flow-card' });
    logCard.appendChild(flow.el('h2', { class: 'card-title', text: '📋 实时日志' }));
    var logBox = flow.el('pre', { class: 'log-box', 'data-log-box': '' });
    logBox.textContent = '等待日志…';
    logCard.appendChild(logBox);
    root.appendChild(logCard);

    var artifactsCard = flow.el('div', { class: 'flow-card' });
    artifactsCard.appendChild(flow.el('h2', { class: 'card-title', text: '📦 产物汇总' }));
    var artifactsList = flow.el('div', { class: 'artifacts-list', 'data-artifacts-list': '' });
    artifactsList.innerHTML = '<div class="muted">暂无产物</div>';
    artifactsCard.appendChild(artifactsList);
    root.appendChild(artifactsCard);

    host.appendChild(root);

    var _logSse = null;
    var _pollTimer = null;

    function load() {
      flow.api('GET', '/api/projects/' + projectId).then(function (res) {
        if (!res.ok) { flow.toast('项目不存在', 'error'); return; }
        var p = res.data.project;
        var jobs = res.data.jobs || [];
        title.textContent = p.name + '  (' + p.tool_id + ')';

        var mg = metaCard.querySelector('[data-meta-grid]');
        var totalDuration = _sumDuration(jobs);
        mg.innerHTML = ''
          + _metaItem('工具', p.tool_id)
          + _metaItem('状态', '<span class="status status-' + p.status + '">' + p.status + '</span>')
          + _metaItem('创建', flow.fmtTime(p.created_at))
          + _metaItem('更新', flow.fmtTime(p.updated_at))
          + _metaItem('Job 数', jobs.length)
          + _metaItem('总耗时', totalDuration);

        var pb = document.querySelector('[data-params-box]');
        if (pb) {
          pb.textContent = JSON.stringify(p.params || {}, null, 2);
        }

        renderSteps(jobs);
        renderArtifacts(jobs);
      });
    }

    function renderSteps(jobs) {
      var sl = document.querySelector('[data-steps-list]');
      if (!sl) return;
      sl.innerHTML = '';
      if (!jobs.length) {
        sl.innerHTML = '<p class="muted">尚无 Job。</p>';
        return;
      }
      jobs.forEach(function (j) {
        var row = flow.el('div', { class: 'step-row' });
        var status = j.status || 'pending';
        row.appendChild(flow.el('span', { class: 'step-status step-status-' + status, text: _statusIcon(status) }));
        row.appendChild(flow.el('span', { class: 'step-name', text: j.step }));
        var bar = flow.el('div', { class: 'step-bar' });
        var fill = flow.el('div', { class: 'step-fill', 'data-fill': j.id });
        fill.style.width = ((j.progress || 0) * 100) + '%';
        bar.appendChild(fill);
        row.appendChild(bar);
        row.appendChild(flow.el('span', { class: 'step-pct', text: ((j.progress || 0) * 100).toFixed(0) + '%' }));

        // 时长
        var dur = _duration(j.started_at, j.finished_at);
        if (dur) row.appendChild(flow.el('span', { class: 'step-dur muted', text: dur }));

        var actions = flow.el('span', { class: 'step-actions' });
        if (status === 'pending' || status === 'running') {
          var cancel = flow.el('button', {
            class: 'btn-mini btn-danger',
            text: '取消',
            on: { click: function () { cancelJob(j.id); } },
          });
          actions.appendChild(cancel);
        } else {
          var rerun = flow.el('button', {
            class: 'btn-mini',
            text: '重跑',
            on: { click: function () { rerunJob(j); } },
          });
          actions.appendChild(rerun);
        }
        row.appendChild(actions);
        sl.appendChild(row);

        // 错误行
        if (j.error) {
          var errRow = flow.el('div', { class: 'step-error' });
          errRow.textContent = '✗ ' + j.error;
          sl.appendChild(errRow);
        }
      });
    }

    function renderArtifacts(jobs) {
      var list = document.querySelector('[data-artifacts-list]');
      if (!list) return;
      list.innerHTML = '';
      var count = 0;
      jobs.forEach(function (j) {
        var arts = j.artifacts || {};
        Object.keys(arts).forEach(function (k) {
          count++;
          var v = arts[k];
          var row = flow.el('div', { class: 'artifact-row' });
          var kEl = flow.el('span', { class: 'artifact-key', text: j.step + ' · ' + k });
          var vEl = flow.el('span', { class: 'artifact-val' });
          if (typeof v === 'string' && (v.startsWith('/') || v.startsWith('http'))) {
            vEl.appendChild(flow.el('span', { class: 'artifact-path', text: v }));
            var copyBtn = flow.el('button', {
              class: 'btn-mini',
              text: '复制',
              on: { click: (function (val) {
                return function () {
                  try {
                    navigator.clipboard.writeText(val);
                    flow.toast('已复制', 'ok');
                  } catch (e) {
                    flow.toast('复制失败', 'error');
                  }
                };
              })(v) },
            });
            vEl.appendChild(copyBtn);
          } else {
            vEl.textContent = JSON.stringify(v);
          }
          row.appendChild(kEl);
          row.appendChild(vEl);
          list.appendChild(row);
        });
      });
      if (!count) list.innerHTML = '<div class="muted">尚无产物</div>';
    }

    function cancelJob(jid) {
      flow.api('POST', '/api/job/' + cid_encode(jid) + '/cancel').then(function (res) {
        if (res.ok) flow.toast('已取消', 'ok');
        else flow.toast('取消失败', 'error');
        load();
      });
    }

    function rerunJob(j) {
      flow.api('POST', '/api/jobs', {
        tool_id: 'ignored', // 由后端从 project 读
        project_id: projectId,
        step: j.step,
        params: {},
      }).then(function (res) {
        if (res.ok) {
          flow.toast(j.step + ' 已重跑', 'ok');
          load();
        } else {
          flow.toast('重跑失败', 'error');
        }
      });
    }

    function startLog() {
      // 暂不接 SSE(后端 /api/project/{id}/log 待实装),降级轮询 + 显示 server 实时日志
      flow.api('GET', '/api/log/recent?limit=30').then(function (res) {
        var box = document.querySelector('[data-log-box]');
        if (box && res.ok) {
          var lines = (res.data.lines || []).map(function (l) {
            return l.method + ' ' + l.path + ' ' + l.status + ' (' + (l.duration_ms || 0) + 'ms)';
          });
          box.textContent = lines.join('\n') || '暂无 access log';
        }
      }).catch(function () {});
      _pollTimer = setInterval(load, 2000);
      load();
    }

    function cid_encode(jid) { return encodeURIComponent(jid); }

    function _metaItem(k, v) {
      return '<div class="meta-item"><span class="meta-key">' + flow.escapeHtml(k) + '</span><span class="meta-val">' + v + '</span></div>';
    }
    function _statusIcon(s) {
      return { pending: '⏳', running: '▶', done: '✓', failed: '✗', cancelled: '⊘' }[s] || '?';
    }
    function _duration(start, end) {
      if (!start) return '';
      var s = end || Date.now();
      var sec = Math.round((s - start) / 1000);
      if (sec < 60) return sec + 's';
      if (sec < 3600) return Math.floor(sec / 60) + 'm' + (sec % 60) + 's';
      return Math.floor(sec / 3600) + 'h' + Math.floor((sec % 3600) / 60) + 'm';
    }
    function _sumDuration(jobs) {
      var total = 0;
      jobs.forEach(function (j) {
        if (j.started_at && j.finished_at) total += (j.finished_at - j.started_at);
      });
      if (!total) return '—';
      return _duration(0, total);
    }

    startLog();

    return {
      name: 'project-detail',
      leave: function () {
        if (_logSse) _logSse.close();
        if (_pollTimer) clearInterval(_pollTimer);
      },
      enter: function () { startLog(); },
    };
  }

  flow.route(/^projects\/([\w-]+)$/, view);
})();