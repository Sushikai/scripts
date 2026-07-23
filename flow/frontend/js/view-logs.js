/* 日志视图 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-logs' });
    root.appendChild(flow.el('h1', { class: 'view-title', text: '📋 日志' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: 'access.log 实时 tail(后端按需实现 SSE)。' }));

    var card = flow.el('div', { class: 'flow-card' });
    var box = flow.el('pre', { class: 'log-box', 'data-log-box': '' });
    box.textContent = '连接中…';
    card.appendChild(box);
    root.appendChild(card);

    host.appendChild(root);

    var sse = null;
    function start() {
      // 简单 SSE 流尝试;后端暂可降级为 /api/log/recent 返回最近 100 行
      try {
        sse = flow.sse('/api/log/stream', function (m) {
          if (m && m.line) {
            box.textContent += '\n' + m.line;
            box.scrollTop = box.scrollHeight;
          }
        });
      } catch (e) { /* ignore */ }

      // 首次拉快照
      flow.api('GET', '/api/log/recent').then(function (res) {
        if (res.ok && res.data && res.data.lines) {
          box.textContent = res.data.lines.join('\n');
          box.scrollTop = box.scrollHeight;
        } else {
          box.textContent = '日志 API 暂未实装(/api/log/recent + /api/log/stream)。';
        }
      });
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