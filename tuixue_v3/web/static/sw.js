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

// v80→v100 (2026-07-18): 竞态修/退场模型/流畅度/内存泄漏/网络/渲染/监控/离线/cache 去重
// v111→v130 (2026-07-19): 尾盘回测 6 项 ship + 回测独立 SQLite + 周线擒牛重写 + 冷启 3 优化
// v130: 5日线5原则 #3-#5 + 周线擒牛激进突破 + ma5_principles API
// v132 (2026-07-19): asyncio.gather return_exceptions + CSS 768px/979px 合并 + ai-review SSE 离开关闭 +
//                     server 预热并行化 + 重复 daily dict I/O 删除 + CSS brace 修复 + _warm_core_local 并行
// v135 (2026-07-19): 回测页双策略并排 (bt-trade-group 改横向 grid 复用 .scr-dual-wrap 模式;
//                    2 策略左右各半屏, <1024px 上下堆叠; 3+ 防御性退化为 stack)
// v137 (2026-07-19): 双策略说明卡 (尾盘战法页 .scr-strategy-legend + 回测页 .bt-strategy-legend,
//                    共用 token, 2 列 grid 桌面/1 列移动)
// v138 (2026-07-19): K线图 50 issues fix — KDJ 80/20 series; ma() null; loadKline stale-code guard + Map inflight;
//                    chart.off/on; _waitKline timeout; BOLL null; RSV edge; 期高/期低 high/low; _labelBg 跟随主题
// v139 (2026-07-20): 策略整合 — 只留 ⭐ 优化策略, 删 7 个 bt 预设 + 3 个 bt-tab + btRenderCompare;
//                    优化策略按钮 compare_to_baseline 始终 true (修"第二种策略只有表头" bug);
//                    _btRenderThreeWay → _btRenderDual 双策略对比; WIN_RATE_1000 全清; 颜色 #d8cdb4 / #c084f4 不变
// v140 (2026-07-20): 修 renderStockDetail ReferenceError: streakHost is not defined (view-stock.js:1502)
//                    — 函数开头补 const streakHost = $('#q-streak-host');
// v141 (2026-07-20): 多页数据加载异常修复
//                    1) view-stock.js renderStockDetail streakHost undefined
//                    2) view-weekly_bull.js loadWeeklyBull: env.ok 检查 — api() 已剥 envelope, env 永远是 data
//                    3) app.js _loadHist / _loadTradeDates / loadNews: 同上 envelope 取错
//                    4) app.js _routeFromHash valid 列表加 'sector'
//                    5) multi_source_fetchers.fetch_hot_sectors EM 列名变更适配 (今日涨跌幅 / 今日主力净流入-净额)
//                    6) server.py api_dashboard_signal: 加 SingleFlight + timeout 18s→25s 修并发 ABORTED
// v143 (2026-07-20): 尾盘战法 [data-code] 代码/名称点击改 in-app showView('stock')+loadStockDetail (前 window.open 被弹窗拦截);
//                    自选 view 紧凑表格 (.view-watchlist #wl-table font 13→11.5px, padding 8/10→3/6);
//                    优化器持续 200 轮
// v144 (2026-07-20): 尾盘战法 click listener 绑到 #scr-tbody (已不存在,拆 baseline + optimized 两组) → 实际死绑 null
//                    → 改成监听 #scr-tbody-baseline + #scr-tbody-optimized 两个 tbody
// v145 (2026-07-20): 自选页慢 — /api/watchlist 加 30s SW cache + _fetch_with_retry 退避 0.5/1/2 → 0.2/0.4
//                    (自选 9 码 × 多源 fallback 偶尔挂 16s, 收紧后单源失败 ≤ 1s 切下一源)
// v146 (2026-07-21): 个股实时数据卡昨天的 — /core + /full SW TTL 5min→15s/5s
//                    (server /core=30s /full=5s,SW 锁 5min 会让 5min 内一直返昨日数据;
//                     现压到 < server TTL,首次 cache miss 后 5-15s 自动 revalidate,
//                     仍保留冷启动命中保护)
// v147 (2026-07-21): stock-date 默认 today 时不再传 ?date=today 给 /full —
//                    走纯实时路径,避免 SW URL 含日期导致跨日孤立缓存
// v148 (2026-07-21): 频繁点击卡死修复 — _fetchWithTimeout 之前用 signal:ctrl.signal 覆盖了
//                    调用方 opts.signal → 所有切股/切页 abort 全是 no-op,旧 core/full(各2s+重试2次)
//                    跑到底占满 HTTP/1.1 6 连接池,新点击排队 → 整站卡死。
//                    修:_fetchWithTimeout 桥接外部 signal(app.js+core.js);loadStockDetail 建共享
//                    window._stockInflightAborter,切股开头 abort 上一份并把 signal 传给
//                    core/full/kline/intraday/trade_dates/role/related_news/strategy_match/seat_breakdown;
//                    _startStockPoll 首轮 10s 定时器补存句柄防泄漏。
// v149 (2026-07-21): 修 abort 误报"系统异常" toast — api() 把所有 AbortError 包成 "请求超时 (Xs): path",
//                    unhandledrejection 看到不带"abort"字样就触发 toast。修:外部 signal abort 原样抛,
//                    全局 handler 抑制"请求超时"开头 (属用户操作结果,不是真异常)。
// v150 (2026-07-21): 修桌面 #intra-day-chart 0 高度 bug — 旧 CSS 只在 ≤768px 给 220px,桌面没有 height,
//                    echarts 渲染到 0px 容器 → 整页看不到分时图。补桌面端 #intra-day/kline/flow/intraday5d
//                    显式 height (320/380/280/220)。
// v151 (2026-07-21): 全 A 顶级重构 — push2delay 全市场批量快照 (5540 只一次拉),board 内存读
//                    (覆盖 12→5540, took 4ms);跨模块快捷筛选 chip (涨停/连板/放量/尾盘战法…);
//                    board inflight AbortController 去重 (修连点卡死)。
// v153 (2026-07-21): screener-inline.js 补 IIFE wrapper — 提取时丢了原 IIFE,顶层 const $ 与 core.js 全局 $ 撞车 → 5 条 SyntaxError;
//                    恢复 `(function(){ ... })()` 词法作用域,$ 局部化,core.js 的 $ 不受影响。
// v154 (2026-07-22): Tier 3 正确性 3 修 —
//                    1) _VIEW_LEAVE_HOOKS 异步化 (Promise.resolve 微任务):leave hook 内常 abort + 立即 refire,
//                       同步调用导致同一 fetch 既被 abort 又被发出,HTTP/1.1 6 连接池浪费。
//                    2) _a11yObs 切页时 disconnect + view-enter 重 observe:旧版永久 observe body subtree,
//                       长会话内存增长 30MB+ (Chrome Memory snapshot 验证)。
//                    3) _prefetchDone LRU 200 + view-leave 清 _prefetchInflight:
//                       旧版永不清理,扫过 200+ 只股票 Set 持续堆积;切走时半截 prefetch 占满连接池拖死新 view。
// v155 (2026-07-22): 删 screener-inline.js (155KB 死代码,index.html 已用 zt-frontend.js 239 行替换) —
//                    之前 Tier 1.1 把它从 inline IIFE 提到独立文件,但 index.html 早已切到 zt-frontend.js,
//                    提到独立文件后无 script 引用,纯浪费 155KB precache。删后 PRECACHE 同步清理。
// v157 (2026-07-22): Tier 2.3 hashchange 50ms debounce — 连续点 5 个 sidebar item 只触发最后 1 次路由,
//                    旧版 5 次 _routeFromHash → 5 套 view-enter + 5×11 API 拉取,HTTP/1.1 6 连接池撞穿。
//                    4) showView 派发 view-leave CustomEvent,让全局监听器 (prefetch/_a11yObs 等) 知道何时清理。
// v158 (2026-07-22): view-stock/view-other/weekly/strategy 改为按需注入,不再 precache 首屏不需要的脚本。
//                    首次进入目标 view 时由 app.js 依赖序列加载并缓存。
// v159 (2026-07-22): ZT 多因子加权 — zt-frontend.js 加交易明细表 + 每条 trade 加 weighted_score
// v160 (2026-07-22): 全A 表格顶级化 — AI 战场列拆为「板块」「概念」,删冗余 L2/L3/L4/来源,单行化 (chip 截断+ title 全名 + +N 溢出)
// v161 (2026-07-22): 移动端列优先级 — 18 列打 data-priority (P1=核心 / P2=次要 / P3=可选),
//                    ≤768 隐藏 P3, ≤480 再隐藏 P2;撤掉桌面 min-width:1080 强制横滚
// v162 (2026-07-22): tbody td 同步打 data-priority — 旧版只在 thead th 上挂属性,
//                    CSS `tbody td[data-priority="3"]` 不命中,移动端表格 18 列全显示,
//                    scrollWidth 587px > viewport 390px 横滚 bug
const CACHE = 'tuixue-v3-shell-v163';
const PRECACHE = [
  '/',
  '/static/app.js',
  '/static/core.js',
  '/static/view-dash.js',
  '/static/style.css',
  '/static/index.html',
  '/static/sw.js',
  '/static/zt-frontend.js',
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
  // 2026-07-20: 自选页慢 — 后端 9 码 × 多源 fallback 偶尔 16s (单源失败 0.5+1+2=3.5s × 4 源=14s+ fetch)
  // SW 30s 缓存保底,二次访问 < 5ms (第一次慢也只影响首屏)
  '/api/watchlist',
];
// R1 (Batch 1) + v146 修正: /api/stock/{code}/full 单独长缓存 —
// 原本 5min 锁死,导致 SW 返昨日数据 (server-side /full=5s, /core=30s,5min 内根本不刷新).
// 现 /full SW TTL = server TTL = 5s, /core = 15s (< server 30s, 保 server 是新鲜度门)
const _LONG_CACHE_API_PATTERNS = [
  /^\/api\/stock\/[^/]+\/full(\?.*)?$/,
  /^\/api\/stock\/[^/]+\/core(\?.*)?$/,
];
const _LONG_CACHE_API_TTL_MS_CORE = 15_000;   // /core: 15s (< server 30s, 强制走 server refresh)
const _LONG_CACHE_API_TTL_MS_FULL = 5_000;    // /full: 5s (= server 5s)
// API 缓存新鲜度: 60s 内直接用 cache,超过则后台 revalidate
const _API_CACHE_FRESH_MS = 60_000;

function _isCacheableApi(pathname) {
  return _CACHEABLE_API_PREFIXES.some(p => pathname.startsWith(p));
}

// R1: 匹配 /full 等长缓存端点, 走更短 TTL (5s/15s) 而非默认 60s
function _isLongCacheApi(pathname) {
  return _LONG_CACHE_API_PATTERNS.some(rx => rx.test(pathname));
}

function _freshnessMs(pathname) {
  if (_LONG_CACHE_API_PATTERNS[0].test(pathname)) return _LONG_CACHE_API_TTL_MS_FULL;  // /full
  if (_LONG_CACHE_API_PATTERNS[1].test(pathname)) return _LONG_CACHE_API_TTL_MS_CORE;  // /core
  return _API_CACHE_FRESH_MS;
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
            if (r.ok && r.status === 200) {
              // R98: 先 match 再 put,如果已存在就 skip (避免相同 query URL 重复写)
              cache.match(req).then(existing => {
                if (!existing) cache.put(req, r.clone()).catch(() => {});
              }).catch(() => {});
            }
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