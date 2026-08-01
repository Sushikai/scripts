// Sprint 9: 前端 per-route RUM 收集 — LCP/INP/CLS/FCP/TTFB
// 收集规则: 30s 一次 flush, 同一 route 合并 sample (n>1 时算 avg), sendBeacon 优先
(function () {
  if (typeof window === 'undefined' || !window.PerformanceObserver) return;
  if (window._txTelemetryInit) return;
  window._txTelemetryInit = true;

  var FLUSH_INTERVAL = 30_000;  // 30s 一次 batch
  var MAX_QUEUE = 50;  // 防止 1000 轮压测时 buffer 无限增长

  var _vitals = { lcp: 0, fcp: 0, inp: 0, cls: 0, ttfb: 0, nav_dur: 0 };
  var _route = location.pathname + location.search;
  var _routeSamples = {};  // route -> { count, lcp, fcp, inp, cls, ttfb, nav_dur, nav_ms }
  var _queue = [];
  var _lastNav = 0;
  var _routeStartTs = performance.now();  // Sprint 9: track route duration (more reliable than LCP per route)

  function _recordRoute(sample) {
    var key = _route;
    var cur = _routeSamples[key] || { count: 0, lcp: 0, fcp: 0, inp: 0, cls: 0, ttfb: 0, nav_dur: 0, nav_ms: 0 };
    cur.count++;
    cur.lcp = Math.max(cur.lcp, sample.lcp || 0);
    cur.fcp = Math.max(cur.fcp, sample.fcp || 0);
    cur.inp = Math.max(cur.inp, sample.inp || 0);
    cur.cls = Math.max(cur.cls, sample.cls || 0);
    cur.ttfb = Math.max(cur.ttfb, sample.ttfb || 0);
    cur.nav_dur = Math.max(cur.nav_dur, sample.nav_dur || 0);
    cur.nav_ms = Math.round(performance.now() - _routeStartTs);  // per-route: 上次路由起 → now 真实 ms
    _routeSamples[key] = cur;
  }

  // LCP (largest-contentful-paint)
  try {
    new PerformanceObserver(function (list) {
      var entries = list.getEntries();
      var last = entries[entries.length - 1];
      if (last) _vitals.lcp = Math.round(last.startTime);
    }).observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (e) {}

  // FCP (first-contentful-paint)
  try {
    new PerformanceObserver(function (list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        if (e.name === 'first-contentful-paint') _vitals.fcp = Math.round(e.startTime);
      }
    }).observe({ type: 'paint', buffered: true });
  } catch (e) {}

  // INP (interaction-to-next-paint) — Sprint 9 自实现近似:event-timing 算 interaction
  try {
    new PerformanceObserver(function (list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        if (e.interactionId) {
          var dur = Math.round(e.duration);
          if (dur > _vitals.inp) _vitals.inp = dur;
        }
      }
    }).observe({ type: 'event', buffered: true, durationThreshold: 16 });
  } catch (e) {}

  // CLS (cumulative-layout-shift)
  try {
    new PerformanceObserver(function (list) {
      for (var i = 0; i < list.getEntries().length; i++) {
        var e = list.getEntries()[i];
        if (!e.hadRecentInput) _vitals.cls = +(e.value + _vitals.cls).toFixed(4);
      }
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (e) {}

  // nav 初始 metric
  try {
    var nt = performance.getEntriesByType('navigation')[0];
    if (nt) {
      _vitals.ttfb = Math.round(nt.responseStart - nt.requestStart);
      _vitals.nav_dur = Math.round(nt.loadEventEnd - nt.startTime);
    }
  } catch (e) {}

  // route change → reset + record prev
  function _onRouteChange() {
    // 任何切路由都收尾(不靠 LCP,改用 nav_ms)
    _recordRoute(_vitals);
    _route = location.pathname + location.search;
    _vitals = { lcp: 0, fcp: 0, inp: 0, cls: 0, ttfb: 0, nav_dur: 0 };
    _routeStartTs = performance.now();
  }

  // 监听 hashchange (主 SPA 路由) + popstate
  window.addEventListener('hashchange', _onRouteChange);
  window.addEventListener('popstate', _onRouteChange);

  // 暴露 setter 给 app.js showView 主动调用(它比 hashchange 准)
  window._txSetRoute = function (routeName) {
    // 收尾上一段路由: 取当前 vitals + 已经过的时间(Sprint 9: 即使 vitals 全 0 也要记 nav_ms)
    _recordRoute(_vitals);
    _route = '/view/' + routeName;
    _vitals = { lcp: 0, fcp: 0, inp: 0, cls: 0, ttfb: 0, nav_dur: 0 };
    _routeStartTs = performance.now();
  };

  // flush: 30s 一次 + 页面 unload 兜底
  function _flush() {
    // 收尾当前 route(任何非空 vitals 都记录;Lcp 0 也保留 nav_ms 维度)
    if (_vitals.lcp || _vitals.fcp || _vitals.inp) {
      _recordRoute(_vitals);
      _vitals = { lcp: 0, fcp: 0, inp: 0, cls: 0, ttfb: 0, nav_dur: 0 };
    }
    var routes = Object.keys(_routeSamples);
    if (!routes.length) return;
    for (var i = 0; i < routes.length; i++) {
      var r = routes[i];
      var s = _routeSamples[r];
      _queue.push({
        ts: Date.now(),
        route: r,
        lcp: s.lcp,
        fcp: s.fcp,
        inp: s.inp,
        cls: s.cls,
        ttfb: s.ttfb,
        nav_dur: s.nav_dur,
        nav_ms: s.nav_ms,
        samples: s.count,
      });
    }
    _routeSamples = {};
    if (_queue.length > MAX_QUEUE) _queue = _queue.slice(-MAX_QUEUE);
    if (!_queue.length) return;
    var body = JSON.stringify(_queue);
    _queue = [];
    if (navigator.sendBeacon) {
      var ok = navigator.sendBeacon('/api/_perf', new Blob([body], { type: 'application/json' }));
      if (!ok) {
        // sendBeacon 失败 (quota 等) → 退回 fetch keepalive
        fetch('/api/_perf', { method: 'POST', body: body, keepalive: true, headers: { 'Content-Type': 'application/json' } }).catch(function () {});
      }
    } else {
      fetch('/api/_perf', { method: 'POST', body: body, keepalive: true, headers: { 'Content-Type': 'application/json' } }).catch(function () {});
    }
  }

  setInterval(_flush, FLUSH_INTERVAL);
  window.addEventListener('pagehide', _flush);
  window.addEventListener('beforeunload', _flush);

  // 暴露给测试
  window._txTelemetryFlush = _flush;
  window._txTelemetryVitals = function () { return Object.assign({}, _vitals); };
  window._txTelemetryRoute = function () { return _route; };
})();
