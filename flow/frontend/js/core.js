/* flow core.js:hash router + api 抽象 + SSE 客户端 + 通用工具 */

(function (global) {
  'use strict';

  // === API 抽象 ===
  async function api(method, path, body, opts) {
    opts = opts || {};
    var headers = { 'X-Trace-Id': _genTraceId() };
    var traceId = sessionStorage.getItem('flow-trace');
    if (traceId) headers['X-Trace-Id'] = traceId;
    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    var url = path.startsWith('http') ? path : (path.startsWith('/') ? path : '/' + path);
    var ctl = opts.signal || null;
    var res;
    try {
      res = await fetch(url, { method: method, headers: headers, body: body, signal: ctl });
    } catch (e) {
      return { ok: false, error: { code: 'NETWORK', message: String(e) } };
    }
    var txt = await res.text();
    var json = null;
    try { json = txt ? JSON.parse(txt) : null; } catch (e) { /* ignore */ }
    if (json && typeof json.ok === 'boolean') {
      if (res.headers.get('X-Trace-Id')) {
        sessionStorage.setItem('flow-trace', res.headers.get('X-Trace-Id'));
      }
      return json;
    }
    return {
      ok: res.ok,
      data: json,
      error: { code: 'HTTP_' + res.status, message: txt.slice(0, 200) },
      status: res.status,
    };
  }

  function _genTraceId() {
    var t = '';
    for (var i = 0; i < 8; i++) t += Math.floor(Math.random() * 16).toString(16);
    return t;
  }

  // === SSE 客户端 ===
  function sse(path, onMsg, opts) {
    opts = opts || {};
    var closed = false;
    var es = null;
    var retryTimer = null;
    var retryDelay = 1000;

    function connect() {
      if (closed) return;
      var headers = { 'X-Trace-Id': _genTraceId() };
      var traceId = sessionStorage.getItem('flow-trace');
      if (traceId) headers['X-Trace-Id'] = traceId;
      try {
        es = new EventSource(path);
      } catch (e) {
        if (opts.onError) opts.onError(e);
        scheduleReconnect();
        return;
      }
      es.onmessage = function (ev) {
        retryDelay = 1000; // 重置退避
        try {
          var data = JSON.parse(ev.data);
          onMsg(data);
        } catch (e) {
          onMsg({ type: 'raw', data: ev.data });
        }
      };
      es.onerror = function () {
        if (closed) return;
        if (es) es.close();
        if (opts.onError) opts.onError(new Error('SSE lost'));
        scheduleReconnect();
      };
    }

    function scheduleReconnect() {
      if (closed) return;
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    }

    connect();

    return {
      close: function () {
        closed = true;
        if (retryTimer) clearTimeout(retryTimer);
        if (es) es.close();
      },
    };
  }

  // === 路由 ===
  var _routes = [];
  var _currentView = null;

  function route(pattern, handler) {
    _routes.push({ pattern: pattern, handler: handler });
  }

  function navigate(hash) {
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    } else {
      handleHash();
    }
  }

  function handleHash() {
    var hash = window.location.hash.replace(/^#/, '') || 'dashboard';
    for (var i = 0; i < _routes.length; i++) {
      var r = _routes[i];
      var m = hash.match(r.pattern);
      if (m) {
        var host = document.getElementById('view-host');
        if (!host) return;
        host.innerHTML = '<div class="flow-skeleton view-skeleton" style="height: 60vh;"></div>';
        if (_currentView && _currentView.leave) {
          try { _currentView.leave(); } catch (e) { console.error(e); }
        }
        try {
          var ret = r.handler(m, host);
          _currentView = ret || {};
          if (_currentView.enter) _currentView.enter();
        } catch (e) {
          host.innerHTML = '<div class="flow-card">加载失败: ' + String(e) + '</div>';
          console.error(e);
        }
        _updateActiveLink(hash);
        return;
      }
    }
    document.getElementById('view-host').innerHTML = '<div class="flow-card">未知视图: ' + hash + '</div>';
  }

  function _updateActiveLink(hash) {
    var links = document.querySelectorAll('.drawer-link');
    for (var i = 0; i < links.length; i++) {
      var l = links[i];
      var href = l.getAttribute('href') || '';
      var key = href.replace(/^#/, '');
      if (key === hash || (key === 'dashboard' && hash === '')) {
        l.classList.add('active');
      } else {
        l.classList.remove('active');
      }
    }
  }

  // === Toast ===
  function toast(msg, kind) {
    kind = kind || 'info';
    var host = document.getElementById('toast-host');
    if (!host) { console.log('[toast]', msg); return; }
    var el = document.createElement('div');
    el.className = 'toast ' + (kind === 'error' ? 'err' : kind === 'ok' ? 'ok' : '');
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0';
      el.style.transition = 'opacity .3s';
      setTimeout(function () { el.remove(); }, 320);
    }, 3500);
  }

  // === 健康检查(连接灯) ===
  function startConnLight() {
    function tick() {
      fetch('/api/health', { headers: { 'X-Trace-Id': _genTraceId() } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var light = document.getElementById('conn-light');
          if (!light) return;
          if (j && j.ok) {
            light.className = 'conn-light ok';
            light.title = '已连接';
          } else {
            light.className = 'conn-light err';
            light.title = '后端异常';
          }
        })
        .catch(function () {
          var light = document.getElementById('conn-light');
          if (light) { light.className = 'conn-light err'; light.title = '离线'; }
        });
    }
    tick();
    setInterval(tick, 8000);
  }

  // === DOM 工具 ===
  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === 'class') n.className = attrs[k];
      else if (k === 'html') n.innerHTML = attrs[k];
      else if (k === 'text') n.textContent = attrs[k];
      else if (k === 'on') {
        for (var ev in attrs.on) n.addEventListener(ev, attrs.on[ev]);
      } else if (k.indexOf('data-') === 0) n.setAttribute(k, attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }
    if (children) {
      if (!Array.isArray(children)) children = [children];
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (typeof c === 'string') n.appendChild(document.createTextNode(c));
        else if (c) n.appendChild(c);
      }
    }
    return n;
  }

  function fmtTime(ms) {
    if (!ms) return '—';
    var d = new Date(ms);
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
      + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // === 全局 API ===
  global.flow = {
    api: api,
    sse: sse,
    route: route,
    navigate: navigate,
    handleHash: handleHash,
    toast: toast,
    startConnLight: startConnLight,
    $: $, $$: $$,
    el: el,
    fmtTime: fmtTime,
    escapeHtml: escapeHtml,
  };

  // === 主题切换 ===
  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        var cur = document.documentElement.getAttribute('data-theme') || 'dark';
        var next = cur === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('flow-theme', next); } catch (e) {}
      });
    }
    var hamburger = document.getElementById('hamburger');
    var drawer = document.getElementById('drawer');
    if (hamburger && drawer) {
      hamburger.addEventListener('click', function () {
        drawer.classList.toggle('open');
      });
    }
    var links = document.querySelectorAll('.drawer-link');
    for (var i = 0; i < links.length; i++) {
      links[i].addEventListener('click', function () {
        if (drawer) drawer.classList.remove('open');
      });
    }
    window.addEventListener('hashchange', handleHash);
    startConnLight();
    handleHash();
  });
})(window);