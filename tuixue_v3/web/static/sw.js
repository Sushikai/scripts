// 退学 v3 · Service Worker
// 离线 fallback + 静态资源 cache-first + API 网络优先
//
// 设计:
//   1) install:  precache / 和 /static/* 主壳,断网时仍可打开 UI
//   2) fetch /static/* : cache-first (静态资源带指纹,长 cache 不会过期)
//   3) fetch /          : network-first,失败回 cache (HTML no-cache)
//   4) fetch /api/*     : network-only,失败抛错给前端
//   5) navigate 失败    : 离线时返回 precached /
//
// 注意:不要在这个文件里 import 任何外部模块 — SW 是 top-level, fetch handler 会捕获所有未命中路径

// 2026-07-18: bump 到 v80 — 10 轮压测 ship: RAF 竞态修 (闭包捕获) + /stream 3 源 fallback (TTLCache → K.QUOTE → K.STOCK_FULL)
// 2026-07-18: bump 到 v86 — R56 退场模型 9套→6套 (trail_80/50/20+water_avg+force_10/close) + 仓位换算 KPI + baseline↔WR1000 自动对比 + 退场模型解释区块
// 2026-07-18: bump 到 v88 — R57+ late_high_discount 折算开关 (1.0/0.7/0.5 三档按钮) + 透传 late_high_discount/require_vwap_strict 到后端
// 2026-07-18: bump 到 v89 — 40 轮流畅度优化: poll+loadStockDetail 去 ?_fresh=1 (3s→100ms) + animateNumber 阈值 0.1% refVal + text-shadow→transform + transition:all→具体属性 + ECharts animation:false + 后台 idle prefetch + 分时/K 线右轴末值标签
// 2026-07-18: bump 到 v90 — 删 LAN 扫码直进 (前端 #tunnel-lan-card + 4 个 handler + 后端 tunnel/status/push 响应去掉 lan_ip/lan 字段)
// 2026-07-18: bump 到 v92 — 前端界面 50 轮巡检 ship: echarts 全局预加载 (修 "echarts is not defined") + 复盘子表 thead 补充 + URL ?theme= 参数解析
// 2026-07-18: bump 到 v93 — 100 轮系统维护 ship batch 1 (R1-R8 race 条件): AbortController 切股取消 + SSE 1s 防 reconnect 风暴 + K线/loadStockDetail/loadIntraDay inflight dedup + ECharts drawToken 防 dispose 抢图 + _patchStockRealtime stale-code 守卫 + view-scoped timer registry 离开自动 clearTimeout
// 2026-07-18: bump 到 v94 — 100 轮系统维护 ship batch 2 (R11-R16 内存泄漏): sessionStorage stock LRU 80 槽位 + _stockAuxCache 切股清空 + 离开 view 清 inflight dedup promise + ECharts dispose 全图 + animateNumber RAF 全局追踪 + cancel 旧动画
// 2026-07-18: bump 到 v96 — 100 轮 Batch 1 R1: /api/stock/{code}/full SW 单独 5min 长缓存 (server-side Redis 5s 已是新鲜度门, SW 防冷启动穿透 ~5ms 而非 ~20ms)
// 2026-07-18: bump 到 v99 — 100 轮 Batch 3 R21+R23: /api/stock/{code}/core 1.5s 强超时, 只返 quote+name+kline(30), 让首屏 < 200ms 出价, /full 后台渐进 patch
const CACHE = 'tuixue-v3-shell-v99';
const PRECACHE = [
  '/',
  '/static/app.js',
  '/static/core.js',
  '/static/view-dash.js',
  '/static/view-stock.js',
  '/static/view-other.js',
  '/static/view-all-stocks.js',
  '/static/style.css',
  '/static/index.html',
  '/static/sw.js',
  '/static/vendor/echarts.min.js',
];

// B7: 关键 API JSON 缓存 (offline shell)
// 这些端点数据变化慢 + 是首屏必需,离线时优先返 cache
const _CACHEABLE_API_PREFIXES = [
  '/api/all_stocks/board',
  '/api/review/portfolio',
  '/api/review/positions',
  '/api/all_stocks/l1',
  '/api/dashboard/signal',
  '/api/dashboard/hot_sectors',
];
// R1 (Batch 1): /api/stock/{code}/full 单独长缓存 5min — server-side Redis 5s 已是新鲜度门,
// SW 这层只防冷启动穿透 (5s 之后重访直接走 SW, ~5ms 而非 ~20ms)
const _LONG_CACHE_API_PATTERNS = [
  /^\/api\/stock\/[^/]+\/full(\?.*)?$/,
];
const _LONG_CACHE_API_TTL_MS = 300_000;  // 5min
// API 缓存新鲜度: 60s 内直接用 cache,超过则后台 revalidate
const _API_CACHE_FRESH_MS = 60_000;

function _isCacheableApi(pathname) {
  return _CACHEABLE_API_PREFIXES.some(p => pathname.startsWith(p));
}

// R1: 匹配 /full 等长缓存端点, 返 5min 而非默认 60s
function _isLongCacheApi(pathname) {
  return _LONG_CACHE_API_PATTERNS.some(rx => rx.test(pathname));
}

function _freshnessMs(pathname) {
  return _isLongCacheApi(pathname) ? _LONG_CACHE_API_TTL_MS : _API_CACHE_FRESH_MS;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) =>
      // addAll 任何一个失败整体失败 — 但用单个 put 容错,避免一个 404 把整个 SW 装不上
      Promise.all(
        PRECACHE.map((u) =>
          // cache: 'reload' 绕过 HTTP cache; 在 SW 里 fetch() 仍会经过本 SW 的 fetch handler,
          // 但本 SW 在 activate 时已把旧 cache 全删,此时 caches.match 必 miss, 最终落到网络,
          // 拿到的是服务器最新版本. 比 no-store 更可靠 — 避免重复 cache 旧文件.
          fetch(u, { cache: 'reload' })
            .then((r) => (r.ok ? c.put(u, r.clone()) : null))
            .catch(() => null)
        )
      )
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;          // POST/PUT 不拦截
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // 跨域 / SSE / tunnel 端点不接管

  // ── API: cacheable → stale-while-revalidate,其它 → network-only ──
  if (url.pathname.startsWith('/api/')) {
    if (_isCacheableApi(url.pathname) || _isLongCacheApi(url.pathname)) {
      event.respondWith(
        caches.open(CACHE).then(async (cache) => {
          const cached = await cache.match(req);
          // 新鲜度检查: cache 在 TTL 内则直接返,否则等网络
          const ttlMs = _freshnessMs(url.pathname);
          if (cached) {
            const cachedTime = new Date(cached.headers.get('date') || 0).getTime();
            const age = Date.now() - (cachedTime || 0);
            if (age < ttlMs) return cached;
          }
          const fetchPromise = fetch(req).then((r) => {
            if (r.ok && r.status === 200) cache.put(req, r.clone()).catch(() => {});
            return r;
          }).catch(() => null);
          return (cached && !navigator.onLine) ? cached : (await fetchPromise) || new Response(
            JSON.stringify({ ok: false, error: 'offline', cached: false }),
            { status: 503, headers: { 'content-type': 'application/json' } }
          );
        })
      );
    }
    return;
  }

  // ── 静态资源: cache-first ──
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => {
        if (hit) return hit;
        return fetch(req).then((r) => {
          if (r.ok && r.status === 200) {
            const copy = r.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return r;
        }).catch(() => caches.match('/static/style.css'));  // 极端:返回空壳 css
      })
    );
    return;
  }

  // ── HTML / navigate: network-first,失败回 cache ──
  if (req.mode === 'navigate' || url.pathname === '/') {
    event.respondWith(
      fetch(req).then((r) => {
        if (r.ok) {
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put('/', copy)).catch(() => {});
        }
        return r;
      }).catch(() => caches.match('/').then((hit) => hit || new Response(
        '<!doctype html><meta charset=utf-8><title>离线 · 退学 v3</title>' +
        '<style>body{font-family:system-ui;background:#0a0908;color:#fbfbfd;padding:2rem}' +
        'h1{font-weight:600}a{color:#d4b87a}</style>' +
        '<h1>已离线</h1><p>远端控制台暂不可达 · 网络恢复后将自动重连。</p>' +
        '<p>上次浏览的板块/持仓数据可能仍是新鲜的(<a href="/#all_stocks">全 A 风向</a> · <a href="/#review">复盘</a>)</p>' +
        '<p><a href="/">重试</a></p>',
        { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } }
      )))
    );
    return;
  }

  // 其他 (favicon, /sw.js 自请求) 默认放行
});