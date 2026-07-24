/* flow service worker:v3 — 新增 paste-card (fengge_url) */
const CACHE_NAME = 'flow-shell-v3';
const PRECACHE_URLS = [
  '/',
  '/static/css/tokens.css',
  '/static/css/base.css',
  '/static/css/views.css',
  '/static/js/core.js',
  '/static/js/app.js',
  '/static/js/view-dashboard.js',
  '/static/js/view-project-new.js',
  '/static/js/view-projects.js',
  '/static/js/view-project-detail.js',
  '/static/js/view-library.js',
  '/static/js/view-accounts.js',
  '/static/js/view-uploads.js',
  '/static/js/view-logs.js',
  '/static/js/view-settings.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return; // API 永远 network-only
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request))
    );
    return;
  }
  if (url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    );
  }
});