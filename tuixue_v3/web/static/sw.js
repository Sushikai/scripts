// 退学 v3 · Service Worker
// 离线 fallback + 静态资源 cache-first + API 网络优先
//
// 设计:
//   1) install:  precache / 和 /static/* 主壳,断网时仍可打开 UI
//   2) fetch /static/* : cache-first (静态资源带指纹,长 cache 不会过期)
//   3) fetch /          : network-first,失败回 cache (HTML no-cache)
//   4) fetch /api/*     : network-only (数据必须新鲜,不能给陈旧缓存)
//   5) navigate 失败    : 离线时返回 precached /
//
// 注意:不要在这个文件里 import 任何外部模块 — SW 是 top-level, fetch handler 会捕获所有未命中路径

const CACHE = 'tuixue-v3-shell-v1';
const PRECACHE = [
  '/',
  '/static/app.js',
  '/static/style.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) =>
      // addAll 任何一个失败整体失败 — 但用单个 put 容错,避免一个 404 把整个 SW 装不上
      Promise.all(
        PRECACHE.map((u) =>
          fetch(u, { cache: 'no-store' })
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

  // ── API: network-only,失败抛错给前端 ──
  if (url.pathname.startsWith('/api/')) {
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
        '<p><a href="/">重试</a></p>',
        { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } }
      )))
    );
    return;
  }

  // 其他 (favicon, /sw.js 自请求) 默认放行
});