/* 退学 v3 · 操作台 前端逻辑
 * v2.0 — 信封 / 并发 / SSE / 心法 / AI
 */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// 启动时检测重复 ID — 防止 querySelector 漏更新第二处
(function _checkDuplicateIds() {
  const seen = new Map();
  document.querySelectorAll('[id]').forEach(el => {
    const id = el.id;
    if (seen.has(id)) {
      console.error(`[tuixue] 重复 ID: #${id} — 后面的元素会被 querySelector 跳过`, seen.get(id), el);
    } else {
      seen.set(id, el);
    }
  });
})();

// ────────────────────────────────────────────
// 全局事件委托 — 处理 data-action 属性
// 替代 innerHTML 渲染时散落的 onclick="fnName()" —
// 1) CSP 严格模式不会断 2) 函数重命名 / 删除不会留死引用
// 支持 action: refresh-dashboard / open-stock:CODE / show-view:NAME /
//        review-delete:ID / review-run:ID / ai-review:TRADE_ID /
//        toggle-seat-detail 等
// ────────────────────────────────────────────
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  if (!action) return;
  const colon = action.indexOf(':');
  const name = colon >= 0 ? action.slice(0, colon) : action;
  const arg = colon >= 0 ? action.slice(colon + 1) : '';
  try {
    switch (name) {
      case 'refresh-dashboard': refreshDashboard(); break;
      case 'open-stock':        loadStockDetail(arg); break;
      case 'show-view':         showView(arg); break;
      case 'review-run':        _reviewRun(arg); break;
      case 'review-delete':     _reviewDelete(arg); break;
      case 'ai-review':         openAiReview(arg); break;
      case 'toggle-seat-detail':toggleSeatDetail(el); break;
      case 'airv-rerun':        _airvRunLLM(true); break;
      case 'review-delete-position':
        // arg 格式: "code|name|shares" — 已 url-encoded by escapeHtml
        const [pc, pn, ps] = arg.split('|');
        _reviewDeletePosition(decodeURIComponent(pc), decodeURIComponent(pn), parseInt(ps, 10));
        break;
      default:
        console.warn('[tuixue] unknown data-action:', action);
    }
  } catch (err) {
    console.error('[tuixue] data-action handler error:', err);
  }
});

// ── 兼容别名 — 旧代码里残留的 refresh 函数名,统一走 Load* ──
// (R1-B 修复: 这些别名曾被静默调用但未定义,导致刷新失效)
async function _reviewRefreshTrades()    { if (typeof _reviewLoadList === 'function')     return _reviewLoadList(); }
async function _reviewRefreshPortfolio() { if (typeof _reviewLoadPortfolio === 'function') return _reviewLoadPortfolio(); }
async function _reviewRefreshFlows()     { if (typeof _reviewLoadPortfolio === 'function') return _reviewLoadPortfolio(); }
async function _reviewStartFlowsPolling(){ /* 已合并到 _reviewOnViewEnter */ }
async function _reviewRun(tradeId) {
  if (typeof openAiReview === 'function') return openAiReview(tradeId);
}

// ── 全局 ESC 关 modal / Ctrl+K 搜索 ──
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal.show, .overlay.show, [data-modal].show')
      .forEach(m => m.classList.remove('show'));
    // R10-A: Escape 关闭移动端 sidebar
    if (document.body.classList.contains('sidebar-open')) {
      _closeSidebar();
    }
  }
  // R9-A: Ctrl+` (反引号) 切换调试面板
  if (e.key === '`' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    _toggleDebugPanel();
  }
});

// R10-A: 移动端 sidebar 抽屉开关
function _toggleSidebar() {
  if (document.body.classList.contains('sidebar-open')) {
    _closeSidebar();
  } else {
    _openSidebar();
  }
}
function _openSidebar() {
  document.body.classList.add('sidebar-open');
  const bd = document.getElementById('sidebar-backdrop');
  if (bd) bd.hidden = false;
  const btn = document.getElementById('menu-btn');
  if (btn) btn.setAttribute('aria-expanded', 'true');
}
function _closeSidebar() {
  document.body.classList.remove('sidebar-open');
  const bd = document.getElementById('sidebar-backdrop');
  if (bd) bd.hidden = true;
  const btn = document.getElementById('menu-btn');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

// R9-A: 调试面板 — 缓存命中率 + AI 调用成本 + DB 健康 + poller 状态
let _debugPanelTimer = null;
function _toggleDebugPanel() {
  const existing = document.getElementById('debug-panel');
  if (existing) { existing.remove(); clearInterval(_debugPanelTimer); return; }
  const el = document.createElement('div');
  el.id = 'debug-panel';
  el.innerHTML = `
    <div class="dp-header">
      <span class="dp-title">🛠 调试面板</span>
      <button class="dp-close" aria-label="关闭">×</button>
    </div>
    <div class="dp-body"><div class="dp-loading">加载中…</div></div>
    <div class="dp-footer">
      <span class="dp-hint">Ctrl+\` 切换</span>
      <button class="dp-refresh">手动刷新</button>
    </div>
  `;
  document.body.appendChild(el);
  el.querySelector('.dp-close').onclick = () => { el.remove(); clearInterval(_debugPanelTimer); };
  el.querySelector('.dp-refresh').onclick = _refreshDebugPanel;
  _refreshDebugPanel();
  _debugPanelTimer = setInterval(_refreshDebugPanel, 5000);
}

async function _refreshDebugPanel() {
  const body = document.querySelector('#debug-panel .dp-body');
  if (!body) return;
  try {
    const m = await api('/api/metrics', { timeout: 5000 });
    const fmtUptime = s => {
      if (!s) return '?';
      const h = Math.floor(s / 3600), mn = Math.floor((s % 3600) / 60);
      return `${h}h${mn}m`;
    };
    const cacheRows = (m.cache || []).map(c =>
      `<tr><td>${esc(c.name)}</td><td>${c.size||0}</td><td>${c.hits||0}</td><td>${c.misses||0}</td><td>${((c.hit_rate||0)*100).toFixed(0)}%</td></tr>`
    ).join('');
    const ai = m.ai || {};
    const aiBuckets = ai.buckets || {};
    const aiTotalCalls = Object.values(aiBuckets).reduce((s, b) => s + (b.calls || 0), 0);
    const aiTotalOk = Object.values(aiBuckets).reduce((s, b) => s + (b.ok || 0), 0);
    const aiRows = Object.entries(aiBuckets).map(([k,v]) =>
      `<tr><td>${esc(k)}</td><td>${v.calls||0}</td><td>${v.ok||0}</td><td>${v.ok_pct||0}%</td><td>${v.avg_latency_ms||0}ms</td><td>$${(v.total_cost_usd||0).toFixed(4)}</td></tr>`
    ).join('');
    const breakers = ai.breakers || {};
    const openBreakers = Object.entries(breakers).filter(([,b]) => b.open);
    const breakerHtml = openBreakers.length
      ? `<div class="dp-section dp-breaker-open"><b>⚡ 熔断中</b> · ${openBreakers.map(([k,b]) =>
          `${esc(k)} (冷却 ${b.cooldown_sec|0}s)`).join(' · ')}</div>`
      : '';
    body.innerHTML = `
      <div class="dp-section"><b>运行</b> · ${esc(fmtUptime(m.uptime_sec))} · ${esc(new Date(m.ts*1000).toLocaleTimeString())}</div>
      ${m.poller ? `<div class="dp-section"><b>poller</b> · alive=${m.poller.alive?'✓':'✗'} ttl=${m.poller.ttl}s</div>` : ''}
      ${breakerHtml}
      <div class="dp-section"><b>缓存</b>
        <table class="dp-table"><thead><tr><th>名称</th><th>size</th><th>hits</th><th>miss</th><th>命中率</th></tr></thead>
        <tbody>${cacheRows||'<tr><td colspan=5>无</td></tr>'}</tbody></table>
      </div>
      <div class="dp-section"><b>AI</b> · 调用 ${aiTotalCalls} · 成功 ${aiTotalOk} · 费用 $${(ai.total_cost_usd||0).toFixed(4)} · tokens ${ai.total_tokens||0}
        <table class="dp-table"><thead><tr><th>bucket</th><th>calls</th><th>ok</th><th>%</th><th>avg</th><th>cost</th></tr></thead>
        <tbody>${aiRows||'<tr><td colspan=6>无</td></tr>'}</tbody></table>
      </div>
      <div class="dp-section"><b>DB</b>
        <pre class="dp-pre">${esc(JSON.stringify(m.db || {}, null, 2))}</pre>
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div class="dp-error">加载失败: ${esc(e.message)}</div>`;
  }
}

// 主题色 — 跟随 [data-theme] 动态刷新 (let 不是 const)
// 2026-07-10: 涨跌色已切 CN 标准（红涨绿跌）
let ACCENT = '#d4a056';
let UP     = '#e84545';
let DOWN   = '#34c759';
let INK    = '#e8e3d8';
let INK2   = '#a8a39a';
let INK3   = '#6b6660';
let GRID   = 'rgba(232,227,216,0.06)';

function _cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function refreshThemeColors() {
  ACCENT = _cssVar('--accent')   || '#d4a056';
  UP     = _cssVar('--up')       || '#e84545';
  DOWN   = _cssVar('--down')     || '#34c759';
  INK    = _cssVar('--ink')      || '#e8e3d8';
  INK2   = _cssVar('--ink-2')    || '#a8a39a';
  INK3   = _cssVar('--ink-3')    || '#6b6660';
  GRID   = 'rgba(127,127,127,0.12)';  // 跟随主题的网格线
}
refreshThemeColors();

const echartsCharts = {};
let lastRefreshTs = 0;

// ────────────────────────────────────────────
// fetch wrapper — 自动解包 {ok,data,error,ts}
// 每个请求都有 timeout + AbortController，避免后端慢导致前端卡死
// ────────────────────────────────────────────
const API_TIMEOUTS = {
  // 默认 8s; AI 分析和数据源密集型接口单独加长
  default: 12_000,
  '/api/backtest':        120_000,  // 回测慢
  '/api/optimize':       1800_000,  // 网格扫描 10 iter × 30+ trials, 实测 30+ min (2026-07-11)
  '/ai_analysis':          35_000,  // AI 分析 7 重兜底 + AI 重试
  '/api/stock/':           20_000,  // 个股综合接口
  '/api/market/':          15_000,  // 大盘概览 (ngrok 域名 6-11s 常见)
  '/api/dragons':          20_000,  // 龙头榜 (冷启动 11s, 热后 0.1s)
  '/api/dashboard/':       35_000,  // 首页信号 + 热门板块 (冷启动 20-30s)
  '/api/sectors/sw':       15_000,  // 板块情绪
  '/api/sector':           15_000,
  '/api/review/trades/':   200_000, // 复盘 AI 单次 55s+ (2026-07-10)
  '/api/review/next_picks': 60_000, // 次日选股 (live screen,沙箱慢,2026-07-12)
  '/api/stream/review/':   210_000, // SSE 复盘
};

function _timeoutFor(path) {
  for (const k of Object.keys(API_TIMEOUTS)) {
    if (k !== 'default' && path.includes(k)) return API_TIMEOUTS[k];
  }
  return API_TIMEOUTS.default;
}

// 区分"真网络错"(要重试) vs "用户主动取消"(不重试) vs "业务逻辑错"(不重试)
// SSR / tunnel 抖动场景:AbortError 可能是 timeout/connection-lost,需要重试
function _isRetryableError(e) {
  if (!e) return false;
  if (e.name === 'AbortError') return true;          // 包括我们 timeout + 网络断 Abort
  if (e.name === 'TypeError') return true;           // fetch 本体的 network failure
  if (e.name === 'NetworkError') return true;
  if (/failed to fetch|networkerror|load failed|timeout/i.test(String(e.message || e))) return true;
  return false;
}

// 指数退避 + jitter — 网好立刻返回,网差自动让开
function _backoffMs(attempt) {
  // attempt 0 -> 0ms, 1 -> ~400ms, 2 -> ~800ms, 3 -> ~1600ms (jitter ±30%)
  const base = 400 * Math.pow(2, attempt);
  return Math.round(base * (0.7 + Math.random() * 0.6));
}

async function _fetchWithTimeout(path, opts = {}) {
  const timeout = opts.timeout != null ? opts.timeout : _timeoutFor(path);
  // 注意:不传 opts.signal — 让重试自管 abort。用户主动 cancel 时,我们用 _aborted 标记
  let attempt = 0;
  const maxRetries = opts.maxRetries != null ? opts.maxRetries : 2;
  let lastErr;
  while (attempt <= maxRetries) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const resp = await fetch(path, {
        ...opts,
        signal: ctrl.signal,
        // R9-A: 给服务端发请求追踪 id,服务端中间件会回传到 X-Trace-Id 响应头
        headers: {
          ...(opts.headers || {}),
          'X-Trace-Id': (window._traceIdFor || (() => Math.random().toString(36).slice(2, 14)))(),
        },
      });
      // 5xx 视为可重试(tunnel 502/503/504 中间错)
      if (resp.status >= 500 && attempt < maxRetries) {
        try { await resp.body?.cancel?.(); } catch {}
        clearTimeout(timer);
        attempt++;
        await new Promise(r => setTimeout(r, _backoffMs(attempt)));
        continue;
      }
      clearTimeout(timer);
      return resp;
    } catch (e) {
      clearTimeout(timer);
      lastErr = e;
      if (!_isRetryableError(e) || attempt >= maxRetries) throw e;
      attempt++;
      await new Promise(r => setTimeout(r, _backoffMs(attempt)));
    }
  }
  throw lastErr || new Error('fetch failed');
}

// ════════════════════════════════════════════════════
// 2026-07-14: 个股↔板块涨停股数
//   缓存 60s (今日涨停池 1 分钟内不变);批量单次接口,max 300 code/批。
//   多股性 (一个 code 多个 L2/L3/L4 板块) → 每个分开显示。
// ════════════════════════════════════════════════════
const _ZT_CHAIN_CACHE_TTL_MS = 60 * 1000;
const _ztChainCache = new Map();   // code -> { ts, rows: [...] }
let _ztChainPendingPromise = null;   // 防止同时 N 个 page 触发 N 次 /api/limitup/per_code
const _ztChainPendingBuf = new Set();

function _ztChainGet(code) {
  const e = _ztChainCache.get(code);
  if (!e) return null;
  if (Date.now() - e.ts > _ZT_CHAIN_CACHE_TTL_MS) {
    _ztChainCache.delete(code);
    return null;
  }
  return e.rows;
}

async function _ztChainFetch(codes) {
  if (!codes || !codes.length) return {};
  const out = {};
  const todo = [];
  for (const c of codes) {
    const hit = _ztChainGet(c);
    if (hit) out[c] = hit;
    else if (!_ztChainPendingBuf.has(c)) todo.push(c);
  }
  if (!todo.length) return out;
  todo.forEach(c => _ztChainPendingBuf.add(c));
  try {
    if (!_ztChainPendingPromise) {
      _ztChainPendingPromise = (async () => {
        try {
          // 一次最多 300,做批次切分
          const batches = [];
          for (let i = 0; i < _ztChainPendingBuf.size; i += 300) {
            batches.push([..._ztChainPendingBuf].slice(i, i + 300));
          }
          const merged = {};
          for (const batch of batches) {
            try {
              const r = await _fetchWithTimeout('/api/limitup/per_code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ codes: batch }),
              });
              if (!r.ok) continue;
              const j = await r.json();
              const counts = (j.data && j.data.counts) || {};
              Object.assign(merged, counts);
            } catch (e) {
              console.warn('limitup/per_code batch fail', e);
            }
          }
          for (const [code, rows] of Object.entries(merged)) {
            _ztChainCache.set(code, { ts: Date.now(), rows: rows || [] });
          }
          return merged;
        } finally {
          _ztChainPendingPromise = null;
          _ztChainPendingBuf.clear();
        }
      })();
    }
    const fresh = await _ztChainPendingPromise;
    Object.assign(out, fresh);
  } catch (e) {
    console.warn('limitup_per_code failed', e);
  }
  // 兜底: cache miss 但接口挂 → 返 [],render 时不显示
  for (const c of todo) if (!(c in out)) out[c] = [];
  // 写回 short-lived cache 以免疯狂反复请求
  for (const c of todo) if (!out[c] || out[c].length) _ztChainCache.set(c, { ts: Date.now(), rows: out[c] || [] });
  return out;
}

function _ztChainRowColor(row) {
  const n = row.zt_count || 0;
  if (n >= 15) return UP;                  // 主线 (≥15)
  if (n >= 5) return ACCENT;               // 二线 (≥5)
  if (n >= 1) return INK2;                 // 1~4 杂毛
  return 'var(--text-dim, #6b6056)';        // 0 灰
}

function _renderZtChainChips(code, opts) {
  opts = opts || {};
  const max = opts.max || 3;                // 单只最多显示几个 chip
  const rows = _ztChainGet(code) || [];
  if (!rows.length) return '';
  // 只要 zt_count > 0 的 (避免一片 0 灰色噪音)。用户可关掉 → opts.includeZero
  const filtered = opts.includeZero ? rows : rows.filter(r => (r.zt_count || 0) > 0);
  if (!filtered.length) return '';
  // 取前 max
  const top = filtered.slice(0, max);
  const more = filtered.length - top.length;
  return top.map(r => {
    const color = _ztChainRowColor(r);
    const title = `${r.level} ${r.chain} · 今日 ${r.zt_count} 家涨停${r.samples && r.samples.length ? ' · 例: ' + r.samples.join(', ') : ''}${r.is_mainline ? ' · ⚡ 主线' : ''}`;
    const lvl = r.level;
    return `<span class="zt-chip" data-level="${lvl}" style="display:inline-flex;align-items:center;gap:3px;padding:1px 5px;border-radius:3px;border:1px solid ${color};color:${color};font-size:10px;margin:0 3px 2px 0;cursor:default" title="${escapeHtml(title)}">
      <span style="opacity:.7;font-weight:600">${lvl}</span>
      <span style="white-space:nowrap">${escapeHtml(r.chain)}</span>
      <b style="font-weight:700">${r.zt_count}</b>
      ${r.is_mainline ? '<span style="color:#ffd84a">⚡</span>' : ''}
    </span>`;
  }).join('') + (more > 0 ? `<span style="font-size:10px;color:var(--text-dim,#6b6056);margin-right:4px">+${more}</span>` : '');
}

async function api(path, opts) {
  opts = opts || {};
  // R6: 长请求 (>500ms) 显示顶部进度条,完成后移除
  const slow = (opts.timeout || _timeoutFor(path)) > 500;
  let _bar;
  if (slow) {
    _bar = setTimeout(() => _showTopProgress(), 400);  // 400ms 后才显示,避免闪烁
  }
  let r;
  try {
    r = await _fetchWithTimeout(path, opts);
  } catch (e) {
    clearTimeout(_bar); _hideTopProgress();
    if (e.name === 'AbortError') {
      const t = (opts.timeout || _timeoutFor(path)) / 1000;
      throw new Error(`请求超时 (${t}s): ${path}`);
    }
    throw e;
  }
  let env;
  try { env = await r.json(); }
  catch { clearTimeout(_bar); _hideTopProgress(); throw new Error(`HTTP ${r.status} (非 JSON)`); }
  if (!env.ok) { clearTimeout(_bar); _hideTopProgress(); throw new Error(env.error || `HTTP ${r.status}`); }
  clearTimeout(_bar); _hideTopProgress();
  return env.data;
}

// R6: 顶部进度条 (CSS class .top-progress 由 style.css R4 定义)
let _topProgTimer = null;
function _showTopProgress() {
  if (document.getElementById('top-progress')) return;
  const el = document.createElement('div');
  el.id = 'top-progress';
  el.className = 'top-progress';
  document.body.appendChild(el);
  // 安全网:最长 30s 强制清除
  _topProgTimer = setTimeout(_hideTopProgress, 30000);
}
function _hideTopProgress() {
  clearTimeout(_topProgTimer);
  const el = document.getElementById('top-progress');
  if (el) el.remove();
}

async function apiRaw(path, opts) {
  return await _fetchWithTimeout(path, opts);
}

// ────────────────────────────────────────────
// keepalive 心跳 — 防止 tunnel / NAT idle 切断所有闲置连接
// 每 25s 发一次轻量 /api/health (200 byte),让远端 cache 和 nginx 代理识别为活
// ────────────────────────────────────────────
let _kaState = 'idle';  // 'idle' | 'sending' | 'offline'
function _keepaliveTick() {
  if (!navigator.onLine) return;  // 系统层就离线,别发
  // R6: tab 隐藏时不发 — 节省电池 + 减少无用请求
  if (document.hidden) return;
  // 用 sendBeacon 而非 fetch:keep-alive 模式下最稳,不会被 page unload kill
  // 但 sendBeacon 不能读响应,只用 fire-and-forget
  const data = new Blob([''], { type: 'application/json' });
  try {
    const ok = navigator.sendBeacon && navigator.sendBeacon('/api/health', data);
    if (!ok) {
      // sendBeacon 不支持时降级到 fetch keepalive
      fetch('/api/health', { method: 'GET', keepalive: true }).catch(() => {});
    }
    if (_kaState !== 'ok') _setNetworkStatus('ok');
  } catch {
    _setNetworkStatus('offline');
  }
}
setInterval(_keepaliveTick, 25_000);

// 全局 fetch 失败监控 — 触发 UI 网络状态条
window.addEventListener('online',  () => _setNetworkStatus('ok'));
window.addEventListener('offline', () => _setNetworkStatus('offline'));
// 拦截 api() 函数抛错,标记离线
const _origApi = api;
function _markOfflineOnApiErr(_path, e) {
  if (_isRetryableError(e) || /timeout|网络|fetch/i.test(String(e?.message))) {
    _setNetworkStatus('switching');
    // 1.5s 后再降为 offline(短抖动不算掉线)
    clearTimeout(_markOfflineOnApiErr._t);
    _markOfflineOnApiErr._t = setTimeout(() => _setNetworkStatus('offline'), 1500);
  }
}
// 在 api 外层 wrap 一下
const _origApiWrap = api;
async function _apiWithStatus(...args) {
  try {
    return await _origApiWrap(...args);
  } catch (e) {
    _markOfflineOnApiErr(args[0], e);
    throw e;
  }
}
// 不替换 api()(会被 ref 引用),只暴露新名字用
window.__api = _apiWithStatus;

// ────────────────────────────────────────────
// 网络状态条 — 把 _kaState / online 事件映射到 DOM pill
//   ok        绿色 dot + "在线"
//   switching 橙色 dot + "切换中"   (短抖动中,1.5s 没恢复就降为 offline)
//   offline   红色 dot + "断线"
// ────────────────────────────────────────────
const _netPill = () => document.getElementById('net-pill');
const _netText = () => document.getElementById('net-text');
const _NET_LABELS = {
  ok: '在线',
  switching: '切换中',
  offline: '断线 · 自动重连',
};
let _netHideTimer = null;
function _setNetworkStatus(state) {
  const pill = _netPill();
  if (!pill) return;
  _kaState = state;
  pill.dataset.state = state;
  pill.hidden = false;
  if (_netText()) _netText().textContent = _NET_LABELS[state] || state;
  pill.title = state === 'ok'
    ? '连接正常 · 25s 心跳保活'
    : state === 'switching'
      ? '网络抖动中 · 已自动重试'
      : '远端连接断开 · 等待网络恢复';
  // ok 状态下 2.5s 后隐藏 pill(避免长期占位),非 ok 持续显示
  clearTimeout(_netHideTimer);
  if (state === 'ok') {
    _netHideTimer = setTimeout(() => {
      const p = _netPill();
      if (p && p.dataset.state === 'ok') p.hidden = true;
    }, 2500);
  }
  // offline 触发一次温和 toast
  if (state === 'offline') {
    try { toast('网络连接已断开,正在等待恢复…', 'error', 3000); } catch {}
  }
}

// ────────────────────────────────────────────
// toast 队列 — 多个错误同时发生时按 FIFO 依次展示,不互相覆盖
// 解决之前 toast 单实例 + 短覆盖 = 用户看不到关键错误的问题
// ────────────────────────────────────────────
const toastEl = $('#toast');
const _toastQueue = [];
let _toastActive = false;
function _drainToast() {
  if (_toastActive) return;
  const next = _toastQueue.shift();
  if (!next || !toastEl) return;
  _toastActive = true;
  toastEl.textContent = next.msg;
  toastEl.className = `toast toast-${next.kind}`;
  toastEl.hidden = false;
  setTimeout(() => {
    toastEl.hidden = true;
    _toastActive = false;
    _drainToast();
  }, next.ms);
}
function toast(msg, kind = 'info', ms = 2400) {
  if (!toastEl) return;
  if (kind === 'error') ms = Math.max(ms, 3200);  // error 至少 3.2s 让人能读完
  _toastQueue.push({ msg, kind, ms });
  _drainToast();
}

// R6: 通用工具 — debounce / skeleton
function debounce(fn, ms = 250) {
  let t = null;
  return function(...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}
function skeletonHTML(lines = 3) {
  return Array.from({length: lines}, () =>
    '<div class="skeleton skel-line" style="width:' + (60 + Math.random()*40) + '%"></div>'
  ).join('');
}
function skeletonTable(rows = 5, cols = 6) {
  let html = '<table class="data-table"><tbody>';
  for (let r = 0; r < rows; r++) {
    html += '<tr>';
    for (let c = 0; c < cols; c++) {
      const w = 50 + Math.round(Math.random() * 40);
      html += '<td><div class="skeleton skel-line" style="width:' + w + '%"></div></td>';
    }
    html += '</tr>';
  }
  return html + '</tbody></table>';
}

// ────────────────────────────────────────────
// 顶部进度条 — 长操作(>3s)的视觉反馈
// 用法: _showLoading('回测中…') / _hideLoading()
// 支持多个并行操作 — 引用计数,所有 hide 后才真正隐藏
// ────────────────────────────────────────────
const _topLoadingEl = $('#top-loading');
let _topLoadingCount = 0;
let _topLoadingLabel = '';
function _showLoading(label) {
  _topLoadingCount++;
  if (label) _topLoadingLabel = label;
  if (_topLoadingEl) _topLoadingEl.classList.add('active');
  // 在 ts-stamp 旁显示标签, 不需要单独的UI
  const ts = $('#ts-stamp');
  if (ts && label) ts.title = `⏳ ${label}`;
}
function _hideLoading() {
  _topLoadingCount = Math.max(0, _topLoadingCount - 1);
  if (_topLoadingCount === 0 && _topLoadingEl) _topLoadingEl.classList.remove('active');
  const ts = $('#ts-stamp');
  if (ts) ts.title = '';
}

// ────────────────────────────────────────────
// 视图切换
// ────────────────────────────────────────────
let _currentViewName = null;
let _currentStockCode = null;
// R-fix-2026-07-15: view 入栈 — 浏览器后退逐层回到上一个 view
// 首屏 dash 不入栈(避免首次后退直接退出站),每次 pushView 入栈一个名字
const _VIEW_STACK = [];
function showView(name, opts) {
  opts = opts || {};
  const cur = _currentViewName;
  // R-ui-012: 先清理上一个 view 的资源(timers/interval) → 避免在多个 view 间反复切页
  // 时堆叠定时器、把后台 fetcher 全部 hold 住
  if (cur && cur !== name && _VIEW_LEAVE_HOOKS[cur]) {
    try { _VIEW_LEAVE_HOOKS[cur](); } catch (e) { console.warn('leave hook err:', e); }
  }
  $$('.view').forEach(v => v.hidden = (v.dataset.view !== name));
  $$('.tabbar-item').forEach(b => b.classList.toggle('active', b.dataset.jump === name));
  $$('.toptab').forEach(b => b.classList.toggle('active', b.dataset.jump === name));
  $$('.sidebar-item').forEach(b => b.classList.toggle('active', b.dataset.jump === name));
  // 同步 main 的 canvas 视图类名 (用于差异化 max-width)
  const main = $('.canvas');
  if (main) {
    main.className = main.className.replace(/\bis-\w+/g, '').trim();
    main.classList.add('is-' + name);
  }
  if (name === 'dash')    refreshTicker();
  if (name === 'optimize') loadReports();
  if (name === 'laws')    renderLawsOnce();
  if (name === 'review')  _reviewOnViewEnter();
  if (name === 'ai-review') _airvOnViewEnter();
  _currentViewName = name;
  // R5: 写 hash 便于深链 & 浏览器后退
  const curHash = location.hash.replace(/^#/, '');
  const want = (name === 'stock' && _currentStockCode) ? `stock=${_currentStockCode}` : name;
  if (curHash !== want && curHash.split('=')[0] !== name) {
    try {
      // R-fix-2026-07-15: 默认 push=true (用户主动导航),init / popstate 走 push:false
      const usePush = opts.push !== false && cur;
      if (usePush) {
        history.pushState({ view: name, stock: _currentStockCode }, '', '#' + want);
        _VIEW_STACK.push({ view: name, stock: _currentStockCode });
        if (_VIEW_STACK.length > 20) _VIEW_STACK.shift();
      } else {
        history.replaceState({ view: name, stock: _currentStockCode }, '', '#' + want);
      }
    } catch (e) {}
  }
  // 触发全局 view-enter 事件,R5 解耦各模块初始化
  document.dispatchEvent(new CustomEvent('view-enter', { detail: { name } }));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
// 浏览器后退/前进 → 切回栈上 view,不重新触发 push
window.addEventListener('popstate', (e) => {
  const state = e.state || {};
  const targetView = state.view || _viewFromHash();
  if (targetView && targetView !== _currentViewName) {
    showView(targetView, { push: false });
  }
});
function _viewFromHash() {
  const h = location.hash.replace(/^#/, '').split('=')[0];
  return h || 'dash';
}

// R-ui-012: view 离开钩子注册表 - { 'view-name': () => { ... cleanup ... } }
// 比让每个 enter hook 兼任 leave 更不容易漏 (之前 capTimer/flowsTimer
// 在反复切页时无限累加, +1s 一次拉取 → 服务端被拖垮)
const _VIEW_LEAVE_HOOKS = {};
function _registerViewLeave(name, fn) { _VIEW_LEAVE_HOOKS[name] = fn; }

// R5: hash 路由 — 让 #stock=603881 / #review / #dragons 都能深链
function _routeFromHash() {
  const h = (location.hash || '').replace(/^#/, '');
  if (!h) return showView('dash', { push: false });   // 首屏 → replace 而非 push
  const [name, arg] = h.split('=');
  const valid = ['dash','stock','review','dragons','screener','watchlist','optimize','laws','all_stocks','ai-review'];
  if (!valid.includes(name)) return showView('dash', { push: false });
  if (name === 'stock' && arg) {
    const code = arg.match(/\d{6}/)?.[0];
    if (code) {
      $('#stock-code').value = code;
      showView('stock', { push: false });
      loadStockDetail(code);
      return;
    }
  }
  showView(name, { push: false });
}
window.addEventListener('hashchange', _routeFromHash);
// 页面加载时跑一次 (放在 DOMContentLoaded 之后,所以用 setTimeout 0)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(_routeFromHash, 0));
} else {
  setTimeout(_routeFromHash, 0);
}

// ────────────────────────────────────────────
// 数字 / 颜色格式化
// ────────────────────────────────────────────

// 从 quote 取第一个存在的字段 — 兼容 server 标准化字段 (pe/total_mcap) 与
// 上游原字段 (市盈率/总市值) 同时存在或只有一个的情况。
function qGet(q, ...keys) {
  if (!q) return null;
  for (const k of keys) {
    const v = q[k];
    if (v !== null && v !== undefined && v !== '' && v !== '-' && v !== 'None') {
      return v;
    }
  }
  return null;
}

const fmtN = (n, d = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtPct = (n, d = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const v = Number(n);
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(d)}%`;
};
const fmtAmt = (n) => {
  if (n === null || n === undefined) return '—';
  const v = Number(n);
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + ' 亿';
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + ' 万';
  return v.toFixed(0);
};
const colorFor = (v) => v > 0 ? UP : (v < 0 ? DOWN : INK2);

// ────────────────────────────────────────────
// 市场概览 + ticker
// ────────────────────────────────────────────
// P-perf: localStorage 快照缓存 — 页面重载时免白屏
const _DASHBOARD_CACHE_KEY = 'tx3_dash_cache';
const _DASHBOARD_CACHE_TTL_MS = 120_000;  // 2min 过期

function _dashCacheSave(data) {
  try {
    localStorage.setItem(_DASHBOARD_CACHE_KEY, JSON.stringify({
      ts: Date.now(), data
    }));
  } catch (e) { /* quota */ }
}
function _dashCacheLoad() {
  try {
    const raw = localStorage.getItem(_DASHBOARD_CACHE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (Date.now() - p.ts > _DASHBOARD_CACHE_TTL_MS) {
      localStorage.removeItem(_DASHBOARD_CACHE_KEY);
      return null;
    }
    return p.data;
  } catch (e) { return null; }
}

async function refreshTicker() {
  const bar = $('#tickerbar');
  try {
    const data = await api('/api/market/overview');
    lastRefreshTs = data.ts || Date.now() / 1000;
    const indices = data.indices || [];
    const fragments = indices.map(i => {
      const c = colorFor(i.change_pct);
      return `<span class="tk-item">
        <span class="tk-name">${escapeHtml(i.name)}</span>
        <span class="tk-price">${fmtN(i.price, 2)}</span>
        <span class="tk-chg" style="color:${c}">${fmtPct(i.change_pct)}</span>
      </span>`;
    });
    bar.innerHTML = fragments.join('') +
      `<span class="tk-item"><span class="tk-name">涨停</span><span class="tk-price">${data.limit_up || 0}</span></span>`;
    // 顶部小时间戳
    if ($('#ts-stamp')) {
      const d = new Date(lastRefreshTs * 1000);
      $('#ts-stamp').textContent = `已刷新 ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
    }
    // P-perf: 分阶段渲染 dashboard — 先刷缓存,再串行独立超时
    _dashLoadPhased();
  } catch (e) {
    bar.innerHTML = '<div class="ticker-empty">市场数据暂不可达 · ' + e.message + '</div>';
    // 即使 ticker 失败,也尝试渲染缓存数据
    _dashLoadPhased();
  }
}

// P-perf: 分阶段渲染: 缓存(0ms) → A股(2s) → 美韩(4s) → 板块(6s)
function _dashLoadPhased() {
  // Phase 0: 立即从 localStorage 渲染
  const cached = _dashCacheLoad();
  if (cached) {
    if (cached.signal) {
      const s = cached.signal;
      if (s.a_share) _paintSignalCol('a', s.a_share, false);
      if (s.kr)      _paintSignalCol('kr', s.kr, false);
      if (s.us)      _paintSignalCol('us', s.us, false);
    }
    if (cached.hot) {
      _paintHotSectors(cached.hot);
    }
  }

  // Phase 1: 异步刷新 signal + hot sectors
  _dashRefreshSignal();
  _dashRefreshHot();
}

async function _dashRefreshSignal() {
  try {
    const r = await _fetchWithTimeout('/api/dashboard/signal', { timeout: 12000 });
    const env = await r.json();
    const d = env.data || {};
    if (env.ok && d.a_share) {
      _paintSignalCol('a', d.a_share, true);
      if (d.kr) _paintSignalCol('kr', d.kr, true);
      if (d.us) _paintSignalCol('us', d.us, true);
      // 写缓存
      const cached = _dashCacheLoad() || {};
      cached.signal = d;
      _dashCacheSave(cached);
    }
  } catch (e) {
    // 静默 — 缓存数据已在 Phase 0 展示
    console.debug('[dash] signal refresh failed:', e.message);
  }
}

async function _dashRefreshHot() {
  try {
    const r = await _fetchWithTimeout('/api/dashboard/hot_sectors', { timeout: 12000 });
    const env = await r.json();
    const d = env.data || {};
    if (env.ok && (d.mainline || []).length) {
      _paintHotSectors(d);
      // 写缓存
      const cached = _dashCacheLoad() || {};
      cached.hot = d;
      _dashCacheSave(cached);
    }
  } catch (e) {
    console.debug('[dash] hot refresh failed:', e.message);
  }
}

function _paintHotSectors(d) {
  const tiles = d.mainline || [];
  const host = $('#hot-sectors-tiles');
  const sub  = $('#hot-sectors-sub');
  if (!host) return;
  if (!tiles.length) {
    host.innerHTML = '<div class="hs-empty"><span class="retry-link" data-action="refresh-dashboard">暂无主线数据 · 点此重试</span></div>';
    if (sub) sub.textContent = '';
    return;
  }
  host.innerHTML = tiles.map(t => {
    const pct = Number(t.change_pct) || 0;
    const cls = pct > 0.1 ? 'up' : pct < -0.1 ? 'down' : '';
    const flow = Number(t.net_inflow_yi) || 0;
    const flowStr = (flow > 0 ? '+' : '') + flow.toFixed(1) + ' 亿';
    const flowCls = flow > 0 ? 'up' : flow < 0 ? 'down' : '';
    const ztN = Number(t.zt_count) || 0;
    const ztBadge = ztN > 0 ? `<span class="hs-tile-zt" title="该板块涨停数">⚡${ztN}</span>` : '';
    return `<div class="hs-tile" title="${escapeHtml(t.name)} · 涨停 ${ztN} · 资金净流入 ${flowStr}">
      <span class="hs-tile-name">${escapeHtml(t.name)}</span>
      <span class="hs-tile-pct ${cls}">${(pct > 0 ? '+' : '') + pct.toFixed(2)}%</span>
      <span class="hs-tile-flow ${flowCls}">资金 ${flowStr}</span>
      ${ztBadge}
    </div>`;
  }).join('');
  if (sub) {
    const sent = d.sentiment || {};
    sub.textContent = sent.label
      ? `情绪 ${sent.label} · 涨停 ${sent.zt_count || 0} · 最高 ${sent.max_streak || 0} 连板`
      : '';
  }
}

// ── 三市场信号面板 (首页) ─────────────────────────────────
const _VERDICT_LABEL = { allow: '适合买入', cautious: '谨慎参与', block: '不适合买入' };
const _VERDICT_DOT   = { allow: '🟢', cautious: '🟡', block: '🔴' };

// P-perf: sessionStorage 个股缓存 — 切股/回退时免白屏
const _STOCK_CACHE_KEY_PREFIX = 'tx3_stock_';

function _stockCacheKey(code, date) {
  return _STOCK_CACHE_KEY_PREFIX + code + '_' + (date || 'today');
}
function _stockCacheSave(code, date, data) {
  try {
    const key = _stockCacheKey(code, date);
    sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), data }));
  } catch (e) { /* quota */ }
}
function _stockCacheLoad(code, date) {
  try {
    const key = _stockCacheKey(code, date);
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const p = JSON.parse(raw);
    // 实时模式下 60s 过期;历史快照模式 300s
    const ttl = date ? 300_000 : 60_000;
    if (Date.now() - p.ts > ttl) {
      sessionStorage.removeItem(key);
      return null;
    }
    return p.data;
  } catch (e) { return null; }
}

// prefix → market 映射 (动画用)
const _MARKET_OF_PREFIX = { a: 'a', kr: 'kr', us: 'us' };

// P-perf: refreshDashboard 向后兼容 — 新实现走 _dashLoadPhased
async function refreshDashboard() {
  _dashLoadPhased();
}

function _paintSignalError(prefix, msg) {
  const verdictEl = $(`#sig-${prefix}-verdict`);
  if (verdictEl) {
    verdictEl.className = 'signal-verdict';
    verdictEl.textContent = '—';
  }
  const pctEl = $(`#sig-${prefix}-pct`);
  if (pctEl) {
    pctEl.className = 'sig-pct flat';
    pctEl.textContent = '—';
  }
  const headEl = $(`#sig-${prefix}-head`);
  if (headEl) {
    headEl.innerHTML = `<span class="retry-link" data-action="refresh-dashboard">${escapeHtml(msg)}</span>`;
  }
  const listEl = $(`#sig-${prefix}-news`);
  if (listEl) listEl.innerHTML = '';
}

// ────────────────────────────────────────────
// DASHBOARD
// ────────────────────────────────────────────
$('#dash-search-go')?.addEventListener('click', () => {
  const q = $('#dash-search').value.trim();
  if (q) gotoStock(q);
});
$('#dash-search')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('#dash-search-go').click();
});

function gotoStock(q) {
  // 6 位代码直接进详情(绕过搜索结果列表,自动刷新数据)
  const code = String(q || '').trim();
  if (/^\d{6}$/.test(code)) {
    $('#stock-search').value = code;
    showView('stock');
    loadStockDetail(code);
    return;
  }
  // 中文名/模糊匹配回退到搜索
  $('#stock-search').value = code;
  showView('stock');
  doStockSearch();
}

// ────────────────────────────────────────────
// BACKTEST — 含回撤可视化
// ────────────────────────────────────────────
// 2026-07-15: 统计层回测 (KPI/分位/场景/板块) — 独立按钮,共享同一面板
$('#bt-stats-run')?.addEventListener('click', async () => {
  const body = {
    start:    $('#bt-start')?.value || '2026-06-01',
    end:      $('#bt-end')?.value   || '2026-07-14',
    top_n:    3,
    hold_days: 5,
    sample:   parseInt($('#bt-sample')?.value || '60', 10),
    sell_mode: $('#bt-sell')?.value || 'rule',
  };
  const btn = $('#bt-stats-run');
  if (btn) { btn.disabled = true; btn.textContent = '统计层回测中…'; }
  $('#bt-kpis').innerHTML = '<div class="dim" style="padding:2rem;text-align:center">统计回测运行中 (90s 上限) …</div>';
  toast(`开始统计回测 ${body.start} → ${body.end}, sample=${body.sample}`, 'info', 3000);
  try {
    const data = await api('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    renderBacktestResults(data);
    toast(`统计回测完成 · ${data.summary?.trades || 0} 笔`, 'success');
  } catch (e) {
    toast('统计回测失败:' + e.message, 'error', 4000);
    $('#bt-kpis').innerHTML = `<div class="dim" style="padding:2rem;text-align:center;color:${DOWN}">回测失败:${e.message}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🧮 统计层回测'; }
  }
});

function renderBacktestResults(data) {
  const s = data.summary || {};
  // KPI grid — 原 8 项 + 2026-07-14 新 6 项统计层 (中位数/标准差/月胜负/期望值/Sharpe/Sortino)
  const kpis = [
    ['交易笔数',  s.trades ?? 0,         '笔'],
    ['胜率',      s.win_rate_pct ?? 0,   '%'],
    ['平均收益',  s.avg_return_pct ?? 0, '%'],
    ['中位收益',  s.median_return_pct ?? 0, '%'],
    ['标准差',    s.stddev_return_pct ?? 0, '%'],
    ['月均收益',  s.monthly_avg_return_pct ?? 0, '%'],
    ['盈亏比',    s.profit_factor ?? 0,  ''],
    ['期望值',    s.expectancy_per_trade_pct ?? 0, '%'],
    ['最大回撤',  s.max_drawdown_pct ?? 0, '%'],
    ['恢复因子',  s.recovery_factor ?? 0, '×'],
    ['最佳单笔',  s.best_trade_pct ?? 0, '%'],
    ['最差单笔',  s.worst_trade_pct ?? 0, '%'],
  ];
  $('#bt-kpis').innerHTML = kpis.map(([k, v, u]) => {
    const color = (typeof v === 'number' && (k.includes('回撤') || k.includes('最差'))) ? DOWN
                : (typeof v === 'number') ? colorFor(v) : INK;
    return `<div class="kpi">
      <div class="kpi-k">${k}</div>
      <div class="kpi-v" style="color:${color}">${fmtN(v, 2)}<span class="kpi-u">${u}</span></div>
    </div>`;
  }).join('');

  // 风控小卡片（铁律三.4）
  const riskActions = data.risk_actions || [];
  // 月胜负小卡片
  const monthCard = `
    <div class="bt-risk" style="border-color:${parseFloat(s.month_win_rate_pct||0)>=50?UP:DOWN}">
      <span class="bt-risk-label">月度胜率 · 实证</span>
      <span class="bt-risk-stat"><strong style="color:${parseFloat(s.month_win_rate_pct||0)>=50?UP:DOWN}">${s.month_win_rate_pct ?? 0}%</strong></span>
      <span class="bt-risk-stat">胜 <strong style="color:${UP}">${s.positive_months ?? 0}</strong> 月 / 负 <strong style="color:${DOWN}">${s.negative_months ?? 0}</strong> 月${(s.zero_months??0)>0?` / 平 ${s.zero_months}`:''}</span>
    </div>`;
  const cardHtml = `
    <div class="bt-risk">
      <span class="bt-risk-label">铁律三.4 · 回撤风控</span>
      <span class="bt-risk-state ${data.risk_state === 'reduced' ? 'on' : ''}">${data.risk_state === 'reduced' ? '已减仓' : '正常'}</span>
      <span class="bt-risk-stat">峰值 ${fmtN(data.peak_equity, 3)} → 当前 ${fmtN(data.final_equity, 3)}</span>
      <span class="bt-risk-stat">触发 <strong>${riskActions.length}</strong> 次 · 减仓日 <strong>${data.risk_reduced_days || 0}</strong></span>
    </div>${monthCard}`;
  $('#bt-risk-host').innerHTML = cardHtml;

  // equity curve with drawdown overlay
  const monthly = data.monthly || [];
  let cum = 0;
  const points = monthly.map(m => {
    cum += m.monthly_return_pct || 0;
    return [m.month + '-01', cum];
  });
  drawEquityChart(points);

  // 月度表 — 2026-07-14 多加正/负列
  const tbody = $('#bt-monthly tbody');
  if (!monthly.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">无交易</td></tr>';
  } else {
    tbody.innerHTML = monthly.map(m => `<tr>
      <td>${m.month}</td>
      <td class="num">${m.trades}</td>
      <td class="num" title="胜${m.wins}/负${m.losses}">${m.win_rate_pct?.toFixed(1) || 0}%</td>
      <td class="num" style="color:${colorFor(m.avg_return_pct)}">${fmtPct(m.avg_return_pct)}</td>
      <td class="num" style="color:${colorFor(m.monthly_return_pct)}">${fmtPct(m.monthly_return_pct)}</td>
      <td class="num" style="color:${UP}">${fmtPct(m.max_return_pct)}</td>
      <td class="num" style="color:${DOWN}">${fmtPct(m.min_return_pct)}</td>
    </tr>`).join('');
  }

  // ── 2026-07-14 新统计:分位数 + 分位条形 ──
  renderBacktestPercentiles(s);

  // ── 2026-07-14 新统计:6 套退场场景横向对比 (核心 — 替代"假设有正值就在正值卖") ──
  renderBacktestScenarios(data.scenario_compare || {});

  // ── 2026-07-14 新统计:退出原因分布 + 持有天数分布 ──
  renderBacktestExitBreakdown(data.exit_breakdown || {}, data.hold_distribution || {});

  // ── 2026-07-14 新统计:sector 表现 ──
  renderBacktestSector(data.sector_breakdown || []);

  // 风控动作详情
  if (riskActions.length) {
    const sec = `<div class="card mt-16">
      <div class="card-eyebrow">RISK ACTIONS · 自动减仓记录</div>
      <h3 class="card-h">${riskActions.length} 次触发</h3>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>触发日期</th><th>当时回撤</th><th>top_n</th><th>资金曲线</th><th>状态</th></tr></thead>
          <tbody>${riskActions.map(a => `<tr>
            <td>${a.date}</td>
            <td class="num" style="color:${DOWN}">${a.drawdown_pct?.toFixed(2)}%</td>
            <td class="num">${a.from_top} → <strong style="color:${ACCENT}">${a.to_top}</strong></td>
            <td class="num">${a.peak_equity} → ${a.equity}</td>
            <td><span class="badge ${a.to_state === 'reduced' ? 'badge-warn' : 'badge-good'}">${a.to_state === 'reduced' ? '减仓' : '恢复'}</span></td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
    </div>`;
    $('#bt-risk-actions-host').innerHTML = sec;
  } else {
    $('#bt-risk-actions-host').innerHTML = '';
  }
}

// ── 2026-07-14 新统计:分位数 + 期望 shortfall ──
function renderBacktestPercentiles(s) {
  const host = $('#bt-percentiles-host');
  if (!host) return;
  const p = s.percentiles || {};
  const keys = ['p5','p10','p25','p50','p75','p90','p95'];
  if (!keys.some(k => p[k])) { host.innerHTML = ''; return; }
  const rows = keys.map(k => {
    const v = p[k] || 0;
    return `<tr>
      <td>${k.toUpperCase()}</td>
      <td class="num" style="color:${colorFor(v)}">${fmtPct(v)}</td>
    </tr>`;
  }).join('');
  host.innerHTML = `<div class="card mt-16">
    <div class="card-eyebrow">PERCENTILES · 收益分布分位数 (实证,无假设)</div>
    <h3 class="card-h">中位 p50=${fmtPct(p.p50||0)} · 尾端 p5/p95 决定你实际惨烈程度</h3>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>分位</th><th class="num">单笔收益</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="caption dim" style="margin-top:.5em">
      • <b>中位数 ${fmtPct(p.p50||0)}</b> 比平均更能代表"普通一日"的真实体感<br>
      • <b>左尾 p5=${fmtPct(p.p5||0)}</b> ≈ Expected Shortfall,5% 最差日的均值 = <b>${fmtPct(s.expected_shortfall_p5_pct||0)}</b><br>
      • <b>右尾 p95=${fmtPct(p.p95||0)}</b> 看运气上限 — 别把单笔运气当策略能力
    </p>
  </div>`;
}

// ── 2026-07-14 新统计:7 套退场场景横向对比 (核心:代替"假设+5%落袋") ──
function renderBacktestScenarios(sc) {
  const host = $('#bt-scenarios-host');
  if (!host) return;
  const KINDS = [
    { key: 'rule_pri', label: '规则 (铁律优先)', desc: 'stop3 → trail8 → close' },
    { key: 'best',     label: '理论最佳',       desc: '期内最高价' },
    { key: 'trail_3pct', label: '止盈 +3%',     desc: '回撤 1.5%' },
    { key: 'trail_5pct', label: '止盈 +5%',     desc: '回撤 2%' },
    { key: 'trail_8pct', label: '止盈 +8%',     desc: '回撤 3% (用户原"5%落袋"近似)' },
    { key: 'stop_3pct',  label: '纯止损 3%',    desc: '不主动止盈' },
    { key: 'close',      label: '朴素持有 (基准)', desc: '持有到期收盘' },
  ];
  const rows = KINDS.filter(k => sc[k.key]).map(k => {
    const r = sc[k.key];
    const pf = r.profit_factor == null ? '∞' : fmtN(r.profit_factor,2);
    return `<tr>
      <td><b>${k.label}</b><br><span class="caption dim">${k.desc}</span></td>
      <td class="num">${r.n}</td>
      <td class="num" style="color:${colorFor(r.avg_pct)}">${fmtPct(r.avg_pct)}</td>
      <td class="num" style="color:${colorFor(r.median_pct)}">${fmtPct(r.median_pct)}</td>
      <td class="num">${fmtPct(r.win_rate_pct||0)}%</td>
      <td class="num">${pf}</td>
      <td class="num" style="color:${colorFor(r.cum_return_pct)}">${fmtPct(r.cum_return_pct)}</td>
      <td class="num">${fmtPct(r.worst_pct)} ~ ${fmtPct(r.best_pct)}</td>
      <td class="num dim">${(r.p25_p75||[]).map(fmtPct).join(' ~ ')}</td>
    </tr>`;
  }).join('');
  const gap = sc._rule_vs_close_gap != null ? `
    <p class="caption" style="margin:.4em 0">
      <b>规则相对朴素持有的实证增益:</b> ${fmtPct(sc._rule_vs_close_gap)} (每笔交易平均)<br>
      <b>规则相对理论最佳的比值:</b> ${sc._rule_efficiency_to_best_pct ?? '?'}% (规则捕到了多少"运气最优")</p>` : '';
  host.innerHTML = `<div class="card mt-16">
    <div class="card-eyebrow">SCENARIO COMPARE · 7 套退场实证对比 (同价格分布,不靠假设)</div>
    <h3 class="card-h">"规则" 必须显著强过 <code>close</code>(朴素持有) 才有意义</h3>
    <div class="table-wrap"><table class="data-table">
      <thead><tr>
        <th>退场策略</th><th class="num">N</th><th class="num">均值</th><th class="num">中位</th>
        <th class="num">胜率</th><th class="num">盈亏比</th><th class="num">累计</th>
        <th class="num">区间</th><th class="num">p25~p75</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="9" class="empty">无数据</td></tr>'}</tbody>
    </table></div>${gap}
  </div>`;
}

// ── 2026-07-14 新统计:退出原因分布 + 持有天数分布 ──
function renderBacktestExitBreakdown(eb, hd) {
  const host = $('#bt-exit-host');
  if (!host) return;
  const ebRows = Object.entries(eb).filter(([k]) => k !== '_total').map(([k, v]) => {
    const color = k.includes('stop') ? DOWN : (k.includes('trail') ? UP : INK);
    return `<tr>
      <td><b style="color:${color}">${escapeHtml(k)}</b></td>
      <td class="num">${v.count}</td>
      <td class="num">${fmtPct(v.pct)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="3" class="empty">无数据</td></tr>';
  const total = eb._total || 0;

  // 持有天数: 渲染简单分布表
  const hdBuckets = hd.buckets || {};
  const hdKeys = Object.keys(hdBuckets).sort((a,b) => +a - +b);
  const hdRows = hdKeys.map(k => `<tr>
    <td class="num">${k} 日</td>
    <td class="num">${hdBuckets[k]}</td>
    <td class="num">${fmtPct(hdBuckets[k] / Math.max(1, total) * 100)}</td>
  </tr>`).join('') || '<tr><td colspan="3" class="empty">无数据</td></tr>';

  host.innerHTML = `<div class="card mt-16">
    <div class="card-eyebrow">EXIT / HOLD · 规则到底是怎么平仓的?</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1em">
      <div>
        <h4 style="margin:.3em 0">退出原因分布 (N=${total})</h4>
        <table class="data-table"><thead><tr><th>触发</th><th class="num">N</th><th class="num">占比</th></tr></thead>
        <tbody>${ebRows}</tbody></table>
      </div>
      <div>
        <h4 style="margin:.3em 0">实际持有天数分布</h4>
        <table class="data-table"><thead><tr><th class="num">天数</th><th class="num">N</th><th class="num">占比</th></tr></thead>
        <tbody>${hdRows}</tbody></table>
        <p class="caption dim" style="margin-top:.4em">
          平均 <b>${fmtN(hd.avg||0,1)}</b> 日 · 中位 <b>${fmtN(hd.median||0,1)}</b> · p10/p90 ${hd.p10||0}/${hd.p90||0} 日
        </p>
      </div>
    </div>
  </div>`;
}

// ── 2026-07-14 新统计:sector 表现 ──
function renderBacktestSector(sectors) {
  const host = $('#bt-sector-host');
  if (!host) return;
  if (!sectors.length) { host.innerHTML = ''; return; }
  const rows = sectors.slice(0, 20).map(s => {
    const wr = parseFloat(s.win_rate_pct);
    const avg = parseFloat(s.avg_return_pct);
    const sum = parseFloat(s.sum_return_pct);
    return `<tr>
      <td>${escapeHtml(s.sector)}</td>
      <td class="num">${s.trades}</td>
      <td class="num" style="color:${wr>=50?UP:DOWN}">${fmtPct(s.win_rate_pct)}</td>
      <td class="num" style="color:${colorFor(avg)}">${fmtPct(s.avg_return_pct)}</td>
      <td class="num" style="color:${colorFor(sum)}">${fmtPct(s.sum_return_pct)}</td>
      <td class="num" style="color:${UP}">${fmtPct(s.best_pct)}</td>
      <td class="num" style="color:${DOWN}">${fmtPct(s.worst_pct)}</td>
    </tr>`;
  }).join('');
  host.innerHTML = `<div class="card mt-16">
    <div class="card-eyebrow">SECTOR · 按板块拆表现 (验主线筛选是否真有效)</div>
    <h3 class="card-h">显示累计收益排序前 20 个板块</h3>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>板块</th><th class="num">笔数</th><th class="num">胜率</th>
        <th class="num">单笔均</th><th class="num">累计</th>
        <th class="num">单笔最佳</th><th class="num">单笔最差</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="caption dim" style="margin-top:.4em">
      <b>怎么读:</b> "累计"为正 → 板块规则捡到肉;为负 → 规则在这个板块选出的票明显跑输大盘。<br>
      <b>主线筛选验证:</b> 如果累计 Top 板块正是当时的市场主线 → 选股层有效;否则要回去查 layer2 情绪/主线判定。
    </p>
  </div>`;
}

function drawEquityChart(points) {
  const dom = $('#equity-chart');
  if (!dom) return;
  if (echartsCharts.equity) echartsCharts.equity.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.equity = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 50, right: 24, top: 20, bottom: 36 },
    tooltip: { trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a241c', textStyle: { color: INK } },
    xAxis: {
      type: 'category', data: points.map(p => p[0]),
      axisLine: { lineStyle: { color: '#2a2825' } },
      axisLabel: { color: INK2, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: INK2, fontSize: 10, formatter: '{value}%' },
    },
    series: [{
      name: '累计收益 %', type: 'line', smooth: true,
      data: points.map(p => p[1]),
      lineStyle: { color: ACCENT, width: 2 },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: 'rgba(212,160,86,0.25)' }, { offset: 1, color: 'rgba(212,160,86,0)' }] } },
      symbol: 'circle', symbolSize: 4, itemStyle: { color: ACCENT },
    }],
  });
}

// ────────────────────────────────────────────
// STOCK SEARCH + DETAIL + AI
// ────────────────────────────────────────────
let searchTimer = null;
$('#stock-search')?.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doStockSearch, 250);
});
$('#stock-go')?.addEventListener('click', doStockSearch);
$('#stock-search')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doStockSearch();
});

// ── 查询历史(服务端 SQLite 永久化,2026-07-11 改造) ──
// 之前用 localStorage,浏览器清数据就丢;现在走 /api/stock_history,
// 跨设备/跨浏览器同步,清缓存也不丢
const _STOCK_HIST_KEY = 'tuixue_stock_history_v1';
const _STOCK_HIST_MAX = 50;          // 服务端上限
let _histCache = null;               // 当前已知的历史(防止 API 抖动时清空)

function _toHistShape(arr) {
  // 兼容旧 localStorage {code, name, ts} 与新服务端 {code, name, last_query_ts}
  return (arr || []).map(it => ({
    code: String(it.code || '').padStart(6, '0'),
    name: it.name || it.code || '',
    ts:   it.ts || (it.last_query_ts ? it.last_query_ts * 1000 : Date.now()),
  })).filter(it => /^\d{6}$/.test(it.code));
}

async function _loadHist() {
  // 主路径:服务端。失败时降级 localStorage。返回时也写一份 localStorage 兜底。
  try {
    const env = await api(`/api/stock_history?limit=${_STOCK_HIST_MAX}`);
    const rows = _toHistShape((env.data || {}).history || []);
    if (rows.length) {
      try { localStorage.setItem(_STOCK_HIST_KEY, JSON.stringify(rows)); } catch {}
    }
    _histCache = rows;
    return rows;
  } catch (e) {
    console.debug('[history] server unreachable, fallback localStorage', e.message);
    try {
      const raw = localStorage.getItem(_STOCK_HIST_KEY);
      const rows = _toHistShape(raw ? JSON.parse(raw) : []);
      _histCache = rows;
      return rows;
    } catch { _histCache = []; return []; }
  }
}
async function _addHist(code, name) {
  if (!code) return;
  code = String(code).padStart(6, '0');
  name = name || code;
  // 本地优先 UI 更新(乐观):直接提到最前
  let arr = (_histCache || []).filter(x => x.code !== code);
  arr.unshift({ code, name, ts: Date.now() });
  _histCache = arr;
  _renderHist();
  // 异步上报服务端
  try {
    await apiRaw('/api/stock_history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, name }),
    });
    // 拉一次最新(含服务端 hit_count/time)保证一致
    await _loadHist();
    _renderHist();
  } catch (e) {
    console.debug('[history] post server failed', e.message);
    // 失败也写一份 localStorage 兜底
    try { localStorage.setItem(_STOCK_HIST_KEY, JSON.stringify(arr)); } catch {}
  }
}
async function _removeHist(code) {
  code = String(code || '').padStart(6, '0');
  _histCache = (_histCache || []).filter(x => x.code !== code);
  _renderHist();
  try {
    await apiRaw(`/api/stock_history/${code}`, { method: 'DELETE' });
  } catch (e) {
    console.debug('[history] delete server failed', e.message);
    try { localStorage.setItem(_STOCK_HIST_KEY, JSON.stringify(_histCache)); } catch {}
  }
}
async function _clearHist() {
  _histCache = [];
  _renderHist();
  try {
    await apiRaw('/api/stock_history', { method: 'DELETE' });
  } catch (e) {
    console.debug('[history] clear server failed', e.message);
  }
}
async function _renderHist() {
  const box = $('#stock-history');
  const list = $('#sh-list');
  if (!box || !list) return;
  const arr = await _loadHist();
  if (!arr.length) { box.hidden = true; return; }
  box.hidden = false;
  list.innerHTML = arr.map(it => `
    <span class="sh-pill" data-code="${it.code}" title="${escapeHtml(it.name)} · ${new Date(it.ts).toLocaleString('zh-CN',{hour12:false})}">
      <span class="sh-code">${escapeHtml(it.code)}</span>
      <span class="sh-name">${escapeHtml((it.name || '').slice(0, 8))}</span>
      <span class="sh-x" data-rm="${it.code}" title="删除">×</span>
    </span>
  `).join('');
  // 绑定点击(排除 ×)
  list.querySelectorAll('.sh-pill').forEach(p => {
    p.addEventListener('click', (e) => {
      if (e.target.dataset.rm) {
        e.stopPropagation();
        _removeHist(e.target.dataset.rm);
        return;
      }
      const c = p.dataset.code;
      if (c) {
        $('#stock-search').value = c;
        loadStockDetail(c);
      }
    });
  });
}
// ────────────────────────────────────────────
// Service Worker 注册 — 离线 fallback / 静态 cache-first
// 由 server.py 在 index.html </head> 前注入 __SW_URL__=/sw.js?v=xxx
// ────────────────────────────────────────────
(function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  if (typeof __SW_URL__ === 'undefined') return;  // server 端没注入(SW 文件不存在)
  // 延迟到 idle,不影响首屏
  const _do = () => navigator.serviceWorker.register(__SW_URL__, { scope: '/' })
    .then((reg) => {
      // 检测到新 SW → 提示刷新 (虽然 server 已带 ?v=,这里作为双重保险)
      if (reg.waiting && navigator.serviceWorker.controller) {
        reg.waiting.postMessage({ type: 'SKIP_WAITING' });
      }
    })
    .catch((e) => console.warn('[SW] register failed', e));
  if ('requestIdleCallback' in window) {
    requestIdleCallback(_do, { timeout: 4000 });
  } else {
    setTimeout(_do, 1500);
  }
})();

// 初始化 + 清空按钮
document.addEventListener('DOMContentLoaded', () => {
  _renderHist();
  $('#sh-clear')?.addEventListener('click', () => {
    if (confirm('清空查询历史?')) _clearHist();
  });
  // R-mob-040: 检测所有 table-wrap 横向溢出 → 加 .has-overflow-x 类 → CSS 显示右边缘渐隐
  _initTableOverflowHints();
  // R10-A: 移动端 sidebar 抽屉控制
  const _menuBtn = document.getElementById('menu-btn');
  if (_menuBtn) _menuBtn.addEventListener('click', _toggleSidebar);
  const _bd = document.getElementById('sidebar-backdrop');
  if (_bd) _bd.addEventListener('click', _closeSidebar);
  // 点击 sidebar 内的导航项 → 移动端自动关闭
  document.querySelectorAll('#sidebar [data-view]').forEach(el => {
    el.addEventListener('click', () => {
      if (window.matchMedia('(max-width: 979px)').matches) _closeSidebar();
    });
  });
  // URL ?code=XXXXX 自动切到个股页 (深链支持)
  const _bootCode = new URLSearchParams(location.search).get('code');
  if (_bootCode && /^\d{6}$/.test(_bootCode.trim())) {
    const c = _bootCode.trim();
    const inp = $('#stock-search'); if (inp) inp.value = c;
    showView('stock');
    loadStockDetail(c);
    currentStockCode = c;
    _startStockPoll(c);
  }
});

async function doStockSearch() {
  const q = $('#stock-search').value.trim();
  const box = $('#stock-search-results');
  if (!q) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="dim" style="padding: .5rem 0">搜索中 …</div>';
  try {
    const data = await api(`/api/stock/search?q=${encodeURIComponent(q)}`);
    const results = data.results || [];
    if (!results.length) {
      box.innerHTML = '<div class="dim" style="padding: .5rem 0">无匹配</div>';
      return;
    }
    box.innerHTML = results.map(s =>
      `<button class="result-pill" data-code="${s.code}" data-name="${s.name}">
        <span class="rp-code">${s.code}</span>
        <span class="rp-name">${s.name}</span>
      </button>`).join('');
    box.querySelectorAll('.result-pill').forEach(p =>
      p.addEventListener('click', () => {
        loadStockDetail(p.dataset.code);
        _addHist(p.dataset.code, p.dataset.name);
      }));
  } catch (e) {
    box.innerHTML = `<div class="dim">搜索失败：${e.message}</div>`;
  }
}

// ────────────────────────────────────────────
// STOCK · 实时轮询 — 页面停留每 10s 拉一次 hero/资金流 (2026-07-11)
// 不重画 K线/分时/AI/板块新闻 — 只 patch 价格 + 资金流柱状图
// ────────────────────────────────────────────
let _stockPollTimer = null;
let _stockPollLastTs = 0;
function _startStockPoll(code) {
  _stopStockPoll();
  if (!code) return;
  // 3s 后先拉一次(让用户进页面 3s 看到第一次跳动),然后 10s 一次
  setTimeout(() => { _pollStockRealtime(code); }, 3000);
  _stockPollTimer = setInterval(() => _pollStockRealtime(code), 10_000);
}
function _stopStockPoll() {
  if (_stockPollTimer) { clearInterval(_stockPollTimer); _stockPollTimer = null; }
}
async function _pollStockRealtime(code) {
  if (!code || code !== currentStockCode) return;          // 已经切到其他股
  // 节流:若上一次还没回(<8s)就跳过,避免重叠
  if (_stockPollLastTs && Date.now() - _stockPollLastTs < 8000) return;
  _stockPollLastTs = Date.now();
  try {
    // 历史快照模式下也带 date (2026-07-11),不然 poll 会覆盖回实时
    const dateInput = $('#stock-date');
    const curDate = dateInput?.value || '';
    const qs = curDate ? `?_fresh=1&date=${encodeURIComponent(curDate)}` : '?_fresh=1';
    const data = await api(`/api/stock/${code}${qs}`);
    if (!data || code !== currentStockCode) return;
    if (data.is_historical) {
      // 历史快照模式:全量重渲染 (patch 没考虑历史快照字段)
      renderStockDetail(code, data);
    } else {
      _patchStockRealtime(code, data);
    }
    // P-perf: 轮询时同步更新缓存
    _stockCacheSave(code, curDate, data);
  } catch (e) {
    // 静默 — 下次 tick 自动再试
    console.debug('[stock-poll]', e.message);
  }
}
function _patchStockRealtime(code, data) {
  const q = data.quote || {};
  const today = (data.fund_flow || {}).today || {};

  // Hero 价格 + 涨跌额 + 涨跌%
  const price = parseFloat(q.最新价 ?? q.price ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);
  const prev = parseFloat(q.昨收 ?? 0);
  if (price > 0) {
    const priceEl = $('#q-price');
    if (priceEl) {
      const prevPriceText = priceEl.dataset.lastPrice;
      const prevPrice = prevPriceText ? parseFloat(prevPriceText) : 0;
      priceEl.dataset.lastPrice = String(price);
      if (Math.abs(price - prevPrice) > 0.005) {
        animateNumber(priceEl, prevPrice, price, 350, (v) => v.toFixed(2));
      } else {
        priceEl.textContent = price.toFixed(2);
      }
      priceEl.className = 'qh-price ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');
    }
    const chgAmt = prev > 0 ? (price - prev) : 0;
    const chgRow = $('#q-change-row');
    if (chgRow) chgRow.className = 'qh-chg-row ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');
    const chgEl = $('#q-change');
    if (chgEl) chgEl.textContent = (chgAmt >= 0 ? '+' : '') + chgAmt.toFixed(2);
    const pctEl = $('#q-chg-pct');
    if (pctEl) pctEl.textContent = fmtPct(chg);
  }

  // 主力净流(大格) — 数字滚动;null 时显示 "—"(data 缺失)
  const mainNet = today.main_net;
  const mainEl = $('#q-main');
  if (mainEl) {
    const prevMainText = mainEl.dataset.lastMain;
    const prevMain = prevMainText ? parseFloat(prevMainText) : null;
    mainEl.dataset.lastMain = mainNet != null ? String(mainNet) : '';
    if (mainNet != null && Math.abs(mainNet - (prevMain ?? 0)) > 1) {
      animateNumber(mainEl, prevMain ?? 0, mainNet, 350, (v) => fmtN(v, 0));
    } else if (mainNet == null) {
      mainEl.innerHTML = '<span style="color:var(--ink-3)">—</span>';
    } else {
      mainEl.innerHTML = fmtN(mainNet, 0);
    }
    mainEl.className = 'qc-value large ' + (mainNet > 0 ? 'up' : mainNet < 0 ? 'down' : 'flat');
  }
  const mainSub = $('#q-main-sub');
  if (mainSub) {
    const _sn = today.super_net, _bn = today.big_net;
    const superBigKnown = _sn != null || _bn != null;
    const superBig = (_sn || 0) + (_bn || 0);
    mainSub.textContent = superBigKnown
      ? `超大+大单 ${fmtN(superBig, 0)} 万`
      : '分单数据不可达 · 仅供参考';
  }

  // 换手率 + 量比(小格)
  const turnover = q.换手率;
  const tEl = $('#q-turnover');
  if (tEl && turnover != null) {
    tEl.innerHTML = `${turnover.toFixed(2)}<span class="qc-unit">%</span>`;
    tEl.className = 'qc-value ' + (turnover > 10 ? 'up' : turnover > 5 ? 'flat' : 'down');
  }
  const tSub = $('#q-turnover-sub');
  if (tSub) tSub.textContent = turnover > 10 ? '高活跃' : turnover > 5 ? '活跃' : '低迷';
  const vr = q.量比;
  const vEl = $('#q-volratio');
  if (vEl && vr != null) {
    vEl.innerHTML = vr.toFixed(2);
    vEl.className = 'qc-value ' + (vr > 2 ? 'up' : vr > 1 ? 'flat' : 'down');
  }

  // 资金流柱状图 — 重画(akshare 缓存命中时 0.1s 内完成)
  if (data.fund_flow && Array.isArray(data.fund_flow.history)) {
    // P-perf: 资金流哈希校验 — 数据未变时跳过重绘
    const fh = data.fund_flow.history;
    const hash = fh.length + '|' + (fh[0]?.main_net ?? 0) + '|' + (fh[fh.length-1]?.main_net ?? 0);
    if (hash !== _patchStockRealtime._lastFlowHash) {
      _patchStockRealtime._lastFlowHash = hash;
      drawFlowChart(fh);
      renderFlowKpi(fh);
    }
  }
}

// visibility 切回页面 → 立即拉一次(避免用户切走 5min 后切回还看到旧价)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentStockCode) {
    _pollStockRealtime(currentStockCode);
  }
});

async function loadStockDetail(code, date) {
  code = code.trim().padStart(6, '0');
  currentStockCode = code;
  // 切股:停旧轮询,新轮询在首次 render 后启动,避免抢数据
  _stopStockPoll();
  // 启用快速工具栏 (含 default 日期),先 await 确保 stock-date 有值
  await _setQuickbarEnabled(code);
  // 日期参数优先级: 调用方传入 > 当前 stock-date input > 空(今日)
  const dateInput = $('#stock-date');
  let dateParam = date || dateInput?.value || '';
  // 拼 URL: 非空 date 才传,空就是今日
  const qs = dateParam
    ? `?_fresh=1&date=${encodeURIComponent(dateParam)}`
    : '?_fresh=1';

  // P-perf: Phase 0 — 从 sessionStorage 取缓存立即渲染(0ms)
  const cached = _stockCacheLoad(code, dateParam);
  if (cached) {
    try { renderStockDetail(code, cached); }
    catch (e) { console.debug('[stock-cache] render fail:', e.message); }
  }

  // Phase 1 — 拉新数据
  try {
    const data = await api(`/api/stock/${code}${qs}`);
    // 写缓存
    _stockCacheSave(code, dateParam, data);
    try { renderStockDetail(code, data); }
    catch (e) { console.error('renderStockDetail failed:', e); toast(`渲染失败:${e.message}`, 'error'); }
    // 记录到历史
    const name = (data.quote && data.quote.name) || (data.name) || code;
    _addHist(code, name);
    _setQuickbarEnabled(code, name);
    if (!data.is_historical) {
      _startStockPoll(code);
    } else {
      $('#q-price')?.setAttribute('title', '历史快照,实时轮询已停');
    }
    // 异步检查自选状态,更新按钮
    _updateStockWatchBtn();
  } catch (e) {
    if (!cached) toast(`加载失败：${e.message}`, 'error');
  }
}

// ────────────────────────────────────────────
// 个股快速工具栏: 日期 / 一键复盘 / 一键自选 / 跳转
// ────────────────────────────────────────────
let _currentStockName = '';
let _tradeDates = [];        // ['YYYY-MM-DD', ...] 按时间倒序 (按需扩展)
let _tradeDatesSet = null;   // Set 加快 lookup
let _tradeDatesLoaded = false;
let _tradeDatesLoading = null;
let _tradeDatesLimit = 0;    // 当前已加载的 limit (用于按需扩展判断)
let _lastTradeDate = null;   // 服务端给的"今日不是交易日时"的回退日
const _TRADE_DATES_LIMIT_MAX = 1500;   // ≈ 6 年,够用且不会跑飞

function _fmtYmd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function _ensureTradeDates(minLimit = 60) {
  if (_tradeDatesLoaded && minLimit <= _tradeDatesLimit) return _tradeDates;
  if (_tradeDatesLoading) return _tradeDatesLoading;
  const wantLimit = Math.min(Math.max(minLimit, _tradeDatesLimit, 60), _TRADE_DATES_LIMIT_MAX);
  _tradeDatesLoading = (async () => {
    try {
      const env = await api(`/api/trade_dates?limit=${wantLimit}`);
      // 用 past_dates 保证不混入未来日期(避免 prev/next 误入未来)
      _tradeDates = env?.past_dates || env?.dates || [];
      _tradeDatesSet = new Set(_tradeDates);
      _tradeDatesLimit = wantLimit;
      if (env?.last_trade_date) _lastTradeDate = env.last_trade_date;
      _tradeDatesLoaded = true;
    } catch (e) {
      console.warn('[quickbar] trade_dates 拉取失败,降级为工作日近似', e);
      _tradeDates = [];
      _tradeDatesSet = new Set();
      _tradeDatesLimit = wantLimit;
      _tradeDatesLoaded = true;
    }
    return _tradeDates;
  })();
  return _tradeDatesLoading;
}

// 按需扩展交易日历(用于分时图翻页撞底):limit += step, 重新拉取
async function _growTradeDates(step = 250) {
  const wantLimit = Math.min(_tradeDatesLimit + step, _TRADE_DATES_LIMIT_MAX);
  if (wantLimit <= _tradeDatesLimit) return _tradeDates;  // 已到上限
  _tradeDatesLimit = wantLimit;  // 先标记目标 limit,后续并发 _growTradeDates 短路
  _tradeDatesLoaded = false;
  _tradeDatesLoading = null;  // 清掉已 resolve 的旧 promise,否则 _ensureTradeDates 会复用它
  return _ensureTradeDates(wantLimit);
}

// 给定 YYYY-MM-DD → 若在交易日历里直接返回;否则向前找最近的交易日
function _snapToTradeDate(yyyy_mm_dd) {
  if (!_tradeDatesSet || _tradeDatesSet.size === 0) {
    // 兜底:工作日近似(去除周六日)
    const d = new Date(yyyy_mm_dd + 'T00:00:00');
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    return _fmtYmd(d);
  }
  if (_tradeDatesSet.has(yyyy_mm_dd)) return yyyy_mm_dd;
  // _tradeDates 已排序倒序,从前往后第一个 <= 入参的就是最近的过去交易日
  for (const d of _tradeDates) {
    if (d <= yyyy_mm_dd) return d;
  }
  return _lastTradeDate || yyyy_mm_dd;
}

// 给定 YYYY-MM-DD → 找它之前/之后最近交易日 (←/→ 按钮用)
function _shiftByTradeDate(yyyy_mm_dd, direction) {
  if (!_tradeDates || _tradeDates.length === 0) {
    const d = new Date(yyyy_mm_dd + 'T00:00:00');
    d.setDate(d.getDate() + direction);
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + direction);
    return _fmtYmd(d);
  }
  if (direction < 0) {
    for (const d of _tradeDates) {
      if (d < yyyy_mm_dd) return d;
    }
    return _tradeDates[_tradeDates.length - 1];
  } else {
    for (let i = _tradeDates.length - 1; i >= 0; i--) {
      if (_tradeDates[i] > yyyy_mm_dd) return _tradeDates[i];
    }
    return _tradeDates[0];
  }
}

async function _setQuickbarEnabled(code, name) {
  _currentStockName = name || code || '';
  const isValid = /^\d{6}$/.test(code || '');
  const dateInput = $('#stock-date');
  if (dateInput && !dateInput.value) {
    // 默认今天 (本地时区),然后做交易日对齐
    const d = new Date();
    const todayStr = _fmtYmd(d);
    await _ensureTradeDates();
    const snapped = _snapToTradeDate(todayStr);
    dateInput.value = snapped;
    dateInput.max = todayStr;  // 未来日期禁用
    if (snapped !== todayStr) {
      toast(`今天 ${todayStr} 是非交易日,已切到最近交易日 ${snapped}`, 'info', 2200);
    }
  }
  $('#stock-review-btn').disabled = !isValid;
  $('#stock-watch-btn').disabled  = !isValid;
  $('#stock-jump-stock').disabled = !isValid;
}

async function _shiftDate(days) {
  const inp = $('#stock-date');
  if (!inp.value) return;
  await _ensureTradeDates();
  // direction 永远是 ±1 交易日,跳过周末/节假日
  const target = _shiftByTradeDate(inp.value, days);
  inp.value = target;
  const dir = days > 0 ? '下' : '上';
  toast(`日期 → ${target} (跳到${dir}一交易日)`, 'info', 1400);
  if (currentStockCode) loadStockDetail(currentStockCode);
}

$('#stock-date-today')?.addEventListener('click', async () => {
  const d = new Date();
  const todayStr = _fmtYmd(d);
  await _ensureTradeDates();
  const snapped = _snapToTradeDate(todayStr);
  $('#stock-date').value = snapped;
  if (snapped === todayStr) {
    toast('回到今天', 'info', 1200);
  } else {
    toast(`今天 ${todayStr} 非交易日,已回到最近交易日 ${snapped}`, 'info', 2200);
  }
  if (currentStockCode) loadStockDetail(currentStockCode);
});
$('#stock-date-prev')?.addEventListener('click', () => _shiftDate(-1));
$('#stock-date-next')?.addEventListener('click', () => _shiftDate(+1));
// 日期选择器直接改日期:同样做交易日对齐(若选了周六/节假日)
$('#stock-date')?.addEventListener('change', async () => {
  if (!currentStockCode) return;
  await _ensureTradeDates();
  const picked = $('#stock-date').value;
  const snapped = _snapToTradeDate(picked);
  if (snapped !== picked) {
    $('#stock-date').value = snapped;
    toast(`${picked} 非交易日,已回退到最近交易日 ${snapped}`, 'warn', 2400);
  }
  loadStockDetail(currentStockCode);
});

// 一键复盘 → 跳到 review 视图,自动填入 code + 日期
$('#stock-review-btn')?.addEventListener('click', () => {
  if (!currentStockCode) return;
  const date = $('#stock-date').value || new Date().toISOString().slice(0, 10);
  // 直接走 review 视图 + 设置 URL hash 让 review.js 自动填
  sessionStorage.setItem('tuixue_review_seed', JSON.stringify({
    code: currentStockCode,
    name: _currentStockName,
    date,
  }));
  showView('review');
  toast(`已跳到复盘页 · ${currentStockCode} · ${date}`, 'info', 2200);
});

// 自选 toggle — 检查 / 添加 / 删除
async function _updateStockWatchBtn() {
  const btn = $('#stock-watch-btn');
  if (!btn || !currentStockCode) return;
  try {
    const r = await _fetchWithTimeout('/api/watchlist');
    const j = await r.json();
    const items = (j.data && j.data.items) || [];
    const inWl = items.some(x => x.code === currentStockCode);
    btn.dataset.inWl = inWl ? '1' : '0';
    btn.disabled = false;
    btn.textContent = inWl ? '✓ 已自选' : '⭐ 一键自选';
  } catch (_) { btn.dataset.inWl = '0'; btn.textContent = '⭐ 一键自选'; btn.disabled = false; }
}
$('#stock-watch-btn')?.addEventListener('click', async () => {
  if (!currentStockCode) return;
  const btn = $('#stock-watch-btn');
  // R-fix-2026-07-15: 600ms 冷却 — 防止用户连点 5 次触发 5 次 toast 闪屏
  // btn.disabled=true 在异步期间会重新被覆盖, 用户连点期间失效
  const cooldownUntil = parseInt(btn.dataset.cooldownUntil || '0', 10);
  if (Date.now() < cooldownUntil) return;
  const inWl = btn.dataset.inWl === '1';
  btn.disabled = true;
  btn.dataset.cooldownUntil = String(Date.now() + 600);
  if (inWl) {
    // 已自选 → 删除
    try {
      const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(currentStockCode), { method: 'DELETE' });
      const j = await r.json();
      if (j.ok || j.data?.removed) {
        toast(`✓ ${currentStockCode} 已移出自选`, 'success', 2200);
        btn.dataset.inWl = '0';
        btn.textContent = '⭐ 一键自选';
      } else {
        throw new Error(j.error || '删除失败');
      }
    } catch (e) {
      toast(`删除失败:${e.message}`, 'error', 3000);
    }
  } else {
    // 未自选 → 添加
    try {
      const r = await _fetchWithTimeout('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: currentStockCode, name: _currentStockName, tag: '自查' }),
      });
      const j = await r.json();
      if (j.ok || j.data?.ok) {
        toast(`✓ ${currentStockCode} 已加入自选`, 'success', 2200);
        btn.dataset.inWl = '1';
        btn.textContent = '✓ 已自选';
      } else {
        throw new Error(j.error || '加入失败');
      }
    } catch (e) {
      toast(`加入失败:${e.message}`, 'error', 3000);
      btn.textContent = '⭐ 一键自选';
    }
  }
  // 冷却结束才解锁 — 防连点
  setTimeout(() => { btn.disabled = false; btn.dataset.cooldownUntil = '0'; }, 600);
});

// 一键跳转个股深查 (URL 锁定 code,方便分享)
$('#stock-jump-stock')?.addEventListener('click', () => {
  if (!currentStockCode) return;
  history.replaceState(null, '', `?code=${currentStockCode}`);
  toast(`URL 锁定 ${currentStockCode}`, 'info', 1500);
});

function renderStockDetail(code, data) {
  const q = data.quote || {};
  const seats = data.seats || {};
  const flow = data.fund_flow || {};
  const today = flow.today || {};
  const extras = data.extras || {};

  const name = q.name || data.name || code;
  const price = parseFloat(q.最新价 ?? q.price ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);
  const prev = parseFloat(q.昨收 ?? 0);
  const chgAmt = prev > 0 ? (price - prev) : 0;

  // 分时图辅助上下文（昨收 + 涨停价），供 drawIntraDayChart 参考线
  const isST = (name || '').startsWith('ST');
  const lu = extras.limit_up_price != null ? extras.limit_up_price : (prev > 0 ? +(prev * (isST ? 1.05 : 1.1)).toFixed(2) : null);
  lastStockContext = { prev_close: prev || null, limit_up_price: lu, code };

  // ─── 顶部标题 + Hero ───
  $('#stock-title').textContent = name;
  $('#stock-code').textContent = code;
  $('#stock-sub').textContent = `${q._source || ''} ${q._fetch_time || ''}`.trim() || '实时行情';

  // 让 stock-head 支持横向滑动 (移动端标题太长)
  const sh = document.querySelector('.view-stock .view-head');
  if (sh) sh.style.overflowX = 'auto';

  $('#qh-name').textContent = name;
  $('#qh-code').textContent = code;
  // Hero 价格 + 涨跌额 + 涨跌幅 + 箭头
  const priceEl = $('#q-price');
  // 数字滚动（首次进入 0 → 当前价，~500ms 平滑）
  const prevPriceText = priceEl.dataset.lastPrice;
  const prevPrice = prevPriceText ? parseFloat(prevPriceText) : 0;
  priceEl.dataset.lastPrice = price > 0 ? String(price) : '';
  if (price > 0 && Math.abs(price - prevPrice) > 0.005) {
    animateNumber(priceEl, prevPrice, price, 500, (v) => v.toFixed(2));
  } else {
    priceEl.textContent = price > 0 ? fmtN(price, 2) : '—';
  }
  priceEl.className = 'qh-price ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');
  const chgRow = $('#q-change-row');
  chgRow.className = 'qh-chg-row ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');
  $('#q-change').textContent = price > 0 && prev > 0
    ? (chgAmt >= 0 ? '+' : '') + chgAmt.toFixed(2)
    : '—';
  $('#q-chg-pct').textContent = chg != null && Number.isFinite(chg) ? fmtPct(chg) : '—';
  $('#q-arrow').textContent = chg > 0 ? '▲' : chg < 0 ? '▼' : '─';
  $('#q-time').textContent = (q._fetch_time || '').trim() || (q._source || '—');

  // Hero tags
  const tagsHtml = [];
  if (extras.is_chinext_star) tagsHtml.push('<span class="qh-tag hot">创业板/科创</span>');
  if (seats.blacklisted) tagsHtml.push('<span class="qh-tag" style="color:var(--down);border-color:rgba(52,199,89,.4)">黑名单</span>');
  if (chg >= 9.7) tagsHtml.push('<span class="qh-tag lu">涨停</span>');
  else if (chg <= -9.7) tagsHtml.push('<span class="qh-tag ld">跌停</span>');
  if (extras.streak && extras.streak >= 1) tagsHtml.push(`<span class="qh-tag hot">${extras.streak} 连板</span>`);
  // 历史快照标签 (2026-07-11 日期切换)
  if (data.is_historical && data.snapshot_date) {
    tagsHtml.push(`<span class="qh-tag" style="color:var(--accent);border-color:var(--accent)" title="实时数据无法回放,以下数据来自历史日线">📅 ${data.snapshot_date} 历史快照</span>`);
  }
  tagsHtml.push(`<span class="qh-tag">${q._source || '—'}</span>`);
  $('#qh-tags').innerHTML = tagsHtml.join(' ');

  // 顶部副标题 + 数据日期 (2026-07-11 用户反馈没显示日期)
  // 优先用东财 quote.时间 (YYYYMMDDHHMMSS), 其次 _fetch_time 当日, 最后回退今日
  let dateLabel;
  if (data.is_historical && data.snapshot_date) {
    dateLabel = `数据日期: ${data.snapshot_date}`;
  } else {
    const quoteDateRaw = String(q.时间 || q.date || '').slice(0, 8);
    let dateStr = '';
    if (quoteDateRaw.length === 8 && /^\d{8}$/.test(quoteDateRaw)) {
      dateStr = `${quoteDateRaw.slice(0, 4)}-${quoteDateRaw.slice(4, 6)}-${quoteDateRaw.slice(6, 8)}`;
    } else if (_lastTradeDate) {
      // _lastTradeDate 由 _ensureTradeDates 加载, 直接用
      dateStr = _lastTradeDate;
    } else {
      dateStr = _fmtYmd(new Date());
    }
    dateLabel = `数据日期: ${dateStr}`;
  }
  $('#stock-sub').textContent = `${name} · ${code} · ${dateLabel}`;

  // ─── 12 卡 Bento ───
  const setVal = (id, val, color) => {
    $$(id).forEach(el => {
      el.innerHTML = val;
      if (color) el.className = 'qc-value ' + color;
    });
  };

  // 主力净流（大格）— 数字滚动
  const mainNet = today.main_net;   // null 时保留 null → 显示 "—" 而不是 0
  const mainEl = $('#q-main');
  const prevMainText = mainEl.dataset.lastMain;
  const prevMain = prevMainText ? parseFloat(prevMainText) : null;
  mainEl.dataset.lastMain = mainNet != null ? String(mainNet) : '';
  if (mainNet != null && Math.abs(mainNet - (prevMain ?? 0)) > 1) {
    animateNumber(mainEl, prevMain ?? 0, mainNet, 600, (v) => fmtN(v, 0));
  } else {
    mainEl.innerHTML = mainNet != null ? fmtN(mainNet, 0) : '<span style="color:var(--ink-3)">—</span>';
  }
  mainEl.className = 'qc-value large ' + (mainNet > 0 ? 'up' : mainNet < 0 ? 'down' : 'flat');
  const _sn = today.super_net, _bn = today.big_net;
  const superBigKnown = _sn != null || _bn != null;
  const superBig = (_sn || 0) + (_bn || 0);
  $('#q-main-sub').textContent = superBigKnown
    ? `超大+大单 ${fmtN(superBig, 0)} 万`
    : '分单数据不可达 · 仅供参考';

  // 换手率
  const turnover = q.换手率;
  setVal('#q-turnover',
    turnover != null ? `${turnover.toFixed(2)}<span class="qc-unit">%</span>` : '—',
    turnover > 10 ? 'up' : turnover > 5 ? 'flat' : 'down');
  $('#q-turnover-sub').textContent = turnover > 10 ? '高活跃' : turnover > 5 ? '活跃' : '低迷';

  // 量比
  const volratio = q.量比;
  setVal('#q-volratio',
    volratio != null ? volratio.toFixed(2) : '—',
    volratio > 2 ? 'up' : volratio > 1 ? 'flat' : 'down');

  // 振幅
  const amp = extras.amplitude_pct;
  setVal('#q-amp',
    amp != null ? `${amp.toFixed(2)}<span class="qc-unit">%</span>` : '—',
    amp > 7 ? 'up' : amp > 3 ? 'flat' : 'down');

  // Bento icon states · 紧急度点
  paintBentoState(turnover, volratio, amp, mainNet, price, extras);

  // 5 日涨跌
  const p5 = extras.pct_5d;
  setVal('#q-5d',
    p5 != null ? `${p5 >= 0 ? '+' : ''}${p5.toFixed(2)}%` : '—',
    p5 > 0 ? 'up' : p5 < 0 ? 'down' : 'flat');

  // 20 日涨跌
  const p20 = extras.pct_20d;
  const p20Html = p20 != null ? `${p20 >= 0 ? '+' : ''}${p20.toFixed(2)}%` : '—';
  setVal('#q-20d', p20Html, p20 > 0 ? 'up' : p20 < 0 ? 'down' : 'flat');
  $$('#q-20d-d').forEach(el => { el.textContent = p20Html; });

  // 总市值 / 流通
  const mcap = q.总市值 || 0;
  const cmcap = q.流通市值 || 0;
  setVal('#q-mcap',
    mcap > 0 ? `${mcap.toFixed(1)}<span class="qc-unit">亿</span>` : '—',
    'flat');
  $('#q-mcap-sub').textContent = `流通 ${cmcap > 0 ? cmcap.toFixed(1) + ' 亿' : '—'}`;

  // PE — 优先用 server 标准化字段 pe,fallback 上游原字段名
  const peVal = Number(qGet(q, 'pe', '市盈率-动态', '市盈率'));
  setVal('#q-pe', peVal > 0 ? peVal.toFixed(2) : '—', 'flat');
  $('#q-pe-sub').textContent = peVal > 0
    ? `PE 动 · ${peVal > 50 ? '高估' : peVal < 0 ? '亏损' : '合理'}`
    : (peVal < 0 ? '亏损 · ' + peVal.toFixed(2) : '亏损/暂无');

  // PB (新加 — 之前没显示)
  const pbVal = Number(qGet(q, 'pb', '市净率'));
  setVal('#q-pb', pbVal > 0 ? pbVal.toFixed(2) : '—', 'flat');

  // 当日高/低
  setVal('#q-hl', `${q.最高 ? fmtN(q.最高, 2) : '—'} / ${q.最低 ? fmtN(q.最低, 2) : '—'}`, 'flat');
  $('#q-hl-sub').textContent = `开 ${fmtN(q.今开, 2)} · 昨收 ${fmtN(q.昨收, 2)}`;

  // 同步到 TODAY 当日明细表 (左下小卡) — 顶部 hero 已有数据,这里只是双显
  const _openNum = parseFloat(q.今开 || 0);
  const _prevNum = parseFloat(q.昨收 || 0);
  const _highNum = parseFloat(q.最高 || 0);
  const _lowNum  = parseFloat(q.最低 || 0);
  if ($('#q-open')) $('#q-open').textContent = _openNum > 0 ? fmtN(_openNum, 2) : '—';
  if ($('#q-prev')) $('#q-prev').textContent = _prevNum > 0 ? fmtN(_prevNum, 2) : '—';
  if ($('#q-high')) $('#q-high').textContent = _highNum > 0 ? fmtN(_highNum, 2) : '—';
  if ($('#q-low'))  $('#q-low').textContent  = _lowNum  > 0 ? fmtN(_lowNum, 2)  : '—';
  if ($('#q-v5'))   {
    const v5 = extras.vol_5d_avg;
    $('#q-v5').textContent = v5 ? fmtN(v5, 0) + ' 手' : '—';
  }

  // 换手率 / 5日涨跌 (新增 - 2026-07-14)
  const turnover_d = q.换手率 != null ? Number(q.换手率) : null;
  $$('#q-turnover-d').forEach(el => { el.textContent = (turnover_d != null && turnover_d > 0) ? turnover_d.toFixed(2) + '%' : '—'; });
  $$('#q-5d-d').forEach(el => {
    const p5 = extras.pct_5d;
    if (p5 == null) { el.textContent = '—'; el.style.color = ''; }
    else {
      el.textContent = (p5 >= 0 ? '+' : '') + p5.toFixed(2) + '%';
      el.style.color = p5 >= 0 ? 'var(--up)' : 'var(--down)';
    }
  });

  // 涨停/跌停
  const luStr = extras.limit_up_price != null ? extras.limit_up_price.toFixed(2) : '—';
  const ldStr = extras.limit_dn_price != null ? extras.limit_dn_price.toFixed(2) : '—';
  setVal('#q-lu', luStr,
    extras.limit_up_price && price >= extras.limit_up_price - 0.001 ? 'up' : 'flat');
  $$('#q-lu-d').forEach(el => { el.textContent = luStr; });
  setVal('#q-ld', ldStr,
    extras.limit_dn_price && price <= extras.limit_dn_price + 0.001 ? 'down' : 'flat');
  $$('#q-ld-d').forEach(el => { el.textContent = ldStr; });

  // 成交量 / 成交额
  const vol = q.成交量 || 0;
  const volStr = vol > 0 ? `${(vol / 1e4).toFixed(1)}` : '—';
  setVal('#q-vol', vol > 0 ? `${volStr}<span class="qc-unit">万手</span>` : '—', 'flat');
  $$('#q-vol-d').forEach(el => { el.textContent = vol > 0 ? `${volStr} 万手` : '—'; });
  const amtHtml = q.成交额 > 0
    ? `<span class="qc-value flat" style="font-size:13px">${(q.成交额 / 1e8).toFixed(2)} 亿</span>`
    : '成交额 —';
  $$('#q-amt').forEach(el => { el.innerHTML = amtHtml; });
  $$('#q-amt-d').forEach(el => { el.textContent = q.成交额 > 0 ? `${(q.成交额 / 1e8).toFixed(2)} 亿` : '—'; });

  // 龙虎席位
  setVal('#q-seats', `${seats.seat_count || 0}<span class="qc-unit">条</span>`, 'flat');
  $('#q-seats-sub').textContent = seats.blacklisted
    ? `近 ${seats.total_lhb_rows || 0} 条 · ⚠ 黑名单`
    : `近 ${seats.total_lhb_rows || 0} 条`;

  // ─── 图表 / 表格 ───
  const empty = $('#flow-empty');
  if (empty) empty.style.display = 'none';
  drawFlowChart(flow.history || []);
  klineState.data = data.kline || [];
  klineState.period = 22;
  syncKlineToolbar();
  drawKlineChart();
  renderFlowKpi(flow.history || []);
  renderKlineKpi(klineState.data);
  renderSeatsTable(seats.rows || [], seats);
  renderHolders(data.holders || null);

  // Hero · sparkline + 涨停距 + 风险标
  renderHeroSparkline(data.kline || [], price);
  renderHeroLimitBand(price, prev, lu, extras.limit_dn_price, chg, extras.amplitude_pct);
  renderHeroRisks(q, extras, chg);

  // 资金成分 (6 类席位 + 占比 + 风险) — 异步
  loadStockSeatBreakdown(code);

  // 分时：清空缓存,重置到当日
  intraDayCache = new Map();
  if (echartsCharts.intraDay)   { echartsCharts.intraDay.dispose();   echartsCharts.intraDay   = null; }
  const pick = $('#intra-day-pick'); if (pick) pick.value = todayStr();
  const lbl = $('#intra-day-label'); if (lbl) lbl.textContent = '';
  const idn = $('#intra-day-note');  if (idn) idn.textContent = '';
  const idk = $('#intra-day-kpi');   if (idk) idk.innerHTML = '';

  // AI 分析面板
  $('#ai-panel').hidden = false;
  $('#ai-status').textContent = 'AI 复盘中 …';
  $('#ai-verdict').textContent = '…';
  $('#ai-summary').textContent = '';

  // 板块情绪 + 相关新闻
  loadStockSector(code);
  $('#ai-detail').innerHTML = '';
  loadAIAnalysis(code);

  // 我的交易 (此股历史买/卖 + 复盘)
  loadStockMyTrades(code);

  // 砸盘风险 (cache 命中 < 0.1s,冷启动 30-60s;先 re-render panel 骨架)
  loadCrashRisk(code);
}

// ─── Hero · sparkline (近 1M 收 + MA5/MA20 + 现价竖线) ───
function renderHeroSparkline(kline, lastPrice) {
  const wrap = $('#qh-spark-wrap');
  if (!wrap) return;
  if (!kline || kline.length < 5) { wrap.hidden = true; return; }
  wrap.hidden = false;

  // 兼容 [{date, close}] / [dict] 两种结构
  const closes = kline.map(k => Number(k.close ?? k[1] ?? k.收盘价 ?? 0)).filter(v => v > 0);
  if (closes.length < 5) { wrap.hidden = true; return; }

  const W = 200, H = 44, PAD = 2;
  const min = Math.min(...closes), max = Math.max(...closes);
  const span = (max - min) || 1;
  const x = (i) => PAD + (i / (closes.length - 1)) * (W - 2 * PAD);
  const y = (v) => PAD + (1 - (v - min) / span) * (H - 2 * PAD);

  const points = closes.map((c, i) => [x(i), y(c)]);
  const lineD = points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const areaD = lineD + ` L${points[points.length - 1][0].toFixed(1)} ${H} L${points[0][0].toFixed(1)} ${H} Z`;

  $('#qh-spark-line').setAttribute('d', lineD);
  $('#qh-spark-area').setAttribute('d', areaD);

  // MA5 / MA20
  const ma = (n) => closes.map((_, i) => {
    if (i < n - 1) return null;
    let s = 0; for (let j = i - n + 1; j <= i; j++) s += closes[j];
    return s / n;
  });
  const ma5 = ma(5), ma20 = ma(20);
  const buildMa = (arr) => arr.map((v, i) => v != null ? [x(i), y(v)] : null)
                              .filter(p => p).map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  $('#qh-spark-ma5').setAttribute('d', buildMa(ma5));
  $('#qh-spark-ma20').setAttribute('d', buildMa(ma20));

  // 现价竖线 — 锚定最后一根 (用 lastPrice 真实位置,无 lastPrice 时用 closes[-1])
  const finalPrice = lastPrice || closes[closes.length - 1];
  const finalX = x(closes.length - 1);
  $('#qh-spark-now').setAttribute('x1', finalX);
  $('#qh-spark-now').setAttribute('x2', finalX);

  // 颜色 = 涨绿跌红 (对比最后值 vs 5 日前)
  const trendUp = finalPrice >= closes[0];
  wrap.classList.remove('up', 'down');
  wrap.classList.add(trendUp ? 'up' : 'down');

  // meta 小字: 区间高 / 低 / 当前点
  const meta = $('#qh-spark-meta');
  const days = kline.length;
  if (meta) {
    meta.innerHTML =
      `<span>${days} 日</span>` +
      `<span>高 <b>${max.toFixed(2)}</b></span>` +
      `<span>低 <b>${min.toFixed(2)}</b></span>` +
      `<span class="${finalPrice >= closes[0] ? 'dot-up' : 'dot-dn'}">现 <b>${finalPrice.toFixed(2)}</b></span>`;
  }
}

// ─── Hero · 涨停距 / 跌停距 可视化 ───
function renderHeroLimitBand(price, prev, lu, ld, chg, amp) {
  const wrap = $('#qh-lu-band');
  if (!wrap) return;
  if (!prev || prev <= 0 || !lu || !ld) { wrap.hidden = true; return; }
  wrap.hidden = false;

  const lo = Math.min(ld, prev * 0.85);
  const hi = Math.max(lu, prev * 1.15);
  const pct = (price - lo) / (hi - lo);
  const left = Math.max(2, Math.min(98, pct * 100));

  const tick = $('#qh-lu-now');
  if (tick) tick.style.left = left + '%';

  const distLU = lu - price;
  const distLD = price - ld;
  const distLU_pct = ((lu - price) / price) * 100;
  const distLD_pct = ((price - ld) / price) * 100;

  $('#qh-lu-distance').textContent = price >= lu
    ? `🔴 已涨停 (+${(chg || 0).toFixed(2)}%)`
    : `距涨停 +${distLU.toFixed(2)} · ${distLU_pct.toFixed(2)}%`;

  const zone = $('#qh-lu-zone');
  let zClass = '', zText = '';
  if (price >= lu - 0.01)                   { zClass = 'zone-lu';  zText = '🚨 封板'; }
  else if (price <= ld + 0.01)               { zClass = 'zone-ld';  zText = '⚠ 跌停'; }
  else if (distLU_pct < 2)                   { zClass = 'zone-hot'; zText = '🔥 近涨停 <2%'; }
  else if (distLD_pct < 2)                   { zClass = 'zone-hot'; zText = '⚡ 近跌停 <2%'; }
  else if (amp && amp > 10)                  { zClass = 'zone-hot'; zText = '🎯 高振幅'; }
  else                                       { zClass = '';         zText = '常态区间'; }
  zone.className = zClass;
  zone.textContent = zText;
}

// ─── Hero · 风险标签 (量比/振幅/换手/连板/弱势) ───
function renderHeroRisks(q, extras, chg) {
  const host = $('#qh-risks');
  if (!host) return;
  const risks = [];

  const turnover = q.换手率;
  if (turnover != null && turnover > 15)      risks.push(['r-turn', `高换手 ${turnover.toFixed(1)}%`]);
  if (turnover != null && turnover < 1 && turnover > 0) risks.push(['r-turn', `冷 ${turnover.toFixed(2)}%`]);

  const vr = q.量比;
  if (vr != null && vr > 3)                   risks.push(['r-vol', `巨量 ${vr.toFixed(1)}×`]);
  else if (vr != null && vr < 0.5 && vr > 0)  risks.push(['r-vol', `缩量 ${vr.toFixed(2)}×`]);

  const amp = extras.amplitude_pct;
  if (amp != null && amp > 12)                risks.push(['r-amp', `高振幅 ${amp.toFixed(1)}%`]);

  if (extras.streak && extras.streak >= 2)    risks.push(['r-streak', `${extras.streak} 连板`]);

  if (chg != null && chg <= -7)              risks.push(['r-bear', `急跌 ${chg.toFixed(2)}%`]);
  else if (chg != null && chg >= 7 && chg < 9.7) risks.push(['r-streak', `冲刺涨停`]);

  if (!risks.length) { host.hidden = true; host.innerHTML = ''; return; }
  host.hidden = false;
  host.innerHTML = risks.map(([cls, txt]) => `<span class="qh-risk ${cls}">${txt}</span>`).join('');
}

// ─── Bento · 每格 icon 状态 + 紧急度点 ───
function paintBentoState(turnover, volratio, amp, mainNet, price, extrasRef) {
  const setIcon = (id, state) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('warn', 'bull', 'bear', 'hot', 'calm');
    if (state) el.classList.add(state);
  };
  const setDot = (id, on, dir) => {
    const dot = document.getElementById(id);
    if (!dot) return;
    dot.classList.toggle('on', !!on);
    dot.classList.remove('dot-up', 'dot-down');
    if (on && dir) dot.classList.add(dir === 'up' ? 'dot-up' : 'dot-down');
  };

  if (mainNet != null) {
    setIcon('qc-icon-main', mainNet > 0 ? 'bull' : mainNet < 0 ? 'bear' : 'calm');
    setDot('qc-dot-main', Math.abs(mainNet) > 5000, mainNet > 0 ? 'up' : 'down');
  }
  if (turnover != null) setIcon('qc-icon-turnover', turnover > 10 ? 'hot' : turnover > 5 ? 'warn' : 'calm');
  if (volratio != null) setIcon('qc-icon-volratio', volratio > 2 ? 'bull' : volratio < 0.6 ? 'bear' : 'calm');
  if (amp != null) setIcon('qc-icon-amp', amp > 8 ? 'hot' : amp > 3 ? 'warn' : 'calm');

  if (extrasRef && price > 0) {
    if (extrasRef.limit_up_price && price >= extrasRef.limit_up_price - 0.005) setIcon('qc-icon-lu', 'bull');
    if (extrasRef.limit_dn_price && price <= extrasRef.limit_dn_price + 0.005) setIcon('qc-icon-ld', 'bear');
  }
}

// 砸盘风险 (https://.../ai_crash_risk) — 量化席位 / 对倒 / 虚假流动性 / 尾盘异动
async function loadCrashRisk(code) {
  const root = $('#crash-panel');
  if (!root) return;
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_crash_risk`).then(r => r.json());
    const d = (env && env.data) || {};
    const risk = d.crash_risk || '—';
    const verdict = d.verdict || '—';
    const conv = +d.conviction || 0;
    // 状态栏
    const rl = $('#crash-risk');    if (rl)   rl.textContent = risk;
    const st = $('#crash-status');  if (st)   st.textContent = `判定 ${verdict}`;
    const cn = $('#crash-conviction'); if (cn) cn.textContent = `${conv} / 100`;
    const cb = $('#crash-conviction-bar'); if (cb) cb.style.width = Math.min(conv, 100) + '%';
    // summary
    const sm = $('#crash-summary'); if (sm) sm.textContent = d.summary || '';
    // 时间戳
    const meta = $('#crash-meta');
    if (meta) {
      const ts = d.ts_updated ? new Date(d.ts_updated * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
      meta.textContent = ts ? `更新 ${ts}` : '';
    }
    // 颜色
    const riskEl = $('#crash-risk');
    if (riskEl) {
      riskEl.className = 'ai-verdict ' + (
        risk === '高' ? 'bad' :
        risk === '中' ? 'warn' :
        risk === '无' ? 'good' : ''
      );
    }
    // signals
    const det = $('#crash-detail');
    const pre = $('#crash-pre-scan');
    if (det) {
      const sigs = d.signals || [];
      det.innerHTML = sigs.length
        ? sigs.map(s => `<div class="cr-row">
            <span class="cr-mark ${(s.weight || '').includes('高') ? 'high' : (s.weight || '').includes('中') ? 'mid' : 'low'}">${(s.weight || '').includes('高') ? '⚠' : (s.weight || '').includes('中') ? '!' : '·'}</span>
            <span class="cr-cat dim">${escapeHtml(s.category || '')}</span>
            <span class="cr-name"><b>${escapeHtml(s.name || '')}</b></span>
            <span class="cr-detail">${escapeHtml(s.detail || '')}</span>
          </div>`).join('')
        : '<p class="caption dim">暂无信号</p>';
    }
    if (pre) {
      const ps = d.pre_scan || {};
      const lines = [];
      if ((ps.quant_seats || []).length) lines.push(`量化席位: ${ps.quant_seats.length}`);
      if ((ps.pair_trades  || []).length) lines.push(`配对交易: ${ps.pair_trades.length}`);
      if ((ps.fake_liquidity || []).length) lines.push(`虚假流动性: ${ps.fake_liquidity.length}`);
      if (ps.late_session) lines.push(`尾盘异动: ${JSON.stringify(ps.late_session).slice(0,80)}`);
      pre.innerHTML = lines.length
        ? `<div class="caption dim">预扫命中: ${lines.join(' · ')}</div>`
        : '<div class="caption dim">预扫: 无异常</div>';
    }
  } catch (e) {
    const st = $('#crash-status'); if (st) st.textContent = '拉取失败: ' + (e.message || e);
  }
}
$('#crash-refresh-btn')?.addEventListener('click', () => {
  if (currentStockCode) loadCrashRisk(currentStockCode);
});

// ────────────────────────────────────────────
// STOCK · 资金成分 (6 类席位 + 占比 + 风险标记)
// ────────────────────────────────────────────
async function loadStockSeatBreakdown(code) {
  const wrap = $('#seat-breakdown');
  if (!wrap) return;
  const tbody = $('#bd-cats-body');
  // 首次加载时显示 skeleton（如果之前没数据）
  if (tbody && tbody.children.length === 1 && tbody.querySelector('.empty')) {
    tbody.innerHTML = `<tr><td colspan="8">${skeletonLines(2, 'lg')}</td></tr>`;
  }
  try {
    const r = await api(`/api/stock/${code}/seat_breakdown`);
    if (!r) { wrap.hidden = true; return; }
    wrap.hidden = false;
    renderSeatBreakdown(r);
  } catch (e) {
    // 不要在超时/网络错误时隐藏已有数据 — 上游 akshare 限频时常见
    console.warn('[seat-breakdown] fetch failed (保持上次渲染):', e.message);
    if (tbody && tbody.querySelector('.empty')) {
      tbody.innerHTML = `<tr><td colspan="8"><div class="error-card"><div class="er-msg">⚠ <b>加载失败</b> · ${escapeHtml(e.message)}</div><button class="er-retry" id="bd-retry">↻ 重试</button></div></td></tr>`;
      const btn = $('#bd-retry');
      if (btn) btn.addEventListener('click', () => loadStockSeatBreakdown(code));
    }
  }
}

function renderSeatBreakdown(d) {
  const wrap = $('#seat-breakdown');
  if (!wrap) return;
  wrap.hidden = false;

  // 头部 meta
  $('#bd-last-date').textContent = d.last_date ? `日期 ${d.last_date}` : '当日无龙虎榜';
  $('#bd-total-amt').textContent = d.total_amount_wan
    ? `当日总成交 ${(d.total_amount_wan/1e4).toFixed(2)} 亿`
    : '';

  // 短线筛选标签
  const tagsEl = $('#bd-tags');
  tagsEl.innerHTML = (d.tags || []).map(t => {
    const cls = t.startsWith('✅') ? 'tag-good' : 'tag-bad';
    return `<span class="bd-tag ${cls}">${escapeHtml(t)}</span>`;
  }).join('') || '<span class="caption dim">无筛选信号</span>';

  // 风险标记
  const risksEl = $('#bd-risks');
  risksEl.innerHTML = (d.risks || []).map(r =>
    `<div class="bd-risk">${escapeHtml(r)}</div>`
  ).join('');

  // 实时主力/散户
  const intraday = d.intraday || {};
  function _setBar(barEl, numEl, pct) {
    if (pct == null) {
      if (barEl) barEl.style.width = '0%';
      if (numEl) numEl.textContent = '—';
      return;
    }
    if (barEl) barEl.style.width = Math.min(100, Math.abs(pct)) + '%';
    if (numEl) numEl.textContent = pct.toFixed(1) + '%';
  }
  _setBar($('#bd-mb-bar'), $('#bd-mb-pct'), intraday.main_buy_pct);
  _setBar($('#bd-ms-bar'), $('#bd-ms-pct'), intraday.main_sell_pct);
  _setBar($('#bd-rb-bar'), $('#bd-rb-pct'), intraday.retail_buy_pct);
  _setBar($('#bd-rs-bar'), $('#bd-rs-pct'), intraday.retail_sell_pct);
  const mnEl = $('#bd-mn-pct');
  if (intraday.main_net_pct == null) mnEl.textContent = '—';
  else {
    mnEl.textContent = (intraday.main_net_pct >= 0 ? '+' : '') + intraday.main_net_pct.toFixed(2) + '%';
    mnEl.style.color = intraday.main_net_pct >= 0 ? 'var(--up)' : 'var(--down)';
  }

  // 6 类汇总表 (可展开每类席位明细)
  const body = $('#bd-cats-body');
  const cats = d.categories || [];
  const totalAmt = d.total_amount_wan;  // 当日总成交额 (万), 用于单席位占比
  const hasAny = cats.some(c => c.seat_count > 0);
  if (!hasAny) {
    body.innerHTML = `<tr><td colspan="9" class="empty">当日无龙虎榜席位 · 占比无法计算</td></tr>`;
    return;
  }
  body.innerHTML = cats.map((c, idx) => {
    const zero = c.seat_count === 0;
    const buyClass  = (c.buy_wan  > 0) ? 'pct-up' : 'num-zero';
    const sellClass = (c.sell_wan > 0) ? 'pct-down' : 'num-zero';
    const netClass  = c.net_wan > 0 ? 'pct-up' : c.net_wan < 0 ? 'pct-down' : 'num-zero';
    const expandable = !zero && (c.seats || []).length > 0;
    const mainRow = `<tr class="bd-cat-row${expandable ? ' bd-expandable' : ''}"${zero ? ' style="opacity:.5"' : ''}${expandable ? ` data-detail="bd-detail-${idx}" data-action="toggle-seat-detail" style="cursor:pointer"` : ''}>
      <td>
        <span class="cat-label">
          ${expandable ? '<span class="bd-caret">▸</span>' : '<span class="bd-caret-ph"></span>'}
          <span class="cat-dot cat-${c.key}"></span>
          ${escapeHtml(c.label)}
        </span>
      </td>
      <td class="${zero ? 'num-zero' : buyClass}">${zero ? '—' : c.buy_wan.toFixed(0)}</td>
      <td class="${zero ? 'num-zero' : sellClass}">${zero ? '—' : c.sell_wan.toFixed(0)}</td>
      <td class="${zero ? 'num-zero' : netClass}">${zero ? '—' : (c.net_wan >= 0 ? '+' : '') + c.net_wan.toFixed(0)}</td>
      <td>${c.buy_pct == null ? '—' : c.buy_pct.toFixed(2) + '%'}</td>
      <td>${c.sell_pct == null ? '—' : c.sell_pct.toFixed(2) + '%'}</td>
      <td>${c.total_pct == null ? '—' : c.total_pct.toFixed(2) + '%'}</td>
      <td>${c.net_pct == null ? '—' : (c.net_pct >= 0 ? '+' : '') + c.net_pct.toFixed(2) + '%'}</td>
      <td>${c.seat_count}</td>
    </tr>`;
    if (!expandable) return mainRow;
    const seatRows = (c.seats || []).map(s => {
      const single = (totalAmt && totalAmt > 0 && s.amount_wan != null)
        ? (s.amount_wan / totalAmt * 100) : null;
      const dushi = single != null && single > 10;
      const dirCls = s.direction === '买入' ? 'bd-buy' : 'bd-sell';
      // 游资席位附 江湖名号 + 风格
      const alias = s.alias ? `<span class="bd-seat-alias" title="${escapeHtml(s.style || '')}">🎭 ${escapeHtml(s.alias)}</span>` : '';
      const styleTag = s.style ? `<span class="bd-seat-style">${escapeHtml(s.style)}</span>` : '';
      return `<div class="bd-seat-row${dushi ? ' bd-dushi' : ''}">
        <span class="bd-seat-name">${escapeHtml(s.seat || '—')}${dushi ? '<span class="bd-dushi-badge">独食</span>' : ''} ${alias}</span>
        <span class="bd-seat-dir ${dirCls}">${escapeHtml(s.direction || '')}</span>
        <span class="bd-seat-amt">${s.amount_wan != null ? s.amount_wan.toFixed(0) + ' 万' : '—'}</span>
        <span class="bd-seat-pct">${single != null ? single.toFixed(2) + '%' : '—'}</span>
        ${styleTag ? `<div class="bd-seat-style-row">${styleTag}</div>` : ''}
      </div>`;
    }).join('');
    const detailRow = `<tr class="bd-seat-detail" id="bd-detail-${idx}" hidden>
      <td colspan="9">
        <div class="bd-seat-detail-head">
          <span class="cat-dot cat-${c.key}"></span>${escapeHtml(c.label)} · ${c.seat_count} 席位 · 单席位占比 = 席位金额 ÷ 当日总成交
        </div>
        ${seatRows}
      </td>
    </tr>`;
    return mainRow + detailRow;
  }).join('');
}

// 展开/收起某一类席位明细
function toggleSeatDetail(rowEl) {
  const id = rowEl.getAttribute('data-detail');
  if (!id) return;
  const detail = document.getElementById(id);
  if (!detail) return;
  const nowHidden = !detail.hidden;
  detail.hidden = nowHidden;
  const caret = rowEl.querySelector('.bd-caret');
  if (caret) caret.textContent = nowHidden ? '▸' : '▾';
  rowEl.classList.toggle('bd-open', !nowHidden);
}

// ────────────────────────────────────────────
// STOCK · 我的交易 banner — 该股的所有历史买入/卖出
// ────────────────────────────────────────────
async function loadStockMyTrades(code) {
  const card = $('#stock-mytrades-card');
  const list = $('#stock-mytrades-list');
  if (!card || !list) return;
  try {
    const r = await _fetchWithTimeout(`/api/review/trades?code=${code}&since_days=720`);
    if (!r.ok) return;
    const j = await r.json();
    const trades = (j.data && j.data.trades) || [];
    if (!trades.length) { card.hidden = true; return; }
    card.hidden = false;
    list.innerHTML = trades.map(t => {
      const dir = t.direction === 'buy';
      const dirBadge = dir
        ? '<span class="cell-up">▲ 买</span>'
        : '<span class="cell-down">▼ 卖</span>';
      const review = t.last_review || {};
      const verdict = review.verdict || '';
      const score = review.score;
      const verdictBadge = verdict
        ? `<span class="verdict-pill ${escapeHtml(verdict)}">${escapeHtml(verdict)}${score != null ? ' · ' + score : ''}</span>`
        : '<span class="caption dim">未复盘</span>';
      const ts = (t.occurred_at || '').replace('T', ' ').slice(0, 16);
      return `<div class="mytr-row">
        <span class="mytr-dir">${dirBadge}</span>
        <span class="mytr-price">¥${fmtN(t.price, 2)}</span>
        <span class="mytr-shares">×${t.shares}</span>
        <span class="mytr-time caption dim">${escapeHtml(ts)}</span>
        <span class="mytr-verdict">${verdictBadge}</span>
        <button class="btn-mini primary" data-action="review-run:${t.id}">AI 复盘</button>
      </div>`;
    }).join('');
    // 一键复盘全部未评分按钮
    const unreviewed = trades.filter(t => !t.last_review);
    if (unreviewed.length) {
      list.insertAdjacentHTML('beforeend',
        `<div class="mytr-actions">
          <button class="btn-mini" id="mytr-bulk-review">⚡ 一键复盘全部未评分 (${unreviewed.length})</button>
        </div>`);
      const btn = $('#mytr-bulk-review');
      if (btn) btn.onclick = () => _reviewBulkRun(unreviewed.map(t => t.id));
    }
  } catch (e) {
    // 静默失败 — banner 是 nice-to-have
    card.hidden = true;
  }
}

async function _reviewBulkRun(tradeIds) {
  if (!tradeIds || !tradeIds.length) return;
  showToast(`开始批量复盘 ${tradeIds.length} 笔 (每笔约 1 分钟)…`, 'info');
  // C3: 批量复盘串行 ~1min/笔,全屏 overlay 让用户知道还在跑
  showLoadingOverlay(`批量 AI 复盘中…`, `0 / ${tradeIds.length}`);
  let done = 0;
  try {
    for (const id of tradeIds) {
      try {
        const r = await _fetchWithTimeout(`/api/review/trades/${id}/review?force=true`, { method: 'POST' });
        const j = await r.json();
        done++;
        const sub = document.getElementById('loading-overlay-sub');
        if (sub) sub.textContent = `${done} / ${tradeIds.length} · #${id}`;
        if (j.ok) {
          showToast(`✓ #${id} ${j.data?.verdict || ''} ${j.data?.score || ''}分`, 'success');
        } else {
          showToast(`#${id} 失败: ${j.error || '?'}`, 'error');
          break;  // 一笔失败就停,避免连续失败刷屏
        }
      } catch (e) {
        showToast(`#${id} 超时/失败: ${e.message}`, 'error');
        break;
      }
    }
    // 刷新当前股页 banner + review view (如果在)
    if (currentStockCode) loadStockMyTrades(currentStockCode);
    if (document.querySelector('.view-review:not([hidden])')) _reviewLoadList();
  } finally {
    hideLoadingOverlay();
  }
}

// ────────────────────────────────────────────
// 各 tab 的 KPI 小卡片
// ────────────────────────────────────────────
function renderKpi(host, items) {
  if (!host) return;
  host.innerHTML = items.map(([label, val, color, sub]) =>
    `<div class="kpi"><span class="kpi-label">${label}</span><span class="kpi-num" style="color:${color || INK}">${val}</span>${sub ? `<span class="kpi-sub">${sub}</span>` : ''}</div>`
  ).join('');
}

function renderFlowKpi(history) {
  if (!history.length) {
    $('#flow-kpi').innerHTML = '<div class="kpi"><span class="kpi-label">资金流</span><span class="kpi-num">无</span></div>';
    $('#flow-detail-wrap').hidden = true;
    return;
  }
  const last5 = history.slice(-5);
  const mainSum = last5.reduce((s, h) => s + (h.main_net || 0), 0);
  const superSum = last5.reduce((s, h) => s + (h.super_net || 0), 0);
  const bigSum = last5.reduce((s, h) => s + (h.big_net || 0), 0);
  const midSum = last5.reduce((s, h) => s + (h.mid_net || 0), 0);
  const smallSum = last5.reduce((s, h) => s + (h.small_net || 0), 0);
  const allSum = history.reduce((s, h) => s + (h.main_net || 0), 0);
  renderKpi($('#flow-kpi'), [
    ['5日主力', fmtN(mainSum, 0) + ' 万', colorFor(mainSum)],
    ['5日超大', fmtN(superSum, 0) + ' 万', colorFor(superSum)],
    ['5日大单', fmtN(bigSum, 0) + ' 万', colorFor(bigSum)],
    ['5日中单', fmtN(midSum, 0) + ' 万', colorFor(midSum)],
    ['5日小单', fmtN(smallSum, 0) + ' 万', colorFor(smallSum)],
    ['全期主力', fmtN(allSum, 0) + ' 万', colorFor(allSum), `${history.length} 日累计`],
  ]);
  // 渲染明细表 — 最新在最上面 + 全部 60 行,容器 max-height + vertical scroll
  const tbody = $('#flow-detail-table tbody');
  const descHistory = [...history].reverse();  // 倒序: 最新在顶
  // proxy 数据(只有 main_net,其他分单为 0) — 对超大/大/中/小/成交额显示 · 而非 0
  const isProxy = h => h && h.source && h.source !== 'akshare_individual';
  const cell = (n, color) => n == null
    ? '<td class="num dim">—</td>'
    : `<td class="num" style="color:${color || colorFor(n)}">${fmtN(n, 0)}</td>`;
  const cellProxy = () => '<td class="num dim" title="代理数据无分单">·</td>';
  tbody.innerHTML = descHistory.map(h => `<tr>
    <td>${h.date || '—'}</td>
    ${cell(h.main_net)}
    ${isProxy(h) ? cellProxy() : cell(h.super_net)}
    ${isProxy(h) ? cellProxy() : cell(h.big_net)}
    ${isProxy(h) ? cellProxy() : cell(h.mid_net)}
    ${isProxy(h) ? cellProxy() : cell(h.small_net)}
    ${h.amount_wan != null ? `<td class="num">${fmtN(h.amount_wan, 0)}</td>` : '<td class="num dim">—</td>'}
  </tr>`).join('');
  $('#flow-detail-wrap').hidden = false;
}

function renderKlineKpi(kline) {
  if (!kline.length) {
    $('#kline-kpi').innerHTML = '<div class="kpi"><span class="kpi-label">K线</span><span class="kpi-num">无</span></div>';
    return;
  }
  const last = kline[kline.length - 1];
  const first = kline[0];
  const cumPct = ((last.close / first.close - 1) * 100);
  const highPct = Math.max(...kline.map(k => (k.close / first.close - 1) * 100));
  const lowPct = Math.min(...kline.map(k => (k.close / first.close - 1) * 100));
  const upDays = kline.filter(k => (k.change_pct || 0) > 0).length;
  const luDays = kline.filter(k => (k.change_pct || 0) >= 9.5).length;
  const lastVr = last.vol_ratio_5d || 0;
  renderKpi($('#kline-kpi'), [
    [`${kline.length}日累`, (cumPct >= 0 ? '+' : '') + cumPct.toFixed(2) + '%', colorFor(cumPct)],
    ['期高', '+' + highPct.toFixed(2) + '%', UP],
    ['期低', lowPct.toFixed(2) + '%', DOWN],
    ['阳线', upDays + ' 天', upDays / kline.length > 0.5 ? UP : INK2],
    ['涨停日', luDays + ' 天', luDays > 0 ? UP : INK2],
    ['最新量比', lastVr.toFixed(2), lastVr > 1.5 ? UP : (lastVr < 0.7 ? DOWN : INK)],
  ]);
}

function renderSeatsKpi(seats) {
  const buy = seats.buy_total_wan || 0;
  const sell = seats.sell_total_wan || 0;
  const net = buy - sell;
  const groups = (seats.known_groups || []).slice(0, 4).join(' · ') || '—';
  renderKpi($('#seats-kpi'), [
    ['买入总金额', buy > 0 ? (buy / 1e4).toFixed(2) + ' 亿' : '—', colorFor(buy)],
    ['卖出总金额', sell > 0 ? (sell / 1e4).toFixed(2) + ' 亿' : '—', colorFor(-sell)],
    ['净买入', (net >= 0 ? '+' : '') + (net / 1e4).toFixed(2) + ' 亿', colorFor(net)],
    ['席位组', groups || '—', INK2, '已知组'],
    ['黑名单', seats.blacklisted ? '⚠ 是' : '否', seats.blacklisted ? DOWN : UP],
  ]);
}

async function loadAIAnalysis(code) {
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_analysis`).then(r => r.json());
    const data = env.data || {};
    if (!env.ok) {
      $('#ai-status').textContent = '';
      renderAIVerdict('—', 0);
      $('#ai-detail').innerHTML = `<div class="ai-rules">
        <div class="cr-mark no">!</div>
        <div class="cr-text">${escapeHtml(env.error || 'AI 调用失败')}</div>
      </div>`;
      $('#ai-summary').textContent = data.summary || '';
      return;
    }
    renderAIVerdict(data.verdict, data.conviction);
    $('#ai-summary').textContent = data.summary || '';

    const lp = data.layer_pass || {};
    const layers = [
      ['L1 风控', lp.L1_风控],
      ['L2 周期主线', lp.L2_周期主线],
      ['L3 形态', lp.L3_形态],
      ['L4 分时', lp.L4_分时],
    ];
    let html = '<div class="ai-layers">';
    for (const [name, status] of layers) {
      const sym = status === true ? '✓' : status === false ? '✗' : '?';
      const cls = status === true ? 'ok' : status === false ? 'no' : 'warn';
      html += `<div class="ai-layer ${cls}">
        <span class="ai-layer-mark">${sym}</span>
        <span class="ai-layer-name">${name}</span>
      </div>`;
    }
    html += '</div>';
    if ((data.rules_passed || []).length) {
      html += '<div class="ai-rule-section"><h4>通过</h4><ul>';
      data.rules_passed.forEach(r => { html += `<li class="ok">${escapeHtml(r)}</li>`; });
      html += '</ul></div>';
    }
    if ((data.rules_failed || []).length) {
      html += '<div class="ai-rule-section"><h4>违背</h4><ul>';
      data.rules_failed.forEach(r => { html += `<li class="no">${escapeHtml(r)}</li>`; });
      html += '</ul></div>';
    }
    if ((data.key_risks || []).length) {
      html += '<div class="ai-rule-section"><h4>关键风险</h4><ul>';
      data.key_risks.forEach(r => { html += `<li class="warn">${escapeHtml(r)}</li>`; });
      html += '</ul></div>';
    }
    $('#ai-detail').innerHTML = html;
    $('#ai-status').textContent = '完成';
  } catch (e) {
    $('#ai-status').textContent = '失败';
    $('#ai-verdict').textContent = '—';
    $('#ai-summary').textContent = `请求失败:${e.message}`;
  }
}

function renderAIVerdict(verdict, conv) {
  const v = (verdict || '—').toString();
  $('#ai-verdict').textContent = v;
  $('#ai-verdict').className = 'ai-verdict v-' + ({'买':'buy','观望':'wait','回避':'avoid'}[v] || 'na');
  $('#ai-conviction').textContent = `${conv ?? 0} / 100`;
  $('#ai-conviction-bar').style.width = `${Math.min(100, Math.max(0, conv || 0))}%`;
  $('#ai-conviction-bar').className = 'ai-conv-bar v-' + ({'买':'buy','观望':'wait','回避':'avoid'}[v] || 'na');
}

function esc(s) { return escapeHtml(s); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function drawFlowChart(history) {
  const dom = $('#flow-chart');
  if (!dom) return;
  if (echartsCharts.flow) echartsCharts.flow.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.flow = chart;
  if (!history.length) {
    chart.setOption(emptyChartOption('暂无资金流数据'));
    return;
  }
  const dates = history.map(h => h.date);
  chart.setOption({
    backgroundColor: 'transparent',
    legend: { data: ['主力','超大单','大单','中单','小单'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 12 },
    grid: { left: 50, right: 16, top: 36, bottom: 50 },
    tooltip: { trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK }, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10, formatter: v => (v/1e4).toFixed(1)+'亿' } },
    dataZoom: [{ type: 'inside', start: 60, end: 100 }, { type: 'slider', height: 18, bottom: 8, textStyle: { color: INK2 } }],
    series: [
      { name: '主力',     type: 'bar', stack: 'm', data: history.map(h => h.main_net),  itemStyle: { color: ACCENT } },
      { name: '超大单',   type: 'bar', data: history.map(h => h.super_net), itemStyle: { color: '#a78bcf' } },
      { name: '大单',     type: 'bar', data: history.map(h => h.big_net),   itemStyle: { color: '#7b9bd1' } },
      { name: '中单',     type: 'bar', data: history.map(h => h.mid_net),   itemStyle: { color: '#7a8088' } },
      { name: '小单',     type: 'bar', data: history.map(h => h.small_net), itemStyle: { color: '#54565b' } },
    ],
  });
}

// ──────────────────────────────────────────────────────────────
// K线状态 + 周期切换 + 指标计算 (MACD / KDJ / BOLL)
// ──────────────────────────────────────────────────────────────
let klineState = {
  period: 22,                 // 当前显示周期 (天) · 默认 1M
  indicators: { ma: true, macd: false, kdj: false, boll: false },
  data: [],                   // 当前缓存的 kline
  loading: false,
};

function ema(arr, n) {
  const k = 2 / (n + 1);
  const out = [];
  let prev = null;
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i];
    if (v == null || Number.isNaN(v)) { out.push(null); continue; }
    prev = prev == null ? v : v * k + prev * (1 - k);
    out.push(+prev.toFixed(4));
  }
  return out;
}

function computeMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const dif = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? +(emaFast[i] - emaSlow[i]).toFixed(4) : null);
  const dea = ema(dif, signal);
  const hist = dif.map((v, i) => v != null && dea[i] != null ? +((v - dea[i]) * 2).toFixed(4) : null);
  return { dif, dea, hist };
}

function computeKDJ(highs, lows, closes, n = 9, m1 = 3, m2 = 3) {
  const k = [], d = [], j = [];
  let prevK = 50, prevD = 50;
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) { k.push(null); d.push(null); j.push(null); continue; }
    let h9 = -Infinity, l9 = Infinity;
    for (let p = i - n + 1; p <= i; p++) {
      if (highs[p] > h9) h9 = highs[p];
      if (lows[p]  < l9) l9 = lows[p];
    }
    const rsv = h9 > l9 ? ((closes[i] - l9) / (h9 - l9)) * 100 : 50;
    const kNow = (m1 - 1) / m1 * prevK + 1 / m1 * rsv;
    const dNow = (m2 - 1) / m2 * prevD + 1 / m2 * kNow;
    const jNow = 3 * kNow - 2 * dNow;
    k.push(+kNow.toFixed(2));
    d.push(+dNow.toFixed(2));
    j.push(+jNow.toFixed(2));
    prevK = kNow; prevD = dNow;
  }
  return { k, d, j };
}

function computeBOLL(closes, n = 20, k = 2) {
  const mid = ma(closes, n);
  const upper = [], lower = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1 || mid[i] === '-') { upper.push(null); lower.push(null); continue; }
    let sum = 0;
    for (let p = i - n + 1; p <= i; p++) sum += (closes[p] - mid[i]) ** 2;
    const std = Math.sqrt(sum / n);
    upper.push(+(mid[i] + k * std).toFixed(3));
    lower.push(+(mid[i] - k * std).toFixed(3));
  }
  return { mid: mid.map(v => v === '-' ? null : v), upper, lower };
}

// 加载 K 线 (按周期)
async function loadKline(code, days) {
  if (klineState.loading) return;
  klineState.loading = true;
  const dom = $('#kline-chart');
  if (dom) dom.dataset.loading = '1';
  try {
    const data = await api(`/api/stock/${code}/kline?days=${days}`);
    klineState.data = data.kline || [];
    drawKlineChart();
    renderKlineKpi(klineState.data);
  } catch (e) {
    toast(`K线加载失败：${e.message}`, 'error');
  } finally {
    klineState.loading = false;
    if (dom) delete dom.dataset.loading;
  }
}

function drawKlineChart() {
  const dom = $('#kline-chart');
  if (!dom) return;
  if (echartsCharts.kline) echartsCharts.kline.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.kline = chart;
  const kline = klineState.data;
  if (!kline || !kline.length) {
    chart.setOption(emptyChartOption('暂无 K 线数据'));
    return;
  }
  const ind = klineState.indicators;
  const dates = kline.map(k => k.date);
  const ohlc = kline.map(k => [+k.open, +k.close, +k.low, +k.high]);
  const closes = kline.map(k => +k.close);
  const highs  = kline.map(k => +k.high);
  const lows   = kline.map(k => +k.low);
  const ma5  = kline[0].ma5  != null ? kline.map(k => k.ma5)  : ma(closes, 5);
  const ma10 = kline[0].ma10 != null ? kline.map(k => k.ma10) : ma(closes, 10);
  const ma20 = kline[0].ma20 != null ? kline.map(k => k.ma20) : ma(closes, 20);
  const ma60 = kline[0].ma60 != null ? kline.map(k => k.ma60) : ma(closes, 60);
  const barColors = kline.map(k => (k.close >= k.open) ? UP : DOWN);
  const vols = kline.map(k => k.volume || 0);

  // BOLL 叠加 (主图) — 提前算
  const boll = ind.boll ? computeBOLL(closes) : null;

  // 选择副图指标（macd / kdj 只能一个显示）
  const subIndicator = ind.macd ? 'macd' : ind.kdj ? 'kdj' : null;
  let macdData = null, kdjData = null;
  if (subIndicator === 'macd') macdData = computeMACD(closes);
  if (subIndicator === 'kdj')  kdjData  = computeKDJ(highs, lows, closes);

  // ── 布局：1 / 2 / 3 个 grid (主 / 量 / 副) ──
  const hasSub = !!subIndicator;
  const grids = [
    { left: 56, right: 56, top: 12, height: hasSub ? '58%' : '70%' },
    { left: 56, right: 56, top: hasSub ? '72%' : '74%', height: hasSub ? '14%' : '20%' },
  ];
  if (hasSub) grids.push({ left: 56, right: 56, top: '88%', height: '10%' });

  const xAxes = [
    { type: 'category', data: dates, gridIndex: 0,
      axisLine: { lineStyle: { color: '#2a2825' } },
      axisLabel: { color: INK2, fontSize: 10, hideOverlap: true },
      splitLine: { show: false } },
    { type: 'category', data: dates, gridIndex: 1,
      axisLine: { lineStyle: { color: '#2a2825' } },
      axisLabel: { show: false },
      splitLine: { show: false } },
  ];
  const yAxes = [
    { scale: true, gridIndex: 0,
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: INK2, fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a2825' } } },
    { gridIndex: 1, scale: true, splitNumber: 2,
      axisLabel: { color: INK2, fontSize: 9, formatter: v => (v/1e4).toFixed(1)+'万' },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: '#2a2825' } } },
  ];
  if (hasSub) {
    xAxes.push({ type: 'category', data: dates, gridIndex: 2,
      axisLine: { lineStyle: { color: '#2a2825' } },
      axisLabel: { show: false },
      splitLine: { show: false } });
    yAxes.push({ gridIndex: 2, scale: true, splitNumber: 2,
      axisLabel: { color: INK2, fontSize: 9 },
      splitLine: { lineStyle: { color: GRID } },
      axisLine: { lineStyle: { color: '#2a2825' } } });
  }

  // ── Series ──
  const series = [];
  // K线（主图）
  series.push({
    name: 'K线', type: 'candlestick', data: ohlc,
    itemStyle: {
      color: UP, color0: DOWN,
      borderColor: UP, borderColor0: DOWN,
      borderColorDoji: UP,
    },
  });
  // MA 叠加（主图）
  if (ind.ma) {
    series.push({ name: 'MA5',  type: 'line', data: ma5,  smooth: true, lineStyle: { color: '#e8b75a', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MA10', type: 'line', data: ma10, smooth: true, lineStyle: { color: '#7b9bd1', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { color: ACCENT,  width: 1.2 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MA60', type: 'line', data: ma60, smooth: true, lineStyle: { color: '#a78bcf', width: 1.2 }, symbol: 'none', connectNulls: true });
  }
  // BOLL 叠加（主图）
  if (boll) {
    series.push({ name: 'BOLL上', type: 'line', data: boll.upper, smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL中', type: 'line', data: boll.mid,   smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL下', type: 'line', data: boll.lower, smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
  }
  // 量（grid 1）
  series.push({
    name: '量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
    data: vols.map((v, i) => ({ value: v, itemStyle: { color: barColors[i] } })),
    barWidth: '60%',
  });

  // MACD 副图（grid 2）
  if (subIndicator === 'macd' && macdData) {
    series.push({ name: 'DIF', type: 'line', data: macdData.dif, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#ffffff', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'DEA', type: 'line', data: macdData.dea, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#f0c075', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MACD', type: 'bar', data: macdData.hist.map(v => v == null ? 0 : v), xAxisIndex: 2, yAxisIndex: 2,
      barWidth: '50%',
      itemStyle: { color: p => (p.value >= 0 ? UP : DOWN) } });
  }
  // KDJ 副图（grid 2）
  if (subIndicator === 'kdj' && kdjData) {
    series.push({ name: 'K', type: 'line', data: kdjData.k, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#ffffff', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'D', type: 'line', data: kdjData.d, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#f0c075', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'J', type: 'line', data: kdjData.j, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#a78bcf', width: 1 }, symbol: 'none', connectNulls: true });
  }

  // ── Tooltip ── THS 风格精确读数
  const allNames = ['K线', 'MA5','MA10','MA20','MA60', ...(boll ? ['BOLL上','BOLL中','BOLL下'] : []), '量'];
  if (subIndicator === 'macd') allNames.push('DIF','DEA','MACD');
  if (subIndicator === 'kdj')  allNames.push('K','D','J');

  const option = {
    backgroundColor: 'transparent',
    animation: false,
    grid: grids,
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: ACCENT, width: 0.6, opacity: 0.6 }, lineStyle: { color: ACCENT, width: 0.6, opacity: 0.6 } },
      backgroundColor: 'rgba(20, 18, 14, 0.96)',
      borderColor: '#2a2825',
      borderWidth: 1,
      padding: [10, 14],
      textStyle: { color: INK, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' },
      extraCssText: 'box-shadow: 0 8px 32px rgba(0,0,0,.4); border-radius: 8px; backdrop-filter: blur(8px);',
      formatter: (params) => {
        if (!params || !params.length) return '';
        const idx = params[0].dataIndex;
        const k = kline[idx];
        if (!k) return '';
        const o = +k.open, h = +k.high, l = +k.low, c = +k.close;
        const chg = k.change_pct || 0;
        const amt = (k.amount || 0) / 1e8;
        let html = `<div style="font-weight:600;color:${INK};margin-bottom:6px;font-family:Manrope,sans-serif">${k.date}</div>`;
        html += `<div style="display:grid;grid-template-columns:auto auto;gap:2px 14px">`;
        html += `<span style="color:${INK3}">开</span><b>${o.toFixed(2)}</b>`;
        html += `<span style="color:${INK3}">收</span><b style="color:${chg >= 0 ? UP : DOWN}">${c.toFixed(2)}</b>`;
        html += `<span style="color:${INK3}">高</span><b style="color:${UP}">${h.toFixed(2)}</b>`;
        html += `<span style="color:${INK3}">低</span><b style="color:${DOWN}">${l.toFixed(2)}</b>`;
        html += `<span style="color:${INK3}">涨跌</span><b style="color:${chg >= 0 ? UP : DOWN}">${(chg >= 0 ? '+' : '') + chg.toFixed(2)}%</b>`;
        html += `<span style="color:${INK3}">成交</span><b>${amt.toFixed(2)} 亿</b>`;
        html += `</div>`;
        if (ind.ma) {
          html += `<div style="margin-top:6px;padding-top:6px;border-top:1px dashed rgba(255,255,255,.1);display:grid;grid-template-columns:auto auto;gap:2px 14px">`;
          const mas = [
            ['MA5', ma5[idx]], ['MA10', ma10[idx]],
            ['MA20', ma20[idx]], ['MA60', ma60[idx]],
          ];
          mas.forEach(([n, v]) => {
            if (v != null && v !== '-') {
              html += `<span style="color:${INK3}">${n}</span><b style="color:${v > c ? UP : DOWN}">${(+v).toFixed(2)}</b>`;
            }
          });
          html += `</div>`;
        }
        if (boll) {
          const u = boll.upper[idx], m = boll.mid[idx], lo = boll.lower[idx];
          if (u != null && m != null && lo != null) {
            html += `<div style="margin-top:4px;color:${INK3};font-size:10px">BOLL ${lo.toFixed(2)} / ${m.toFixed(2)} / ${u.toFixed(2)}</div>`;
          }
        }
        if (subIndicator === 'macd' && macdData) {
          const d = macdData.dif[idx], e = macdData.dea[idx], h2 = macdData.hist[idx];
          if (d != null && e != null && h2 != null) {
            html += `<div style="margin-top:6px;padding-top:6px;border-top:1px dashed rgba(255,255,255,.1);display:grid;grid-template-columns:auto auto;gap:2px 14px">`;
            html += `<span style="color:${INK3}">DIF</span><b style="color:${d >= 0 ? UP : DOWN}">${d.toFixed(3)}</b>`;
            html += `<span style="color:${INK3}">DEA</span><b style="color:${e >= 0 ? UP : DOWN}">${e.toFixed(3)}</b>`;
            html += `<span style="color:${INK3}">MACD</span><b style="color:${h2 >= 0 ? UP : DOWN}">${h2.toFixed(3)}</b>`;
            html += `</div>`;
          }
        }
        if (subIndicator === 'kdj' && kdjData) {
          const kv = kdjData.k[idx], dv = kdjData.d[idx], jv = kdjData.j[idx];
          if (kv != null) {
            html += `<div style="margin-top:6px;padding-top:6px;border-top:1px dashed rgba(255,255,255,.1);display:grid;grid-template-columns:auto auto;gap:2px 14px">`;
            html += `<span style="color:${INK3}">K</span><b style="color:${kv >= 80 ? UP : kv <= 20 ? DOWN : INK}">${kv.toFixed(1)}</b>`;
            html += `<span style="color:${INK3}">D</span><b style="color:${dv >= 80 ? UP : dv <= 20 ? DOWN : INK}">${dv.toFixed(1)}</b>`;
            html += `<span style="color:${INK3}">J</span><b style="color:${jv >= 100 ? UP : jv <= 0 ? DOWN : INK}">${jv.toFixed(1)}</b>`;
            html += `</div>`;
          }
        }
        return html;
      },
    },
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: hasSub ? [0,1,2] : [0,1] },
      { type: 'slider', xAxisIndex: hasSub ? [0,1,2] : [0,1], height: 18, bottom: 4,
        textStyle: { color: INK2, fontSize: 9 },
        borderColor: '#2a2825',
        fillerColor: 'rgba(212,160,86,0.15)',
        handleStyle: { color: ACCENT, borderColor: ACCENT } },
    ],
    series,
  };
  chart.setOption(option);

  // KDJ 参考线 (80/20)
  if (subIndicator === 'kdj') {
    chart.setOption({
      yAxis: yAxes.map((y, i) => i === 2 ? {
        ...y,
        markLine: {
          silent: true, symbol: 'none',
          data: [
            { yAxis: 80, label: { show: false }, lineStyle: { color: '#5a5852', type: 'dotted', width: 0.8 } },
            { yAxis: 20, label: { show: false }, lineStyle: { color: '#5a5852', type: 'dotted', width: 0.8 } },
          ],
        },
      } : y),
    });
  }

  // ── 浮动 readout 同步（鼠标移动）──
  chart.on('updateAxisPointer', (event) => {
    const xAxisInfo = event.axesInfo && event.axesInfo[0];
    if (!xAxisInfo || xAxisInfo.value == null) return;
    const idx = xAxisInfo.value;
    const k = kline[idx];
    if (!k) return;
    const c = +k.close, prev = idx > 0 ? +kline[idx - 1].close : c;
    const pct = prev > 0 ? ((c - prev) / prev) * 100 : 0;
    const rd = $('#kline-readout');
    if (rd) {
      rd.querySelector('.kr-date').textContent = k.date;
      rd.querySelector('.kr-ohlc').innerHTML =
        `O <span class="v">${(+k.open).toFixed(2)}</span> ` +
        `H <span class="v up">${(+k.high).toFixed(2)}</span> ` +
        `L <span class="v down">${(+k.low).toFixed(2)}</span> ` +
        `C <span class="v ${pct >= 0 ? 'up' : 'down'}">${c.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)</span>`;
    }
  });
}

// 同步工具栏高亮态 (周期 + 指标)
function syncKlineToolbar() {
  $$('#kline-period .kt-pill').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.days === klineState.period);
  });
  $$('#kline-indicators .kt-chip').forEach(btn => {
    btn.classList.toggle('active', !!klineState.indicators[btn.dataset.ind]);
  });
}

// 切换指标 (MACD 与 KDJ 互斥:只允许其中一个副图)
function toggleKlineIndicator(name) {
  const ind = klineState.indicators;
  if (name === 'macd' || name === 'kdj') {
    const willOn = !ind[name];
    ind.macd = false; ind.kdj = false;
    if (willOn) ind[name] = true;
  } else {
    ind[name] = !ind[name];
  }
  syncKlineToolbar();
  drawKlineChart();
}

function ma(arr, n) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < arr.length; i++) {
    sum += arr[i];
    if (i >= n) sum -= arr[i - n];
    out.push(i >= n - 1 ? +(sum / n).toFixed(2) : '-');
  }
  return out;
}

function renderSeatsTable(rows, seats) {
  const tbody = $('#seats-table tbody');
  if (seats) renderSeatsKpi(seats);
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">近 30 日无龙虎席位</td></tr>';
    return;
  }
  const buyRows = rows.filter(r => (r.direction || '').includes('买'));
  const sellRows = rows.filter(r => (r.direction || '').includes('卖'));
  let html = '';
  if (buyRows.length) {
    html += `<tr><td colspan="6" class="dim" style="text-align:left;padding:8px 0 4px;background:transparent;border:none">▼ 买入席位 (${buyRows.length})</td></tr>`;
    html += buyRows.map(renderSeatRow).join('');
  }
  if (sellRows.length) {
    html += `<tr><td colspan="6" class="dim" style="text-align:left;padding:8px 0 4px;background:transparent;border:none">▲ 卖出席位 (${sellRows.length})</td></tr>`;
    html += sellRows.map(renderSeatRow).join('');
  }
  tbody.innerHTML = html;
}

function renderHolders(holders) {
  const tbody = $('#holders-table tbody');
  if (!holders || !holders.holder_total) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">无最新季报数据</td></tr>';
    if ($('#holders-kpi')) $('#holders-kpi').innerHTML = '<div class="metric"><span class="m-num">—</span><span class="m-unit">暂无</span></div>';
    return;
  }
  // KPI: 散户占比 / 主力占比 / 集中度 / 户均
  const retail = holders.retail_proxy_pct;
  const main = holders.main_proxy_pct;
  const focus = holders.focus_label || '—';
  const avg = holders.avg_shares || 0;
  renderKpi($('#holders-kpi'), [
    ['散户占比(估)', retail != null ? retail.toFixed(1) + ' %' : '—', retail > 50 ? UP : (retail < 20 ? DOWN : INK2), '非前十大 + 中小单'],
    ['主力占比(估)', main != null ? main.toFixed(1) + ' %' : '—', main > 70 ? UP : INK2, '前十大 + 大单'],
    ['股东户数', (holders.holder_total || 0).toLocaleString(), INK, holders.report_date || ''],
    ['户均持股', avg > 0 ? avg.toLocaleString() + ' 股' : '—', INK],
    ['集中度', focus, focus.includes('集中') ? UP : (focus.includes('分散') ? DOWN : INK2)],
  ]);
  // 表格:history 倒序 → 最新在上
  const rows = (holders.history || []).slice().reverse();
  tbody.innerHTML = rows.map(r => {
    const cp = r.change_pct;
    const cpStr = cp != null
      ? `<span style="color:${cp > 0 ? DOWN : (cp < 0 ? UP : INK2)}">${cp > 0 ? '+' : ''}${cp.toFixed(2)}%</span>`
      : '—';
    const top10 = r.top10_pct != null ? r.top10_pct.toFixed(1) + ' %' : '—';
    const avg = r.avg_shares != null ? r.avg_shares.toLocaleString() : '—';
    const fl = r.focus_label || '—';
    const flColor = fl.includes('集中') ? UP : (fl.includes('分散') ? DOWN : INK2);
    return `<tr>
      <td>${r.report_date || '—'}</td>
      <td>${(r.holder_total || 0).toLocaleString()}</td>
      <td>${cpStr}</td>
      <td>${avg}</td>
      <td><span style="color:${flColor}">${fl}</span></td>
      <td>${top10}</td>
    </tr>`;
  }).join('');
}

function renderSeatRow(r) {
  const amt = r.amount_wan;
  const amtStr = amt != null
    ? `<span style="color:${(r.direction || '').includes('买') ? UP : DOWN}">${amt >= 1e4 ? (amt/1e4).toFixed(2) + ' 亿' : amt.toFixed(0) + ' 万'}</span>`
    : '—';
  // 席位单元格:江湖主别名(粗)+ 衍生别名(中)+ 营业部全称(暗)
  const primary = (r.label || '').trim();
  const aliases = (r.aliases || []);
  const realName = (r.real_name || '').trim();
  const tier = (r.tier || '').trim();
  const aliasChips = aliases.length
    ? aliases.slice(0, 3).map(a => `<span class="alias-chip-mini">${escapeHtml(a)}</span>`).join(' ')
    : '';
  const seatCell = primary
    ? `<div class="seat-cell">
         <div class="seat-alias">「${escapeHtml(primary)}」${tier ? `<span class="seat-tier">${escapeHtml(tier)}</span>` : ''}</div>
         ${aliasChips ? `<div class="seat-aliases">${aliasChips}</div>` : ''}
         ${realName ? `<div class="seat-real dim">本名: ${escapeHtml(realName)}</div>` : ''}
         <div class="seat-full dim">${escapeHtml(r.seat || '—')}</div>
       </div>`
    : `<div class="seat-cell"><div class="seat-full">${escapeHtml(r.seat || '—')}</div></div>`;
  // tier badge 作为 group cell
  const groupCell = tier && tier !== '论坛ID'
    ? `<span class="badge badge-tier-${escapeHtml(tier)}">${escapeHtml(tier)}</span>`
    : (r.group ? `<span class="badge badge-${escapeHtml(r.group)}">${escapeHtml(r.group)}</span>` : '<span class="dim">—</span>');
  return `<tr>
    <td>${r.date || '—'}</td>
    <td>${seatCell}</td>
    <td><span class="dir-${(r.direction || '').includes('买') ? 'buy' : 'sell'}">${r.direction || '—'}</span></td>
    <td class="num">${amtStr}</td>
    <td>${groupCell}</td>
    <td>${primary ? `<span class="dim">${escapeHtml(r.note || '')}</span>` : '<span class="dim">—</span>'}</td>
  </tr>`;
}

function emptyChartOption(msg) {
  return {
    backgroundColor: 'transparent',
    graphic: [{ type: 'text', left: 'center', top: 'middle',
      style: { text: msg, fill: INK2, font: '14px Manrope' } }],
  };
}

// ────────────────────────────────────────────
// Skeleton / Retry / 数字滚动
// ────────────────────────────────────────────
function skeletonLines(count = 3, size = '') {
  const cls = size ? ` skeleton-line ${size}` : ' skeleton-line';
  return Array.from({ length: count }, () => `<div class="skeleton${cls}">.</div>`).join('');
}
function errorCard(msg, onRetry) {
  const retry = onRetry
    ? `<button class="er-retry" id="er-retry-btn">↻ 重试</button>`
    : '';
  return `<div class="error-card">
    <div class="er-msg">⚠ <b>加载失败</b> · ${escapeHtml(msg)}</div>
    ${retry}
  </div>`;
}
// 数字滚动（首次显示或大变化时使用，~500ms 平滑过渡）
function animateNumber(el, from, to, dur = 500, fmt = (v) => v.toFixed(2), dir) {
  if (!el) return;
  if (dir == null) dir = to > from ? 'up' : to < from ? 'down' : 'flat';
  const start = performance.now();
  const delta = to - from;
  el.classList.add('is-animating', `flash-${dir}`);
  function step(t) {
    const k = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - k, 3); // easeOutCubic
    el.textContent = fmt(from + delta * eased);
    if (k < 1) requestAnimationFrame(step);
    else el.classList.remove('is-animating');
  }
  requestAnimationFrame(step);
  setTimeout(() => el.classList.remove(`flash-${dir}`), 700);
}
// retry 绑定辅助：渲染后调用 bindRetry(el, fn) 把 er-retry 按钮接到 fn
function bindRetry(host, fn) {
  const btn = host.querySelector('#er-retry-btn');
  if (btn) btn.addEventListener('click', () => fn());
}

// ────────────────────────────────────────────
// STOCK · 任意日分时回看
// ────────────────────────────────────────────
let intraDayCache = new Map();  // date -> data
let intraDayLoading = null;

function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}
function shiftDate(s, days) {
  const d = new Date(s + 'T00:00:00');
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
function weekdayCN(s) {
  const wk = ['日','一','二','三','四','五','六'];
  const d = new Date(s + 'T00:00:00');
  return '周' + wk[d.getDay()];
}

function initIntraDayPicker(code) {
  const pick = $('#intra-day-pick');
  const prev = $('#intra-day-prev');
  const next = $('#intra-day-next');
  const load = $('#intra-day-load');
  const label = $('#intra-day-label');
  if (!pick) return;
  if (!pick.value) pick.value = todayStr();
  const refreshLabel = () => {
    const v = pick.value || todayStr();
    label.textContent = v + ' ' + weekdayCN(v);
  };
  refreshLabel();
  pick.onchange = refreshLabel;
  load.onclick = () => autoLoadIntraDay();
  const todayBtn = $('#intra-day-today');
  if (todayBtn) todayBtn.onclick = () => { pick.value = todayStr(); refreshLabel(); autoLoadIntraDay(); };

  // 触发首次交易日历加载(背景跑,不阻塞 UI)
  _ensureTradeDates();

  // 交易日导航: 用 _tradeDates 缓存(全局共享,按需扩展),不走 klineState.data (1 个月太短)
  const walkIntraDay = async (dir) => {
    const cur = pick.value || todayStr();
    await _ensureTradeDates();
    let target = _shiftByTradeDate(cur, dir);

    // 撞底检测: prev 已经走到 _tradeDates 最旧且目标不变 → 按需扩展
    if (dir < 0 && _tradeDates.length > 0 && target === cur && cur === _tradeDates[_tradeDates.length - 1]) {
      const note = $('#intra-day-note');
      if (note) { note.textContent = '扩展交易日历…'; note.style.color = INK2; }
      await _growTradeDates(250);  // 一次扩 ≈ 1 年
      target = _shiftByTradeDate(cur, dir);
    }

    if (target > todayStr()) target = todayStr();  // 不看未来
    pick.value = target;
    refreshLabel();
    autoLoadIntraDay();
  };
  prev.onclick = () => walkIntraDay(-1);
  next.onclick = () => walkIntraDay(+1);

  function autoLoadIntraDay() {
    if (!currentStockCode) return;
    loadIntraDay(currentStockCode, pick.value);
    updateIntraDayNavState();
  }

  window.updateIntraDayNavState = updateIntraDayNavState;
  function updateIntraDayNavState() {
    if (!prev || !next) return;
    next.disabled = (pick.value || todayStr()) >= todayStr();  // 已是最新交易日/今天,不能往后
    // 往前按钮: 撞到 _tradeDates 缓存最旧且已到上限 → 禁用
    const cur = pick.value || todayStr();
    const atOldest = _tradeDates.length > 0 && cur === _tradeDates[_tradeDates.length - 1];
    prev.disabled = atOldest && _tradeDatesLimit >= _TRADE_DATES_LIMIT_MAX;
  }
  updateIntraDayNavState();
}

async function loadIntraDay(code, dateStr) {
  if (!code || !dateStr) return;
  if (intraDayLoading === dateStr) return;
  const cached = intraDayCache.get(dateStr);
  if (cached && cached.code === code) {
    renderIntraDay(cached);
    return;
  }
  intraDayLoading = dateStr;
  const note = $('#intra-day-note');
  note.textContent = `加载 ${dateStr} 分时 …`;
  note.style.color = INK2;
  try {
    const data = await api(`/api/stock/${code}/intraday?date=${encodeURIComponent(dateStr)}`);
    const merged = { code, date: dateStr, ...data };
    intraDayCache.set(dateStr, merged);
    renderIntraDay(merged);
  } catch (e) {
    note.textContent = `加载失败：${e.message}`;
    note.style.color = DOWN;
  } finally {
    intraDayLoading = null;
  }
}

function renderIntraDay(data) {
  const code = data.code;
  const date = data.date || data.code;
  const ticks = data.ticks || [];
  const note = $('#intra-day-note');
  const kpi = $('#intra-day-kpi');

  if (!ticks.length) {
    note.textContent = data.note || `${date} 无分时数据（可能非交易日或数据源不可达）`;
    note.style.color = data.note ? INK2 : INK2;
    renderKpi(kpi, [['分时', '无数据', INK3], ['来源', data.source || '—', INK2]]);
    drawIntraDayChart(code, date, [], null, null, null);
    return;
  }

  // 计算日内 KPI
  const opens = ticks.map(t => t.open).filter(v => v != null);
  const highs = ticks.map(t => t.high).filter(v => v != null);
  const lows  = ticks.map(t => t.low).filter(v => v != null);
  const prices = ticks.map(t => t.price).filter(v => v != null);
  const openRef = opens.length ? opens[0] : prices[0];
  const lastPrice = prices[prices.length - 1];
  const hi = highs.length ? Math.max(...highs) : null;
  const lo = lows.length ? Math.min(...lows) : null;
  const pct = (openRef && lastPrice) ? ((lastPrice - openRef) / openRef * 100) : null;
  const totalVol = ticks.reduce((s, t) => s + (t.volume_hand || 0), 0);

  // 量加权均价(VWAP)
  let cumPV = 0, cumV = 0;
  for (const t of ticks) {
    if (t.price != null) {
      const v = t.volume_hand || 0;
      cumPV += t.price * v;
      cumV += v;
    }
  }
  const vwap = cumV > 0 ? +(cumPV / cumV).toFixed(3) : null;

  // 振幅 = (最高 - 最低) / 昨收
  const refForAmp = (data.prev_close ?? lastStockContext.prev_close) ?? openRef;
  const amp = (refForAmp && hi != null && lo != null) ? +(((hi - lo) / refForAmp) * 100).toFixed(2) : null;

  // 主动买卖笔数 (side 含"买"/"卖"/"b"/"s")
  let buyCnt = 0, sellCnt = 0;
  for (const t of ticks) {
    const s = (t.side || '').toLowerCase();
    if (s.includes('买') || s === 'b' || s.startsWith('buy') || s.includes('bid')) buyCnt++;
    else if (s.includes('卖') || s === 's' || s.startsWith('sell') || s.includes('ask')) sellCnt++;
  }
  const sideRatio = (buyCnt + sellCnt) > 0 ? (buyCnt / (buyCnt + sellCnt)) : null;
  const isSina = (data.source || '').startsWith('sina');
  const volStr = isSina
    ? (totalVol >= 1e8 ? (totalVol / 1e8).toFixed(2) + ' 亿股' : (totalVol / 1e4).toFixed(2) + ' 万股')
    : (totalVol >= 1e4 ? (totalVol / 1e4).toFixed(2) + ' 万手' : totalVol.toFixed(0) + ' 手');

  renderKpi(kpi, [
    ['开盘',     openRef != null ? openRef.toFixed(2) : '—', INK],
    ['最新',     lastPrice != null ? lastPrice.toFixed(2) : '—', colorFor(pct)],
    ['日内涨跌', pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '—', colorFor(pct)],
    ['均价VWAP', vwap != null ? vwap.toFixed(3) : '—', ACCENT],
    ['振幅',     amp != null ? (amp >= 0 ? '+' : '') + amp.toFixed(2) + '%' : '—', amp != null ? (amp >= 5 ? UP : INK2) : INK3],
    ['主动买/卖',
      (buyCnt + sellCnt) > 0
        ? `${buyCnt} / ${sellCnt}` + (sideRatio != null ? ` · ${(sideRatio * 100).toFixed(0)}%` : '')
        : '—',
      sideRatio != null ? (sideRatio >= 0.55 ? UP : sideRatio <= 0.45 ? DOWN : INK2) : INK3],
    ['最高',     hi != null ? hi.toFixed(2) : '—', UP],
    ['最低',     lo != null ? lo.toFixed(2) : '—', DOWN],
    ['Tick 数',  ticks.length, INK2],
    ['成交',     volStr, INK],
    ['数据源',   data.source || '—', INK2],
  ]);

  note.textContent = `${date} ${weekdayCN(date)} · ${data.source || ''} · ${ticks.length} 根 K`;
  note.style.color = INK2;

  drawIntraDayChart(code, date, ticks, openRef,
    data.prev_close ?? lastStockContext.prev_close,
    data.limit_up_price ?? lastStockContext.limit_up_price);
}

function drawIntraDayChart(code, date, ticks, openRef, prevClose, limitUp) {
  const dom = $('#intra-day-chart');
  if (!dom) return;
  if (echartsCharts.intraDay) echartsCharts.intraDay.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.intraDay = chart;

  if (!ticks.length) {
    chart.setOption(emptyChartOption('暂无分时数据'));
    return;
  }

  const times = ticks.map(t => t.time);
  const prices = ticks.map(t => t.price);
  // 昨收参考线（如果没有则用 openRef）
  const refVal = prevClose != null ? prevClose : openRef;
  const refLine = times.map(_ => refVal);

  // ── 均价线（量加权 rolling）──
  const avgLine = [];
  let cumPV = 0, cumV = 0;
  for (let i = 0; i < ticks.length; i++) {
    const p = ticks[i].price;
    const v = ticks[i].volume_hand || 0;
    if (p != null) { cumPV += p * v; cumV += v; }
    avgLine.push(cumV > 0 ? +(cumPV / cumV).toFixed(3) : null);
  }

  // ── 量能柱：按 side 着色（买盘红 / 卖盘绿 / 中性灰）──
  const volBars = ticks.map(t => {
    const v = t.volume_hand || 0;
    const s = (t.side || '').toLowerCase();
    let color;
    if (s.includes('买') || s.includes('b')) color = UP;
    else if (s.includes('卖') || s.includes('s')) color = DOWN;
    else color = INK3;
    return { value: v, itemStyle: { color, opacity: 0.6 } };
  });

  // ── 时间网格标记（markLine）：9:30 / 11:30 / 13:00 / 15:00 ──
  const refTimes = ['09:30', '11:30', '13:00', '15:00'];
  const refIndex = refTimes.map(rt => times.findIndex(t => t && t.startsWith(rt))).filter(i => i >= 0);
  const timeMarkers = refIndex.map(i => ({
    xAxis: i, lineStyle: { color: INK3, type: 'dashed', width: 0.8, opacity: 0.4 },
    label: { show: true, formatter: times[i].slice(0, 5), position: 'start', color: INK3, fontSize: 9 }
  }));

  // ── 涨停价参考线 ──
  const limitUpLine = (limitUp != null && limitUp > 0)
    ? { name: '涨停价', type: 'line', data: times.map(_ => limitUp),
        showSymbol: false, lineStyle: { color: UP, type: 'dashed', width: 1, opacity: 0.7 },
        tooltip: { show: false } }
    : null;

  // ── 日线均线参考(MA5/MA10/MA20):取截止选中日的日收盘算均值,水平参考线 ──
  let refLines = [];
  {
    const bars = (klineState.data || [])
      .filter(k => k.date && k.close != null && k.date <= date)
      .sort((a, b) => (a.date < b.date ? -1 : 1));
    const closesUpTo = bars.map(k => +k.close);
    const maSpecs = [
      [5,  '#ff9f43', 1.6],
      [10, '#7fc8c9', 1.1],
      [20, '#9a8cff', 1.1],
    ];
    for (const [n, color, w] of maSpecs) {
      if (closesUpTo.length < n) continue;
      const seg = closesUpTo.slice(-n);
      const ma = seg.reduce((a, b) => a + b, 0) / n;
      refLines.push({
        yAxis: +ma.toFixed(3),
        lineStyle: { color, type: n === 5 ? 'solid' : 'dashed', width: w, opacity: 0.85 },
        label: { formatter: `MA${n} ${ma.toFixed(2)}`, color, fontSize: n === 5 ? 10 : 9,
                 position: n === 5 ? 'insideStartTop' : 'insideEndTop', fontWeight: n === 5 ? 700 : 500 },
      });
    }
  }

  chart.setOption({
    backgroundColor: 'transparent',
    title: { text: `${code}  ${date}  分时`, textStyle: { color: INK2, fontSize: 11 }, left: 8, top: 4 },
    grid: [
      { left: 56, right: 64, top: 30, height: '58%' },
      { left: 56, right: 24, top: '74%', height: '22%' },
    ],
    tooltip: {
      trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825',
      borderWidth: 1, textStyle: { color: INK, fontSize: 11 },
      formatter: (params) => {
        if (!params || !params.length) return '';
        const t = params[0].axisValue;
        const pMap = Object.fromEntries(params.map(p => [p.seriesName, p.value]));
        let s = `<div style="color:${INK2};margin-bottom:4px">${t}</div>`;
        const price = pMap['价格'];
        if (price != null) {
          const pct = refVal ? ((price - refVal) / refVal * 100) : 0;
          const upCls = pct >= 0 ? `color:${UP}` : `color:${DOWN}`;
          s += `<div>价 <b style="${upCls}">${price.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)</b></div>`;
        }
        if (pMap['均价'] != null) {
          s += `<div>均价 <b style="color:${ACCENT}">${(+pMap['均价']).toFixed(3)}</b></div>`;
        }
        if (pMap['昨收'] != null && refVal != null) {
          s += `<div style="color:${INK3}">昨收 ${refVal.toFixed(2)}</div>`;
        }
        if (limitUp && price != null) {
          s += `<div style="color:${UP}">涨停 ${limitUp.toFixed(2)}</div>`;
        }
        if (pMap['成交量'] != null) {
          const v = pMap['成交量'];
          s += `<div>成交 <b style="color:${INK}">${v >= 1e4 ? (v / 1e4).toFixed(2) + ' 万' : v.toFixed(0)}</b></div>`;
        }
        return s;
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: {
      textStyle: { color: INK2, fontSize: 10 }, top: 4, right: 8,
      data: limitUp ? ['价格', '均价', '昨收', '涨停价', '成交量'] : ['价格', '均价', '昨收', '成交量']
    },
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, axisLine: { lineStyle: { color: '#2a2825' } },
        axisLabel: { color: INK2, fontSize: 10 }, splitLine: { show: false } },
      { type: 'category', data: times, gridIndex: 1, axisLine: { lineStyle: { color: '#2a2825' } },
        axisLabel: { color: INK2, fontSize: 9, interval: Math.max(1, Math.floor(times.length / 8)) }, splitLine: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: GRID } },
        axisLabel: { color: INK2, fontSize: 10 },
        // 右侧价格标签（当前价 + 昨收 + 涨停）
        axisPointer: { label: { show: false } } },
      { gridIndex: 1, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 9 } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ],
    series: [
      { name: '价格', type: 'line', data: prices, showSymbol: false, smooth: false,
        lineStyle: { color: ACCENT, width: 1.6 }, itemStyle: { color: ACCENT },
        areaStyle: { color: 'rgba(212,160,86,0.08)' },
        markLine: { silent: true, symbol: 'none', data: [...timeMarkers, ...refLines] } },
      { name: '均价', type: 'line', data: avgLine, showSymbol: false,
        lineStyle: { color: '#ff9f43', width: 1.8, type: 'solid' },
        itemStyle: { color: '#ff9f43' },
        z: 3 },
      { name: '昨收', type: 'line', data: refLine, showSymbol: false,
        lineStyle: { color: INK3, type: 'dashed', width: 1 } },
      ...(limitUpLine ? [limitUpLine] : []),
      { name: '成交量', type: 'bar', data: volBars, xAxisIndex: 1, yAxisIndex: 1, barWidth: '70%' },
    ],
  });
}

// ────────────────────────────────────────────
// STOCK 内部 tab
// ────────────────────────────────────────────
let currentStockCode = null;
// 当日分时辅助上下文（renderStockDetail 时填充，loadIntraDay 使用）
let lastStockContext = { prev_close: null, limit_up_price: null, code: null };
$$('.tab[data-tab]').forEach(t => {
  t.addEventListener('click', () => {
    const tab = t.dataset.tab;
    // 限定到当前 tab 所在的 view，避免影响其它视图
    const view = t.closest('.view');
    if (view) {
      view.querySelectorAll('.tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      view.querySelectorAll('[data-tab-pane]').forEach(p => p.hidden = (p.dataset.tabPane !== tab));
      const titleEl = view.querySelector('[data-tab-title]');
      if (titleEl) titleEl.textContent = ({
        flow: '资本动向', kline: 'K 线走势', intraday: '当日分时 · 成交数据', seats: '游资席位',
        news: '📰 财经新闻 · AI 评分', sectors: '📊 申万 31 行业 · 新闻情绪', ai: 'AI 复盘'
      }[tab] || ' ');
    }
    if (tab === 'flow'  && echartsCharts.flow)  echartsCharts.flow.resize();
    if (tab === 'kline' && echartsCharts.kline) echartsCharts.kline.resize();
    if (tab === 'intraday') {
      if (echartsCharts.intraDay) echartsCharts.intraDay.resize();
      if (currentStockCode) {
        initIntraDayPicker(currentStockCode);
        // 首次进入 tab 自动加载当日分时
        const pick = $('#intra-day-pick');
        if (pick && pick.value && !intraDayCache.has(pick.value)) {
          loadIntraDay(currentStockCode, pick.value);
        }
      } else {
        $('#intra-day-note').textContent = '请先在上方搜索一只股票';
      }
    }
    if (tab === 'news')   loadNewsList(false);
    if (tab === 'sectors') loadSectorsList(false);
    if (tab === 'crash' && currentStockCode) loadCrashRisk(currentStockCode);
    if (tab === 'ai' && !currentStockCode) {
      $('#ai-panel').hidden = false;
      $('#ai-status').textContent = '空闲';
      $('#ai-verdict').textContent = '—';
      $('#ai-summary').textContent = '请先搜索一只股票，再点 AI 复盘。';
      $('#ai-detail').innerHTML = '';
    }
  });
});

// ────────────────────────────────────────────
// OPTIMIZE
// ────────────────────────────────────────────
async function loadReports() {
  try {
    const data = await api('/api/reports');
    const tbody = $('#reports-table tbody');
    const list = data.reports || [];
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无报告</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(p => `<tr>
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.type)}</td>
      <td class="num">${escapeHtml(String(p.size_kb))} KB</td>
      <td>${escapeHtml(p.mtime)}</td>
    </tr>`).join('');
  } catch (e) {
    $('#reports-table tbody').innerHTML = `<tr><td colspan="4" class="empty">加载失败</td></tr>`;
  }
}

$('#run-optimize')?.addEventListener('click', async () => {
  const btn = $('#run-optimize');
  btn.disabled = true;
  btn.querySelector('span').textContent = '调优中…';
  $('#optimize-status').textContent = '启动 SSE 进度流 …';
  toast('开始参数调优，进度会实时显示', 'info', 3000);
  _showLoading('参数调优 网格扫描');
  const es = new EventSource('/api/stream/optimize');
  es.addEventListener('progress', (ev) => {
    try {
      const p = JSON.parse(ev.data);
      if (p.phase === 'iter_done') {
        $('#optimize-status').textContent =
          `iter ${p.iter}/${p.total} 完成 · trials=${p.trials} · best=${p.best_score?.toFixed(2) || '?'} · ${p.elapsed_sec}s`;
      } else if (p.phase === 'new_best') {
        $('#optimize-status').textContent =
          `⭐ iter ${p.iter} 新最佳 ${p.key}=${p.value} score=${p.score?.toFixed(2)}`;
      } else if (p.phase === 'iter_start') {
        $('#optimize-status').textContent = `iter ${p.iter}/${p.total} 进行中 ...`;
      } else if (p.phase === 'done') {
        $('#optimize-status').textContent =
          `完成 · trials=${p.total_trials} · best=${p.best_score?.toFixed(2)}`;
      }
    } catch {}
  });
  es.addEventListener('done', (ev) => {
    try {
      const r = JSON.parse(ev.data);
      $('#optimize-status').textContent = `完成 · 用时 ${r.elapsed_sec || '?'}s · trials=${r.total_trials || '?'}`;
      toast('调优完成，已写入报告目录', 'success');
      loadReports();
    } catch {
      $('#optimize-status').textContent = '完成';
      loadReports();
    }
    es.close();
    _hideLoading();
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  });
  es.onerror = () => {
    // EventSource 不会自动重连 (server 不重试); 只显示错误
    $('#optimize-status').textContent = 'SSE 连接中断（可重试）';
    es.close();
    _hideLoading();
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  };
});

// ────────────────────────────────────────────
// LAWS view — 读 /api/laws（与 AI 复用同一源）
// ────────────────────────────────────────────
let lawsRendered = false;
let lawsData = null;
async function renderLawsOnce() {
  const host = $('#laws-categories');
  const kj = $('#laws-koujue');
  const auditHost = $('#laws-compliance');
  if (!host) return;

  // 第一次进：拉后端
  if (!lawsData) {
    host.innerHTML = '<div class="dim" style="padding:1rem">加载铁律 …</div>';
    try {
      lawsData = await api('/api/laws');
    } catch (e) {
      host.innerHTML = `<div class="dim" style="padding:1rem;color:${DOWN}">加载失败: ${e.message}</div>`;
      return;
    }
  }
  if (lawsRendered) return;
  lawsRendered = true;

  const cats = lawsData.categories || [];
  host.innerHTML = cats.map(c => `
    <article class="law-card">
      <div class="law-head">
        <span class="law-num">${c.num}</span>
        <h3 class="law-title">${c.name}</h3>
      </div>
      <span class="law-sub">${c.sub}</span>
      <ol class="law-list">
        ${c.items.map(t => `<li>${t}</li>`).join('')}
      </ol>
    </article>
  `).join('');

  if (kj) kj.textContent = lawsData.koujue || '';

  const audit = lawsData.audit || [];
  auditHost.innerHTML = audit.map(g => {
    const ratio = g.passed / Math.max(1, g.total);
    const ratioCls = ratio >= 0.5 ? 'good' : ratio >= 0.25 ? '' : 'warn';
    return `
      <button class="compliance-cat" aria-expanded="false" data-target="${g.name}">
        <span class="cc-name">${g.name}</span>
        <span class="cc-ratio ${ratioCls}">${g.passed} / ${g.total} 已实现 · ${Math.round(ratio*100)}%</span>
      </button>
      <div class="compliance-rows" data-rows="${g.name}" hidden>
        ${g.rows.map(([k, txt]) => `<div class="compliance-row">
          <span class="cr-mark ${k}">${k === 'ok' ? '✓' : k === 'warn' ? '!' : '✗'}</span>
          <span class="cr-text">${escapeHtml(txt)}</span>
        </div>`).join('')}
      </div>`;
  }).join('');

  $$('.compliance-cat').forEach(btn => {
    btn.addEventListener('click', () => {
      const tgt = btn.dataset.target;
      const rows = $(`[data-rows="${tgt}"]`);
      const open = !rows.hidden;
      rows.hidden = open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      btn.classList.toggle('open', !open);
    });
  });
}

// ────────────────────────────────────────────
// DRAGONS · 龙头战法
// ────────────────────────────────────────────
let _dragonsLoaded = false;
let _dragonsLoading = false;
let _dragonsData = null;                          // 缓存最近一次 /api/dragons 返回
const _dragonsSortState = { key: 'rank', dir: 'asc' };  // 全涨停表排序状态

// 排序键映射(对应 dragons.py 输出的字段)
const _DRAGONS_SORT_KEYS = {
  rank:        s => s.rank ?? 999,
  code:        s => s.code || '',
  name:        s => s.name || '',
  sector:      s => s.sector || '',
  streak:      s => s.streak ?? 0,
  market_cap:  s => s.market_cap_yi ?? 0,
  turnover:    s => s.turnover_pct ?? 0,
  seal:        s => s.seal_ratio_pct ?? -1,         // 缺失值排最后
  score:       s => s.score_total ?? 0,
};
function _sortDragonsAll(list, key, dir) {
  const fn = _DRAGONS_SORT_KEYS[key];
  if (!fn || !list) return list || [];
  const sorted = [...list].sort((a, b) => {
    const av = fn(a), bv = fn(b);
    if (typeof av === 'string') {
      return dir === 'asc' ? av.localeCompare(bv, 'zh-Hans') : bv.localeCompare(av, 'zh-Hans');
    }
    return dir === 'asc' ? av - bv : bv - av;
  });
  return sorted;
}

// 龙虎榜 STEP 4 行内 AI 评分明细(6 维卡片) — 2026-07-11 找回 (之前提交丢失了定义)
function _renderAIAnalysisCards(bd, s) {
  if (!bd) bd = {};
  const labels = ['连板强度', '资金认可', '封成比', '市值匹配', '技术形态', '题材纯度'];
  const cards = labels.map(k => {
    const v = bd[k] || { pts: 0, max: 0, note: '' };
    const max = v.max || 0;
    const pct = max > 0 ? Math.round((v.pts || 0) / max * 100) : 0;
    const cls = pct >= 70 ? 'high' : pct >= 40 ? 'mid' : 'low';
    const ptsStr = `<span class="adc-pts">${v.pts || 0}<span class="max">/${max}</span></span>`;
    return `<div class="ai-detail-card">
      <div class="adc-label">${k}</div>
      <div class="adc-bar"><div class="adc-bar-fill ${cls}" style="width:${pct}%"></div></div>
      ${ptsStr}
      <div class="adc-note">${escapeHtml(v.note || '—')}</div>
    </div>`;
  }).join('');
  const aliases = (s.seat_aliases || []).slice(0, 4);
  const aliasLine = aliases.length
    ? ` · 江湖: ${aliases.map(a => '「' + escapeHtml(a) + '」').join(' · ')}`
    : '';
  const warnLine = (s.warnings || []).length
    ? ` · ⚠ ${s.warnings.length} 项警告`
    : '';
  return `<div class="ai-detail-grid">${cards}</div>
    <div class="ai-detail-footer">
      <span class="meta">${s.code} · ${s.name} · ${s.sector || '—'} · ${s.streak}板 · 评分 <b>${s.score_total || 0}</b>${aliasLine}${warnLine}</span>
      <button class="btn btn-mini" data-goto="${s.code}">→ 查看完整个股分析</button>
    </div>`;
}

function renderDragons(data) {
  // api() 已 unwrap envelope, data 本身就是 {date, sentiment, ...}
  if (!data || typeof data !== 'object') return;
  const d = data;

  // 头部信息
  $('#dragons-date').textContent = d.date || '—';
  $('#dragons-elapsed').textContent = d.stats ? `耗时 ${d.stats.elapsed_sec}s · 评分 ${d.stats.total_zt}只 · 龙虎榜 ${d.stats.lhb_loaded}/${d.stats.total_zt} · 技术面 ${d.stats.tech_loaded}/${d.stats.total_zt}` : '';
  if (d.stats?.seal_degraded) {
    $('#dragons-elapsed').textContent += ` · 封单降级 ${d.stats.seal_degraded}只`;
  }

  // STEP 1: 情绪
  const s = d.sentiment || {};
  const sentimentColor = s.action === '积极' ? 'good' : s.action === '空仓' ? 'bad' : 'neutral';
  $('#dragons-sentiment-label').innerHTML =
    `<span class="sentiment-pill sentiment-${sentimentColor}">${s.label || '—'}</span>` +
    `<span class="caption dim" style="margin-left: .5rem">操作: <b>${s.action || '—'}</b></span>`;
  $('#dragons-zt-count').textContent = s.zt_count ?? '—';
  $('#dragons-max-streak').textContent = (s.max_streak || 0) + '板';
  const sd = s.streak_dist || {};
  const sdStr = Object.keys(sd).sort((a,b)=>Number(b)-Number(a))
    .map(k => `${k}板×${sd[k]}`).join(' · ') || '—';
  $('#dragons-streak-dist').textContent = sdStr;

  // STEP 2: 主线 Top 5
  const main = d.mainline || [];
  if (main.length === 0) {
    $('#dragons-mainline').innerHTML = '<p class="empty">无主线数据</p>';
  } else {
    $('#dragons-mainline').innerHTML = main.slice(0, 5).map(m => {
      const pct = (m.change_pct ?? 0).toFixed(2);
      const inflow = (m.net_inflow_yi ?? 0).toFixed(2);
      const flowBadge = m.rank_flow ? `<span class="badge">流#${m.rank_flow}</span>` : '';
      const pctBadge = m.rank_pct ? `<span class="badge">幅#${m.rank_pct}</span>` : '';
      const secName = m.name || '';
      return `
        <div class="mainline-card">
          <a href="#" class="mainline-name sector-link" data-sector="${escapeHtml(secName)}">${escapeHtml(secName) || '—'}</a>
          <div class="mainline-meta">
            <span class="${pct >= 0 ? 'good' : 'bad'}">${pct >= 0 ? '+' : ''}${pct}%</span>
            <span class="dim">净流入 ${inflow}亿</span>
          </div>
          <div class="mainline-badges">${flowBadge}${pctBadge}</div>
        </div>`;
    }).join('');
    // 板块名点击 → 切到 sector 视图
    $$('#dragons-mainline .sector-link').forEach(a => {
      a.onclick = e => {
        e.preventDefault();
        showView('sector', a.dataset.sector);
      };
    });
  }

  // STEP 3: Top 10 龙头卡片
  const top10 = d.top10 || [];
  if (top10.length === 0) {
    $('#dragons-top10').innerHTML = '<p class="empty">无龙头候选</p>';
  } else {
    $('#dragons-top10').innerHTML = top10.map(s => {
      const bd = s.score_breakdown || {};
      const breakdown = ['连板强度','资金认可','封成比','市值匹配','技术形态','题材纯度'].map(k => {
        const v = bd[k] || {pts: 0, max: 0, note: ''};
        const pct = v.max > 0 ? Math.round(v.pts / v.max * 100) : 0;
        const barClass = pct >= 70 ? 'high' : pct >= 40 ? 'mid' : 'low';
        return `<div class="bd-row">
          <span class="bd-label">${k}</span>
          <span class="bd-bar"><span class="bd-fill bd-${barClass}" style="width:${pct}%"></span></span>
          <span class="bd-pts">${v.pts}/${v.max}</span>
        </div>`;
      }).join('');
      const warn = (s.warnings || []).length
        ? `<div class="dragon-warn">⚠ ${s.warnings.join(' · ')}</div>`
        : '';
      const mainlineBadge = s.is_mainline ? '<span class="badge badge-main">主线</span>' : '';
      const sealTxt = s.seal_ratio_pct != null ? `${s.seal_ratio_pct.toFixed(1)}%` : '—';
      const aliasChips = (s.seat_aliases || []).length
        ? `<div class="dragon-aliases">${s.seat_aliases.slice(0, 4).map(a => `<span class="alias-chip">「${escapeHtml(a)}」</span>`).join('')}</div>`
        : '';
      return `
        <div class="dragon-card${s.rank && s.rank <= 3 ? ' rank-top3' : ''}">
          <div class="dragon-head">
            <span class="dragon-rank">#${escapeHtml(String(s.rank))}</span>
            <span class="dragon-code">${escapeHtml(s.code)}</span>
            <span class="dragon-name">${escapeHtml(s.name)}</span>
            <span class="dragon-score">${escapeHtml(String(s.score_total))}</span>
          </div>
          <div class="dragon-meta">
            <span>${escapeHtml(s.sector || '—')}</span> ${mainlineBadge}
            <span class="dim"> · ${escapeHtml(String(s.streak))}板 · 市值${escapeHtml(String(s.market_cap_yi))}亿 · 换手${escapeHtml(String(s.turnover_pct))}% · 封成${escapeHtml(sealTxt)}</span>
          </div>
          <div class="dragon-bd">${breakdown}</div>
          ${aliasChips}
          ${warn}
        </div>`;
    }).join('');
  }

  // STEP 4: 全部涨停 (默认折叠)
  $('#dragons-all-count').textContent = (d.all || []).length;
  const allBody = $('#dragons-all-table tbody');
  const allList = d.all || [];
  // 排序(应用当前状态)
  const sortedAll = _sortDragonsAll(allList, _dragonsSortState.key, _dragonsSortState.dir);
  // 更新列头视觉
  $$('#dragons-all-table th.sortable').forEach(th => {
    th.classList.remove('active-sort');
    const arrow = th.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = '';
    if (th.dataset.sort === _dragonsSortState.key) {
      th.classList.add('active-sort');
      if (arrow) arrow.textContent = _dragonsSortState.dir === 'asc' ? '▲' : '▼';
    }
  });
  if (allList.length === 0) {
    allBody.innerHTML = '<tr><td colspan="10" class="empty">无数据</td></tr>';
  } else {
    allBody.innerHTML = sortedAll.map(s => {
      const sealTxt = s.seal_ratio_pct != null ? `${s.seal_ratio_pct.toFixed(1)}%` : '—';
      const warnTxt = (s.warnings || []).length ? escapeHtml(s.warnings.join('; ')) : '—';
      const bd = s.score_breakdown || {};
      const bdHtml = _renderAIAnalysisCards(bd, s);
      return `<tr data-code="${escapeHtml(s.code)}" class="clickable ai-toggle">
        <td>${escapeHtml(String(s.rank))}</td>
        <td><a href="#" class="stock-link" data-code="${escapeHtml(s.code)}">${escapeHtml(s.code)}</a></td>
        <td>${escapeHtml(s.name)}</td>
        <td>${escapeHtml(s.sector || '—')}</td>
        <td>${escapeHtml(String(s.streak))}板</td>
        <td>${escapeHtml(String(s.market_cap_yi))}亿</td>
        <td>${escapeHtml(String(s.turnover_pct))}%</td>
        <td>${escapeHtml(sealTxt)}</td>
        <td><b>${escapeHtml(String(s.score_total))}</b></td>
        <td class="dim">${warnTxt}</td>
      </tr>
      <tr class="ai-detail-row" data-bd-code="${s.code}" hidden>
        <td colspan="10">${bdHtml}</td>
      </tr>`;
    }).join('');
  }

  // 行点击 → 展开/收起 AI 评分明细(代码 a 自己 stopPropagation,不会双触发)
  $('#dragons-all-table tbody').querySelectorAll('tr.ai-toggle[data-code]').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('a') || e.target.closest('button')) return;
      const code = tr.dataset.code;
      const detail = document.querySelector(`#dragons-all-table tr.ai-detail-row[data-bd-code="${code}"]`);
      if (!detail) return;
      const willShow = detail.hidden;
      detail.hidden = !willShow;
      tr.classList.toggle('expanded', willShow);
    });
  });
  // 行内"→ 查看完整"按钮 → 跳个股页
  $('#dragons-all-table tbody').querySelectorAll('button[data-goto]').forEach(b => {
    b.addEventListener('click', e => {
      e.stopPropagation();
      gotoStock(b.dataset.goto);
    });
  });
  // 代码 a → 跳个股页
  $('#dragons-all-table tbody').querySelectorAll('.stock-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      gotoStock(a.dataset.code);
    });
  });

  // 表头点击 → 切换排序(只重绘表格,不发后端)
  $$('#dragons-all-table th.sortable').forEach(th => {
    th.onclick = () => {
      const key = th.dataset.sort;
      if (_dragonsSortState.key === key) {
        _dragonsSortState.dir = _dragonsSortState.dir === 'asc' ? 'desc' : 'asc';
      } else {
        _dragonsSortState.key = key;
        _dragonsSortState.dir = (key === 'rank' || key === 'code' || key === 'name' || key === 'sector') ? 'asc' : 'desc';
      }
      if (_dragonsData) renderDragons(_dragonsData);
    };
  });

  // STEP 4 折叠交互
  $('#dragons-all-toggle').onclick = () => {
    const wrap = $('#dragons-all-wrap');
    wrap.classList.toggle('hidden');
    $('#dragons-all-toggle .arrow').textContent =
      wrap.classList.contains('hidden') ? '▶' : '▼';
  };

  // STEP 4 决策建议
  const dec = d.decisions || {};
  const overall = dec.overall || '—';
  const plays = dec.plays || [];
  const dips = dec.dips || [];
  const avoids = dec.avoids || [];
  if (!plays.length && !dips.length && !avoids.length) {
    $('#dragons-decision').innerHTML = `<p class="empty">${escapeHtml(overall)} (Top10 中无可执行标的)</p>`;
  } else {
    const playHtml = plays.length
      ? `<div class="decision-col">
          <div class="decision-title">🎯 尾盘打板 (${plays.length})</div>
          ${plays.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    const dipHtml = dips.length
      ? `<div class="decision-col">
          <div class="decision-title">📉 次日低吸 (${dips.length})</div>
          ${dips.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · ${escapeHtml(String(p.streak))}板 · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    const avoidHtml = avoids.length
      ? `<div class="decision-col">
          <div class="decision-title">⚠ 回避 (${avoids.length})</div>
          ${avoids.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason decision-warn">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    $('#dragons-decision').innerHTML = `
      <p class="decision-overall">💡 <b>${escapeHtml(overall)}</b></p>
      <div class="decision-grid">${playHtml}${dipHtml}${avoidHtml}</div>
    `;
    // 重新绑定 stock-link (新插入的 DOM)
    $('#dragons-decision').querySelectorAll('.stock-link').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        const code = a.dataset.code;
        $('#stock-code').value = code;
        showView('stock');
        loadStockDetail(code);
      });
    });
  }
}

async function loadDragons(refresh = false) {
  if (_dragonsLoading) return;
  _dragonsLoading = true;
  $('#dragons-status').textContent = refresh ? '刷新中 …' : '加载中 …';
  try {
    const url = '/api/dragons' + (refresh ? '?refresh=true' : '');
    const data = await api(url, { timeout: 60000 });
    _dragonsData = data;
    renderDragons(data);
    $('#dragons-status').textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN');
    _dragonsLoaded = true;
  } catch (e) {
    $('#dragons-status').textContent = '加载失败: ' + (e.message || e);
    toast('龙头加载失败');
  } finally {
    _dragonsLoading = false;
  }
}

$('#dragons-refresh')?.addEventListener('click', () => loadDragons(true));

// ────────────────────────────────────────────
// 全局绑定
// ────────────────────────────────────────────
const _origShowView = showView;
showView = function(name, ctx) {
  _origShowView(name);
  if (name === 'dragons' && !_dragonsLoaded) loadDragons(false);
  if (name === 'review') _reviewOnViewEnter();
  if (name === 'watchlist') _watchlistOnViewEnter();
  if (name === 'sector' && ctx) loadSector(ctx);
};
$$('[data-jump]').forEach(el => {
  el.addEventListener('click', () => {
    showView(el.dataset.jump);
  });
});

// R5: 跨页 stock-link 点击统一拦截 — 当前页打开个股详情
document.addEventListener('click', e => {
  const a = e.target.closest('a.stock-link[data-code]');
  if (!a) return;
  e.preventDefault();
  const code = a.dataset.code;
  if (!code) return;
  $('#stock-code').value = code;
  showView('stock');
  loadStockDetail(code);
});

$('#refresh-ticker')?.addEventListener('click', () => {
  refreshTicker();
  toast('已刷新');
});

// R12-A: 一键清空所有交易 (清库重测用)
$('#review-clear-all')?.addEventListener('click', async () => {
  if (!confirm('⚠ 确定清空所有交易记录?\n\n此操作不可逆!\n• 删除所有 trades 行\n• 删除所有 trade_reviews 行\n• 清空 Redis AI 缓存')) return;
  if (!confirm('⚠ 最后确认: 真的要清空吗?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades_all?confirm=YES', { method: 'DELETE', timeout: 10000 });
    const j = await r.json();
    if (!j.ok) { showToast('✗ 清空失败: ' + (j.error || ''), 'error'); return; }
    showToast(`✓ 已清空 (trades=${j.data.deleted_trades} reviews=${j.data.deleted_reviews})`, 'success');
    if (typeof _reviewLoadList === 'function') await _reviewLoadList();
    if (typeof _reviewLoadPortfolio === 'function') await _reviewLoadPortfolio();
    if (typeof _reviewLoadStats === 'function') await _reviewLoadStats();
    if (typeof _reviewRefreshIntegrity === 'function') await _reviewRefreshIntegrity();
  } catch (e) {
    showToast('✗ 请求失败: ' + e.message, 'error');
  }
});

// R14: 一键 AI 复盘所有交易 — 后台并发跑,不阻塞页面
$('#review-bulk-ai')?.addEventListener('click', async () => {
  const trades = (_reviewState && _reviewState.trades) || [];
  if (!trades.length) { showToast('✗ 当前没有交易可复盘', 'error', 2500); return; }
  const needRun = trades.filter(t => !t.last_review).length;
  const cached  = trades.length - needRun;
  const lines = [
    `将对 ${trades.length} 笔交易启动 AI 复盘`,
    needRun ? `其中 ${needRun} 笔需要现跑(≈60s/笔),${cached} 笔走缓存秒回` : `${cached} 笔全部命中缓存,瞬时完成`,
    '',
    '后台并发 2 路,可在原地继续浏览/操作其它页',
    '完成每笔后只局部更新该行,账单/持仓不会闪',
  ];
  if (!confirm(lines.join('\n'))) return;
  const btn = document.getElementById('review-bulk-ai');
  const original = btn.textContent;
  btn.disabled = true;
  let done = 0, okCnt = 0, failCnt = 0;
  const CONC = 2;
  const queue = trades.slice();
  const patchProgress = () => { btn.textContent = `⏳ ${done}/${queue.length}`; };
  patchProgress();
  async function worker() {
    while (queue.length) {
      const t = queue.shift();
      const wasCached = !!t.last_review;
      try {
        // force=false:已复盘的笔秒回,未复盘的笔调 LLM (≈60s)
        const r = await _fetchWithTimeout(`/api/review/trades/${t.id}/review?force=false`, { method: 'POST' });
        const j = await r.json();
        if (j.ok && j.data) {
          okCnt++;
          // R15-fix: 局部更新行 — 不重渲整张表,不影响账单 / 持仓 / 浮盈
          _reviewPatchRow(t.id, j.data);
          const local = (_reviewState.trades || []).find(x => x.id === t.id);
          if (local) local.last_review = j.data;
          const v = j.data.verdict || '';
          const s = (j.data.score != null) ? `${j.data.score}分` : '';
          showToast(`✓ #${t.id} ${v} ${s}${wasCached ? ' ⌛缓存' : ''}`.trim(), 'success', 1800);
        } else {
          failCnt++;
          showToast(`✗ #${t.id} ${j.error || '失败'}`, 'error', 2500);
        }
      } catch (e) {
        failCnt++;
        showToast(`✗ #${t.id} ${e.message}`, 'error', 2500);
      } finally {
        done++;
        patchProgress();
        // R15-fix: 不要每笔都重渲 — 只在全部完成时再统一刷一次
      }
    }
  }
  const ws = Array.from({ length: CONC }, () => worker());
  await Promise.all(ws);
  btn.disabled = false;
  btn.textContent = original;
  showToast(`✅ 全部完成 · 成功 ${okCnt} / 失败 ${failCnt}`, 'success', 4000);
  // R15-fix: 一次性刷,账单不闪
  try { await _reviewLoadList(); } catch {}
  try { await _reviewRefreshIntegrity(); } catch {}
  try { await _reviewLoadPortfolio(); } catch {}
});

// R13: 「修复脏数据」按钮 — dirty 时显示, 点击等同 clear-all + 引导重录
$('#review-fix-dirty')?.addEventListener('click', async () => {
  if (!confirm('⚠ 检测到 DB 残留历史脏数据 (老解析器切碎 shares / 无法反查的 code)。\n\n清空所有交易后请重新粘贴录入。\n\n继续?')) return;
  if (!confirm('⚠ 最终确认?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades_all?confirm=YES', { method: 'DELETE', timeout: 10000 });
    const j = await r.json();
    if (!j.ok) { showToast('✗ 清空失败: ' + (j.error || ''), 'error'); return; }
    showToast(`✓ 脏数据已清 (trades=${j.data.deleted_trades}) — 请重新粘贴导入`, 'success');
    if (typeof _reviewLoadList === 'function') await _reviewLoadList();
    if (typeof _reviewLoadPortfolio === 'function') await _reviewLoadPortfolio();
    if (typeof _reviewRefreshIntegrity === 'function') await _reviewRefreshIntegrity();
  } catch (e) {
    showToast('✗ 请求失败: ' + e.message, 'error');
  }
});

// R13: 一致性校验 — 前端分组聚合 vs 后端 FIFO 单源真值
async function _reviewRefreshIntegrity() {
  const badge = document.getElementById('integrity-badge');
  const fixBtn = document.getElementById('review-fix-dirty');
  if (!badge) return;
  badge.dataset.state = 'loading';
  badge.querySelector('.ib-text').textContent = '对账中…';
  try {
    const r = await _fetchWithTimeout('/api/review/integrity', { timeout: 5000 });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'API err');
    const d = j.data;
    if (d.dirty_codes && d.dirty_codes.length) {
      badge.dataset.state = 'dirty';
      badge.querySelector('.ib-text').textContent = `脏数据 ${d.dirty_codes.length} 项`;
      badge.title = d.recommendation + `\n脏: ${d.dirty_codes.map(x => x.name).join(', ')}`;
      if (fixBtn) fixBtn.hidden = false;
    } else if (!d.ok || Math.abs(d.discrepancy) > (d.threshold || 0.01)) {
      badge.dataset.state = 'mismatch';
      badge.querySelector('.ib-text').textContent =
        `差 ${d.discrepancy >= 0 ? '+' : ''}${d.discrepancy.toFixed(2)} 元`;
      badge.title = `前端分组: ${d.group_sum}\n后端 portfolio: ${d.portfolio_total}\n差异: ${d.discrepancy}`;
      if (fixBtn) fixBtn.hidden = true;
    } else {
      badge.dataset.state = 'ok';
      const total = d.portfolio_total;
      const sign = total > 0 ? '+' : '';
      badge.querySelector('.ib-text').textContent =
        d.n_groups ? `✓ ${sign}${total.toFixed(2)}` : '✓ 空仓';
      badge.title = `前端分组: ${d.group_sum}\n后端 portfolio: ${d.portfolio_total}\n已实现: ${d.portfolio_realized} · 浮: ${d.portfolio_unrealized}\n分组数: ${d.n_groups}`;
      if (fixBtn) fixBtn.hidden = true;
    }
  } catch (e) {
    badge.dataset.state = 'mismatch';
    badge.querySelector('.ib-text').textContent = '对账失败';
    badge.title = '拉取 /api/review/integrity 失败: ' + e.message;
  }
}

// ─── 主题切换 (深/浅/跟随系统) ────────────────────────────────
function getActiveTheme() {
  return document.documentElement.getAttribute('data-theme') || 'dark';
}
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  // 同步 meta theme-color (Safari 顶栏)
  const meta = document.querySelector('meta[name="theme-color"]:not([media])') ||
               document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'light' ? '#fbfbfd' : '#0a0908');
}
$('#theme-toggle')?.addEventListener('click', () => {
  const cur = getActiveTheme();
  const next = cur === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  try { localStorage.setItem('tuixue-theme', next); } catch {}
  refreshThemeColors();
  // B8: ECharts 主题切换 — 用 setOption + resize 复用实例(避免白/黑闪烁)
  Object.values(echartsCharts).forEach(c => {
    if (!c) return;
    try { c.resize(); } catch {}
    try { c.setOption(c.getOption(), true); } catch {}
  });
  // 当前可见的 view 重渲一次 (showView 内部有 view-specific 加载)
  const activeView = $$('.view').find(v => !v.hidden)?.dataset?.view;
  if (activeView) showView(activeView);
  toast(next === 'light' ? '已切换至浅色模式' : '已切换至深色模式', 'info', 1500);
});
// 系统主题变更时,如果用户没显式选择则跟随
window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', (e) => {
  if (localStorage.getItem('tuixue-theme')) return;
  applyTheme(e.matches ? 'light' : 'dark');
});

window.addEventListener('resize', () => {
  Object.values(echartsCharts).forEach(c => c && c.resize());
});

// 启动
refreshTicker();
setInterval(refreshTicker, 30 * 1000);

// ────────────────────────────────────────────
// TUNNEL · 外网访问状态 + 启动
// ────────────────────────────────────────────
async function refreshTunnel() {
  const dot = $('#tunnel-dot');
  const text = $('#tunnel-text');
  const urlRow = $('#tunnel-url-row');
  const urlEl = $('#tunnel-url');
  const btnLabel = $('#tunnel-btn-label');
  const status = $('#tunnel-status');
  if (!dot) return;
  try {
    const r = await api('/api/tunnel/status');
    if (!r) return;
    // LAN 默认一直显示(同一 WiFi 入口)
    const lanUrl = $('#tunnel-lan-url');
    if (lanUrl && r.lan_ip && r.port) {
      const full = `http://${r.lan_ip}:${r.port}`;
      lanUrl.href = full;
      lanUrl.textContent = full;
    }
    const state = r.state || (r.running ? 'online' : 'offline');

    // 2026-07-12: 显示 sentinel-based 后端的指示 (TG-bot / MQTT)
    const sentinels = r.sentinels || [];
    const sentinelHint = $('#tunnel-sentinel-hint');
    if (sentinelHint) {
      if (sentinels.length) {
        const lines = sentinels.map(s => {
          if (s.name.includes('Telegram')) {
            return '🤖 Telegram bot 也在跑 — 打开 @&lt;bot&gt; 发 `GET /api/health` 试试';
          }
          if (s.name.includes('MQTT')) {
            return '📡 MQTT 代理也在跑 — 用任意 MQTT iOS app 连接到 broker.hivemq.com:8883';
          }
          return `🛰 ${s.name}: ${s.info ? '就绪' : '初始化中'}`;
        });
        sentinelHint.innerHTML = lines.join('<br>');
        sentinelHint.removeAttribute('hidden');
      } else {
        sentinelHint.setAttribute('hidden', '');
      }
    }

    if (state === 'online' && r.url) {
      status.classList.add('online');
      status.classList.remove('offline', 'starting');
      // 2026-07-12: 显示用的是哪条机制 (用 emoji 让机制一目了然)
      const methodEmoji = methodEmojiFor(r.method);
      text.textContent = `公网已通 · ${methodEmoji} ${r.method || ''}`.trim();
      urlRow.hidden = false;
      urlEl.href = r.url;
      urlEl.textContent = r.url.replace(/^https?:\/\//, '').slice(0, 48);
      btnLabel.textContent = '重启';
      $('#tunnel-diag')?.setAttribute('hidden', '');
    } else if (state === 'starting') {
      status.classList.remove('online', 'offline');
      status.classList.add('starting');
      text.textContent = '启动中 (18 路 fallback)…';
      urlRow.hidden = true;
      btnLabel.textContent = '重启';
    } else {
      // offline → LAN fallback
      status.classList.remove('online', 'starting');
      status.classList.add('offline');
      text.textContent = `📶 局域网 ${r.lan_ip}:${r.port}`;
      urlRow.hidden = true;
      btnLabel.textContent = '启动隧道';
    }
  } catch (e) {
    text.textContent = '状态读取失败';
  }
}

function methodEmojiFor(method) {
  if (!method) return '🌐';
  const m = method.toLowerCase();
  if (m.includes('tailscale'))    return '🔒';
  if (m.includes('zerotier'))     return '🔗';
  if (m.includes('telegram'))     return '🤖';
  if (m.includes('ntfy'))         return '🔔';
  if (m.includes('mqtt'))         return '📡';
  if (m.includes('cf-worker') || m.includes('cf'))     return '☁️';
  if (m.includes('paas'))         return '🐳';
  if (m.includes('trystero'))     return '🌊';
  if (m.includes('cloudflare'))   return '☁️';
  if (m.includes('ngrok'))        return '🪜';
  if (m.includes('localhost') || m.includes('lhr')) return '🌍';
  if (m.includes('serveo'))       return '🐡';
  return '🌐';
}
$('#tunnel-btn')?.addEventListener('click', async () => {
  const btn = $('#tunnel-btn');
  const btnLabel = $('#tunnel-btn-label');
  btn.disabled = true;
  btnLabel.textContent = '启动中…';
  // 即时显示诊断面板
  const diag = $('#tunnel-diag');
  const diagBody = $('#tunnel-diag-body');
  if (diag) diag.removeAttribute('hidden');
  if (diagBody) diagBody.innerHTML = '⏳ 后台启动 18 路 fallback (tailscale · tg-bot · ntfy · mqtt · cf-worker · paas · trystero · cloudflared quic/http2/ipv4 · ngrok · ...)...';
  try {
    const r = await api('/api/tunnel/start', { method: 'POST', timeout: 75_000 });
    const d = r.data || r;
    if (d && d.url) {
      if (diag) diag.setAttribute('hidden', '');
      await refreshTunnel();
      const tgMsg = d.tg_sent
        ? '✅ 已自动推到 Telegram'
        : `⚠ TG 推送失败 (${d.tg_err || 'DNS 阻断'}), URL 仍可访问`;
      toast(`✓ 公网入口 ${d.url.slice(8, 36)}… · ${tgMsg}`, d.tg_sent ? 'success' : 'warn', 4500);
    } else {
      // 启动失败 — 给清晰的诊断 + LAN 兜底
      const err = (d && d.error) || r.error || '60s 内未拿到 URL';
      if (diagBody) {
        diagBody.innerHTML = `
          <p style="margin:.25rem 0">${escapeHtml(err)}</p>
          <p style="margin:.25rem 0">常见原因:</p>
          <ul style="margin:.25rem 0 .5rem 1.25rem">
            <li>当前网络 DNS 被劫持到 198.18.x <code>(~/.hermes/.env 配 VPN/自定义 DNS 可解)</code></li>
            <li>运营商/路由器拦截 trycloudflare.com / ngrok.com / serveo.net / lhr.life</li>
            <li>cloudflared 没装: <code>brew install cloudflared</code></li>
          </ul>
          <p style="margin:.25rem 0;color:var(--ink-2)">💡 此时仍可用上方 <b>局域网入口</b> (同一 WiFi 手机直接访问)</p>`;
      }
      toast(`启动失败: ${err}`, 'error', 6000);
    }
  } catch (e) {
    if (diagBody) diagBody.innerHTML = `⏱ 后端调用超时/失败: ${escapeHtml(e.message)}`;
    toast('启动失败: ' + e.message, 'error', 4500);
  } finally {
    btn.disabled = false;
    btnLabel.textContent = '重启';
  }
});

// LAN 入口 QR 码
$('#tunnel-lan-qr-btn')?.addEventListener('click', () => {
  const url = $('#tunnel-lan-url')?.href;
  const wrap = $('#tunnel-lan-qr-wrap');
  const img = $('#tunnel-lan-qr-img');
  if (!url || !wrap || !img) return;
  if (wrap.hidden) {
    img.src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&margin=2&data=${encodeURIComponent(url)}`;
    wrap.hidden = false;
  } else {
    wrap.hidden = true;
  }
});
$('#tunnel-qr-btn')?.addEventListener('click', () => {
  const url = $('#tunnel-url').href;
  const qrWrap = $('#tunnel-qr-wrap');
  const qrImg = $('#tunnel-qr');
  if (!url || url === '#') return;
  if (qrWrap.hidden) {
    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&margin=2&data=${encodeURIComponent(url)}`;
    qrWrap.hidden = false;
  } else {
    qrWrap.hidden = true;
  }
});
$('#tunnel-tg-btn')?.addEventListener('click', async () => {
  const btn = $('#tunnel-tg-btn');
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '推送中…';
  try {
    const data = await api('/api/tunnel/push', { method: 'POST' });
    const target = data.target || data.url || data.lan;
    const label = data.url ? '公网' : 'LAN';

    if (data.tg_ok) {
      toast(`✅ 已推到 Telegram · ${label} ${target.slice(8, 32)}…`, 'success', 3200);
      return;
    }
    // TG 不可用 → fallback：剪贴板 + 原生分享面板（移动端）
    const shareText = data.text || `${label} 入口：${target}`;
    let copied = false;
    try {
      await navigator.clipboard.writeText(target);
      copied = true;
    } catch {}
    // 移动端优先走 navigator.share，会弹出系统分享面板（含 Telegram 选项）
    if (navigator.share) {
      try {
        await navigator.share({ title: '退学 v3 · 控制台', text: shareText, url: target });
        toast(`📤 已唤起系统分享（含 Telegram）`, 'success', 2800);
        return;
      } catch (e) { /* 用户取消 */ }
    }
    if (copied) {
      toast(`⚠ TG 推送失败（${data.tg_err || '网络'}），已复制到剪贴板 — 长按聊天框粘贴`, 'info', 4500);
    } else {
      // 兜底：弹个 prompt 让用户手动复制
      prompt('TG 推送失败，手动复制 URL：', target);
    }
  } catch (e) {
    toast('推送失败:' + e.message, 'error', 4000);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
});
refreshTunnel();
setInterval(refreshTunnel, 10 * 1000);

// ────────────────────────────────────────────
// NEWS · 全局新闻 tab
// ────────────────────────────────────────────
let newsCache = null;

async function loadNewsList(forceRefresh) {
  const meta = $('#news-meta');
  const list = $('#news-list');
  if (!list) return;
  if (forceRefresh) {
    meta.textContent = '刷新中…（抓取 + AI 评分，约 60s）';
    meta.style.color = INK2;
    list.innerHTML = Array.from({ length: 5 }, () =>
      `<div class="news-card"><div class="skeleton skeleton-block" style="width:100%"></div></div>`).join('');
  }
  try {
    const data = forceRefresh
      ? await (await fetch('/api/news/refresh', { method: 'POST' })).json().then(d => d.data || {})
      : (await api('/api/news')).data || {};
    newsCache = data;
    const fa = data.fetched_at ? new Date(data.fetched_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    const aa = data.analyzed_at ? new Date(data.analyzed_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    meta.textContent = `抓取 ${fa}  ·  AI ${data.ai_count || 0}/${data.count || 0} · 分析 ${aa}`;
    meta.style.color = INK2;
    renderNewsList(data.news || []);
  } catch (e) {
    meta.textContent = `加载失败：${e.message}`;
    meta.style.color = DOWN;
    list.innerHTML = errorCard(e.message, () => loadNewsList(false));
    bindRetry(list, () => loadNewsList(false));
  }
}

function renderNewsList(items) {
  const list = $('#news-list');
  if (!items.length) {
    list.innerHTML = '<p class="caption dim">暂无新闻</p>';
    return;
  }
  list.innerHTML = items.map(n => {
    const a = n.ai || null;
    const score = a ? a.score : null;
    const dir = (a && a.direction) || '';
    const cls = score == null ? '' : (score >= 7 ? 'hot' : score >= 4 ? 'warm' : 'cold');
    const dirColor = dir === '利好' ? UP : dir === '利空' ? DOWN : INK2;
    const sectorChips = (a?.sectors || []).slice(0, 3).map(s => `<span class="chip">${escapeHtml(s)}</span>`).join('');
    const stockChips = (a?.stocks || []).slice(0, 4).map(s => `<span class="chip chip-code">${s}</span>`).join('');
    const reason = a?.reason ? `<div class="news-reason">${escapeHtml(a.reason)}</div>` : '';
    const href = n.url ? escapeHtml(n.url) : '#';
    return `
      <div class="news-card ${cls}">
        <div class="news-score">
          ${score != null ? `<div class="news-score-num" style="color:${dirColor}">${score.toFixed(1)}</div><div class="news-score-cap">${dir}</div>` : '<div class="news-score-num dim">—</div><div class="news-score-cap dim">未评分</div>'}
        </div>
        <div class="news-body">
          <a class="news-title" href="${href}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a>
          <div class="news-meta">
            <span class="dim">${n.ctime_str || ''}</span>
            <span class="dim">· ${escapeHtml(n.media || '')}</span>
            <span class="dim">· ${n.lid_name || ''}</span>
          </div>
          ${reason}
          ${sectorChips || stockChips ? `<div class="news-chips">${sectorChips}${stockChips}</div>` : ''}
        </div>
      </div>`;
  }).join('');
}

// ────────────────────────────────────────────
// SECTORS · 申万 31 行业聚合情绪
// ────────────────────────────────────────────
let sectorsCache = null;

async function loadSectorsList(forceRefresh) {
  const meta = $('#sectors-meta');
  const list = $('#sectors-list');
  if (!list) return;
  if (forceRefresh) {
    meta.textContent = '刷新中…';
    meta.style.color = INK2;
    await (await fetch('/api/news/refresh', { method: 'POST' })).json();
  }
  try {
    const data = await api('/api/sectors/sw') || {};
    sectorsCache = data;
    const fa = data.fetched_at ? new Date(data.fetched_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    const total = (data.sectors || []).filter(s => s.news_count > 0).length;
    meta.textContent = `抓取 ${fa}  ·  ${total}/31 行业有新闻利好`;
    meta.style.color = INK2;
    renderSectorsList(data.sectors || []);
  } catch (e) {
    meta.textContent = `加载失败：${e.message}`;
    meta.style.color = DOWN;
  }
}

function renderSectorsList(sectors) {
  const list = $('#sectors-list');
  const hot = sectors.filter(s => s.news_count > 0);
  if (!hot.length) {
    list.innerHTML = '<p class="caption dim">暂无板块新闻（先点 🔄 刷新触发 AI 评分）</p>';
    return;
  }
  list.innerHTML = `
    <div class="sectors-grid">
      ${hot.map(s => {
        const bullPct = s.news_count ? Math.round(s.bull_count / s.news_count * 100) : 0;
        const bearPct = s.news_count ? Math.round(s.bear_count / s.news_count * 100) : 0;
        const sentiment = s.avg_score >= 6 ? 'hot' : s.avg_score >= 4 ? 'warm' : s.avg_score >= 2 ? 'mid' : 'cold';
        return `
        <div class="sector-card ${sentiment}">
          <div class="sector-head">
            <span class="sector-name">${escapeHtml(s.sw)}</span>
            <span class="sector-avg">${s.avg_score || '—'}</span>
          </div>
          <div class="sector-stats">
            <span class="bull">利好 ${s.bull_count}</span>
            <span class="bear">利空 ${s.bear_count}</span>
            <span class="dim">共 ${s.news_count}</span>
          </div>
          <div class="sector-bar">
            <div class="bull-bar" style="width:${bullPct}%"></div>
            <div class="bear-bar" style="width:${bearPct}%"></div>
          </div>
          ${(s.top_news || []).map(n => `
            <div class="sector-news">
              <span style="color:${n.direction === '利好' ? UP : n.direction === '利空' ? DOWN : INK2}">${n.score.toFixed(1)}</span>
              <span class="dim">·</span>
              <span>${escapeHtml(n.title.slice(0, 38))}</span>
            </div>
          `).join('')}
        </div>`;
      }).join('')}
    </div>`;
}

// ────────────────────────────────────────────
// STOCK 页：板块情绪 + 相关新闻
// ────────────────────────────────────────────
async function loadStockSector(code) {
  const host1 = $('#q-sector-board');
  const host2 = $('#q-sector-industries');
  const host3 = $('#q-sector-source');
  const host4 = $('#q-sector-sentiment');
  const host5 = $('#q-related-news');
  if (!host1) return;

  host1.innerHTML = '<div class="kv-row"><span>加载中…</span></div>';
  host2.innerHTML = '';
  host3.textContent = '';
  host4.innerHTML = '';
  host5.innerHTML = '';

  let sec = {};
  try {
    sec = await api(`/api/stock/${code}/sector`) || {};
    const b = sec.board || {};
    host1.innerHTML = `
      <div class="kv-row"><span>市场</span><b>${escapeHtml(b.board_name || '—')}</b></div>
      <div class="kv-row"><span>代码前缀</span><b>${escapeHtml(b.prefix || '—')}</b></div>
      <div class="kv-row"><span>涨跌幅</span><b>±${b.pct_limit || '—'}%</b></div>
      <div class="kv-row"><span>门槛</span><b>${b.capital_floor_wan ? b.capital_floor_wan + ' 万' : '无'}</b></div>`;
    const inds = [
      ['申万',  sec.sw,    '#a78bcf'],
      ['证监会', sec.csrc, '#7fb6c9'],
      ['中证',  sec.cics,  '#d4a056'],
      ['GICS',  sec.gics,  '#4fb074'],
    ];
    const stdChips = inds.filter(([,v]) => v).map(([k,v,c]) =>
      `<span class="chip" style="border-color:${c};color:${c}">${k}·${escapeHtml(v)}</span>`
    ).join('');
    // AI 概念标（机器人/AI 各子赛道）
    const aiTags = sec.ai_tags || {labels: [], is_main_field: false};
    const aiColors = {
      '机器人本体': '#ff5722', '机器人零部件': '#ff7043', '机器视觉': '#ff8a65',
      'AI 算力': '#9c27b0', 'AI 芯片': '#7b1fa2', 'AI 软件': '#ba68c8',
      '智能驾驶': '#03a9f4', '半导体': '#00bcd4', '新能源车': '#4caf50',
      '传统行业': '#9e9e9e', '未分类': '#bdbdbd'
    };
    const aiChips = aiTags.labels.map(l => {
      const c = aiColors[l] || '#9e9e9e';
      const warn = (l === '传统行业' || l === '未分类') ? ' ⚠️非主战场' : '';
      return `<span class="chip" style="border-color:${c};color:${c};font-weight:bold">🏷️ ${escapeHtml(l)}${warn}</span>`;
    }).join('');
    host2.innerHTML = stdChips + (aiChips ? '<br/>' + aiChips : (stdChips ? '' : '<span class="dim">行业分类待补</span>'));
    host3.textContent = `行业来源：${sec.source || '—'}${sec.fresh ? '（刚拉到）' : ''}`;

    // 相关新闻 + 板块情绪
    const rel = await api(`/api/stock/${code}/related_news`) || {};
    const news = rel.news || [];
    // 板块情绪从 sectors/sw 接口抓
    const secOv = await api('/api/sectors/sw') || {};
    const mySector = (secOv.sectors || []).find(s => s.sw === sec.sw);

    if (mySector) {
      const bullPct = mySector.news_count ? Math.round(mySector.bull_count / mySector.news_count * 100) : 0;
      renderKpi(host4, [
        ['板块新闻数',  mySector.news_count, INK],
        ['板块利好',    mySector.bull_count + ' (' + bullPct + '%)', UP],
        ['板块利空',    mySector.bear_count, DOWN],
        ['板块均分',    mySector.avg_score || '—', colorFor(mySector.avg_score)],
      ]);
    } else {
      host4.innerHTML = '<div class="kpi"><span class="kpi-label">板块情绪</span><span class="kpi-num dim">暂无该行业新闻</span></div>';
    }

    if (news.length) {
      host5.innerHTML = `
        <div class="related-news-list">
          ${news.slice(0, 8).map(n => {
            const a = n.ai;
            const dirColor = a.direction === '利好' ? UP : a.direction === '利空' ? DOWN : INK2;
            return `<div class="news-card ${a.score >= 7 ? 'hot' : a.score >= 4 ? 'warm' : 'cold'}" style="margin-bottom:.5rem">
              <div class="news-score"><div class="news-score-num" style="color:${dirColor};font-size:1.4rem">${a.score.toFixed(1)}</div></div>
              <div class="news-body">
                <div class="news-title" style="font-size:.9rem">${escapeHtml(n.title)}</div>
                <div class="news-meta"><span class="dim">${n.ctime_str || ''} · ${escapeHtml(n.media || '')}</span></div>
                <div class="news-reason">${escapeHtml(a.reason || '')}</div>
                <div class="news-chips">
                  ${(typeof n.hit_reason === 'string' ? n.hit_reason : '').split(' · ').filter(Boolean).map(r => `<span class="chip" style="color:${ACCENT}">${escapeHtml(r)}</span>`).join('')}
                  ${(a.sectors || []).slice(0,2).map(s => `<span class="chip">${escapeHtml(s)}</span>`).join('')}
                </div>
              </div>
            </div>`;
          }).join('')}
        </div>
        <p class="caption dim" style="margin:.25rem 0 0">命中 ${news.length} 条（按 AI 评分降序）</p>`;
    } else {
      host5.innerHTML = '<p class="caption dim">暂无与该股直接相关的新闻（AI 评分按申万行业 / 涉及股票过滤）</p>';
    }
  } catch (e) {
    host1.innerHTML = `<div class="kv-row"><span class="down">板块加载失败</span><b>${escapeHtml(e.message)}</b></div>`;
  }

  // 加载连板 & 板块联动面板（用 申万行业 sw 作为 sector 过滤）
  loadStockLimitUp(code, sec?.sw || sec?.csrc || sec?.gics);
}

// 连板 & 板块联动数据 — 调 /api/stock/{code}/limit_up_context
// 2026-07-15 增强: 板块当日空时, 从 /api/dragons 拉同板块或相关 L1/L2 的涨停股, 仍空就显示全市场 Top
async function loadStockLimitUp(code, sectorName) {
  const host = $('#stock-limit-up-body');
  if (!host) return;
  host.innerHTML = '<p class="caption dim">连板数据加载中…</p>';
  try {
    const url = sectorName
      ? `/api/stock/${code}/limit_up_context?sector=${encodeURIComponent(sectorName)}`
      : `/api/stock/${code}/limit_up_context`;
    const res = await api(url) || {};
    if (res.error && res.error.includes('超时')) {
      host.innerHTML = `<p class="caption down">${res.error}</p>`;
      return;
    }
    const today = res.today;
    const recent5 = res.recent_5d || [];
    const sectorZt = res.sector_today || [];
    const relatedCon = res.related_concepts || [];
    const summary = res.summary || '';
    const nature = res.stock_nature || {};
    const leader = res.leadership || {};

    // ─── 顶部 hero: 股性 + 龙头 + 概念 chips ───
    const natureTier = nature.tier || '—';
    const natureColors = {
      '妖股':  { bg: '#d32f2f', fg: '#fff' },
      '活跃':  { bg: '#f57c00', fg: '#fff' },
      '一般':  { bg: '#9e9e9e', fg: '#fff' },
      '死股':  { bg: '#424242', fg: '#bbb' },
    };
    const nc = natureColors[natureTier] || natureColors['死股'];
    const natureBadge = `
      <span class="chip"
            style="background:${nc.bg};color:${nc.fg};padding:.25rem .55rem;font-weight:700;border:none"
            title="股性评分 ${nature.score || 0}/100 · ${nature.reason || ''}">
        🐲 ${natureTier}${nature.score ? ' · ' + nature.score : ''}
      </span>
    `;
    const leaderBadge = leader.role && leader.role !== '—' ? `
      <span class="chip"
            style="background:${leader.streak >= 5 ? '#b71c1c' : leader.streak >= 3 ? '#d32f2f' : leader.streak >= 2 ? '#f57c00' : '#7b1fa2'};color:#fff;padding:.25rem .55rem;font-weight:700;border:none"
            title="${escapeHtml(leader.reason || '')}">
        ${leader.role}${leader.is_top_in_sector ? ' · 板块最高' : ''}
      </span>
    ` : '';
    const conceptChips = (nature.concepts || []).map(c => `
      <span class="chip"
            style="border:1px solid ${c.level === 'L4' ? ACCENT : c.level === 'L3' ? '#7b9bd1' : INK2};color:${c.level === 'L4' ? ACCENT : c.level === 'L3' ? '#7b9bd1' : INK2};font-size:.78rem"
            title="${c.level} · ${c.role || ''}">
        <span class="cap" style="font-size:.65rem">${c.level}</span>
        <b>${escapeHtml(c.name)}</b>
      </span>
    `).join('');

    // 板块龙头股 (非当前股时显示,可点击切换)
    const sl = leader.sector_leader;
    const sectorLeaderHtml = sl ? `
      <div class="kv-row" style="font-size:.85rem;cursor:pointer" data-action="open-stock:${escapeHtml(sl.code)}" title="点击查看 ${escapeHtml(sl.name)}">
        <span>板块龙头股</span>
        <b style="color:#b71c1c">
          👑 ${escapeHtml(sl.name)} <span class="caption dim">${escapeHtml(sl.code)}</span>
          · ${sl.streak} 连板
          ${sl.封单金额 ? ' · 封单 ' + (sl.封单金额 / 1e8).toFixed(2) + '亿' : ''}
        </b>
      </div>
    ` : '';

    const heroHtml = `
      <div style="display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin-bottom:.6rem">
        ${natureBadge}
        ${leaderBadge}
      </div>
      ${conceptChips ? `<div style="display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.5rem">${conceptChips}</div>` : ''}
      ${sectorLeaderHtml}
    `;

    // ─── 今日一行密集 ───
    const todayHtml = today ? `
      <div class="kv-row" style="font-size:.85rem">
        <span>今日</span>
        <b style="color:${today.连板数 >= 3 ? UP : today.连板数 >= 1 ? ACCENT : INK2}">
          ${today.连板数 >= 2 ? '🔥' : '✓'} ${today.连板数 || 0} 板
          · 封单 ${today.封单金额 ? (today.封单金额 / 1e8).toFixed(2) + '亿' : '—'}
          · 首封 ${today.首次封板时间 ? String(today.首次封板时间).replace(/^(\d{2})(\d{2})\d{2}$/, '$1:$2') : '—'}
          ${today.炸板次数 ? ' · 炸板 ' + today.炸板次数 + '次' : ''}
          ${today.涨停统计 ? ' · 涨停统计 ' + today.涨停统计 : ''}
        </b>
      </div>
    ` : `<div class="kv-row" style="font-size:.85rem"><span>今日</span><b class="dim">未涨停</b></div>`;

    const recentHtml = recent5.length > 0 ? `
      <div style="display:flex;flex-wrap:wrap;gap:.3rem;margin:.4rem 0">
        ${recent5.map(r => `
          <span class="chip" style="color:${ACCENT};font-size:.78rem">${(r.date||'').split('-').slice(1).join('/')} · ${r.连板数 || 1}板</span>
        `).join('')}
      </div>
    ` : '';

    // ─── 板块联动: 板块当日空 → 从 /api/dragons 找同板块 / 同 L1 / 同 L2 ───
    let sectorHtml = '';
    let sectorLabel = '';
    let sectorSource = '';
    if (sectorZt.length > 0) {
      sectorHtml = renderSectorRows(sectorZt.slice(0, 10), code);
      sectorLabel = `🔥 板块当日涨停 ${sectorZt.length} 只（取 ${Math.min(sectorZt.length, 10)}）`;
      sectorSource = 'sector_exact';
    } else {
      // 板块当日空: 用 龙头 全量挑同板块或相邻 L1/L2
      try {
        const dr = await api('/api/dragons') || {};
        const allZt = dr.all || [];
        const myL1 = res.taxonomy_l1 || '';
        const myL2 = res.taxonomy_l2 || '';
        // 三层 fallback: 完全 sector 名 → L1 集群 → L2 行业
        const sameSec = allZt.filter(s => sectorName && (s.sector === sectorName || s.sector?.includes(sectorName) || sectorName.includes(s.sector)));
        let picked = sameSec;
        let labelKind = 'sector_fallback';
        if (picked.length === 0 && myL1) {
          picked = allZt.filter(s => s.taxonomy?.l1 === myL1);
          labelKind = 'l1_fallback';
        }
        if (picked.length === 0 && myL2) {
          picked = allZt.filter(s => s.taxonomy?.l2 === myL2);
          labelKind = 'l2_fallback';
        }
        if (picked.length === 0) {
          picked = allZt.slice().sort((a, b) => (b.score_total || 0) - (a.score_total || 0)).slice(0, 12);
          labelKind = 'top_fallback';
        }
        if (picked.length > 0) {
          const labels = {
            l1_fallback:    `🧬 同 L1 主题「${escapeHtml(myL1)}」当日涨停 ${picked.length} 只（取 ${Math.min(picked.length, 10)}）· 本股板块无涨停`,
            l2_fallback:    `🧬 同 L2 行业「${escapeHtml(myL2)}」当日涨停 ${picked.length} 只（取 ${Math.min(picked.length, 10)}）· 本股板块无涨停`,
            top_fallback:   `🌐 全市场涨停 ${allZt.length} 只 · 本股板块无涨停 · 取 Top ${Math.min(picked.length, 10)}`,
          };
          sectorLabel = labels[labelKind] || `🌐 全市场涨停 ${picked.length} 只`;
          sectorHtml = renderDragonRows(picked.slice(0, 10), code);
          sectorSource = labelKind;
        } else {
          sectorLabel = '今天全市场无涨停记录';
          sectorHtml = '';
        }
      } catch (e) {
        sectorLabel = `本股板块「${escapeHtml(sectorName || '')}」今日无涨停`;
        sectorHtml = '';
      }
    }

    // 板块列的标题 (供下面 sectorHtml 用)
    function _renderLimitUpTable(label, body) {
      if (!body) return '';
      return `
      <div class="caption" style="margin:.75rem 0 .25rem;border-top:.5px solid var(--line);padding-top:.5rem">${label}</div>
      ${body}
      <p class="caption dim" style="margin:.4rem 0 0">👑 当前股 · ★ 板块最高连板 · 点击名称 → 切换个股 · <a href="#view-dragons" data-jump="dragons" style="color:${ACCENT}">查看完整龙头榜</a></p>
    `;
    }

    // 把上面的 sectorHtml 重新包上标题
    sectorHtml = _renderLimitUpTable(sectorLabel, sectorHtml);

    const relatedConHtml = relatedCon.length > 0 ? `
      <div class="caption" style="margin:.75rem 0 .25rem;border-top:.5px solid var(--line);padding-top:.5rem">🧬 相关概念当日涨停 (按 L3 产业链 / L4 细分聚合)</div>
      <div style="display:flex;flex-wrap:wrap;gap:.4rem">
        ${relatedCon.map(c => `
          <span class="chip" style="cursor:default;border:1px solid ${c.zt_count >= 5 ? UP : c.zt_count >= 2 ? ACCENT : INK2};color:${c.zt_count >= 5 ? UP : c.zt_count >= 2 ? ACCENT : INK2}" title="${escapeHtml(c.concept)} (${c.level}) · ${c.zt_count} 只涨停 · 例: ${escapeHtml((c.samples || []).join(', '))}">
            <span class="cap">${c.level}</span>
            <b>${escapeHtml(c.concept)}</b>
            <span class="up">⚡${c.zt_count}</span>
          </span>
        `).join('')}
      </div>
      <p class="caption dim" style="margin:.4rem 0 0">同一产业链或细分标签下的涨停总数 · 颜色: ≥5 主线(红) / ≥2 二线(琥珀) / 其他杂毛(灰)</p>
    ` : '';

    host.innerHTML = `
      ${heroHtml}
      ${todayHtml}
      ${recentHtml}
      ${sectorHtml}
      ${relatedConHtml}
      ${summary ? `<div class="kv-row mt-8" style="border-top:.5px solid var(--line);padding-top:.5rem;font-size:.85rem"><span>总结</span><b>${escapeHtml(summary)}</b></div>` : ''}
    `;
  } catch (e) {
    host.innerHTML = `<p class="caption down">连板数据加载失败: ${escapeHtml(e.message)}</p>`;
  }
}

// 板块当日涨停 (limit_up_context.sector_today 行) → 表格 rows
function renderSectorRows(rows, code) {
  if (!rows || rows.length === 0) return '';
  const maxLb = Math.max(...rows.map(x => x.连板数 || 0));
  return `
    <table class="mini-table" style="width:100%;font-size:.85rem">
      <tr>
        <th style="text-align:left">名称</th>
        <th style="text-align:left">连板</th>
        <th style="text-align:left">概念</th>
        <th style="text-align:right">封单</th>
        <th style="text-align:right">涨幅</th>
      </tr>
      ${rows.map(s => {
        const isMe = String(s.代码).padStart(6, '0') === String(code).padStart(6, '0');
        const isMax = (s.连板数 || 0) === maxLb && (s.连板数 || 0) >= 2;
        const tagIcon = isMe ? '👑' : (isMax ? '★' : '');
        const tagColor = isMe ? '#b71c1c' : (isMax ? '#d32f2f' : INK2);
        let conceptHtml = '<span class="caption dim">—</span>';
        try {
          const sTax = (window._taxCache && window._taxCache[String(s.代码).padStart(6, '0')]) || null;
          const sConcepts = [];
          if (sTax) {
            if (sTax.level2_sw)         sConcepts.push({ name: sTax.level2_sw, level: 'L2' });
            if (sTax.level3_chain)      sConcepts.push({ name: sTax.level3_chain, level: 'L3' });
            (sTax.level4_subconcept || []).forEach(t => sConcepts.push({ name: t, level: 'L4' }));
          }
          if (sConcepts.length) {
            conceptHtml = sConcepts.slice(0, 2).map(c =>
              `<span class="chip" style="font-size:.7rem;padding:1px 5px;color:${c.level === 'L4' ? ACCENT : c.level === 'L3' ? '#7b9bd1' : INK2};border:1px solid currentColor">${escapeHtml(c.name)}</span>`
            ).join('');
          }
        } catch (e) {}
        return `
          <tr style="cursor:pointer" data-action="open-stock:${s.代码}">
            <td><b style="color:${tagColor}">${tagIcon} ${escapeHtml(s.名称)}</b></td>
            <td style="color:${(s.连板数||0) >= 2 ? UP : INK2};font-weight:bold">${s.连板数 || 0}</td>
            <td style="display:flex;flex-wrap:wrap;gap:2px">${conceptHtml}</td>
            <td style="text-align:right">${s.封单金额 ? (s.封单金额 / 1e8).toFixed(2) + '亿' : '—'}</td>
            <td style="text-align:right;color:${(s.涨跌幅||0) > 0 ? UP : DOWN}">${(s.涨跌幅||0).toFixed(1)}%</td>
          </tr>`;
      }).join('')}
    </table>
  `;
}

// 龙头榜行 (dragons.all 行) → 板块联动表格 rows
function renderDragonRows(rows, code) {
  if (!rows || rows.length === 0) return '';
  const maxLb = Math.max(...rows.map(x => x.streak || 0));
  return `
    <table class="mini-table" style="width:100%;font-size:.85rem">
      <tr>
        <th style="text-align:left">名称</th>
        <th style="text-align:left">连板</th>
        <th style="text-align:left">板块</th>
        <th style="text-align:right">封单</th>
        <th style="text-align:right">评分</th>
      </tr>
      ${rows.map(s => {
        const codeS = String(s.code).padStart(6, '0');
        const isMe = codeS === String(code).padStart(6, '0');
        const isMax = (s.streak || 0) === maxLb && (s.streak || 0) >= 2;
        const tagIcon = isMe ? '👑' : (isMax ? '★' : '');
        const tagColor = isMe ? '#b71c1c' : (isMax ? '#d32f2f' : INK2);
        const secLabel = s.taxonomy?.l1 || s.sector || '—';
        const secColor = s.taxonomy?.l1_color || INK2;
        return `
          <tr style="cursor:pointer" data-action="open-stock:${codeS}">
            <td><b style="color:${tagColor}">${tagIcon} ${escapeHtml(s.name)}</b></td>
            <td style="color:${(s.streak||0) >= 2 ? UP : INK2};font-weight:bold">${s.streak || 0}</td>
            <td><span class="chip" style="font-size:.7rem;padding:1px 5px;color:${secColor};border:1px solid ${secColor}">${escapeHtml(secLabel)}</span></td>
            <td style="text-align:right">${s.limit_order_amount_yi ? s.limit_order_amount_yi.toFixed(2) + '亿' : '—'}</td>
            <td style="text-align:right"><b style="color:${ACCENT}">${(s.score_total || 0).toFixed(1)}</b></td>
          </tr>`;
      }).join('')}
    </table>
  `;
}

// 按钮绑定
document.addEventListener('DOMContentLoaded', () => {
  const nr = $('#news-refresh-btn');      if (nr) nr.addEventListener('click', () => loadNewsList(true));
  const sr = $('#sectors-refresh-btn');  if (sr) sr.addEventListener('click', () => loadSectorsList(true));
});

// ═══════════════════════════════════════════════════════════
// REVIEW 复盘 view · 铁律冲突 + 资金占比 + AI 建议 (2026-07-10)
// ═══════════════════════════════════════════════════════════

const _reviewState = {
  trades: [],
  flows: new Map(),   // code -> {main_pct, retail_pct, fund_pct, ...}
  flowsTimer: null,
};

function _reviewFmtNum(n, d = 2) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(d);
}

function _reviewPct(n) {
  if (n == null || isNaN(n)) return { text: '—', cls: 'cell-flat' };
  if (n > 0.5)  return { text: '+' + n.toFixed(1) + '%', cls: 'cell-up' };
  if (n < -0.5) return { text: n.toFixed(1) + '%',  cls: 'cell-down' };
  return { text: n.toFixed(1) + '%', cls: 'cell-flat' };
}

function _reviewConflictBadge(n) {
  if (n == null) return '<span class="conflict-badge low">—</span>';
  if (n === 0) return `<span class="conflict-badge low">0</span>`;
  if (n <= 2)  return `<span class="conflict-badge mid">${n}</span>`;
  return `<span class="conflict-badge high">${n}</span>`;
}

function _reviewRulePills(rules, kind) {
  if (!rules || rules.length === 0) {
    return `<span class="caption dim">—</span>`;
  }
  return rules.slice(0, 4).map(r => {
    const id = (r && r.id) ? r.id : '?';
    const text = (r && r.text) ? r.text : (typeof r === 'string' ? r : '');
    return `<span class="rule-pill ${kind}" title="${escapeHtml(text)}"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(text.slice(0, 18))}</span>`;
  }).join('');
}

function _reviewDirection(d) {
  if (d === 'buy')  return '<span class="cell-up">▲ 买</span>';
  if (d === 'sell') return '<span class="cell-down">▼ 卖</span>';
  return d;
}

function _reviewVerdict(v) {
  if (!v || v === '—') return '<span class="caption dim">—</span>';
  return `<span class="verdict-pill ${escapeHtml(v)}">${escapeHtml(v)}</span>`;
}

// 盈亏金额上色 (红涨绿跌 · A 股习惯)
function _reviewMoney(n) {
  if (n == null || isNaN(n)) return { text: '—', cls: 'cell-flat' };
  const v = Number(n);
  const s = (v > 0 ? '+' : '') + v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  if (v > 0.5)  return { text: s, cls: 'cell-up' };
  if (v < -0.5) return { text: s, cls: 'cell-down' };
  return { text: '0', cls: 'cell-flat' };
}

async function _reviewLoadList() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="11" class="dim center">加载中…</td></tr>';
  try {
    const r = await _fetchWithTimeout('/api/review/trades?limit=80&since_days=180');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    _reviewState.trades = j.data || [];
    _reviewRender();
    _reviewLoadStats();
    const ts = $('#review-ts');
    if (ts) ts.textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11" class="dim center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function _reviewStatusPill(live) {
  const s = (live && live.status) || '-';
  const map = {
    holding: { t: '持仓', c: 'st-hold' },
    open:    { t: '持仓', c: 'st-hold' },
    sold:    { t: '已卖', c: 'st-sold' },
    cleared: { t: '清仓', c: 'st-clear' },
  };
  const m = map[s];
  return m ? `<span class="pos-pill ${m.c}">${m.t}</span>` : '';
}

function _reviewRender() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  if (!_reviewState.trades.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="dim center">暂无交易 · 上面录入第一笔</td></tr>';
    return;
  }
  // ── 按 code 分组: 一只股票多笔 → 主行(持仓/汇总) + 可折叠明细 ──
  const groups = new Map();
  for (const t of _reviewState.trades) {
    const k = t.code;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(t);
  }
  const html = [];
  for (const [code, list] of groups) {
    list.sort((a, b) => (b.id || 0) - (a.id || 0)); // 新的在前
    const first  = list[0];
    const name   = list.find(x => x.name)?.name || first.name || '—';
    // 持仓状态:有任一 holding/open → 持仓;全清 → 清仓
    // 主行的「持仓 / 已清仓」状态 — 用 totalHeld 驱动,而不是 per-row live.status
    // (用户反馈: 后端 status 字段不可靠,totalHeld > 0 才是真正的持仓)
    const totalHeld = list.reduce((s, t) => s + ((t.live && t.live.held_shares) || 0), 0);
    const groupStatusPill = totalHeld > 0
      ? `<span class="pos-pill st-hold">持仓 <b>${totalHeld}</b> 股</span>`
      : `<span class="pos-pill st-clear">清仓</span>`;
    // GROUP 累计盈亏 — 跨所有行求和,不依赖某一行 live (用户要求与手算账单完全一致)
    //   卖单 row.live.cum_pnl = 该笔已实现盈亏
    //   买单 row.live.cum_pnl = 仍未卖出部分的浮动盈亏 (持仓归 0 时清 0)
    //   → 所有行累加 = 总盈亏 = 已实现 + 未实现
    const groupTodayPnl = list.reduce((s, t) => s + ((t.live && t.live.today_pnl) || 0), 0);
    const groupCumPnl   = list.reduce((s, t) => s + ((t.live && t.live.cum_pnl) || 0), 0);
    // 累计盈亏比 = 总盈亏 / 该股"净投入成本"(买入总额 - 卖出收入, 即仍在仓的真实成本)
    const groupCost = list.reduce((s, t) => {
      const v = (t.price || 0) * (t.shares || 0);
      return s + (t.direction === 'buy' ? v : -v);
    }, 0);
    const groupCumPct = groupCost > 0 ? (groupCumPnl / groupCost * 100) : 0;
    const today = _reviewMoney(groupTodayPnl);
    const cum = _reviewMoney(groupCumPnl);
    const cumPct = _reviewPct(groupCumPct);
    // 子行最新一笔的 live — 用于 PnL 子表
    const holding = list.find(t => {
      const s = (t.live && t.live.status) || '';
      return s === 'holding' || s === 'open';
    });
    const dateStr = (first.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
    const timeStr = (first.occurred_at || '').replace('T', ' ').slice(11, 16) || '—';
    const mistake = (holding || first).last_review?.main_mistake
                 || (holding || first).last_review?.mistake_pattern
                 || '';
    const mistakeHtml = mistake
      ? `<span class="main-mistake-pill" title="${escapeHtml(mistake)}">${escapeHtml(mistake)}</span>`
      : '<span class="caption dim">未复盘</span>';
    const reviewed = !!(holding || first).last_review;
    // 持仓/汇总统计 (显示给用户看"这是这只股票当前的总账")
    const totalHoldTxt = totalHeld > 0 ? `${totalHeld} 股` : '已清仓';
    // 主行「方向」列改为更实用的汇总: 持仓中显示持仓占比; 清仓显示买/卖笔数分布
    const buyCount = list.filter(t => t.direction === 'buy').length;
    const sellCount = list.filter(t => t.direction === 'sell').length;
    const groupSummary = totalHeld > 0
      ? `<span class="group-summary-hold"><b>${buyCount}</b><span class="dim">买</span> / <b>${sellCount}</b><span class="dim">卖</span></span>`
      : `<span class="group-summary-clear"><b>${buyCount}</b><span class="dim">买</span> / <b>${sellCount}</b><span class="dim">卖</span> · 已清</span>`;

    const expandable = list.length > 1;
    const gid = `grp-${code}-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
    // 主行: 价格/股数/PnL 取最新一笔 (避免误导)
    html.push(`
      <tr class="rv-group-hd ${expandable ? 'rv-expandable' : ''}" data-code="${escapeHtml(code)}" data-group="${gid}">
        <td class="rv-nm">
          ${expandable
            ? `<button type="button" class="rv-expand-btn" data-toggle="${gid}" aria-label="展开明细">▶</button>`
            : `<span class="rv-expand-spacer"></span>`}
          <a class="np-code" href="#" data-jump-code="${escapeHtml(code)}" title="点击进入个股详情">${escapeHtml(code)}</a>
          <span class="np-name" data-edit-name="1" data-trade-id="${first.id}" data-code="${escapeHtml(code)}" title="点击修改股票名">${escapeHtml(name)}</span>
          ${groupStatusPill}
          ${expandable ? `<span class="caption dim rv-n">${list.length} 笔明细</span>` : ''}
        </td>
        <td class="group-summary-cell">${groupSummary}</td>
        <td class="caption">${escapeHtml(dateStr || '—')}</td>
        <td class="cell-num">${_reviewFmtNum(first.price, 2)}</td>
        <td class="caption">${escapeHtml(timeStr)}</td>
        <td class="cell-num"><span title="持仓股数 (跨多笔汇总)">${totalHoldTxt}</span></td>
        <td class="cell-num ${today.cls}">${today.text}</td>
        <td class="cell-num ${cum.cls}">${cum.text}</td>
        <td class="cell-num ${cumPct.cls}">${cumPct.text}</td>
        <td>${mistakeHtml}</td>
        <td class="rv-act">
          <button class="btn-mini ${reviewed ? '' : 'primary'}" data-action="ai-review:${first.id}">${reviewed ? 'AI 复盘' : 'AI 复盘 ●'}</button>
          <button class="btn-mini danger" data-action="review-delete:${first.id}">删</button>
        </td>
      </tr>
    `);
    if (expandable) {
      // 折叠明细:子表显示每一笔独立行
      const childRows = list.map(t => {
        const live = t.live || {};
        const rev = t.last_review || {};
        const tToday = _reviewMoney(live.today_pnl);
        const tCum   = _reviewMoney(live.cum_pnl);
        const tCumPct = _reviewPct(live.cum_pnl_pct);
        const dStr = (t.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
        const tmStr = (t.occurred_at || '').replace('T', ' ').slice(11, 16) || '—';
        const tk = rev.main_mistake || rev.mistake_pattern || '';
        const tkHtml = tk
          ? `<span class="main-mistake-pill" title="${escapeHtml(tk)}">${escapeHtml(tk)}</span>`
          : '<span class="caption dim">未复盘</span>';
        const tRev = !!t.last_review;
        return `
          <tr class="rv-child" data-trade-id="${t.id}" data-code="${escapeHtml(code)}" data-trade-date="${escapeHtml(t.trade_date || '')}" data-group="${gid}">
            <td class="rv-nm rv-child-nm">
              <span class="rv-child-line"></span>
              <a class="np-code" href="#" data-jump-code="${escapeHtml(code)}" data-jump-date="${escapeHtml(t.trade_date || '')}" data-jump-time="${escapeHtml(t.occurred_at || '').slice(0,16)}" title="进入 ${escapeHtml(code)} 个股详情 · 跳到 ${escapeHtml(dStr || '此笔对应日')} 行情">${escapeHtml(code)}</a>
              <span class="caption dim" style="margin-left:.3rem">${escapeHtml(t.occurred_at || '').slice(0,16)}</span>
            </td>
            <td>${_reviewDirection(t.direction)}</td>
            <td class="caption">${escapeHtml(dStr || '—')}</td>
            <td class="cell-num">${_reviewFmtNum(t.price, 2)}</td>
            <td class="caption">${escapeHtml(tmStr)}</td>
            <td class="cell-num">${t.shares}</td>
            <td class="cell-num ${tToday.cls}">${tToday.text}</td>
            <td class="cell-num ${tCum.cls}">${tCum.text}</td>
            <td class="cell-num ${tCumPct.cls}">${tCumPct.text}</td>
            <td>${tkHtml}</td>
            <td class="rv-act">
              <button class="btn-mini ${tRev ? '' : 'primary'}" data-action="ai-review:${t.id}">${tRev ? 'AI' : 'AI●'}</button>
              <button class="btn-mini danger" data-action="review-delete:${t.id}">×</button>
            </td>
          </tr>
        `;
      }).join('');
      html.push(`
        <tr class="rv-child-wrap" data-group="${gid}" hidden>
          <td colspan="11" class="rv-child-cell">
            <table class="data-table review-table review-table-child">
              <tbody>${childRows}</tbody>
            </table>
          </td>
        </tr>
      `);
    }
  }
  tbody.innerHTML = html.join('');

  // ── 底部汇总 (所有可见交易 · 含子行 · 不含 000000 占位) ──
  // - 今日盈亏 = Σ today_pnl
  // - 累计盈亏 = 已实现 + 浮动 (cleared 不再重复计)
  // - 含手续费累计 = 累计 − 笔数 × 5 (用户口径:每笔买卖固定 5 元手续费)
  const tfoot = $('#review-tfoot');
  if (tfoot) {
    const PLACEHOLDER = new Set(['', '000000', '—']);
    const allTrades = (_reviewState.trades || []).filter(t => !PLACEHOLDER.has(String(t.code || '').trim()));
    if (allTrades.length) {
      let sToday = 0, sRealized = 0, sFloat = 0;
      let nHolding = 0, nSold = 0, nCleared = 0;
      for (const t of allTrades) {
        const live = t.live || {};
        const st = live.status || '-';
        const today = +(live.today_pnl || 0);
        const cum = +(live.cum_pnl || 0);
        if (st === 'holding' || st === 'open') {
          sToday += today;
          sFloat += cum;
          nHolding++;
        } else if (st === 'sold') {
          sToday += today;
          sRealized += cum;
          nSold++;
        } else if (st === 'cleared') {
          nCleared++;
        }
      }
      const sCum = sRealized + sFloat;
      const totalTrades = nHolding + nSold + nCleared;
      const feeTotal = totalTrades * 5;
      const sReal = sCum - feeTotal;
      const fmtMoney = (v) => {
        return (v > 0 ? '+' : (v < 0 ? '−' : '')) + '¥' + Math.abs(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      };
      const clsToday = sToday > 0.5 ? 'cell-up' : (sToday < -0.5 ? 'cell-down' : 'cell-flat');
      const clsCum   = sCum > 0.5 ? 'cell-up' : (sCum < -0.5 ? 'cell-down' : 'cell-flat');
      const clsReal  = sReal > 0.5 ? 'cell-up' : (sReal < -0.5 ? 'cell-down' : 'cell-flat');
      const todayEl  = $('#rv-sum-today');
      const cumEl    = $('#rv-sum-cum');
      const cumSubEl = $('#rv-sum-cum-sub');
      const realEl   = $('#rv-sum-real');
      const realSubEl = $('#rv-sum-real-sub');
      const metaEl   = $('#rv-sum-meta');
      if (todayEl)   { todayEl.textContent   = fmtMoney(sToday); todayEl.className   = `cell-num bold ${clsToday}`; }
      if (cumEl)     { cumEl.textContent     = fmtMoney(sCum);   cumEl.className     = `cell-num bold ${clsCum}`; }
      if (cumSubEl)  { cumSubEl.textContent  = `实 ${fmtMoney(sRealized)} · 浮 ${fmtMoney(sFloat)}`; }
      if (realEl)    { realEl.textContent    = fmtMoney(sReal);  realEl.className    = `cell-num bold ${clsReal}`; }
      if (realSubEl) { realSubEl.textContent = `含手续费 −¥${feeTotal.toLocaleString('zh-CN')} (${totalTrades} × ¥5)`; }
      if (metaEl)    { metaEl.textContent    = `共 ${totalTrades} 笔 · 持仓 ${nHolding} · 已卖 ${nSold} · 清仓 ${nCleared}`; }
      tfoot.hidden = false;
    } else {
      tfoot.hidden = true;
    }
  }

  // ── 代码点击 → 跳个股详情 (带 trade 日期上下文) ──
  // 主行:不传日期 → 取最新; 子行: 传 trade_date → 历史快照到那一天
  tbody.querySelectorAll('[data-jump-code]').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const code = a.dataset.jumpCode;
      if (!code) return;
      // YYYYMMDD → YYYY-MM-DD
      const rawDate = a.dataset.jumpDate || '';
      const date = rawDate && /^\d{8}$/.test(rawDate)
        ? `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`
        : '';
      // 跳到 stock 视图并加载个股
      if (typeof showView === 'function') showView('stock');
      else window.location.hash = '#/stock';
      loadStockDetail(code, date);
    });
  });

  // ── 股票名点击 → 转 <input> 内联编辑 ──
  tbody.querySelectorAll('[data-edit-name]').forEach(span => {
    span.addEventListener('click', (e) => {
      e.stopPropagation();
      _inlineEditName(span);
    });
  });

  // 主行: 空白处点击 = 默认跳个股详情 (避开按钮 + 编辑中的 input)
  tbody.querySelectorAll('.rv-group-hd > td.rv-nm').forEach(td => {
    td.style.cursor = 'pointer';
    // R-a11y-013: 键盘可达
    td.setAttribute('tabindex', '0');
    td.setAttribute('role', 'button');
    td.setAttribute('aria-label', '跳到个股详情');
    const handler = (e) => {
      if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('button')) return;
      if (e.target.closest('input')) return;
      if (e.target.closest('[data-jump-code],[data-edit-name]')) return;
      e.preventDefault();
      const tr = td.closest('tr.rv-group-hd');
      const c = tr?.dataset.code;
      if (c) loadStockDetail(c);
    };
    td.addEventListener('click', handler);
    td.addEventListener('keydown', handler);
  });

  // 折叠按钮
  tbody.querySelectorAll('.rv-expand-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const gid = btn.dataset.toggle;
      const wrap = tbody.querySelector(`tr.rv-child-wrap[data-group="${gid}"]`);
      const expanded = wrap && !wrap.hidden;
      if (wrap) wrap.hidden = expanded;
      btn.textContent = expanded ? '▶' : '▼';
      btn.classList.toggle('open', !expanded);
      btn.setAttribute('aria-expanded', String(!expanded));
    });
  });
  // 2026-07-14: 用户反馈每只股票交易明细"不见了" — 实际是默认折叠,要点击 ▶ 才看
  // 解决:首次进入页面默认全部展开,信息密度优先(沿用 feedback_more_info_visible 规则)
  // localStorage 记忆用户后续手动折叠的组,刷新不丢
  const collapsedKey = 'review_collapsed_groups';
  let collapsed = new Set();
  try { collapsed = new Set(JSON.parse(localStorage.getItem(collapsedKey) || '[]')); } catch {}
  tbody.querySelectorAll('tr.rv-child-wrap[data-group]').forEach(wrap => {
    const gid = wrap.dataset.group;
    const btn = tbody.querySelector(`.rv-expand-btn[data-toggle="${gid}"]`);
    if (!btn) return;
    if (!collapsed.has(gid)) {
      wrap.hidden = false;
      btn.textContent = '▼';
      btn.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
    }
    // 单击 ▶ 也会同步写 collapsed 集合,刷新保留
    btn.addEventListener('click', () => {
      const isOpen = !wrap.hidden;
      if (isOpen) collapsed.add(gid);
      else collapsed.delete(gid);
      try { localStorage.setItem(collapsedKey, JSON.stringify(Array.from(collapsed))); } catch {}
    }, true);  // capture 阶段优先于主 click,避免时序冲突
  });
  // 子行空白处点击 = 跳个股 (避开按钮 + 编辑中 input) — 带该笔 trade_date
  tbody.querySelectorAll('tr.rv-child > td.rv-child-nm').forEach(td => {
    td.style.cursor = 'pointer';
    td.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      if (e.target.closest('input')) return;
      if (e.target.closest('[data-jump-code]')) return;
      const tr = td.closest('tr.rv-child');
      const c = tr?.dataset.code;
      const td2 = tr?.dataset.tradeDate || '';
      const date = td2 && /^\d{8}$/.test(td2)
        ? `${td2.slice(0,4)}-${td2.slice(4,6)}-${td2.slice(6,8)}`
        : '';
      if (c) loadStockDetail(c, date);
    });
  });
}

// ── 内联编辑股票名 — 点击 → 输入框 → 失焦 / Enter 保存 ──
async function _inlineEditName(span) {
  if (span.querySelector('input')) return;            // 已经在编辑
  const tradeId = span.dataset.tradeId;
  const code = span.dataset.code;
  const orig = span.textContent.trim();
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.value = orig;
  inp.className = 'np-name-input';
  inp.maxLength = 32;
  inp.style.cssText = 'width:11em;font:inherit;padding:2px 6px;border:1px solid var(--accent);border-radius:4px;background:#fff;color:var(--ink-1)';
  span.textContent = '';
  span.appendChild(inp);
  inp.focus();
  inp.select();
  let committed = false;
  const finish = async (save) => {
    if (committed) return;
    committed = true;
    const v = (inp.value || '').trim();
    if (!save || v === orig || !v) {
      // 取消 / 无变化
      span.textContent = orig;
      return;
    }
    span.textContent = '…';           // saving 状态
    try {
      const r = await fetch(`/api/review/trades/${encodeURIComponent(tradeId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ name: v, code }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'HTTP ' + r.status);
      span.textContent = v;
      // 更新本地 state — 同时刷新 _reviewState.trades
      const tr = _reviewState.trades.find(x => x.id === tradeId);
      if (tr) tr.name = v;
      // 顶部资金栏 / 持仓标签可能也要刷
      if (typeof _reviewLoadPortfolio === 'function') {
        try { await _reviewLoadPortfolio(); } catch {}
      }
    } catch (e) {
      console.warn('[inline name edit] failed', e);
      span.textContent = orig;
      span.title = '保存失败: ' + e.message;
    }
  };
  inp.addEventListener('blur', () => finish(true));
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); inp.value = orig; inp.blur(); }
  });
}

// ── 顶部资金栏 ──
async function _reviewLoadPortfolio() {
  const bar = $('#review-capbar');
  if (!bar) return;
  try {
    const r = await _fetchWithTimeout('/api/review/portfolio');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    _renderCapbar(j.data || {});
    const ts = $('#pf-ts');
    if (ts) ts.textContent = '实时 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }) +
      ` · 报价 ${j.data?.quotes_ok ?? 0}/${j.data?.codes ?? 0}`;
  } catch (e) {
    bar.innerHTML = `<span class="dim">资金栏加载失败: ${escapeHtml(e.message)}</span>`;
  }
}

function _capTile(lbl, valObj, sub) {
  const cls = valObj.cls || '';
  return `<div class="cap-tile">
    <div class="cap-lbl">${lbl}</div>
    <div class="cap-val ${cls}">${valObj.text}</div>
    ${sub ? `<div class="cap-sub ${sub.cls || ''}">${sub.text}</div>` : ''}
  </div>`;
}

function _renderCapbar(d) {
  const bar = $('#review-capbar');
  if (!bar) return;
  const yuan = n => (n == null ? '—' : '¥' + Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 }));
  const total = { text: d.total_capital ? yuan(d.total_capital) : '未设置', cls: '' };
  const posText = d.position_value != null ? yuan(d.position_value) : '—';
  const posRatio = d.position_ratio != null
    ? { text: d.position_ratio.toFixed(1) + '% 仓 · ' + d.position_count + ' 只', cls: 'dim' }
    : { text: (d.position_count || 0) + ' 只 · 设总资金看仓位%', cls: 'dim' };
  const today = _reviewMoney(d.today_pnl);
  const todaySub = d.today_pnl_pct != null ? _reviewPct(d.today_pnl_pct) : null;
  const total_pnl = _reviewMoney(d.total_pnl);
  const totalSub = {
    text: `浮 ${_reviewMoney(d.unrealized_pnl).text} · 实 ${_reviewMoney(d.realized_pnl).text}` +
      (d.codes ? ` · ${d.trade_count || d.codes} 笔` : ''),
    cls: 'dim',
  };
  // 含手续费总盈亏 = 总盈亏 − 笔数 × 5 (用户口径)
  const tCount = d.trade_count || 0;
  const feeAdj = (d.total_pnl != null && tCount > 0) ? round2(d.total_pnl - tCount * 5) : null;
  const feeAdjObj = feeAdj != null
    ? _reviewMoney(feeAdj)
    : { text: '—', cls: '' };
  const feeSub = tCount > 0
    ? { text: `−¥${(tCount * 5).toLocaleString('zh-CN')} (${tCount} × ¥5)`, cls: 'dim' }
    : { text: '无交易 · 0', cls: 'dim' };
  const ratio = d.total_pnl_pct != null
    ? _reviewPct(d.total_pnl_pct)
    : { text: '设总资金', cls: 'cell-flat' };
  bar.innerHTML =
    _capTile('总资金 (满仓)', total, { text: d.cash != null ? '可用 ' + yuan(d.cash) : '', cls: 'dim' }) +
    _capTile('仓位', { text: posText, cls: '' }, posRatio) +
    _capTile('今日盈亏', today, todaySub) +
    _capTile('总盈亏', total_pnl, totalSub) +
    _capTile('含手续费', feeAdjObj, feeSub) +
    _capTile('盈亏比', ratio, { text: '总盈亏 / 总资金', cls: 'dim' });
  _renderPositions(d.positions || []);
}

function round2(v) { return Math.round((+v || 0) * 100) / 100; }

function _renderPositions(positions) {
  const box = $('#review-positions');
  if (!box) return;
  if (!positions.length) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="pos-title">当前持仓 · 实时</div>' +
    '<div class="pos-grid">' + positions.map(p => {
      const up = _reviewPct(p.unrealized_pct);
      const today = _reviewPct(p.prev_close ? (p.price - p.prev_close) / p.prev_close * 100 : null);
      const code = escapeHtml(p.code);
      return `<div class="pos-card" data-action="open-stock:${code}" style="cursor:pointer">
        <div class="pos-hd"><code>${code}</code> <span>${escapeHtml(p.name || '')}</span>
          <button class="pos-del" title="删除该股全部交易(不可逆)" data-action="review-delete-position:${encodeURIComponent(code)}|${encodeURIComponent(p.name || '')}|${p.shares}">×</button>
        </div>
        <div class="pos-row"><span class="dim">现价</span><b class="${today.cls}">${_reviewFmtNum(p.price, 2)}</b> <span class="${today.cls}">${today.text}</span></div>
        <div class="pos-row"><span class="dim">${p.shares}股 @ ${_reviewFmtNum(p.avg_cost, 2)}</span></div>
        <div class="pos-row"><span class="dim">浮盈</span><b class="${up.cls}">${_reviewMoney(p.unrealized).text}</b> <span class="${up.cls}">${up.text}</span></div>
      </div>`;
    }).join('') + '</div>';
}

async function _reviewDeletePosition(code, name, shares) {
  if (!confirm(`确定删除 ${code} ${name || ''} 的全部交易记录吗?\n(共 ${shares || '?'} 股持仓)\n此操作不可逆。`)) return;
  try {
    const r = await _fetchWithTimeout('/api/review/positions/' + encodeURIComponent(code), { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
      showToast(`✓ 已清空 ${code} · 删除 ${j.data.deleted} 笔`, 'success');
      _reviewLoadPortfolio();
      _reviewLoadList();
    } else {
      showToast('删除失败: ' + (j.error || ''), 'error');
    }
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error');
  }
}

// ── 总资金设置 ──
async function _reviewLoadSettings() {
  try {
    const r = await _fetchWithTimeout('/api/review/settings');
    const j = await r.json();
    const inp = $('#cap-total');
    if (inp && j.data?.total_capital) inp.value = j.data.total_capital;
  } catch (e) { /* ignore */ }
}

function _reviewBindCapital() {
  const btn = $('#cap-save');
  if (!btn || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', async () => {
    const v = parseFloat($('#cap-total').value);
    if (!v || v <= 0) { showToast('请填一个正数总资金', 'error'); return; }
    btn.disabled = true;
    try {
      const r = await _fetchWithTimeout('/api/review/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ total_capital: v }),
      });
      const j = await r.json();
      if (j.ok) { showToast('✓ 总资金已保存', 'success'); _reviewLoadPortfolio(); }
      else showToast('保存失败: ' + (j.error || ''), 'error');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
    finally { btn.disabled = false; }
  });
}

// ── 录入表单折叠展开 (R50-FIX: 用户先看表, 再记一笔) ──
function _reviewBindToggle() {
  const btn = $('#rf-toggle-btn');
  const wrap = $('#review-form-wrap');
  if (!btn || !wrap || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', () => {
    const open = !wrap.hidden;
    wrap.hidden = open;
    btn.textContent = open ? '+ 记一笔' : '× 收起';
    if (!open) {
      // 展开时滚到表单,便于操作
      setTimeout(() => wrap.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
      setTimeout(() => $('#rf-code')?.focus(), 280);
    }
  });
}

// ── 买入时刻点推算 ──
function _reviewBindInfer() {
  const btn = $('#rf-infer');
  if (!btn || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', async () => {
    const code = ($('#rf-code').value || '').trim();
    const price = parseFloat($('#rf-price').value);
    const dateRaw = ($('#rf-date').value || '').replace(/-/g, '');
    const hint = $('#rf-infer-hint');
    if (!code) { showToast('先填股票代码', 'error'); return; }
    btn.disabled = true; if (hint) hint.textContent = '分时反推中…';
    try {
      let url = '/api/review/time_points?code=' + encodeURIComponent(code);
      if (dateRaw) url += '&date=' + dateRaw;
      if (price) url += '&price=' + price;
      const r = await _fetchWithTimeout(url);
      const j = await r.json();
      const d = j.data || {};
      const sel = $('#rf-time');
      sel.innerHTML = '<option value="">自动/手填</option>';
      if (d.available && d.points && d.points.length) {
        d.points.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.time;
          const tag = p.match === 'exact' ? ' ✓命中' : (p.match === 'near' ? ' ~接近' : '');
          opt.textContent = `${p.time} @ ${p.close}${tag}`;
          sel.appendChild(opt);
        });
        const firstMatch = d.points.find(p => p.match === 'exact') || d.points[0];
        if (firstMatch) sel.value = firstMatch.time;
        if (hint) hint.textContent = d.reason || `${d.points.length} 个候选时刻`;
      } else {
        if (hint) hint.textContent = d.reason || '无可用分时,请手动填时间';
      }
    } catch (e) {
      if (hint) hint.textContent = '推算失败: ' + e.message;
    } finally { btn.disabled = false; }
  });
}

async function _reviewRefreshFlows() {
  // 兼容旧调用:直接走 portfolio 实时价刷新
  await _reviewLoadPortfolio();
}

function _reviewStartFlowsPolling() {
  if (_reviewState.flowsTimer) clearInterval(_reviewState.flowsTimer);
  _reviewState.flowsTimer = setInterval(_reviewLoadPortfolio, 10000);
}

// ── AI 复盘子页面 · 进入入口 ──
const _aiReviewState = {
  tradeId: null,
  trade: null,
  review: null,
  running: false,
};

function openAiReview(tradeId) {
  // 在主表里找这笔交易
  const t = (_reviewState.trades || []).find(t => t.id === tradeId) || null;
  const hasReview = !!(t && t.last_review);
  _aiReviewState.tradeId = tradeId;
  _aiReviewState.trade = t;
  _aiReviewState.review = t?.last_review || null;
  if (hasReview) {
    // 已有复盘 → 跳面板看详细结果(原行为)
    showView('ai-review');
    return;
  }
  // 未复盘 → 后台跑,不要跳转页面
  _reviewRunInBackground(tradeId, t);
}

// R-bug-2 + R-fix-2026-07-14: 后台跑 AI 复盘 — POST 立刻返 202 不阻塞前端;UI 立刻解锁,后台跑完只 patch 单行 + toast,失败/超时也不影响主表。
async function _reviewRunInBackground(tradeId, t) {
  if (!tradeId) return;
  // 视觉反馈:把当前所有指向这 tradeId 的 AI 复盘按钮打上"⏳"状态
  const btns = document.querySelectorAll(`button[data-action="ai-review:${tradeId}"], button[data-action="review-run:${tradeId}"]`);
  btns.forEach(b => { b.dataset._oldText = b.textContent; b.disabled = true; b.textContent = '⏳'; });
  showToast(`🌀 AI 复盘 #${tradeId} 已排队 · 约 30-60s 后完成`, 'info', 2500);
  const t0 = Date.now();
  try {
    const r = await _fetchWithTimeout(`/api/review/trades/${tradeId}/review?force=true`, { method: 'POST', timeout: 8000 });
    const j = await r.json();
    if (!j.ok) {
      btns.forEach(b => { b.disabled = false; b.textContent = b.dataset._oldText || 'AI 复盘'; });
      showToast(`✗ #${tradeId} 排队失败: ${j.error || '未知错误'}`, 'error', 4000);
      return;
    }
    if (j.data && !j.data.queued && j.data.verdict) {
      _aiReviewState.review = j.data;
      _aiReviewState.trade = t;
      _aiReviewState.tradeId = tradeId;
      btns.forEach(b => { b.disabled = false; b.textContent = '✓ ' + (j.data.verdict || '已复盘'); });
      await _reviewLoadList();
      return;
    }
    btns.forEach(b => { b.disabled = false; b.textContent = '⏳ 后台'; });
    _reviewPollOne(tradeId, btns, t0);
  } catch (e) {
    btns.forEach(b => { b.disabled = false; b.textContent = '⏳ 后台'; });
    _reviewPollOne(tradeId, btns, t0);
    if (!String(e.message || '').includes('abort')) {
      console.warn('AI review POST error (will poll anyway):', e);
    }
  }
}

// 轮询单笔复盘状态:每 4s 一次,最多 90s;完成只 patch 单行 + toast,不动主表
function _reviewPollOne(tradeId, btns, t0) {
  const startedAt = t0 || Date.now();
  const deadline = startedAt + 90000;
  const tick = async () => {
    if (Date.now() > deadline) {
      btns.forEach(b => { b.disabled = false; b.textContent = b.dataset._oldText || 'AI 复盘'; });
      showToast(`⏰ #${tradeId} 复盘超时未完成(>90s)`, 'warn', 4000);
      return;
    }
    try {
      const r = await _fetchWithTimeout(`/api/review/trades/${tradeId}/status`, { timeout: 5000 });
      const j = await r.json();
      if (j.ok && j.data && j.data.has_review && (j.data.ts_created * 1000) >= startedAt - 1000) {
        btns.forEach(b => { b.disabled = false; b.textContent = '✓ ' + (j.data.verdict || '已复盘'); });
        showToast(`✓ #${tradeId} 复盘完成 · ${j.data.verdict || ''} ${j.data.score || ''}分`, 'success', 3500);
        _reviewPatchRow(tradeId, j.data);
        return;
      }
    } catch (e) {}
    setTimeout(tick, 4000);
  };
  setTimeout(tick, 3000);
}

function _reviewPatchRow(tradeId, statusData) {
  if (!_reviewState || !Array.isArray(_reviewState.trades)) return;
  for (const t of _reviewState.trades) {
    if (t.id === tradeId) {
      t.last_review = t.last_review || {};
      t.last_review.verdict = statusData.verdict || t.last_review.verdict || '';
      t.last_review.score = statusData.score || t.last_review.score || 0;
      break;
    }
  }
  const row = document.querySelector(`tr[data-trade-id="${tradeId}"]`);
  if (row) {
    const btn = row.querySelector(`button[data-action="ai-review:${tradeId}"], button[data-action="review-run:${tradeId}"]`);
    if (btn) { btn.disabled = false; btn.textContent = '✓ ' + (statusData.verdict || '已复盘'); }
  }
}

async function _airvOnViewEnter() {
  const view = document.querySelector('.view-ai-review');
  if (!view || view.hidden) return;
  const tid = _aiReviewState.tradeId;
  if (!tid) { _renderAiReviewEmpty(); return; }
  // 标题
  const t = _aiReviewState.trade;
  if (t) {
    $('#airv-title').textContent = `${t.direction === 'buy' ? '买' : '卖'} ${t.name || t.code} @ ${_reviewFmtNum(t.price, 2)}`;
    const sub = `${t.code} · ${(t.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')} ${(t.occurred_at || '').slice(11, 16) || ''} · ${t.shares} 股`;
    $('#airv-sub').textContent = sub;
  }
  // 后退按钮绑定 (幂等)
  const back = $('#airv-back');
  if (back && !back._bound) {
    back._bound = true;
    back.addEventListener('click', () => showView('review'));
  }
  // 先拉一次历史 review 列表(取最新一条直接显示,免 LLM)
  try {
    const r = await _fetchWithTimeout('/api/review/trades/' + tid + '/reviews');
    const j = await r.json();
    const reviews = (j.data && j.data.reviews) || [];
    if (reviews.length) {
      _aiReviewState.review = reviews[0];  // 最新一条
      _renderAiReview(reviews[0]);
      return;
    }
  } catch (e) { /* ignore */ }
  _renderAiReviewPending();
  await _airvRunLLM(false);
}

async function _airvRunLLM(force = true) {
  const tid = _aiReviewState.tradeId;
  if (!tid || _aiReviewState.running) return;
  // 非强制重算 → 走 SSE 流,实时显示阶段进度(拉盘面→AI→铁律→完成)
  if (!force && typeof EventSource !== 'undefined') {
    return _airvRunViaSSE(tid);
  }
  _aiReviewState.running = true;
  const hint = $('#airv-status');
  if (hint) hint.textContent = force ? '🌀 AI 强制重算中…约需 1 分钟' : '🌀 AI 复盘中…约需 1 分钟';
  try {
    const r = await _fetchWithTimeout(`/api/review/trades/${tid}/review?force=${force}`, { method: 'POST' });
    const j = await r.json();
    if (j.ok && j.data) {
      _aiReviewState.review = j.data;
      _renderAiReview(j.data);
      if (hint) hint.textContent = '✓ 已完成 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
      _reviewLoadList();  // 同步主表 reviewed 标记
    } else {
      if (hint) hint.textContent = '✗ 复盘失败: ' + (j.error || '未知错误');
    }
  } catch (e) {
    if (hint) hint.textContent = '✗ 复盘超时/失败: ' + e.message;
  } finally {
    _aiReviewState.running = false;
  }
}

// R-ui-021: SSE 流式复盘 — 实时推送阶段/铁律,完成后渲染 + 同步主表
function _airvRunViaSSE(tid) {
  return new Promise((resolve) => {
    _aiReviewState.running = true;
    const hint = $('#airv-status');
    const es = new EventSource(`/api/stream/review/${tid}`);
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      try { es.close(); } catch {}
      _aiReviewState.running = false;
      resolve();
    };
    es.addEventListener('progress', (ev) => {
      try { const d = JSON.parse(ev.data); if (hint) hint.textContent = `🌀 ${d.msg || d.stage || 'AI 复盘中…'}`; } catch {}
    });
    es.addEventListener('rule_failed', (ev) => {
      if (hint) { const cur = hint.textContent || ''; hint.textContent = cur.includes('铁律') ? cur : '🔍 铁律分析中…'; }
    });
    es.addEventListener('done', (ev) => {
      try {
        const data = JSON.parse(ev.data);
        _aiReviewState.review = data;
        _renderAiReview(data);
        if (hint) hint.textContent = '✓ 已完成 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
        _reviewLoadList();
      } catch (e) {
        if (hint) hint.textContent = '✗ 解析失败: ' + e.message;
      }
      finish();
    });
    es.addEventListener('error', (ev) => {
      // SSE 断连或后端 error 事件 → 回退到 POST(仅一次)
      if (settled) return;
      try { es.close(); } catch {}
      _aiReviewState.running = false;
      settled = true;
      _airvRunLLM(true).then(resolve);
    });
  });
}

function _renderAiReviewEmpty() {
  $('#airv-title').textContent = 'AI 复盘';
  $('#airv-sub').textContent = '先回到复盘主页,选一笔交易点 AI 复盘';
  $('#airv-body').innerHTML = '<article class="card mt-16"><div class="dim center" style="padding:2rem">还没有选中交易</div></article>';
}

function _renderAiReviewPending() {
  $('#airv-body').innerHTML = '<article class="card mt-16"><div class="dim center" style="padding:2rem">⏳ AI 复盘数据收集中…<div class="caption dim mt-8">限价/分时/K线/席位/新闻,全部拉完后才出结论</div></div></article>';
}

function _airvClass(verdict) {
  if (verdict === '优秀') return 'v-good';
  if (verdict === '及格') return 'v-pass';
  if (verdict === '失误') return 'v-bad';
  if (verdict === '严重失误') return 'v-worse';
  return '';
}

function _renderAiReview(rev) {
  const body = $('#airv-body');
  if (!body) return;
  const summary = rev.summary || '';
  const advice = rev.ai_advice || '';
  const recap = rev.limit_up_recap || '';
  const mainM = rev.main_mistake || rev.mistake_pattern || '';
  const verdict = rev.verdict || '—';
  const score = rev.score || 0;
  const risks = rev.key_risks || [];
  const rulesP = rev.rules_passed || [];
  const rulesF = rev.rules_failed || [];
  const improv = rev.improvement || '';
  const ts = rev.ts_created ? new Date(rev.ts_created * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
  const cls = _airvClass(verdict);
  body.innerHTML = `
    <article class="card mt-16 airv-card">
      <div class="card-eyebrow flex-between">
        <span>VERDICT · AI 评分</span>
        <span class="caption dim">${escapeHtml(ts)}</span>
      </div>
      <div class="airv-head">
        <div class="airv-verdict ${cls}">${escapeHtml(verdict)}</div>
        <div class="airv-score">
          <div class="num">${score}</div><div class="cap">/ 100</div>
        </div>
        ${mainM ? `<div class="main-mistake-pill big" title="${escapeHtml(mainM)}">${escapeHtml(mainM)}</div>` : ''}
      </div>
    </article>

    ${recap ? `
    <article class="card mt-12">
      <div class="card-eyebrow">PART 1 · 当日涨停全景回溯</div>
      <div class="airv-md">${escapeHtml(recap)}</div>
    </article>` : ''}

    <article class="card mt-12">
      <div class="card-eyebrow">PART 2 · 本次操作 AI 复盘</div>
      <div class="airv-md">${escapeHtml(summary)}</div>
      ${advice ? `<div class="airv-advice"><span class="cap">AI 建议</span><div>${escapeHtml(advice)}</div></div>` : ''}
    </article>

    <article class="card mt-12">
      <div class="card-eyebrow">铁律对照</div>
      <div class="airv-rules">
        <div>
          <div class="cap dim">通过 (${rulesP.length})</div>
          ${rulesP.length ? rulesP.map(r => {
            const id = (r && r.id) || '?';
            const tx = (r && r.text) || (typeof r === 'string' ? r : '');
            return `<span class="rule-pill pass"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(tx).slice(0, 60)}</span>`;
          }).join('') : '<span class="caption dim">无</span>'}
        </div>
        <div>
          <div class="cap dim">违反 (${rulesF.length})</div>
          ${rulesF.length ? rulesF.map(r => {
            const id = (r && r.id) || '?';
            const tx = (r && r.text) || (typeof r === 'string' ? r : '');
            return `<span class="rule-pill fail"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(tx).slice(0, 60)}</span>`;
          }).join('') : '<span class="caption dim">无</span>'}
        </div>
      </div>
    </article>

    ${(risks.length || improv) ? `
    <article class="card mt-12">
      ${risks.length ? `<div class="airv-risks"><div class="cap dim">关键风险</div>${risks.map(k => `<div class="risk-line">⚠ ${escapeHtml(k)}</div>`).join('')}</div>` : ''}
      ${improv ? `<div class="airv-improv"><div class="cap dim">下一步改进</div><div class="airv-md">${escapeHtml(improv)}</div></div>` : ''}
    </article>` : ''}

    <article class="card mt-12">
      <div class="card-eyebrow flex-between">
        <span>操作</span>
        <span id="airv-status" class="caption dim">${escapeHtml(ts)} · 模型 ${escapeHtml(rev.model || 'MiniMax-M3')}</span>
      </div>
      <div class="flex-row gap-8 mt-8">
        <button class="btn btn-mini primary" data-action="airv-rerun">↻ 强制重跑</button>
        <button class="btn btn-mini" data-action="show-view:review">‹ 返回复盘</button>
      </div>
    </article>
  `;
}

// R-ui-011: 单一 toast 路径 — showToast 直通 toast() 队列, 不再 remove+create 闪屏
// 之前: 复盘每笔完成 → remove + createElement(z-index 9999) 一次, 14 笔就是 14 次闪
// 现在: 复用 drainToast 队列 + 同 kind 相邻去重, 自动节流
function showToast(msg, type) {
  const kind = type === 'success' ? 'success' : type === 'error' ? 'error' : 'info';
  if (typeof toast === 'function') {
    return toast(msg, kind, type === 'error' ? 4000 : 2400);
  }
  // 兜底 (toast 未定义时): 保留老 inline 行为
  if (window.__toastBox) window.__toastBox.remove();
  const colors = { info: '#d4a056', success: '#4fb074', error: '#d97a6c' };
  const box = document.createElement('div');
  box.textContent = msg;
  box.style.cssText = `
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 12px 24px; background: rgba(20,18,14,0.95); color: ${colors[kind] || colors.info};
    border: 1px solid ${colors[kind] || colors.info}; border-radius: 8px;
    font-size: 14px; z-index: 9999; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    max-width: 80vw;
  `;
  document.body.appendChild(box);
  window.__toastBox = box;
  setTimeout(() => { if (box.parentNode) box.remove(); }, 4000);
}

async function _reviewDelete(tradeId) {
  if (!confirm('确认删除这笔交易及其复盘?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades/' + tradeId, { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) _reviewLoadList();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

// R-relax-2026-07-14: 复盘页 next_picks 放宽档状态 — 默认 0 严格,用户点按钮才改
const _RELAX_LABELS = {
  0: { label: '严格', caps: '5 只', desc: '默认 7 条规则全开' },
  1: { label: '放宽', caps: '15 只', desc: '多送候选,screen 拉空时全 A 兜底' },
  2: { label: '极宽松', caps: '50 只', desc: '接近全 A 流动性筛选,适合 market 安静 / 数据源挂时' },
};
let _reviewRelaxLevel = 0;

function _reviewUpdateRelaxInfo() {
  const info = document.getElementById('review-next-relax-info');
  if (!info) return;
  const cfg = _RELAX_LABELS[_reviewRelaxLevel] || _RELAX_LABELS[0];
  info.innerHTML = `当前筛选档:<b>${cfg.label}</b> (${cfg.caps}) — ${cfg.desc}`;
}

let _reviewNextPickToken = 0;

function _reviewRenderPicks(d, listEl, metaEl) {
  if (!d.picks || !d.picks.length) return false;
  if (metaEl) {
    if (d.user_patterns && d.user_patterns.length) {
      metaEl.innerHTML = `⚠ <span style="color:var(--accent)">你的常见错模式:</span> ${d.user_patterns.slice(0, 4).map(p => `<span class="rule-pill fail">${escapeHtml(p)}</span>`).join(' ')}`;
    } else {
      metaEl.textContent = '✅ 暂无历史错模式(继续积累交易后会有更精准预警)';
    }
  }
  listEl.innerHTML = d.picks.map((p, i) => {
    const v = p.ai_verdict || '观望';
    const score = p.ai_score != null ? p.ai_score : '?';
    const risk = (p.risk_warnings || []).map(r => `<span class="rule-pill warn">${escapeHtml(r)}</span>`).join(' ');
    return `<li>
      <span class="np-idx">${i+1}</span>
      <code class="np-code" data-action="open-stock:${p.code}" style="cursor:pointer">${escapeHtml(p.code)}</code>
      <span class="np-name">${escapeHtml(p.name || '—')}</span>
      <span class="np-sector caption dim">${escapeHtml(p.sector || '')}</span>
      <span class="verdict-pill ${escapeHtml(v)}">${escapeHtml(v)} ${score}/100</span>
      <span class="np-risk">${risk}</span>
    </li>`;
  }).join('');
  return true;
}

async function _reviewLoadNextPicks(target = 'review') {
  const listEl = $(`#${target}-next-pick-list`);
  const metaEl = $(`#${target}-next-meta`);
  if (!listEl) return;
  const myToken = ++_reviewNextPickToken;   // 防并发:切档/重复点只认最后一次
  const relax = _reviewRelaxLevel;
  listEl.innerHTML = '<li class="caption dim">后端筛选中 (screen + 错模式预警)…</li>';
  if (metaEl) metaEl.textContent = '—';
  _reviewUpdateRelaxInfo();
  const emptyTip = () => {
    if (myToken !== _reviewNextPickToken) return;
    listEl.innerHTML = `<li class="caption dim">${relax === 0 ? '无候选 · 试试点 [放宽] 拉到 15 只' : '无候选 · 数据源未通 / market 太安静'}</li>`;
    if (metaEl) metaEl.textContent = '';
  };
  try {
    // 1) force=1 触发后台重算,秒回 (computing 或 陈旧缓存)
    let r = await _fetchWithTimeout(`/api/review/next_picks?relax=${relax}&force=1`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    let j = await r.json();
    if (myToken !== _reviewNextPickToken) return;   // 期间用户又切了档
    if (_reviewRenderPicks(j.data || {}, listEl, metaEl)) return;
    // 2) 后台在算 → 轮询 force=0 读缓存,最多 ~24s
    const deadline = Date.now() + 24000;
    while (Date.now() < deadline) {
      await new Promise(res => setTimeout(res, 2500));
      if (myToken !== _reviewNextPickToken) return;
      r = await _fetchWithTimeout(`/api/review/next_picks?relax=${relax}`);
      if (!r.ok) continue;
      j = await r.json();
      if (myToken !== _reviewNextPickToken) return;
      const meta = j.meta || {};
      if (_reviewRenderPicks(j.data || {}, listEl, metaEl)) return;
      if (!meta.computing && !meta.in_flight && !meta.refreshing) { emptyTip(); return; }   // 算完了仍空
    }
    emptyTip();   // 超时兜底
  } catch (e) {
    if (myToken !== _reviewNextPickToken) return;
    listEl.innerHTML = `<li class="caption dim">加载失败: ${escapeHtml(e.message)}</li>`;
  }
}

// R-relax-2026-07-14: 切换 relax 档 → 立即刷新
function _wireReviewRelaxButtons() {
  document.querySelectorAll('.np-relax-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const lv = parseInt(btn.dataset.relax || '0', 10);
      _reviewRelaxLevel = lv;
      document.querySelectorAll('.np-relax-btn').forEach(b => b.classList.toggle('on', b === btn));
      _reviewLoadNextPicks();
    });
  });
}

async function _reviewLoadStats() {
  try {
    const r = await _fetchWithTimeout('/api/review/stats?since_days=90');
    if (!r.ok) return;
    const j = await r.json();
    const d = j.data || {};
    const tiles = [
      { lbl: '已平仓', val: d.closed ?? 0 },
      { lbl: '胜率',   val: d.win_rate != null ? d.win_rate.toFixed(1) + '%' : '—', cls: d.win_rate >= 50 ? 'cell-up' : 'cell-down' },
      { lbl: '平均盈亏', val: d.avg_pnl != null ? (d.avg_pnl > 0 ? '+' : '') + d.avg_pnl.toFixed(2) + '%' : '—', cls: d.avg_pnl > 0 ? 'cell-up' : 'cell-down' },
      { lbl: '最佳', val: d.best ? (d.best.pnl_pct > 0 ? '+' : '') + d.best.pnl_pct.toFixed(2) + '%' : '—', code: d.best?.code, cls: 'cell-up' },
      { lbl: '最差', val: d.worst ? d.worst.pnl_pct.toFixed(2) + '%' : '—', code: d.worst?.code, cls: 'cell-down' },
    ];
    const tradeClickable = t => t.code ? `data-action="open-stock:${escapeHtml(t.code)}" style="cursor:pointer"` : '';
    $('#review-stats').innerHTML = tiles.map(t => `
      <div class="stat-tile" ${tradeClickable(t)}>
        <div class="lbl">${t.lbl}${t.code ? ` · <code style="color:var(--accent);font-size:.7rem">${escapeHtml(t.code)}</code>` : ''}</div>
        <div class="val ${t.cls || ''}">${t.val}</div>
      </div>
    `).join('') + (d.by_pattern && d.by_pattern.length ? `
      <div class="stat-tile" style="grid-column: span 2">
        <div class="lbl">常见错误模式</div>
        <div style="font-size:.85rem; margin-top:.3rem">
          ${d.by_pattern.slice(0, 5).map(p => `<span class="rule-pill fail">${escapeHtml(p.pattern)} ×${p.count}</span>`).join(' ')}
        </div>
      </div>
    ` : '');
  } catch (e) { console.warn('stats load failed', e); }
}

// ─── 截图 / 批量文本 AI 自动录入 (2026-07-11 增强:支持批量) ─────────
const _snapState = {
  running: 0,
  trades: [],   // [{ direction, code, name, price, shares, trade_date, occurred_at, memo, source }]
  thumbSlots: [], // 缩略图卡片列表
};

function _snapNormTime(timeStr) {
  if (!timeStr) return '';
  const m = String(timeStr).match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return '';
  return `${String(m[1]).padStart(2, '0')}:${m[2]}`;
}

function _snapNormDate(dateStr) {
  const s = String(dateStr || '').trim();
  let m;
  if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2})/))) return `${m[1]}-${m[2]}-${m[3]}`;
  if ((m = s.match(/^(\d{4})(\d{2})(\d{2})$/))) return `${m[1]}-${m[2]}-${m[3]}`;
  // 默认今天
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function _snapYMD(dateStr) {
  const n = _snapNormDate(dateStr);
  return n.replace(/-/g, '');
}

function _snapPreviewRender() {
  const box = $('#snap-preview-box');
  const tbody = $('#snap-tbody');
  const cntEl = $('#snap-count');
  const metaEl = $('#snap-meta');
  if (!box || !tbody) return;
  if (!_snapState.trades.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (cntEl) cntEl.textContent = _snapState.trades.length;
  if (metaEl) {
    const ai = _snapState.trades.filter(t => t.source === 'ai').length;
    const ocr = _snapState.trades.filter(t => t.source === 'ocr').length;
    const txt = _snapState.trades.filter(t => t.source === 'text').length;
    const parts = [];
    if (ai)  parts.push(`AI ${ai} 笔`);
    if (ocr) parts.push(`OCR ${ocr} 笔`);
    if (txt) parts.push(`文本 ${txt} 笔`);
    metaEl.textContent = `来源:${parts.join(' / ') || '-'} · 编辑后可点 "全部录入"`;
  }
  tbody.innerHTML = _snapState.trades.map((t, i) => `
    <tr data-idx="${i}">
      <td>${i + 1}</td>
      <td>
        <select class="snap-edit" data-field="direction">
          <option value="buy" ${t.direction === 'buy' ? 'selected' : ''}>买</option>
          <option value="sell" ${t.direction === 'sell' ? 'selected' : ''}>卖</option>
        </select>
      </td>
      <td><input class="snap-edit" data-field="code" value="${String(t.code || '').replace(/"/g,'&quot;')}" maxlength="6"></td>
      <td><input class="snap-edit" data-field="name" value="${String(t.name || '').replace(/"/g,'&quot;')}" maxlength="20"></td>
      <td><input class="snap-edit" data-field="price" type="number" step="0.01" value="${t.price || 0}"></td>
      <td><input class="snap-edit" data-field="shares" type="number" step="100" value="${t.shares || 0}"></td>
      <td><input class="snap-edit" data-field="date" type="date" value="${_snapNormDate(t.trade_date)}"></td>
      <td><input class="snap-edit" data-field="time" placeholder="HH:MM" value="${_snapNormTime(t.occurred_at)}"></td>
      <td><input class="snap-edit" data-field="memo" value="${String(t.memo || '').replace(/"/g,'&quot;')}" maxlength="120"></td>
      <td><span class="src-tag ${t.source === 'ocr' ? 'ocr' : ''}">${t.source === 'ai' ? 'AI' : t.source === 'ocr' ? 'OCR' : '文本'}</span></td>
      <td><button type="button" class="row-del" data-action="del">×</button></td>
    </tr>
  `).join('');

  // 单元格编辑同步到 state
  tbody.querySelectorAll('.snap-edit').forEach(el => {
    el.addEventListener('change', (e) => {
      const tr = e.target.closest('tr');
      const i = parseInt(tr.dataset.idx, 10);
      const f = e.target.dataset.field;
      const t = _snapState.trades[i];
      if (!t) return;
      if (f === 'direction') t.direction = e.target.value;
      else if (f === 'code') t.code = String(e.target.value).replace(/\D/g, '').slice(0, 6);
      else if (f === 'name') t.name = e.target.value.trim();
      else if (f === 'price') t.price = parseFloat(e.target.value) || 0;
      else if (f === 'shares') t.shares = parseInt(e.target.value, 10) || 0;
      else if (f === 'date') {
        t.trade_date = _snapYMD(e.target.value);
        t.occurred_at = (t.occurred_at && _snapNormTime(t.occurred_at))
          ? `${e.target.value}T${_snapNormTime(t.occurred_at)}:00`
          : '';
      }
      else if (f === 'time') {
        t.occurred_at = e.target.value
          ? `${_snapNormDate(t.trade_date)}T${e.target.value}:00`
          : '';
      }
      else if (f === 'memo') t.memo = e.target.value;
    });
  });
  tbody.querySelectorAll('button[data-action="del"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      const i = parseInt(tr.dataset.idx, 10);
      _snapState.trades.splice(i, 1);
      _snapPreviewRender();
    });
  });
}

function _snapAppend(trades, source) {
  if (!Array.isArray(trades) || !trades.length) return 0;
  let n = 0;
  for (const t of trades) {
    if (!t.code && !t.price && !t.shares) continue;
    _snapState.trades.push({
      direction: t.direction || 'buy',
      code: String(t.code || '').slice(0, 6),
      name: t.name || '',
      price: parseFloat(t.price) || 0,
      shares: parseInt(t.shares, 10) || 0,
      trade_date: t.trade_date || _snapYMD(new Date()),
      occurred_at: t.occurred_at || '',
      memo: t.memo || '',
      source,
    });
    n++;
  }
  if (n) _snapPreviewRender();
  return n;
}

function _snapFillFormFromPreview() {
  // 单笔 quick action: 拿第一笔填入"录入新交易"表单
  const t = _snapState.trades[0];
  if (!t) return;
  if (t.direction) $('#rf-direction').value = t.direction;
  if (t.code) $('#rf-code').value = t.code;
  if (t.name) { $('#rf-name').value = t.name; delete $('#rf-name').dataset.autoFilled; }
  if (t.price) $('#rf-price').value = t.price;
  if (t.shares) $('#rf-shares').value = t.shares;
  if (t.trade_date) {
    $('#rf-date').value = _snapNormDate(t.trade_date);
  }
  if (t.occurred_at) {
    const hhmm = _snapNormTime(t.occurred_at);
    if (hhmm) {
      const sel = $('#rf-time');
      if (sel) {
        let found = false;
        for (const o of sel.options) {
          if (o.value === hhmm) { found = true; o.selected = true; break; }
        }
        if (!found) {
          const opt = document.createElement('option');
          opt.value = hhmm; opt.textContent = hhmm + ' (从截图)';
          opt.selected = true;
          sel.appendChild(opt);
        }
      }
    }
  }
  if (t.memo && !$('#rf-memo').value) $('#rf-memo').value = t.memo;
  document.querySelectorAll('#review-form input, #review-form select').forEach(el => {
    if (el.value && el.offsetParent) {
      el.style.transition = 'background .3s';
      el.style.background = 'rgba(74,222,128,0.15)';
      el.classList.add('flash-green');
      setTimeout(() => { el.style.background = ''; el.classList.remove('flash-green'); }, 700);
    }
  });
}

function _reviewBindScreenshot() {
  const drop = $('#snap-drop');
  const inp = $('#snap-file');
  const thumbs = $('#snap-thumbs');
  const status = $('#snap-status');
  const tag = $('#snap-source-tag');
  const tabImg = $('#snap-tab-img');
  const tabText = $('#snap-tab-text');
  const paneImg = $('#snap-pane-img');
  const paneText = $('#snap-pane-text');
  const textArea = $('#snap-text');
  const textParseBtn = $('#snap-text-parse');
  const textExampleBtn = $('#snap-text-example');
  const clearBtn = $('#snap-clear');
  const saveBtn = $('#snap-batch-save');
  if (!drop || drop._bound) return;
  drop._bound = true;
  // 即使已初始化过,也要重新 render 一次空表 (view re-enter)
  _snapPreviewRender();

  const setStatus = (text, cls = '') => {
    if (!status) return;
    status.className = 'snap-status' + (cls ? ' ' + cls : '');
    status.innerHTML = text;
  };

  const parseOneFile = async (file) => {
    _snapState.running++;
    setStatus(`<span class="snap-spinner"></span>AI 解析中: ${file.name}…`, '');
    if (tag) { tag.hidden = false; tag.textContent = '解析中'; tag.style.color = ''; }
    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'shot.png');
      const r = await _fetchWithTimeout('/api/review/parse_trade_image', {
        method: 'POST',
        body: fd,
        timeout: 60_000,
      });
      const j = await r.json();
      if (!j.ok || !j.data || j.data.missing) {
        setStatus('✗ ' + (j.error || '未识别出有效字段'), 'err');
        return { ok: false, err: j.error || 'missing' };
      }
      const trades = j.data.trades || [];
      const source = j.data.source || 'ai';
      const conf = j.data.confidence || 0;
      const added = _snapAppend(trades, source);
      return { ok: !!added, added, source, conf };
    } catch (e) {
      return { ok: false, err: e.message };
    } finally {
      _snapState.running--;
    }
  };

  const parseBatchFiles = async (fileList) => {
    if (!fileList || !fileList.length) return;
    const files = Array.from(fileList);
    for (const f of files) {
      if (f.size > 6 * 1024 * 1024) {
        setStatus(`✗ ${f.name} 超过 6MB,跳过`, 'err');
        continue;
      }
      if (!/^image\/(png|jpe?g|webp)$/i.test(f.type) && !/\.(png|jpe?g|webp)$/i.test(f.name)) {
        setStatus(`✗ ${f.name} 格式不支持`, 'err');
        continue;
      }
      await parseOneFile(f);
    }
    finalizeBatch();
  };

  const finalizeBatch = () => {
    const total = _snapState.trades.length;
    if (!total) {
      setStatus('✗ 没识别出任何有效交易,请手填或换 OCR', 'err');
      if (tag) { tag.textContent = '失败'; tag.style.color = '#f87171'; }
      return;
    }
    const ai  = _snapState.trades.filter(t => t.source === 'ai').length;
    const ocr = _snapState.trades.filter(t => t.source === 'ocr').length;
    setStatus(
      `✓ 共识别出 <b>${total}</b> 笔交易 (${ai} AI + ${ocr} OCR) · 可在表中编辑,再点 "全部录入"`,
      'ok'
    );
    if (tag) {
      tag.textContent = ai ? `🤖 AI ${ai}` : (ocr ? '🔤 OCR' : '已就绪');
      tag.style.color = ai ? '#4ade80' : '#d4a056';
    }
    // 缩略图保留(显示计数);若有单笔且表单为空,可自动填
    if (total === 1 && !$('#rf-code').value) {
      _snapFillFormFromPreview();
    }
  };

  // tab 切换
  const switchTab = (which) => {
    const useImg = which === 'img';
    if (tabImg) tabImg.classList.toggle('active', useImg);
    if (tabText) tabText.classList.toggle('active', !useImg);
    if (paneImg) paneImg.hidden = !useImg;
    if (paneText) paneText.hidden = useImg;
  };
  if (tabImg) tabImg.addEventListener('click', () => switchTab('img'));
  if (tabText) tabText.addEventListener('click', () => switchTab('text'));

  // 一键粘贴示例 — 让用户立即看到格式,降低试用门槛
  if (textExampleBtn && textArea) {
    textExampleBtn.addEventListener('click', () => {
      textArea.value =
        '600519  贵州茅台  buy   1820.50  100  2026-07-11  09:35\n' +
        '002747  埃斯顿   buy    42.00  100  2026-07-11  10:00\n' +
        '300750  宁德时代 sell  320.00  100  2026-07-11  14:30\n' +
        '\n' +
        '# 也可以从券商 App 直接复制粘贴历史成交 (Tab/空格/逗号都行)';
      textArea.focus();
      showToast('已填入示例 — 点「解析 → 预览」试试', 'info', 2200);
    });
  }

  // 文件选择 + 拖放 + 粘贴 → 多文件
  drop.addEventListener('click', () => inp.click());
  inp.addEventListener('change', async (e) => {
    await parseBatchFiles(e.target.files);
    e.target.value = '';
  });
  drop.addEventListener('dragover', (e) => {
    e.preventDefault();
    drop.classList.add('dragover');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', async (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    await parseBatchFiles(e.dataTransfer.files);
  });
  drop.addEventListener('paste', async (e) => {
    const items = e.clipboardData?.items || [];
    const files = [];
    for (const it of items) {
      if (it.kind === 'file' && /^image\//.test(it.type)) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      await parseBatchFiles(files);
    }
  });

  // R12-A: 顶级智能文本解析 — 字段提取而非 split+scan
  // 支持任意分隔符 (空格/Tab/ASCII|/全角｜/中英逗号/顿号)
  // 支持多种时间格式 (HH:MM / HH:MM:SS / HH-MM)
  // 支持多种日期格式 (YYYY-MM-DD / YYYYMMDD / YYYY/M/D / YYYY.M.D / M月D日)
  // 支持中文或英文 direction (任意位置)
  // 智能识别标题行并跳过
  function _smartParseTradeText(raw) {
    const today = _snapYMD(new Date());
    const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#') && !l.startsWith('//'));
    const parsed = [];
    const stats = { header: 0, dedup: 0, noStock: 0, valid: 0 };

    const headerKwRe = /(操作|方向|证券|成交价|成交金额|成交量|股票名|股票代码|代码|名称|价格|时间|金额|数量)/;
    const HEADER_NAMES = /^(|证券|成交价|成交金额|成交量|股票名|股票代码|代码|名称|价格|时间|金额|数量|方向|操作|名称)$/;
    const DIRECTION_RE = /(买入|卖出|买\b|卖\b|\bbuy\b|\bsell\b)/i;
    const TIME_RE = /(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?:[:：]([0-5]\d))?(?!\d)/;
    const DATE_RE = /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2})[:：](\d{1,2})(?:[:：](\d{1,2}))?)?/;
    const DATE_YMD = /(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)/;
    const CODE_RE = /(?<![-\d])([036]\d{5})(?![-\d])/;
    const CN_RUN_RE = /[一-龥]{2,8}/g;
    // R13 关键修复: 优先级问题 — 原正则首支 `\d{1,3}(?:,\d{3})*` 把 `18004.00` 错切成 `180` + `04.00`,
    // 直接吞掉 shares (2800 → 100 fallback)。改为先匹配带小数点的整体,再回退到整数。
    const NUM_RE = /(\d+\.\d+|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+)/g;

    for (const ln of lines) {
      // ── 标题行判定 ──
      const has6digitCode = CODE_RE.test(ln);
      const hasDateTime = DATE_RE.test(ln) || DATE_YMD.test(ln) || TIME_RE.test(ln);
      const hasDirection = DIRECTION_RE.test(ln);
      const chineseRunRatio = (ln.match(/[一-龥]/g) || []).length / Math.max(ln.length, 1);
      // 有方向词 + 6 位代码 → 一定是交易行,跳过表头判定
      if (!has6digitCode && !hasDirection) {
        if (headerKwRe.test(ln)) { stats.header++; continue; }
        if (!hasDateTime && chineseRunRatio > 0.5 && ln.length <= 30) { stats.header++; continue; }
      }

      // ── 方向 (任意位置) ──
      const dirMatch = ln.match(DIRECTION_RE);
      if (!dirMatch) { stats.noStock++; continue; }
      const direction = /卖|sell/i.test(dirMatch[0]) ? 'sell' : 'buy';

      // ── 6 位代码 ──
      let code = '';
      const codeMatch = ln.match(CODE_RE);
      if (codeMatch) code = codeMatch[1];

      // ── 时间戳 ──
      let dateStr = '', timeStr = '';
      const dt = ln.match(DATE_RE);
      if (dt) {
        dateStr = `${dt[1]}-${String(dt[2]).padStart(2,'0')}-${String(dt[3]).padStart(2,'0')}`;
        if (dt[4]) timeStr = `${String(dt[4]).padStart(2,'0')}:${String(dt[5]).padStart(2,'0')}`;
      }
      if (!dateStr) {
        const d2 = ln.match(DATE_YMD);
        if (d2) dateStr = `${d2[1]}-${d2[2]}-${d2[3]}`;
      }
      const tm = ln.match(TIME_RE);
      if (tm && !timeStr) timeStr = `${String(tm[1]).padStart(2,'0')}:${tm[2]}`;
      if (!dateStr) {
        const d3 = ln.match(/(\d{1,2})月(\d{1,2})日?/);
        if (d3) {
          const yr = new Date().getFullYear();
          dateStr = `${yr}-${String(d3[1]).padStart(2,'0')}-${String(d3[2]).padStart(2,'0')}`;
        }
      }
      if (!dateStr) dateStr = today;

      // ── 数字分类: 价格 / 总额 / 股数 ──
      const allNums = [];
      let nm;
      while ((nm = NUM_RE.exec(ln)) !== null) {
        const raw = nm[1].replace(/,/g, '');
        const val = parseFloat(raw);
        if (isNaN(val)) continue;
        const before = ln.slice(Math.max(0, nm.index - 1), nm.index);
        const after = ln.slice(nm.index + nm[1].length, nm.index + nm[1].length + 1);
        if (/[-/:：.]/.test(before) || /[-/:：.]/.test(after)) continue;
        if (code && raw === code) continue;
        allNums.push({ val, idx: nm.index, hasDecimal: /\./.test(raw) });
      }

      let price = 0, total = 0, shares = 0;
      const decimalNums = allNums.filter(n => n.hasDecimal);
      const intNums = allNums.filter(n => !n.hasDecimal);

      // 价格: 第一个小数。如果第 2 个小数 > price×50 → total
      if (decimalNums.length) {
        price = decimalNums[0].val;
        if (decimalNums.length >= 2 && decimalNums[1].val > price * 50) {
          total = decimalNums[1].val;
        }
      }
      // 整数分类
      for (const n of intNums) {
        const v = n.val;
        if (!shares && v >= 100 && v <= 100000 && v % 100 === 0) {
          shares = v;
        } else if (!total && v >= 100) {
          total = v;
        }
      }

      // ── 名字: 方向词后的整段字段 (支持 ASCII 前缀如 "TCL科技" / "ST星云") ──
      let name = '';
      const dirIdx = ln.search(DIRECTION_RE);
      if (dirIdx >= 0) {
        // 取方向词所在位置,以及方向词的结束位置
        const dirEnd = dirIdx + ln.slice(dirIdx).match(DIRECTION_RE)[0].length;
        // 从 dirEnd 后跳过空白 / 分隔符
        let cursor = dirEnd;
        while (cursor < ln.length && /[\s,，|/／、:：]/.test(ln[cursor])) cursor++;
        // 截到第一个数字 / 6 位代码 / 日期为止
        const rest = ln.slice(cursor);
        const stopRe = /(?<![A-Za-z])(?=\d)|(?=\d{4}[-/.\s])/;
        const stopMatch = rest.search(stopRe);
        name = stopMatch > 0 ? rest.slice(0, stopMatch).trim() : rest.trim();
        // 去掉尾部标点
        name = name.replace(/[，,。.\s]+$/, '');
      }
      // 兜底: 旧法 — 最长中文段 (排除方向词)
      if (!name || /^(买入|卖出|买|卖|操作|方向)$/.test(name)) {
        const cnRuns = [];
        let cm2;
        CN_RUN_RE.lastIndex = 0;
        while ((cm2 = CN_RUN_RE.exec(ln)) !== null) {
          if (HEADER_NAMES.test(cm2[0])) continue;
          if (/^(买入|卖出|买|卖|操作|方向|证券)$/.test(cm2[0])) continue;
          cnRuns.push({ text: cm2[0], len: cm2[0].length });
        }
        if (cnRuns.length) {
          cnRuns.sort((a, b) => b.len - a.len);
          name = cnRuns[0].text;
        }
      }

      // ── 兜底 ──
      if (!price && total > 0 && shares > 0) {
        price = Math.round((total / shares) * 100) / 100;
      }
      if (!shares) shares = 100;
      if (!price || !name) { stats.noStock++; continue; }

      // ── 批内去重 ──
      const dedupKey = `${direction}|${code}|${name}|${price}|${shares}|${dateStr}|${timeStr}`;
      if (_parseDedupSet && _parseDedupSet.has(dedupKey)) { stats.dedup++; continue; }
      if (!_parseDedupSet) _parseDedupSet = new Set();
      _parseDedupSet.add(dedupKey);

      parsed.push({
        direction, code, name,
        price, shares, total_amount: total,
        trade_date: _snapYMD(dateStr),
        occurred_at: timeStr ? `${dateStr}T${timeStr}:00` : '',
        memo: '',
      });
      stats.valid++;
    }
    return { trades: parsed, stats };
  }

  // 文本批量解析
  if (textParseBtn) {
    textParseBtn.addEventListener('click', () => {
      const raw = (textArea?.value || '').trim();
      if (!raw) {
        setStatus('✗ 请先粘贴或输入交易行', 'err');
        return;
      }
      _parseDedupSet = new Set();
      const { trades: parsed, stats } = _smartParseTradeText(raw);
      if (!parsed.length) {
        let msg = '✗ 没解析出有效字段';
        if (stats.header) msg += ` · 跳过 ${stats.header} 行标题`;
        if (stats.noStock) msg += ` · ${stats.noStock} 行无法识别`;
        setStatus(msg, 'err');
        return;
      }
      const added = _snapAppend(parsed, 'text');
      const n = _snapState.trades.length;
      if (!added) {
        setStatus('✗ 解析后无有效字段', 'err');
        return;
      }
      const extras = [];
      if (stats.header) extras.push(`跳过 ${stats.header} 行标题`);
      if (stats.dedup) extras.push(`批内去重 ${stats.dedup}`);
      if (stats.noStock) extras.push(`无法识别 ${stats.noStock}`);
      setStatus(`✓ 解析出 <b>${added}</b> 笔 (累计 <b>${n}</b>)${extras.length ? ' · ' + extras.join(' · ') : ''} · 核对后录入`, 'ok');
      if (n === 1) _snapFillFormFromPreview();
    });
  }
  // 清空预览
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      _snapState.trades = [];
      _snapPreviewRender();
      setStatus('已清空 · 可重新上传/粘贴/输入', '');
      if (tag) tag.hidden = true;
    });
  }

  // 批量录入
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      if (!_snapState.trades.length) {
        setStatus('✗ 没有可录入的交易', 'err');
        return;
      }
      saveBtn.disabled = true;
      saveBtn.textContent = '📥 录入中…';
      try {
        // code/name 任一 + price/shares 必须有;后端 _normalize 会用 name 反查 code
        const clean = _snapState.trades
          .map(t => ({
            direction: t.direction || 'buy',
            code: String(t.code || '').replace(/\D/g, '').slice(0, 6).padStart(6, '0'),
            name: t.name || '',
            price: parseFloat(t.price) || 0,
            shares: parseInt(t.shares, 10) || 0,
            total_amount: parseFloat(t.total_amount) || 0,
            occurred_at: t.occurred_at || '',
            trade_date: t.trade_date || _snapYMD(new Date()),
            memo: t.memo || '',
          }))
          .filter(t => (t.code || t.name) && t.price > 0 && t.shares >= 100);
        if (!clean.length) {
          setStatus('✗ 没有可录入的完整记录 (需 code 或 name + 价格 + 股数)', 'err');
          return;
        }
        const r = await _fetchWithTimeout('/api/review/trades', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trades: clean }),
          timeout: 30_000,
        });
        const j = await r.json();
        if (!j.ok) {
          setStatus('✗ 录入失败: ' + (j.error || 'unknown'), 'err');
          return;
        }
        const ok = j.data?.ok || 0;
        const fail = j.data?.fail || 0;
        const total = j.data?.total || clean.length;
        setStatus(`✓ 已录入 <b>${ok}</b>/${total} 笔${fail ? ` · 失败 ${fail} 笔` : ''}`, 'ok');
        showToast(`✓ 批量录入完成 (${ok}/${total})`, 'success');
        // 成功 → 清空成功的,失败保留
        const failedInputs = (j.data?.errors || []).map(e => e.input);
        const failedSet = new Set(failedInputs.map(x => JSON.stringify(x)));
        _snapState.trades = _snapState.trades.filter(t => failedSet.has(JSON.stringify({
          direction: t.direction || 'buy',
          code: String(t.code || '').replace(/\D/g, '').slice(0, 6).padStart(6, '0'),
          name: t.name || '',
          price: parseFloat(t.price) || 0,
          shares: parseInt(t.shares, 10) || 0,
          occurred_at: t.occurred_at || '',
          trade_date: t.trade_date || _snapYMD(new Date()),
          memo: t.memo || '',
        })));
        _snapPreviewRender();
        // 刷新交易明细
        if (typeof _reviewRefreshTrades === 'function') await _reviewRefreshTrades();
        if (typeof _reviewRefreshPortfolio === 'function') await _reviewRefreshPortfolio();
        // 后台批量触发 AI 复盘 (每笔错开 1.2s, 避免瞬时打爆 AI 限频)
        const inserted = j.data?.inserted || [];
        if (inserted.length && typeof _reviewRun === 'function') {
          let delay = 400;
          for (const it of inserted) {
            const tid = it.trade_id;
            if (!tid) continue;
            setTimeout(() => {
              _reviewRun(tid).catch(err => console.warn('batch AI review trade', tid, err));
            }, delay);
            delay += 1200;
          }
          showToast(`🤖 已排队 AI 复盘 ${inserted.length} 笔`, 'info');
        }
      } catch (e) {
        setStatus('✗ 录入请求失败: ' + e.message, 'err');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '📥 全部录入';
      }
    });
  }
}

// 录入表单
function _reviewBindForm() {
  const form = $('#review-form');
  if (!form || form._bound) return;
  form._bound = true;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = $('#rf-code').value.trim();
    const nameInput = $('#rf-name');
    const name = (nameInput.value || '').trim() || null;
    const direction = $('#rf-direction').value;
    const price = parseFloat($('#rf-price').value);
    const shares = parseInt($('#rf-shares').value);
    const memo = ($('#rf-memo').value || '').trim();
    if (!code || !price || !shares) {
      showToast('请填代码、价格、股数', 'error');
      return;
    }
    showToast(`保存中…${code} ${direction} @ ${price}`, 'info');
    try {
      const r = await _fetchWithTimeout('/api/review/trades', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name, direction, price, shares, memo }),
      });
      const j = await r.json();
      if (j.ok) {
        $('#rf-code').value = '';
        nameInput.value = ''; delete nameInput.dataset.autoFilled;
        $('#rf-price').value = ''; $('#rf-shares').value = ''; $('#rf-memo').value = '';
        showToast(`✓ 已记录 trade #${j.data.trade_id} · AI 复盘中…`, 'success');
        _reviewLoadList();
        // 自动跑复盘(后台,不阻塞)
        if (j.data?.trade_id) {
          setTimeout(() => _reviewRun(j.data.trade_id), 300);
        }
      } else {
        showToast(`保存失败: ${j.error || '未知错误'}`, 'error');
      }
    } catch (err) {
      showToast(`保存失败: ${err.message}`, 'error');
      console.error('save trade failed', err);
    }
  });
  // 股票代码联想 — 复用 /api/stock/search
  const codeInput = $('#rf-code');
  const nameInput = $('#rf-name');
  if (codeInput && nameInput) {
    let _searchBox = null;
    let _searchTimer = null;

    function _hideSuggest() {
      if (_searchBox) { _searchBox.remove(); _searchBox = null; }
    }

    function _showSuggest(items) {
      _hideSuggest();
      if (!items || items.length === 0) return;
      _searchBox = document.createElement('div');
      _searchBox.className = 'review-suggest';
      _searchBox.style.cssText = `
        position: absolute; background: rgba(20,18,14,0.98);
        border: 1px solid rgba(212,160,86,0.3); border-radius: 6px;
        max-height: 280px; overflow-y: auto; z-index: 100;
        min-width: 240px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      `;
      items.slice(0, 10).forEach(item => {
        const row = document.createElement('div');
        row.style.cssText = 'padding: 8px 12px; cursor: pointer; font-size: 13px; border-bottom: 1px solid rgba(232,227,216,0.05);';
        row.innerHTML = `<code style="color:#d4a056">${item.code}</code> <span style="color:#e8e3d8">${escapeHtml(item.name || '')}</span>`;
        row.addEventListener('mouseenter', () => row.style.background = 'rgba(212,160,86,0.15)');
        row.addEventListener('mouseleave', () => row.style.background = '');
        row.addEventListener('click', () => {
          codeInput.value = item.code;
          nameInput.value = item.name || '';
          _hideSuggest();
          codeInput.focus();
        });
        _searchBox.appendChild(row);
      });
      // 定位到 codeInput 下方
      const rect = codeInput.getBoundingClientRect();
      _searchBox.style.left = rect.left + 'px';
      _searchBox.style.top = (rect.bottom + 4) + 'px';
      _searchBox.style.position = 'fixed';
      document.body.appendChild(_searchBox);
    }

    codeInput.addEventListener('input', () => {
      clearTimeout(_searchTimer);
      const q = codeInput.value.trim();
      if (!q) { _hideSuggest(); nameInput.value = ''; return; }
      // 用户已填 name 时不打扰
      if (nameInput.value && nameInput.dataset.autoFilled) {
        // 如果继续改 code,清掉 autoFilled
        delete nameInput.dataset.autoFilled;
      }
      _searchTimer = setTimeout(async () => {
        try {
          const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
          if (!r.ok) return;
          const j = await r.json();
          const items = (j.data && j.data.results) || [];
          if (items.length === 1 && items[0].code === q) {
            // 精确匹配 — 直接填 name
            nameInput.value = items[0].name;
            nameInput.dataset.autoFilled = '1';
            _hideSuggest();
          } else {
            _showSuggest(items);
          }
        } catch (e) { /* ignore */ }
      }, 250);
    });

    codeInput.addEventListener('blur', () => {
      // 延迟关闭,让 click 触发
      setTimeout(_hideSuggest, 200);
    });

    // 也支持 name 输入反向查 code(可选)
    nameInput.addEventListener('input', () => {
      if (nameInput.dataset.autoFilled) delete nameInput.dataset.autoFilled;
    });
  }
}

// 切到 review view 时加载
function _reviewOnViewEnter() {
  if (document.querySelector('.view-review:not([hidden])')) {
    _reviewBindForm();
    _reviewBindScreenshot();
    _reviewBindCapital();
    _reviewBindInfer();
    _reviewBindToggle();
    _reviewLoadSettings();
    _reviewLoadPortfolio();
    _reviewLoadList();
    _reviewRefreshIntegrity();                  // R13: 对账 badge
    _reviewLoadNextPicks();
    // 顶部资金栏 + 持仓 15s 刷新 — 离开页面自动停
    if (_reviewState.capTimer) clearInterval(_reviewState.capTimer);
    _reviewState.capTimer = setInterval(() => {
      if (document.querySelector('.view-review:not([hidden])')) {
        _reviewLoadPortfolio();
      }
    }, 15000);
    const btn = $('#review-next-pick-refresh');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', () => _reviewLoadNextPicks());
    }
    // R-relax-2026-07-14: 放宽档按钮绑定 (严格 / 放宽 / 极宽松)
    if (!_wireReviewRelaxButtons._bound) {
      _wireReviewRelaxButtons._bound = true;
      _wireReviewRelaxButtons();
    }
    // R15: 进入页面 — 如果有未复盘的笔,后台并发补齐,逐笔刷新主表,不阻塞浏览
    //  - force=false:已复盘的笔走缓存秒回,未复盘的笔调 LLM (≈60s)
    //  - 用户可点 banner 上的"停"中断
    //  - 离开 view 不停(后台继续跑),再次进入会显示当前进度
    setTimeout(() => _reviewAutoReviewTick(), 600);
  }
}

// R-ui-012: 离开 review view 时清理所有定时器 + abort 进中的 in-flight fetch
// 之前这个 cleanup 不存在,反复切页会 capTimer 等 +1s 一次拉取
function _reviewOnViewLeave() {
  // 1) 顶部资金栏刷新定时器
  if (_reviewState.capTimer) {
    clearInterval(_reviewState.capTimer);
    _reviewState.capTimer = null;
  }
  // 2) 其它 setInterval 一次清掉
  for (const k of Object.keys(_reviewState)) {
    if (/Timer$/i.test(k) && _reviewState[k]) {
      try { clearInterval(_reviewState[k]); clearTimeout(_reviewState[k]); } catch {}
      _reviewState[k] = null;
    }
  }
  // 3) 任何 AbortController 池
  if (_reviewState._inflightAborter) {
    try { _reviewState._inflightAborter.abort(); } catch {}
    _reviewState._inflightAborter = null;
  }
}
_registerViewLeave('review', _reviewOnViewLeave);

// 离开个股页停掉实时轮询 + abort in-flight
function _stockOnViewLeave() {
  try { _stopStockPoll(); } catch {}
  if (window._stockInflightAborter) {
    try { window._stockInflightAborter.abort(); } catch {}
    window._stockInflightAborter = null;
  }
}
_registerViewLeave('stock', _stockOnViewLeave);

// R15: 自动复盘调度 — 状态机
let _reviewAuto = { running: false, queue: [], done: 0, total: 0, startedTs: 0, stop: false };
function _reviewAutoReviewTick() {
  // 不在 review view → 不主动启动,但已运行的允许继续
  if (!document.querySelector('.view-review:not([hidden])')) return;
  const trades = (_reviewState && _reviewState.trades) || [];
  // 只复盘当前 DB 里有 last_review 缺失的笔 (过滤 000000 占位)
  const pending = trades.filter(t => {
    const code = (t.code || '').toString().padStart(6, '0');
    const isPlaceholder = code === '000000' && !(t.name && /[一-龥]/.test(t.name || ''));
    return !isPlaceholder && !t.last_review;
  });
  // 2026-07-14: 用户反馈进入页面 banner 一直显示,即使已全部复盘
  // 先看 pending: 空 → 直接收尾 + 隐藏 banner(忽略 running 状态,允许在跑但无 pending 时收尾)
  if (!pending.length) {
    if (_reviewAuto.running) {
      // 之前有任务在跑但现在没 pending 了,直接收尾
      _reviewAuto.running = false;
      _reviewAuto.queue = [];
      _reviewAutoHideBanner();
    } else {
      _reviewAutoHideBanner();
    }
    return;
  }
  // 已有跑的任务还在 → 不要重启,让现有 worker 继续
  if (_reviewAuto.running) return;
  _reviewAuto = {
    running: true,
    queue: pending.slice(),
    done: 0,
    total: pending.length,
    startedTs: Date.now(),
    stop: false,
  };
  _reviewAutoShowBanner();
  // 启动一次性 integrity check 让 badge 反映开始前的真值,后续不再每笔重打
  _reviewRefreshIntegrity().catch(() => {});
  // 2 路并发 worker
  _reviewAutoRunWorker(0);
  _reviewAutoRunWorker(1);
}
function _reviewAutoRunWorker(workerId) {
  const next = async () => {
    // 用户中途点了"停" → 这条 worker 退出(已 in-flight 的请求让它跑完)
    if (_reviewAuto.stop) return;
    if (!_reviewAuto.queue.length) return;
    const t = _reviewAuto.queue.shift();
    if (!t) return;
    try {
      const r = await _fetchWithTimeout(`/api/review/trades/${t.id}/review?force=false`, { method: 'POST' });
      const j = await r.json();
      if (j.ok && j.data) {
        // R15-fix: 局部更新行 — 不重渲整张表 → 不影响账单 / 持仓 / 浮盈
        _reviewPatchRow(t.id, j.data);
        // 把 review 也写回 _reviewState.trades 内存 (后续汇总/筛选还要用)
        const local = (_reviewState.trades || []).find(x => x.id === t.id);
        if (local) local.last_review = j.data;
        showToast(`✓ #${t.id} 已复盘 · ${j.data.verdict || ''} ${j.data.score || ''}分`.trim(), 'success', 1500);
      } else {
        showToast(`✗ #${t.id} 失败: ${j.error || '?'}`, 'error', 2000);
      }
    } catch (e) {
      showToast(`✗ #${t.id} ${e.message}`, 'error', 2000);
    } finally {
      _reviewAuto.done++;
      _reviewAutoUpdateBanner();
      // R15-fix: 不要每笔都 _reviewLoadList / _reviewRefreshIntegrity — 会闪账单
      // 只在最后一次性刷新
      if (_reviewAuto.done >= _reviewAuto.total) {
        _reviewAutoFinish();
        return;
      }
      next();
    }
  };
  next();
}

// R15-fix: 局部更新单笔 review 信息 (不改行顺序 / 不闪持仓 / 不重算 PnL)
function _reviewPatchRow(tradeId, review) {
  if (!tradeId || !review) return;
  const mm = review.main_mistake || review.mistake_pattern || '';
  // 主行 + 子行 — 用属性 [data-trade-id]
  const rows = document.querySelectorAll(`tr[data-trade-id="${tradeId}"]`);
  rows.forEach(tr => {
    // 行结构: [name, direction, date, price, time, shares, today, cum, cum%, mistake, action]
    //                              0   1   2    3    4    5     6     7    8       9         10
    const tdList = tr.querySelectorAll(':scope > td');
    if (tdList.length >= 11) {
      const mistakeTd = tdList[9];  // mistake pill 列
      if (mistakeTd && mm) {
        const pill = mistakeTd.querySelector('.main-mistake-pill');
        const safe = mm.replace(/</g,'&lt;').replace(/"/g,'&quot;');
        if (pill) {
          pill.textContent = mm;
          pill.title = mm;
        } else {
          mistakeTd.innerHTML = `<span class="main-mistake-pill" title="${safe}">${safe}</span>`;
        }
      }
    }
    // 2) AI 复盘按钮 — 去掉 primary, 文案从 ● 变普通
    const btn = tr.querySelector(`button[data-action="ai-review:${tradeId}"]`);
    if (btn) {
      btn.classList.remove('primary');
      btn.textContent = 'AI 复盘';
    }
  });
}

function _reviewAutoShowBanner() {
  const b = document.getElementById('review-auto-banner');
  if (!b) return;
  b.hidden = false;
  const stopBtn = b.querySelector('.arb-stop');
  if (stopBtn && !stopBtn._bound) {
    stopBtn._bound = true;
    stopBtn.addEventListener('click', () => {
      _reviewAuto.stop = true;
      showToast('已请求停止,正在收尾…', 'info', 2000);
    });
  }
  _reviewAutoUpdateBanner();
}
function _reviewAutoUpdateBanner() {
  const b = document.getElementById('review-auto-banner');
  if (!b || b.hidden) return;
  const dt = Math.round((Date.now() - _reviewAuto.startedTs) / 1000);
  const m = Math.floor(dt / 60), s = dt % 60;
  b.querySelector('.arb-text').textContent =
    `正在后台复盘 ${_reviewAuto.done}/${_reviewAuto.total} 笔 · 已用 ${m}m${s}s · 可继续浏览`;
  b.querySelector('.arb-prog').textContent = '';
}
function _reviewAutoHideBanner() {
  const b = document.getElementById('review-auto-banner');
  if (b) b.hidden = true;
}
function _reviewAutoFinish() {
  if (!_reviewAuto.running) return;  // 防重入
  _reviewAuto.running = false;
  _reviewAuto.queue = [];
  const total = _reviewAuto.total;
  showToast(total > 0 ? `✅ 自动复盘完成 · 共 ${total} 笔` : '✅ 自动复盘完成', 'success', 4000);
  setTimeout(_reviewAutoHideBanner, 6000);
  // R15-fix: 全部完成后再统一刷新一次 (此时不会再闪了,因为只刷一次)
  try { _reviewLoadList(); } catch {}
  try { _reviewRefreshIntegrity(); } catch {}
  try { _reviewLoadPortfolio(); } catch {}
}

// 暴露:被 review bulk 按钮 / 别的流程复用
window.__reviewAutoAPI = { stop: () => { _reviewAuto.stop = true; }, get running() { return _reviewAuto.running; } };

// 切到 review view 时加载 (已通过 showView 钩子触发,这里不重复)
// const _origJump = window.jumpTo; // 项目用 showView,不用 jumpTo — 之前的覆盖无效

// ────────────────────────────────────────────
// WATCHLIST · 自选股池 (2026-07-11)
// ────────────────────────────────────────────
let _watchlistLoaded = false;
let _watchlistItems = [];
let _watchlistBatchRunning = false;

function _watchlistOnViewEnter() {
  if (!document.querySelector('.view-watchlist:not([hidden])')) return;
  _watchlistBindAdd();
  if (!_watchlistLoaded) {
    _watchlistLoaded = true;
    _watchlistLoad();
  } else {
    // 重新进入也要刷新一次 (用户从个股页回来时 watchlist_ai 已写入)
    _watchlistLoad();
  }
  // 集成次日选股 + 错模式预警 (复用 review 的 next_picks endpoint)
  _watchlistBindNextPick();
  _reviewLoadNextPicks('wl');
}

// "次日选股" 卡片按钮 + 防重入
let _wlNextPickLoaded = false;
function _watchlistBindNextPick() {
  const btn = $('#wl-next-pick-refresh');
  if (btn && !btn._bound) {
    btn._bound = true;
    btn.addEventListener('click', () => _reviewLoadNextPicks('wl'));
  }
}

function _watchlistBindAdd() {
  const btn = $('#wl-add-go');
  const input = $('#wl-add-code');
  const hint = $('#wl-add-hint');
  if (!btn || btn._bound) return;
  btn._bound = true;
  const doAdd = async (code, name) => {
    if (!code) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const r = await _fetchWithTimeout('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name }),
      });
      const j = await r.json();
      if (j.ok) {
        showToast(`✓ 已添加 ${j.data.item.name} (${j.data.item.code})`, 'success');
        input.value = '';
        $('#wl-add-results').innerHTML = '';
        _watchlistLoaded = false;
        _watchlistLoad();
        // 自动触发 AI (1.5s 后, 让用户能连续加多只)
        setTimeout(() => _watchlistAnalyzeOne(code, /*silent=*/true), 1500);
      } else {
        showToast(`添加失败: ${j.error || '未知错误'}`, 'error');
      }
    } catch (e) {
      showToast(`添加失败: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '+ 添加 ↗';
    }
  };
  btn.addEventListener('click', async () => {
    const q = (input.value || '').trim();
    if (!q) { showToast('请输入代码或名称', 'error'); return; }
    // 先尝试解析 — 如果是 6 位数字直接加,否则走搜索联想
    if (/^\d{6}$/.test(q)) { doAdd(q); return; }
    try {
      const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
      const j = await r.json();
      const items = (j.data && j.data.results) || [];
      if (items.length === 0) {
        showToast(`没找到 "${q}"`, 'error');
      } else if (items.length === 1) {
        doAdd(items[0].code, items[0].name);
      } else {
        // 多个候选 → 显示列表让用户点
        _wlShowSearchResults(items, (item) => doAdd(item.code, item.name));
      }
    } catch (e) {
      showToast(`搜索失败: ${e.message}`, 'error');
    }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); btn.click(); }
  });
  // 联想 (250ms debounce)
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q || /^\d{6}$/.test(q)) { $('#wl-add-results').innerHTML = ''; return; }
    timer = setTimeout(async () => {
      try {
        const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
        const j = await r.json();
        const items = (j.data && j.data.results) || [];
        _wlShowSearchResults(items, (item) => doAdd(item.code, item.name));
      } catch (e) { /* ignore */ }
    }, 250);
  });
}

function _wlShowSearchResults(items, onPick) {
  const host = $('#wl-add-results');
  if (!host) return;
  if (!items || !items.length) { host.innerHTML = ''; return; }
  host.innerHTML = items.slice(0, 8).map(it => `
    <div class="wl-suggest-row" data-code="${escapeHtml(it.code)}">
      <code style="color:var(--accent)">${escapeHtml(it.code)}</code>
      <span>${escapeHtml(it.name || '')}</span>
      <span class="caption dim">${escapeHtml(it.market || '')}</span>
    </div>
  `).join('');
  host.querySelectorAll('.wl-suggest-row').forEach(row => {
    row.addEventListener('click', () => {
      const item = items.find(x => x.code === row.dataset.code);
      if (item && onPick) onPick(item);
    });
  });
}

async function _watchlistLoad() {
  const tbody = $('#wl-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="12" class="dim center">加载中 …</td></tr>';
  try {
    const r = await _fetchWithTimeout('/api/watchlist');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '加载失败');
    _watchlistItems = (j.data && j.data.items) || [];
    $('#wl-count').textContent = String(_watchlistItems.length);
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    $('#wl-ts').textContent = `更新 ${ts}`;
    _watchlistRender();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="12" class="dim center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function _watchlistRender() {
  const tbody = $('#wl-tbody');
  if (!tbody) return;
  if (!_watchlistItems.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty">📭 自选股池为空 — 在上方添加第一只股票</td></tr>`;
    return;
  }
  tbody.innerHTML = _watchlistItems.map(it => _watchlistRowHtml(it)).join('');
  // 绑定 row 内操作
  tbody.querySelectorAll('[data-wl-remove]').forEach(b => {
    b.addEventListener('click', () => _watchlistRemove(b.dataset.wlRemove));
  });
  tbody.querySelectorAll('[data-wl-ai]').forEach(b => {
    b.addEventListener('click', () => _watchlistAnalyzeOne(b.dataset.wlAi));
  });
  tbody.querySelectorAll('[data-wl-detail]').forEach(b => {
    b.addEventListener('click', () => {
      showView('stock');
      loadStockDetail(b.dataset.wlDetail);
    });
  });
}

function _watchlistRowHtml(it) {
  const code = it.code;
  const name = it.name || code;
  const snap = it.snapshot || {};
  const ai = it.ai;
  const q = it.quote || {};

  // 价格 / 涨幅 cell
  const price = snap.price != null ? fmtN(snap.price, 2) : '—';
  const chgPct = snap.chg_pct;
  const chgCls = chgPct > 0 ? 'up' : chgPct < 0 ? 'down' : 'flat';
  const chgHtml = chgPct != null && Number.isFinite(chgPct)
    ? `<span class="cell-${chgCls}">${(chgPct >= 0 ? '+' : '') + chgPct.toFixed(2)}%</span>`
    : '—';
  const turnover = snap.turnover != null ? `${snap.turnover.toFixed(2)}%` : '—';
  const mainPct = snap.main_pct;
  const retailPct = snap.retail_pct;
  const mainPctHtml = mainPct != null
    ? `<span class="${mainPct >= 30 ? 'cell-up' : mainPct < 20 ? 'cell-down' : 'cell-flat'}">${mainPct.toFixed(1)}%</span>`
    : '—';
  const pct5 = snap.pct_5d;
  const pct10 = snap.pct_10d;
  const pct5Html = pct5 != null
    ? `<span class="${pct5 >= 0 ? 'cell-up' : 'cell-down'}">${(pct5 >= 0 ? '+' : '') + pct5.toFixed(1)}%</span>`
    : '<span class="dim">—</span>';
  const pct10Html = pct10 != null
    ? `<span class="${pct10 >= 0 ? 'cell-up' : 'cell-down'}">${(pct10 >= 0 ? '+' : '') + pct10.toFixed(1)}%</span>`
    : '<span class="dim">—</span>';
  const secZt = snap.sector_zt;
  const secLink = secZt != null
    ? `⚡${secZt}只 <span class="dim">/ ${snap.streak || 0}连板</span>`
    : '<span class="dim">—</span>';

  // AI cell
  let aiCellHtml;
  if (!ai) {
    aiCellHtml = `<button class="btn btn-mini wl-btn-add" data-wl-ai="${escapeHtml(code)}">+ 添加分析</button>`;
  } else {
    const v = ai.verdict || '-';
    const vCls = ({ '买': 'buy', '观望': 'wait', '回避': 'avoid' })[v] || 'na';
    const conv = ai.conviction ?? 0;
    const stale = ai.is_stale ? '<span class="wl-stale-tag" title="跨日判定">昨日</span>' : '';
    aiCellHtml = `
      <div class="wl-ai-cell">
        <span class="verdict-pill v-${vCls}">${escapeHtml(v)} <b>${conv}</b></span>
        ${stale}
        ${ai.summary ? `<p class="wl-ai-summary" title="${escapeHtml(ai.summary)}">${escapeHtml(ai.summary.slice(0, 40))}${ai.summary.length > 40 ? '…' : ''}</p>` : ''}
        <button class="btn btn-tiny wl-btn-reai" data-wl-ai="${escapeHtml(code)}" title="重新 AI">↻</button>
      </div>
    `;
  }

  // 时间窗口 cell
  let windowHtml;
  if (ai && ai.suggested_window) {
    const winCls = ai.suggested_window === '暂观望' ? 'wl-win-wait' :
                   ai.suggested_window === '今早竞价' ? 'wl-win-fast' :
                   ai.suggested_window === '14:00 后' ? 'wl-win-late' : '';
    windowHtml = `<span class="wl-window ${winCls}">${escapeHtml(ai.suggested_window)}</span>`;
    if (ai.entry_price_range) {
      windowHtml += `<div class="wl-entry">入 ${escapeHtml(ai.entry_price_range)}</div>`;
    }
    if (ai.stop_loss) {
      windowHtml += `<div class="wl-stop">止 ${escapeHtml(ai.stop_loss)}</div>`;
    }
  } else {
    windowHtml = '<span class="dim">—</span>';
  }

  return `
    <tr data-code="${escapeHtml(code)}">
      <td><code class="wl-code" data-wl-detail="${escapeHtml(code)}">${escapeHtml(code)}</code></td>
      <td><span data-wl-detail="${escapeHtml(code)}" style="cursor:pointer">${escapeHtml(name)}</span></td>
      <td>${price}</td>
      <td>${chgHtml}</td>
      <td>${turnover}</td>
      <td>${mainPctHtml}${retailPct != null ? `<div class="dim" style="font-size:.7rem">散户 ${retailPct.toFixed(0)}%</div>` : ''}</td>
      <td>${pct5Html}</td>
      <td>${pct10Html}</td>
      <td>${secLink}</td>
      <td>${aiCellHtml}</td>
      <td>${windowHtml}</td>
      <td class="wl-ops">
        <button class="btn btn-tiny" data-wl-ai="${escapeHtml(code)}" title="AI 判定">✨</button>
        <button class="btn btn-tiny" data-wl-detail="${escapeHtml(code)}" title="查看个股">→</button>
        <button class="btn btn-tiny wl-btn-del" data-wl-remove="${escapeHtml(code)}" title="删除">✕</button>
      </td>
    </tr>
  `;
}

async function _watchlistRemove(code) {
  // 2026-07-15: 不再弹 confirm,直接删 + 提示成功
  try {
    const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(code), { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
      showToast(`✓ 已删除 ${code}`, 'success');
      _watchlistLoaded = false;
      _watchlistLoad();
    } else {
      showToast(`删除失败: ${j.error || ''}`, 'error');
    }
  } catch (e) {
    showToast(`删除失败: ${e.message}`, 'error');
  }
}

async function _watchlistAnalyzeOne(code, silent = false) {
  if (!silent) showToast(`AI 判定 ${code} 中 … (建议 20-30 秒)`, 'info');
  const btn = $(`#wl-tbody [data-wl-ai="${code}"]`);
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(code) + '/ai', {
      method: 'POST',
    });
    const j = await r.json();
    if (j.ok && j.data && j.data.ai) {
      showToast(`✓ ${code} AI 完成: ${j.data.ai.verdict || '-'} (${j.data.ai.conviction || 0}/100)`, 'success');
      _watchlistLoad();
    } else {
      if (!silent) showToast(`AI 失败: ${j.error || '未知错误'}`, 'error');
      if (btn) { btn.disabled = false; btn.textContent = '↻'; }
    }
  } catch (e) {
    if (!silent) showToast(`AI 失败: ${e.message}`, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '↻'; }
  }
}

async function _watchlistBatchAI() {
  if (_watchlistBatchRunning) { showToast('已有批量任务进行中', 'info'); return; }
  const items = _watchlistItems.filter(it => !it.ai);
  if (!items.length) {
    showToast('所有股票都已有 AI 建议 · 单击 ↻ 重新判定', 'info');
    return;
  }
  // 2026-07-15: 不再弹 confirm,直接跑
  _watchlistBatchRunning = true;
  const card = $('#wl-batch-card');
  const fill = $('#wl-batch-fill');
  const status = $('#wl-batch-status');
  const log = $('#wl-batch-log');
  card.hidden = false;
  fill.style.width = '0%';
  log.innerHTML = '';
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    status.textContent = `(${i + 1}/${items.length}) 判定 ${it.code} ${it.name}`;
    try {
      const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(it.code) + '/ai', { method: 'POST' });
      const j = await r.json();
      if (j.ok && j.data && j.data.ai) {
        const ai = j.data.ai;
        const item = document.createElement('li');
        item.className = 'wl-batch-ok';
        item.innerHTML = `<b>${escapeHtml(it.code)}</b> ${escapeHtml(it.name)} — <span class="verdict-pill v-${({ '买': 'buy', '观望': 'wait', '回避': 'avoid' })[ai.verdict] || 'na'}">${escapeHtml(ai.verdict)} ${ai.conviction || 0}/100</span>`;
        log.appendChild(item);
      } else {
        const item = document.createElement('li');
        item.className = 'wl-batch-fail';
        item.textContent = `${it.code} 失败: ${j.error || '未知'}`;
        log.appendChild(item);
      }
    } catch (e) {
      const item = document.createElement('li');
      item.className = 'wl-batch-fail';
      item.textContent = `${it.code} 失败: ${e.message}`;
      log.appendChild(item);
    }
    fill.style.width = `${((i + 1) / items.length) * 100}%`;
  }
  status.textContent = `✅ 完成 · 共 ${items.length} 只`;
  _watchlistBatchRunning = false;
  _watchlistLoad();
}

// 一键清空全部自选
async function _watchlistClearAll() {
  if (!_watchlistItems.length) { showToast('自选股池已为空', 'info'); return; }
  // 2026-07-15: 不再弹 confirm,直接清空 + toast 提示
  const n = _watchlistItems.length;
  let ok = 0, fail = 0;
  for (const it of _watchlistItems) {
    try {
      const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(it.code), { method: 'DELETE' });
      const j = await r.json();
      if (j.ok || j.data?.removed) ok++; else fail++;
    } catch (e) { fail++; }
  }
  showToast(`清空完毕: 成功 ${ok} 只` + (fail ? `, 失败 ${fail} 只` : ''), fail ? 'warn' : 'success');
  _watchlistLoaded = false;
  _watchlistLoad();
}

// R-mob-040: 检测 table-wrap 横向溢出 — 容器超宽时加 .has-overflow-x,触发右边缘渐隐
function _initTableOverflowHints() {
  const wraps = document.querySelectorAll('.table-wrap');
  const update = (wrap) => {
    const has = wrap.scrollWidth > wrap.clientWidth + 1;
    wrap.classList.toggle('has-overflow-x', has);
  };
  wraps.forEach(update);
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => wraps.forEach(update), 100);
  });
  if (typeof MutationObserver !== 'undefined') {
    const mo = new MutationObserver(() => wraps.forEach(update));
    wraps.forEach(w => mo.observe(w, { childList: true, subtree: true }));
  }
}

// 初始绑定 (DOMContentLoaded 时执行一次)
document.addEventListener('DOMContentLoaded', () => {
  // 批量 AI 按钮
  const batchBtn = $('#wl-batch-ai');
  if (batchBtn && !batchBtn._bound) {
    batchBtn._bound = true;
    batchBtn.addEventListener('click', _watchlistBatchAI);
  }
  // 刷新行情按钮
  const refBtn = $('#wl-refresh-quote');
  if (refBtn && !refBtn._bound) {
    refBtn._bound = true;
    refBtn.addEventListener('click', () => {
      _watchlistLoaded = false;
      _watchlistLoad();
      showToast('已刷新行情', 'info');
    });
  }
  // 清空全部自选按钮
  const clearBtn = $('#wl-clear-all');
  if (clearBtn && !clearBtn._bound) {
    clearBtn._bound = true;
    clearBtn.addEventListener('click', _watchlistClearAll);
  }
});

// 初始绑定(用户直接打开 review 时)
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(_reviewOnViewEnter, 200);
  // 资金占比轮询由 _reviewOnViewEnter 内的 capTimer 全权负责,这里不再重复 setInterval
  // (之前重复导致每 5s 一次拉取)

  // K线 · 周期切换
  $$('#kline-period .kt-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const days = +btn.dataset.days;
      if (days === klineState.period) return;
      klineState.period = days;
      syncKlineToolbar();
      if (currentStockCode) loadKline(currentStockCode, days);
    });
  });
  // K线 · 指标 toggle (MACD/KDJ 互斥)
  $$('#kline-indicators .kt-chip').forEach(btn => {
    btn.addEventListener('click', () => toggleKlineIndicator(btn.dataset.ind));
  });
});

/* ════════════════════════════════════════════════════════════════════
 * 全 A 风向 · initAllStocks 函数族 (2026-07-14 从 all_stocks.html 迁入)
 * 入口: 监听 view-enter 当 name === 'all_stocks' 时初始化
 * 容器: .view-all_stocks · ID 前缀: as- · 复用全 app shell (sidebar/topbar/ticker)
 * 修复:
 *   1. 真正的 #as-scroll-sentinel 放入 DOM (R16 无限滚动触发)
 *   2. 涨跌额排序 (change_amt) 后端已支持 + dropdown 已加选项
 *   3. 深链 ps/off 状态由 syncUrl (silent) 与 applyAllStocksDeepLink 处理
 *   4. Reset 清 state.pageSize/offset = 30/0,syncUrl 不再写出 ps/off
 *   5. 统一级联: applyAllStocksCascade(layer)
 *   6. 自选写走 POST /api/watchlist (跟读同源)
 *   7. 列显隐用 data-col 属性匹配,不再 textContent
 *   8. 领域由 /api/all_stocks/filters.domains 填充
 *   9. 单 filter UI: 桌面 popup + 移动 placeholder 复用 app shell
 * ════════════════════════════════════════════════════════════════════ */
(function() {
  if (window._allStocksInit) return;
  window._allStocksInit = true;

  // === 0. scope helpers (用 $1 区分 all_stocks 内,$ = app.js 全局) ========
  function $(s, root) { return (root || document).querySelector(s); }
  function $$(s, root) { return Array.from((root || document).querySelectorAll(s)); }
  function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }
  function el(tag, opts, ...children) {
    const e = document.createElement(tag);
    if (opts) Object.entries(opts).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style' && typeof v === 'object') Object.entries(v).forEach(([sk, sv]) => e.style[sk] = sv);
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) e.setAttribute(k, v);
    });
    children.flat().forEach(c => { if (c == null) return; if (typeof c === 'string') e.appendChild(document.createTextNode(c)); else e.appendChild(c); });
    return e;
  }

  // === 1. state ==========================================================
  const state = {
    pageSize: 30,
    offset: 0,
    loadedCount: 0,
    totalAvailable: 0,
    hasMore: true,
    loading: false,
    l1: '', l2: '', l3: '', l4: '', domain: '',
    sort: 'amount', order: 'desc',
    _filterData: null,
    _watchedCodes: new Set(),
    _sentinelObserver: null,
    _qsTimer: null,
    _hiddenCols: new Set(),
    _colLabelsByDataCol: {},
    _initialised: false,
  };

  // === 2. toast ==========================================================
  function toast(msg, type, ms) {
    const t = $('#as-toast');
    if (!t) return;
    t.textContent = msg;
    t.className = `as-toast show ${type || ''}`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.className = 'as-toast'; }, ms || 2400);
  }

  // === 3. fetchJSON =======================================================
  async function fetchJSON(path, params) {
    params = params || {};
    const url = new URL(path, location.origin);
    Object.entries(params).forEach(([k, v]) => {
      if (v == null || v === '') return;
      if (Array.isArray(v)) v.forEach(x => url.searchParams.append(k, x));
      else url.searchParams.set(k, v);
    });
    const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const env = await r.json();
    if (!env.ok) throw new Error(env.error || 'API err');
    return env.data;
  }

  // === 4. computePageSize =================================================
  function computePageSize() {
    const scroll = $('#as-table-scroll');
    if (!scroll) return state.pageSize || 30;
    const containerH = scroll.clientHeight || (window.innerHeight - 380);
    let rowH = 36;
    const sample = $('#as-stocks-tbody tr.stock-row');
    if (sample) rowH = sample.offsetHeight || 36;
    if (!rowH || rowH < 20) rowH = 36;
    const visible = Math.max(8, Math.floor(containerH / rowH));
    return Math.max(15, Math.ceil(visible * 1.5));
  }

  function readMultiSelect(id) {
    const sel = $('#' + id);
    if (!sel) return [];
    return Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  }
  function setMultiSelect(id, vals) {
    const sel = $('#' + id);
    if (!sel) return;
    Array.from(sel.options).forEach(o => o.selected = vals.includes(o.value));
  }

  // === 5. loadFilters =====================================================
  async function loadFilters() {
    try {
      const data = await fetchJSON('/api/all_stocks/filters');
      state._filterData = data;
      // L2 申万
      setSelectOptions('as-l2', data.industries || []);
      // L3 产业链
      setSelectOptions('as-l3', (data.chains || []).map(c => c.name));
      // L4 细分
      setSelectOptions('as-l4', data.l4 || []);
      // 领域 — 优先用后端返回 (Step 2e),fallback 静态兜底
      const domains = data.domains || data.DOMAINS || [
        '机器人本体','机器人零部件','机器视觉','AI 算力','AI 芯片','AI 软件',
        '智能驾驶','半导体','新能源车','传统行业','未分类',
      ];
      setSelectOptions('as-domain', domains);

      // L1 集群 chip
      if (data.clusters && data.clusters.length) {
        renderClusterChips(data.clusters);
      }
    } catch (e) {
      console.warn('loadFilters failed:', e);
    }
  }
  function setSelectOptions(id, items) {
    const sel = $('#' + id);
    if (!sel) return;
    sel.innerHTML = items.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  }

  function renderClusterChips(clusters) {
    const row = $('#as-cluster-row');
    if (!row) return;
    row.innerHTML = '';
    const all = el('span', {
      class: 'cluster-chip' + (state.l1 ? '' : ' active'),
      'data-l1': '',
    }, el('span', { class: 'dot', style: 'background: var(--accent-grad-rainbow);' }), '全部');
    row.appendChild(all);
    clusters.forEach(c => {
      const color = c.color || '#888';
      const chip = el('span', {
        class: 'cluster-chip' + (state.l1 === c.name ? ' active' : ''),
        'data-l1': c.name,
        title: `${c.name} · ${c.desc || ''}\n申万: ${(c.sw_set || []).join(' / ')}`,
        style: { '--cc': color },
      }, el('span', { class: 'dot', style: `background:${color};` }),
        (c.icon ? c.icon + ' ' : '') + c.name);
      row.appendChild(chip);
    });
    $$('.cluster-chip', row).forEach(c => {
      c.addEventListener('click', () => {
        state.l1 = c.dataset.l1 || '';
        // 切 L1: 清 L2/L3/L4/domain (粗筛为主)
        state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
        syncUI();
        syncUrl();
        loadBoard();
        toast(state.l1 ? `已切到 ${state.l1}` : '已重置集群');
      });
    });
  }

  // === 6. 统一级联 (Bug 5) =================================================
  // layer ∈ {'l1','l2','l3','l4','domain'} — 切细层时不再 wipe 粗层
  function applyAllStocksCascade(layer) {
    if (layer === 'l1') {
      // 切 L1 粗筛,清细层
      state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
    } else if (layer === 'l2') {
      // L2 申万 切,保留 L1 联合
      // 语义: L1 ∩ L2 (但当前后端是 l2 → l1, 仍兼容)
    } else if (layer === 'l3') {
      // L3 是细分产业链,选具体后清 L4 / domain
      state.l4 = ''; state.domain = '';
    } else if (layer === 'l4') {
      // L4 是最细分,清 domain
      state.domain = '';
    }
  }

  // === 7. loadBoard (首屏 / 筛选重置) =====================================
  async function loadBoard() {
    if (state.loading) return;
    state.loading = true;
    state.offset = 0;
    state.loadedCount = 0;
    state.hasMore = true;
    state.pageSize = computePageSize();

    const tbody = $('#as-stocks-tbody');
    tbody.innerHTML = renderSkeleton(state.pageSize);
    setSentinel('loading', '首屏加载中…');

    try {
      const data = await fetchJSON('/api/all_stocks/board', {
        page_size: state.pageSize,
        offset: 0,
        l1: state.l1 || '',
        l2: state.l2 || '',
        l3: state.l3 || '',
        l4: state.l4 || '',
        domain: state.domain || '',
        sort: state.sort,
        order: state.order,
        with_fund: true,
      });
      renderRows(data, false);
      state.offset = data.next_offset || (data.items && data.items.length) || 0;
      state.loadedCount = state.offset;
      state.totalAvailable = data.total_available || state.loadedCount;
      state.hasMore = !!data.has_more;
      renderMeta(data);
      updateSentinel();
      // 首屏渲染后,如果样本行替换,重算一次更准的 pageSize
      const newPS = computePageSize();
      if (Math.abs(newPS - state.pageSize) > 4 && state.hasMore) state.pageSize = newPS;
    } catch (e) {
      console.error('loadBoard failed:', e);
      tbody.innerHTML = `<tr><td colspan="20" class="empty">
        <div class="empty-icon">!</div>
        <div class="empty-title">加载失败</div>
        <div>${escapeHtml(e.message)}</div>
        <div class="empty-suggestion">
          <div style="font-size:11px;margin-bottom:6px;">网络受限/限频时,可能需要重试</div>
          <button onclick="window.__initAllStocksLoadBoard && window.__initAllStocksLoadBoard()">重新加载</button>
        </div>
      </td></tr>`;
      setSentinel('idle', '加载失败 — 滚动重试');
    } finally {
      state.loading = false;
    }
  }
  window.__initAllStocksLoadBoard = loadBoard;  // 给 empty-state 按钮调用

  // === 8. loadMore (滚动追加) ==============================================
  async function loadMore() {
    if (state.loading || !state.hasMore) return;
    state.loading = true;
    setSentinel('loading', '加载中…');
    try {
      const data = await fetchJSON('/api/all_stocks/board', {
        page_size: state.pageSize,
        offset: state.offset,
        l1: state.l1 || '',
        l2: state.l2 || '',
        l3: state.l3 || '',
        l4: state.l4 || '',
        domain: state.domain || '',
        sort: state.sort,
        order: state.order,
        with_fund: true,
      });
      renderRows(data, true);
      state.offset = data.next_offset || (state.offset + ((data.items && data.items.length) || 0));
      state.loadedCount = state.offset;
      state.totalAvailable = data.total_available || state.loadedCount;
      state.hasMore = !!data.has_more;
      renderMeta(data);
      updateSentinel();
    } catch (e) {
      console.warn('loadMore failed:', e);
      setSentinel('error', `加载失败 — 滚动重试 (${escapeHtml(e.message)})`);
    } finally {
      state.loading = false;
    }
  }

  // === 9. 滚动哨兵 ========================================================
  function setupSentinelObserver() {
    if (state._sentinelObserver) state._sentinelObserver.disconnect();
    const sentinel = $('#as-scroll-sentinel');
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;
    state._sentinelObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting && !state.loading && state.hasMore) loadMore();
      }
    }, {
      root: $('#as-table-scroll') || null,
      rootMargin: '300px 0px',
      threshold: 0,
    });
    state._sentinelObserver.observe(sentinel);
  }
  function setSentinel(s, text) {
    const el_ = $('#as-scroll-sentinel');
    if (!el_) return;
    el_.dataset.state = s;
    el_.textContent = '';
    const t = el('span', { class: 'ss-text' }, text);
    el_.appendChild(t);
  }
  function updateSentinel() {
    if (!state.hasMore) { setSentinel('done', `已加载全部 ${state.totalAvailable} 只`); return; }
    if (state.loading)  { setSentinel('loading', '加载中…'); return; }
    setSentinel('idle', `滚动加载更多 · 还有 ${state.totalAvailable - state.loadedCount} 只`);
  }

  // === 10. renderSkeleton =================================================
  function renderSkeleton(n) {
    const rows = Math.min(n, 12);
    let html = '';
    for (let i = 0; i < rows; i++) {
      html += `<tr class="skeleton-row">
        <td class="sticky-left"><span class="sk sk-sm"></span></td>
        <td class="sticky-left-2"><span class="sk sk-lg"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
      </tr>`;
    }
    return html;
  }

  // === 11. renderRows =====================================================
  function renderRows(data, append) {
    const items = (data && data.items) || [];
    const tbody = $('#as-stocks-tbody');
    if (!items.length) {
      if (append) return;
      tbody.innerHTML = `<tr><td colspan="20" class="empty">
        <div class="empty-icon">?</div>
        <div class="empty-title">没有符合筛选的股票</div>
        <div>试试放宽筛选条件</div>
        <div class="empty-suggestion">
          <button onclick="document.getElementById('as-btn-reset').click()">清除全部筛选</button>
        </div>
      </td></tr>`;
      return;
    }
    const html = items.map(r => {
      const pct = r.change_pct || 0;
      const pctCls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
      const pctFl  = pct > 0 ? 'flash-up' : (pct < 0 ? 'flash-down' : '');
      const amt = r.change_amt || 0;
      const amtCls = amt > 0 ? 'up' : amt < 0 ? 'down' : '';
      const fund = r.main_fund_inflow_wan || 0;
      const fundCls = fund > 0 ? 'up' : fund < 0 ? 'down' : '';
      const tax = r.taxonomy || {};
      const domains = r.domain || [];
      const domainChip = domains.length
        ? domains.map(d => `<span class="chip chip-domain chip-click" data-goto-domain="${escapeHtml(d)}" title="查看所有「${escapeHtml(d)}」标的">${escapeHtml(d)}</span>`).join('')
        : '<span class="dim">—</span>';
      const l1 = tax.l1
        ? `<span class="chip chip-l1" style="--chip-bg:${tax.l1_color || '#888'}22;--chip-fg:${tax.l1_color || '#888'};border-color:${tax.l1_color || '#888'};" title="L1 集群">${escapeHtml(tax.l1)}</span>`
        : '<span class="dim">—</span>';
      const l2 = tax.l2
        ? `<span class="chip chip-click" data-goto-l2="${escapeHtml(tax.l2)}" title="查看所有 ${escapeHtml(tax.l2)} 标的">${escapeHtml(tax.l2)}</span>`
        : '<span class="dim">—</span>';
      const l3 = tax.l3
        ? `<span class="chip chip-click" data-goto-l3="${escapeHtml(tax.l3)}" title="查看所有 ${escapeHtml(tax.l3)} 标的">${escapeHtml(tax.l3)}${tax.l3_source && tax.l3_source !== 'cache' ? ` <span class="dim" style="font-size:9px;">(${escapeHtml(tax.l3_source)})</span>` : ''}</span>`
        : '<span class="dim">—</span>';
      const l4List = tax.l4 || [];
      const l4 = l4List.length
        ? l4List.map(x => `<span class="chip chip-click" data-goto-l4="${escapeHtml(x)}" title="查看所有 ${escapeHtml(x)} 标的">${escapeHtml(x)}</span>`).join('')
        : '<span class="dim">—</span>';
      const role = tax.role ? `<span class="chip-role role-${escapeHtml(tax.role)}">${escapeHtml(tax.role)}</span>` : '';
      const ztTag = r.zt_today ? `<span class="zt-tag"><span class="zt-icon"></span>涨停</span>`
                  : r.zt_recent ? `<span class="zt-recent" title="近 3 日累计涨停 ${r.zt_recent} 次">${r.zt_recent}日</span>`
                  : '<span class="dim">—</span>';
      const taxSrc = ((tax.l3_source || '').slice(0, 4));
      const srcTag = taxSrc
        ? `<span class="dim" style="font-size:10px;" title="taxonomy 来源: ${escapeHtml(taxSrc)}">${escapeHtml(taxSrc)}</span>`
        : '<span class="dim">—</span>';
      return `<tr class="stock-row" data-code="${escapeHtml(r.code)}" data-name="${escapeHtml(r.name || '')}">
        <td class="cat" data-col="自选"><span class="star-btn" data-star-code="${escapeHtml(r.code)}" data-star-name="${escapeHtml(r.name || '')}" title="加自选"></span></td>
        <td class="cat sticky-left" data-col="代码"><span class="code-link" data-code="${escapeHtml(r.code)}">${escapeHtml(r.code)}</span></td>
        <td class="cat sticky-left-2" data-col="名称"><span class="name">${escapeHtml(r.name || '')}</span></td>
        <td class="cat" data-col="领域">${domainChip}</td>
        <td class="num ${pctCls} ${pctFl}" data-col="涨幅">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
        <td class="num ${amtCls}" data-col="涨跌额">${amt >= 0 ? '+' : ''}${amt.toFixed(2)}</td>
        <td class="num" data-col="换手">${(r.turnover || 0).toFixed(2)}</td>
        <td class="num" data-col="量比">${(r.volume_ratio || 0).toFixed(2)}</td>
        <td class="num" data-col="振幅">${(r.amplitude || 0).toFixed(2)}</td>
        <td class="num cat-mid" data-col="成交额">${(r.amount_yi || 0).toFixed(2)}</td>
        <td class="num cat-mid" data-col="市值">${(r.mcap_yi || 0).toFixed(0)}</td>
        <td class="num cat-mid" data-col="PE">${r.pe_ttm ? r.pe_ttm.toFixed(1) : '—'}</td>
        <td class="num cat-mid ${fundCls}" data-col="主力净流入">${fund >= 0 ? '+' : ''}${fund.toFixed(0)}</td>
        <td class="cat-mid" data-col="L1">${l1}</td>
        <td class="cat-mid" data-col="L2">${l2}</td>
        <td class="cat-mid" data-col="L3">${l3}${role}</td>
        <td class="cat-mid" data-col="L4">${l4}</td>
        <td class="cat-mid" data-col="来源">${srcTag}</td>
        <td class="cat-mid" data-col="涨停">${ztTag}</td>
        <td class="cat-mid" data-col="同链涨停" data-code="${escapeHtml(r.code)}" data-ztchips="1"><span class="zt-chips-placeholder dim" style="font-size:11px">…</span></td>
      </tr>`;
    }).join('');
    if (append) tbody.insertAdjacentHTML('beforeend', html);
    else tbody.innerHTML = html;
    // 追加/替换后立即套列显隐 + 自选染色 + 行内 handler
    if (typeof applyColVisibility === 'function') applyColVisibility();
    if (typeof refreshStarMarks === 'function') refreshStarMarks();
    bindRowHandlers();
    // 2026-07-14: 同链涨停 chips — 触发批量拉取(防重复入队)
    hydrateZtChainChips();
  }

  // ── 2026-07-14: 把所有没注入 chips 的<td data-ztchips> 填上 (防抖 + 空跑优化)
  let _ztHydrateTimer = null;
  function hydrateZtChainChips() {
    if (_ztHydrateTimer) return;
    _ztHydrateTimer = setTimeout(async () => {
      _ztHydrateTimer = null;
      const cells = Array.from(document.querySelectorAll('#as-stocks-tbody td[data-ztchips="1"]'));
      if (!cells.length) return;
      const codes = cells.map(c => c.dataset.code).filter(Boolean);
      const rows = await _ztChainFetch(codes);
      for (const cell of cells) {
        const code = cell.dataset.code;
        const html = _renderZtChainChips(code, { max: 3 });
        // chips 渲染前清掉占位
        cell.innerHTML = html || '<span class="dim" style="font-size:11px">—</span>';
      }
    }, 50);
  }

  // === 12. bindRowHandlers ================================================
  function bindRowHandlers() {
    // ⭐ 加自选 — 统一走 POST /api/watchlist (Bug 6)
    $$('.star-btn', $('#as-stocks-table tbody')).forEach(el_ => {
      el_.addEventListener('click', async (e) => {
        e.stopPropagation();
        const isActive = el_.classList.contains('active');
        const code = el_.dataset.starCode;
        const name = el_.dataset.starName || '';
        if (isActive) {
          // 已自选 → 删除 (2026-07-15: 不再弹 confirm)
          try {
            const resp = await fetch('/api/watchlist/' + encodeURIComponent(code), { method: 'DELETE' });
            const env = await resp.json();
            if (env.ok || env.data?.removed) {
              el_.classList.remove('active');
              state._watchedCodes.delete(code);
              toast(`已删自选 ${code}`, 'ok');
            } else {
              toast('删除失败: ' + (env.error || ''), 'err');
            }
          } catch (err) {
            toast('删除失败: ' + err.message, 'err');
          }
        } else {
          // 未自选 → 添加
          try {
            const resp = await fetch('/api/watchlist', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ code, name, tag: '全A风向' }),
            });
            const env = await resp.json();
            if (env.ok) {
              el_.classList.add('active');
              state._watchedCodes.add(code);
              toast(`已加自选 ${code} ${name}`, 'ok');
            } else {
              toast('加自选失败: ' + (env.error || '未知'), 'err');
            }
          } catch (err) {
            toast('加自选失败: ' + err.message, 'err');
          }
        }
      });
    });
    // 代码 → 个股详情 (新窗口,带 from=all_stocks)
    $$('.code-link', $('#as-stocks-table tbody')).forEach(el_ => {
      el_.addEventListener('click', () => {
        const code = el_.dataset.code;
        window.open(`/?code=${code}&from=all_stocks`, '_blank', 'noopener');
      });
    });
    // L2 / L3 / L4 / 领域 chip 联动 — 切细层不再 wipe L1 (R4)
    function bindGotoLayer(attr, layer) {
      $$(`[data-goto-${attr}]`, $('#as-stocks-table tbody')).forEach(el_ => {
        el_.addEventListener('click', () => {
          state[layer] = el_.dataset[('goto' + attr.charAt(0).toUpperCase() + attr.slice(1)).replace('L', 'L')];
          // compute from dataset
          const val = el_.dataset['goto' + attr.toUpperCase()] || el_.dataset[attr.replace(/^./, c => c)] || '';
          // dataset for "goto-l2" → "gotoL2"
          const key = attr.replace(/-/g, '');
          state[layer] = el_.dataset['goto' + (attr.charAt(0).toUpperCase() + attr.slice(1))];
          applyAllStocksCascade(layer);
          syncUI();
          syncUrl();
          loadBoard();
          toast(`已联动 ${layer.toUpperCase()} = ${state[layer]}`);
        });
      });
    }
    // 简化版(明确层映射):
    function makeGotoBinder(attr, layer, dsKey) {
      $$(`[data-goto-${attr}]`, $('#as-stocks-table tbody')).forEach(el_ => {
        el_.addEventListener('click', () => {
          state[layer] = el_.dataset[dsKey];
          applyAllStocksCascade(layer);
          syncUI(); syncUrl(); loadBoard();
          toast(`已联动 ${layer} = ${state[layer]}`);
        });
      });
    }
    makeGotoBinder('l2', 'l2', 'gotoL2');
    makeGotoBinder('l3', 'l3', 'gotoL3');
    makeGotoBinder('l4', 'l4', 'gotoL4');
    makeGotoBinder('domain', 'domain', 'gotoDomain');
  }

  // === 13. renderMeta + active filters ====================================
  function renderMeta(data) {
    const tookMs = (data && data.took_ms) || 0;
    const count = (data && data.count) || 0;
    const totalUni = (data && data.total_universe) || 0;
    const totalCand = (data && data.total_candidates) || 0;
    const totalAvail = state.totalAvailable || count;
    const loaded = state.loadedCount || count;
    const cacheTag = data && data.cache_hit ? ' · <span style="color:var(--accent)">cache</span>' : '';
    const node = $('#as-meta-count');
    if (node) node.innerHTML =
      `<b>${loaded}</b> / ${totalAvail} 只 · 候选 ${totalCand} / 总池 ${totalUni} · ${(data && data.sort) || state.sort} ${(data && data.order) || state.order} · ${tookMs}ms${cacheTag}`;
    renderActiveFilters();
  }

  function renderActiveFilters() {
    const tags = [];
    if (state.l1) tags.push({ key: 'l1', label: `L1 · ${state.l1}` });
    [[state.l2,'l2','L2'],[state.l3,'l3','L3'],[state.l4,'l4','L4'],[state.domain,'domain','领域']].forEach(([v, key, prefix]) => {
      (v || '').split(',').filter(Boolean).forEach(x => {
        tags.push({ key: `${key}-multi`, label: `${prefix} · ${x}`, val: x });
      });
    });
    const root = $('#as-active-filters');
    if (!root) return;
    root.innerHTML = tags.map(t =>
      `<span class="chip-active" data-key="${t.key}" data-val="${escapeHtml(t.val || '')}">${escapeHtml(t.label)} <span class="x" title="移除">✕</span></span>`
    ).join('');
    $$('#as-active-filters .chip-active .x').forEach(x => {
      x.addEventListener('click', () => {
        const parent = x.parentElement;
        const key = parent.dataset.key;
        const val = parent.dataset.val;
        if (key === 'l1') state.l1 = '';
        else if (key === 'l2-multi') state.l2 = state.l2.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'l3-multi') state.l3 = state.l3.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'l4-multi') state.l4 = state.l4.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'domain-multi') state.domain = state.domain.split(',').filter(v => v && v !== val).join(',');
        syncUI(); syncUrl(); loadBoard();
      });
    });
  }

  // === 14. syncUI =========================================================
  function syncUI() {
    $$('.cluster-chip').forEach(c => c.classList.toggle('active', (c.dataset.l1 || '') === state.l1));
    // 排序
    const sortSel = $('#as-sort');
    if (sortSel) {
      Array.from(sortSel.options).forEach(o => {
        if (o.value === state.sort && o.dataset.order === state.order) sortSel.value = o.value;
      });
    }
    // 多选
    setMultiSelect('as-l2',     state.l2.split(',').filter(Boolean));
    setMultiSelect('as-l3',     state.l3.split(',').filter(Boolean));
    setMultiSelect('as-l4',     state.l4.split(',').filter(Boolean));
    setMultiSelect('as-domain', state.domain.split(',').filter(Boolean));
    // 角标
    [['as-l2',state.l2],['as-l3',state.l3],['as-l4',state.l4],['as-domain',state.domain]].forEach(([id, val]) => {
      const sel = $('#' + id);
      if (!sel) return;
      const group = sel.closest('.filter-group');
      if (!group) return;
      const n = val.split(',').filter(Boolean).length;
      group.classList.toggle('has-active', n > 0);
      const oldBadge = group.querySelector('.count-badge');
      if (oldBadge) oldBadge.remove();
      if (n > 0) {
        const label = group.querySelector('label');
        if (label && !label.querySelector('.count-badge')) {
          label.appendChild(el('span', { class: 'count-badge' }, String(n)));
        }
      }
    });
  }

  // === 15. URL 同步 / 深链 ================================================
  function syncUrl() {
    const params = new URLSearchParams();
    if (state.l1)     params.set('l1',     state.l1);
    if (state.l2)     params.set('l2',     state.l2);
    if (state.l3)     params.set('l3',     state.l3);
    if (state.l4)     params.set('l4',     state.l4);
    if (state.domain) params.set('domain', state.domain);
    if (state.sort !== 'amount' || state.order !== 'desc') {
      params.set('sort',  state.sort);
      params.set('order', state.order);
    }
    // Bug 4: pageSize 只在非默认 30 时写;offset 只在 >0 时写,reset 后默认不写
    if (state.pageSize && state.pageSize !== 30) params.set('ps', state.pageSize);
    if (state.offset > 0)                          params.set('off', state.offset);
    const q = params.toString();
    const newUrl = '#all_stocks' + (q ? '?' + q : '');
    if (location.hash !== newUrl) {
      try { history.replaceState(null, '', '/' + newUrl); } catch (e) {}
    }
  }

  function applyAllStocksDeepLink(qs) {
    const q = new URLSearchParams(qs || '');
    const get = k => q.get(k);
    if (get('l1'))     state.l1     = get('l1');
    if (get('l2'))     state.l2     = get('l2');
    if (get('l3'))     state.l3     = get('l3');
    if (get('l4'))     state.l4     = get('l4');
    if (get('domain')) state.domain = get('domain');
    if (get('sort'))   state.sort   = get('sort');
    if (get('order'))  state.order  = get('order');
    if (get('ps'))     state.pageSize = Math.max(15, parseInt(get('ps')) || 30);
    if (get('off'))    state.offset  = Math.max(0, parseInt(get('off')) || 0);
  }

  // === 16. 排序表头 + 排序 sync ============================================
  function bindSortHeader() {
    $$('#as-stocks-table thead th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const sort = th.dataset.sort;
        let order = th.dataset.order || 'desc';
        if (state.sort === sort) order = state.order === 'desc' ? 'asc' : 'desc';
        state.sort = sort;
        state.order = order;
        th.dataset.order = order;
        const sortSel = $('#as-sort');
        if (sortSel) {
          let matched = false;
          Array.from(sortSel.options).forEach(o => {
            const isMatch = o.value === sort && o.dataset.order === order;
            o.selected = isMatch;
            if (isMatch) matched = true;
          });
          // 后端没该组合的 option 时,加一个临时 option
          if (!matched) {
            const o = document.createElement('option');
            o.value = sort; o.dataset.order = order; o.selected = true;
            o.textContent = `${sort} ${order}`;
            sortSel.appendChild(o);
          }
        }
        syncUrl();
        loadBoard();
        updateSortArrows();
      });
    });
    updateSortArrows();
  }

  function updateSortArrows() {
    $$('#as-stocks-table thead th.sortable').forEach(th => {
      const arrow = th.querySelector('.arrow');
      if (!arrow) return;
      if (th.dataset.sort === state.sort) {
        arrow.textContent = state.order === 'desc' ? '↓' : '↑';
      } else {
        arrow.textContent = '';
      }
    });
  }

  // === 17. bindControls (按钮 + select) ===================================
  function bindControls() {
    // 排序 select
    const sortSel = $('#as-sort');
    if (sortSel) sortSel.addEventListener('change', (e) => {
      const opt = e.target.selectedOptions[0];
      if (!opt) return;
      state.sort = opt.value;
      state.order = opt.dataset.order || 'desc';
      syncUrl(); loadBoard();
      updateSortArrows();
    });

    // 多选 select 改动
    [['as-l2','l2'],['as-l3','l3'],['as-l4','l4'],['as-domain','domain']].forEach(([id, key]) => {
      const sel = $('#' + id);
      if (!sel) return;
      sel.addEventListener('change', () => {
        const vals = readMultiSelect(id);
        state[key] = vals.join(',');
        applyAllStocksCascade(key);
        syncUI(); syncUrl(); loadBoard();
      });
    });

    // 重置
    const resetBtn = $('#as-btn-reset');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.l1 = ''; state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
      state.sort = 'amount'; state.order = 'desc';
      state.pageSize = 30;
      state.offset = 0;
      // 同时清掉快速搜索框 + 触发行过滤
      if (qsInput) {
        qsInput.value = '';
        qsBox.classList.remove('has-value');
        if (qsClear) qsClear.hidden = true;
        applyQuickSearch('');
      }
      syncUI(); syncUrl(); loadBoard();
      toast('已重置所有筛选', 'ok');
    });
    // 刷新
    const refreshBtn = $('#as-btn-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => {
      loadBoard();
      toast('已刷新', 'ok');
    });

    // 快速搜索
    const qsBox = $('#as-quick-search');
    const qsInput = $('#as-qs-input');
    const qsClear = $('#as-qs-clear');
    if (qsInput) {
      qsInput.addEventListener('input', () => {
        const v = qsInput.value.trim();
        qsBox.classList.toggle('has-value', !!v);
        if (qsClear) qsClear.hidden = !v;
        clearTimeout(state._qsTimer);
        state._qsTimer = setTimeout(() => applyQuickSearch(v), 180);
      });
      qsInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); applyQuickSearch(qsInput.value.trim()); }
        if (e.key === 'Escape') {
          qsInput.value = ''; qsBox.classList.remove('has-value');
          if (qsClear) qsClear.hidden = true;
          applyQuickSearch('');
        }
      });
    }
    if (qsClear) qsClear.addEventListener('click', () => {
      qsInput.value = ''; qsBox.classList.remove('has-value'); qsClear.hidden = true;
      applyQuickSearch('');
      qsInput.focus();
    });

    // 列显隐
    setupColToggle();

    // 滚回顶部 FAB (rAF 节流 + passive,避免每帧触发 layout)
    const fab = $('#as-scroll-top-fab');
    const scrollEl = $('#as-table-scroll');
    if (fab && scrollEl) {
      let _fabRafPending = false;
      scrollEl.addEventListener('scroll', () => {
        if (_fabRafPending) return;
        _fabRafPending = true;
        requestAnimationFrame(() => {
          fab.classList.toggle('show', scrollEl.scrollTop > scrollEl.clientHeight * 1.5);
          _fabRafPending = false;
        });
      }, { passive: true });
      fab.addEventListener('click', () => {
        scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }

    // pull-to-refresh (移动端)
    bindPullToRefresh();

    // 移动底部操作栏
    bindMobileActionBar();
  }

  // === 18. applyQuickSearch ===============================================
  function applyQuickSearch(q) {
    q = (q || '').toLowerCase();
    const rows = $$('#as-stocks-tbody tr.stock-row');
    let shown = 0;
    rows.forEach(tr => {
      const code = tr.dataset.code || '';
      const name = (tr.dataset.name || '').toLowerCase();
      const show = !q || code.includes(q) || name.includes(q);
      tr.style.display = show ? '' : 'none';
      if (show) shown++;
    });
    const s = $('#as-scroll-sentinel');
    if (s) {
      if (q && shown === 0 && !state.loading) {
        setSentinel('error', `无匹配 "${q}" 的股票 — 试试更短的关键词`);
      } else if (!q) {
        updateSentinel();
      }
    }
  }

  // === 19. 列显隐 (Bug 7: 用 data-col 而非 textContent) ====================
  function setupColToggle() {
    const wrap = $('#as-col-toggle');
    const btn  = $('#as-col-toggle-btn');
    const menu = $('#as-col-toggle-menu');
    if (!wrap || !btn || !menu) return;

    // Bug 7: 用 thead th data-col 直接收集,不再 textContent 匹配
    const cols = $$('#as-stocks-table thead th[data-col]').map(th => ({
      col: th.dataset.col, label: th.dataset.col,
    }));
    // 显示顺序按 thead 顺序
    state._colLabelsByDataCol = {};
    cols.forEach(c => { state._colLabelsByDataCol[c.col] = c; });

    // 用户偏好
    try {
      const saved = JSON.parse(localStorage.getItem('all_stocks_hidden_cols') || '[]');
      state._hiddenCols = new Set(saved);
    } catch (_) { state._hiddenCols = new Set(); }

    menu.innerHTML = cols.map(c => `
      <div class="col-toggle-row">
        <input type="checkbox" id="ct-${c.col}" data-col="${escapeHtml(c.col)}" ${state._hiddenCols.has(c.col) ? '' : 'checked'} />
        <label for="ct-${c.col}">${escapeHtml(c.label)}</label>
      </div>
    `).join('');

    applyColVisibility();

    menu.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => {
        const col = cb.dataset.col;
        if (cb.checked) state._hiddenCols.delete(col); else state._hiddenCols.add(col);
        try { localStorage.setItem('all_stocks_hidden_cols', JSON.stringify([...state._hiddenCols])); } catch (_) {}
        applyColVisibility();
      });
    });

    btn.addEventListener('click', (e) => { e.stopPropagation(); wrap.classList.toggle('open'); });
    document.addEventListener('click', (e) => {
      if (!wrap.contains(e.target)) wrap.classList.remove('open');
    });
  }

  function applyColVisibility() {
    const showMap = {};
    Object.values(state._colLabelsByDataCol).forEach(c => { showMap[c.col] = !state._hiddenCols.has(c.col); });
    $$('#as-stocks-table thead th').forEach(th => {
      const col = th.dataset.col;
      if (col in showMap) th.style.display = showMap[col] ? '' : 'none';
    });
    $$('#as-stocks-table tbody td[data-col]').forEach(td => {
      const col = td.dataset.col;
      if (col in showMap) td.style.display = showMap[col] ? '' : 'none';
    });
  }

  // === 20. refreshStarMarks ===============================================
  async function loadWatchlistForStars() {
    try {
      const data = await fetchJSON('/api/watchlist');
      const items = (data && data.items) || [];
      state._watchedCodes = new Set(items.map(x => x.code || x));
    } catch (e) {
      console.warn('loadWatchlistForStars failed:', e);
      state._watchedCodes = new Set();
    }
    refreshStarMarks();
  }
  function refreshStarMarks() {
    $$('#as-stocks-tbody .star-btn').forEach(el_ => {
      if (state._watchedCodes.has(el_.dataset.starCode)) el_.classList.add('active');
      else el_.classList.remove('active');
    });
  }

  // === 21. pull-to-refresh ================================================
  function bindPullToRefresh() {
    const isTouch = 'ontouchstart' in window;
    const scroll = $('#as-table-scroll');
    if (!scroll) return;
    let startY = 0, active = false;
    const start = (e) => {
      if (scroll.scrollTop > 5) return;
      startY = (e.touches ? e.touches[0].clientY : e.clientY);
      active = true;
    };
    const move = (e) => {
      if (!active) return;
      const y = (e.touches ? e.touches[0].clientY : e.clientY);
      const dy = y - startY;
      if (dy > 60) {
        active = false;
        showPtrIndicator();
        setTimeout(() => { loadBoard(); hidePtrIndicator(); }, 400);
      }
    };
    const end = () => { active = false; };
    if (isTouch) {
      scroll.addEventListener('touchstart', start, { passive: true });
      scroll.addEventListener('touchmove', move, { passive: true });
      scroll.addEventListener('touchend', end);
    }
  }
  function showPtrIndicator() {
    let ind = $('#ptr-indicator');
    if (!ind) {
      ind = el('div', { id: 'ptr-indicator', class: 'ptr-indicator' },
        el('span', { class: 'ptr-spinner' }), '刷新中…');
      document.body.appendChild(ind);
    }
    ind.classList.add('show');
  }
  function hidePtrIndicator() {
    const ind = $('#ptr-indicator');
    if (ind) ind.classList.remove('show');
  }

  // === 22. table-scroll fade indicator ====================================
  function bindTableScrollIndicator() {
    const card = $('.as-table-card');
    const scroll = $('#as-table-scroll');
    if (!card || !scroll) return;
    function update() {
      const sl = scroll.scrollLeft;
      const max = scroll.scrollWidth - scroll.clientWidth;
      card.classList.toggle('has-scroll-left',  sl > 4);
      card.classList.toggle('has-scroll-right', sl < max - 4);
    }
    let _fadeRafPending = false;
    scroll.addEventListener('scroll', () => {
      if (_fadeRafPending) return;
      _fadeRafPending = true;
      requestAnimationFrame(() => { update(); _fadeRafPending = false; });
    }, { passive: true });
    window.addEventListener('resize', update);
    setTimeout(update, 100);
    const obs = new MutationObserver(() => setTimeout(update, 50));
    obs.observe(scroll, { childList: true, subtree: true });
  }

  // === 23. 移动底部 sheet (复用原 all_stocks.html 逻辑,精简) ================
  function createBottomSheet() {
    const backdrop = el('div', { class: 'bottom-sheet-backdrop' });
    const sheet = el('div', { class: 'bottom-sheet' });
    const head = el('div', { class: 'bottom-sheet-head' },
      el('h3', null, '筛选'),
      el('button', { class: 'bottom-sheet-close', 'aria-label': '关闭' }, '✕'));
    const body = el('div', { class: 'bottom-sheet-body' });
    sheet.appendChild(head); sheet.appendChild(body);
    document.body.appendChild(backdrop);
    document.body.appendChild(sheet);
    function close() {
      backdrop.classList.remove('show');
      sheet.classList.remove('show');
      document.dispatchEvent(new CustomEvent('mab:closed'));
    }
    function show() { backdrop.classList.add('show'); sheet.classList.add('show'); }
    backdrop.addEventListener('click', close);
    head.querySelector('.bottom-sheet-close').addEventListener('click', close);
    return { backdrop, sheet, body, head, close, show };
  }

  function buildFilterSheetHTML() {
    return `
      <div class="sheet-section">
        <div class="sheet-label">集群 (L1)</div>
        <div class="sheet-chips" id="as-sheet-cluster-row"></div>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">申万 (L2)</div>
        <select id="as-sheet-l2" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">产业链 (L3)</div>
        <select id="as-sheet-l3" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">细分 (L4)</div>
        <select id="as-sheet-l4" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">主战场 (领域)</div>
        <select id="as-sheet-domain" class="sheet-select" multiple></select>
      </div>
      <button class="btn sheet-apply">应用筛选</button>
    `;
  }

  function buildSortSheetHTML() {
    const sortDefs = [
      {sort:'amount',order:'desc',label:'成交额 ↓'},
      {sort:'change_pct',order:'desc',label:'涨幅 ↓'},
      {sort:'change_pct',order:'asc',label:'涨幅 ↑'},
      {sort:'change_amt',order:'desc',label:'涨跌额 ↓'},
      {sort:'turnover',order:'desc',label:'换手 ↓'},
      {sort:'volume_ratio',order:'desc',label:'量比 ↓'},
      {sort:'main_fund_inflow',order:'desc',label:'主力净流入 ↓'},
      {sort:'mcap',order:'desc',label:'市值 ↓'},
      {sort:'amplitude',order:'desc',label:'振幅 ↓'},
    ];
    return sortDefs.map(s => {
      const active = state.sort === s.sort && state.order === s.order;
      return `<button class="sheet-sort-row ${active?'active':''}" data-sort="${s.sort}" data-order="${s.order}">
        <span class="sheet-sort-label">${s.label}</span>
        ${active?'<span class="sheet-sort-tick">✓</span>':''}
      </button>`;
    }).join('');
  }

  function bindSheetHandlers(scope) {
    const clusterRow = scope.querySelector('#as-sheet-cluster-row');
    if (clusterRow) {
      const cs = (state._filterData && state._filterData.clusters) || [];
      const allBtn = el('button', {
        class: 'sheet-cluster-chip' + (!state.l1 ? ' active' : ''),
        'data-l1': '',
      }, '全部');
      clusterRow.appendChild(allBtn);
      cs.forEach(c => {
        const b = el('button', {
          class: 'sheet-cluster-chip' + (state.l1 === c.name ? ' active' : ''),
          'data-l1': c.name,
          style: '--cc:' + (c.color || '#888'),
        }, el('span', { class: 'dot', style: `background:${c.color || '#888'}` }), (c.icon || '') + c.name);
        clusterRow.appendChild(b);
      });
      clusterRow.addEventListener('click', (e) => {
        const chip = e.target.closest('.sheet-cluster-chip');
        if (!chip) return;
        clusterRow.querySelectorAll('.sheet-cluster-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        state.l1 = chip.dataset.l1;
        applyAllStocksCascade('l1');
      });
    }
    const fdata = state._filterData;
    if (fdata) {
      fillSheetSelect('as-sheet-l2', fdata.industries || []);
      fillSheetSelect('as-sheet-l3', (fdata.chains || []).map(c => c.name));
      fillSheetSelect('as-sheet-l4', fdata.l4 || []);
      const DOMAINS = (fdata && fdata.domains) || [
        '机器人本体','机器人零部件','机器视觉','AI 算力','AI 芯片','AI 软件',
        '智能驾驶','半导体','新能源车','传统行业','未分类',
      ];
      fillSheetSelect('as-sheet-domain', DOMAINS);
    }
    [['as-sheet-l2','l2'],['as-sheet-l3','l3'],['as-sheet-l4','l4'],['as-sheet-domain','domain']].forEach(([id, key]) => {
      const sel = scope.querySelector('#' + id);
      if (!sel) return;
      Array.from(sel.options).forEach(o => o.selected = (state[key] || '').split(',').filter(Boolean).includes(o.value));
    });
    const apply = scope.querySelector('.sheet-apply');
    if (apply) apply.addEventListener('click', () => {
      state.l2 = readMultiSelectRaw(scope.querySelector('#as-sheet-l2')).join(',');
      state.l3 = readMultiSelectRaw(scope.querySelector('#as-sheet-l3')).join(',');
      state.l4 = readMultiSelectRaw(scope.querySelector('#as-sheet-l4')).join(',');
      state.domain = readMultiSelectRaw(scope.querySelector('#as-sheet-domain')).join(',');
      applyAllStocksCascade('l2');
      applyAllStocksCascade('l3');
      applyAllStocksCascade('l4');
      applyAllStocksCascade('domain');
      syncUI(); syncUrl(); loadBoard();
      scope.querySelector('.bottom-sheet-backdrop').classList.remove('show');
      scope.querySelector('.bottom-sheet').classList.remove('show');
      updateMabBadge();
      toast('筛选已应用', 'ok');
    });
    scope.querySelectorAll('.sheet-sort-row').forEach(r => {
      r.addEventListener('click', () => {
        state.sort = r.dataset.sort;
        state.order = r.dataset.order;
        const sortSel = $('#as-sort');
        if (sortSel) {
          Array.from(sortSel.options).forEach(o => { o.selected = (o.value === state.sort && o.dataset.order === state.order); });
        }
        syncUrl(); loadBoard();
        updateSortArrows();
        const sheetEl = r.closest('.bottom-sheet');
        sheetEl.classList.remove('show');
        document.querySelector('.bottom-sheet-backdrop').classList.remove('show');
        updateMabBadge();
        toast(`已切到 ${r.textContent.replace('✓','').trim()}`, 'ok');
      });
    });
  }
  function fillSheetSelect(id, items) {
    const sel = $('#' + id);
    if (!sel) return;
    sel.innerHTML = items.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  }
  function readMultiSelectRaw(sel) {
    if (!sel) return [];
    return Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  }

  function bindMobileActionBar() {
    const bar = $('#as-mobile-action-bar');
    if (!bar) return;
    let sheet = null;
    function getSheet() { if (!sheet) sheet = createBottomSheet(); return sheet; }
    const filterBtn = $('#as-mab-filters');
    const sortBtn = $('#as-mab-sort');
    const refreshBtn = $('#as-mab-refresh');
    const resetBtn = $('#as-mab-reset');
    if (filterBtn) filterBtn.addEventListener('click', () => {
      const s = getSheet();
      s.head.querySelector('h3').textContent = '筛选';
      s.body.innerHTML = buildFilterSheetHTML();
      // 需要重新解析
      const tmpScope = el('div');
      tmpScope.appendChild(s.body);
      // 把 s.body 的 children 临时挪到 scope
      const tmp = el('div');
      while (s.body.firstChild) tmp.appendChild(s.body.firstChild);
      bindSheetHandlers(tmp);
      while (tmp.firstChild) s.body.appendChild(tmp.firstChild);
      s.show();
    });
    if (sortBtn) sortBtn.addEventListener('click', () => {
      const s = getSheet();
      s.head.querySelector('h3').textContent = '排序';
      s.body.innerHTML = buildSortSheetHTML();
      const tmp = el('div');
      while (s.body.firstChild) tmp.appendChild(s.body.firstChild);
      bindSheetHandlers(tmp);
      while (tmp.firstChild) s.body.appendChild(tmp.firstChild);
      s.show();
    });
    if (refreshBtn) refreshBtn.addEventListener('click', () => { loadBoard(); toast('已刷新', 'ok'); });
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.l1 = ''; state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
      state.sort = 'amount'; state.order = 'desc';
      state.pageSize = 30; state.offset = 0;
      syncUI(); syncUrl(); loadBoard();
      toast('已重置所有筛选', 'ok');
    });
    document.addEventListener('mab:closed', updateMabBadge);
    updateMabBadge();
  }

  function updateMabBadge() {
    const n = [state.l1,
      ...state.l2.split(',').filter(Boolean),
      ...state.l3.split(',').filter(Boolean),
      ...state.l4.split(',').filter(Boolean),
      ...state.domain.split(',').filter(Boolean)].filter(Boolean).length;
    const badge = $('#as-mab-filter-count');
    if (badge) badge.textContent = n > 0 ? String(n) : '';
    const sortLabel = $('#as-mab-sort-label');
    if (sortLabel) {
      const sortNames = {
        amount: '成交额', change_pct: '涨幅', change_amt: '涨跌额',
        turnover: '换手', volume_ratio: '量比', main_fund_inflow: '主力',
        mcap: '市值', amplitude: '振幅',
      };
      sortLabel.textContent = sortNames[state.sort] || '排序';
    }
  }

  // === 24. 智能返回按钮 (R17 兼容 — 走主 app shell smart-back) ===========
  function setupAllStocksBackNav() {
    // 用主 app shell 的 smart-back 风格 — 写入 _prev_page 给详情页读
    try {
      const curr = { url: location.pathname + (location.hash || ''), label: '全 A 风向' };
      sessionStorage.setItem('_curr_page', JSON.stringify(curr));
      window.addEventListener('pagehide', () => {
        try { sessionStorage.setItem('_prev_page', JSON.stringify(curr)); } catch (_) {}
      });
    } catch (_) {}
  }

  // === 25. init ===========================================================
  function init() {
    // 解析深链 hash
    const h = (location.hash || '').replace(/^#/, '');
    if (h.startsWith('all_stocks')) {
      const qs = h.includes('?') ? h.split('?').slice(1).join('?') : '';
      if (qs) applyAllStocksDeepLink(qs);
    }
    setupSentinelObserver();
    setupAllStocksBackNav();
    bindSortHeader();
    bindControls();
    bindTableScrollIndicator();
    syncUI();
    loadWatchlistForStars().then(loadFilters).then(() => {
      syncUI();
      return loadBoard();
    }).then(() => {
      updateSortArrows();
      if (state.l1 || state.l2 || state.l3 || state.l4 || state.domain) {
        toast(`深链已应用: ${[state.l1, state.l2, state.l3, state.l4, state.domain].filter(Boolean).join(' / ')}`);
      }
    });
  }

  // === 26. view-enter 钩子 ================================================
  document.addEventListener('view-enter', (e) => {
    if (!e.detail || e.detail.name !== 'all_stocks') return;
    if (state._initialised) {
      // 重新进入 view — 重新解析 URL 深链 (支持 sidebar 跨页切换)
      const h = (location.hash || '').replace(/^#/, '');
      if (h.startsWith('all_stocks')) {
        const qs = h.includes('?') ? h.split('?').slice(1).join('?') : '';
        applyAllStocksDeepLink(qs);
        syncUI(); loadBoard();
      } else {
        loadBoard();
      }
      return;
    }
    state._initialised = true;
    init();
  });

  // === 27. view-leave cleanup =============================================
  _registerViewLeave('all_stocks', () => {
    if (state._sentinelObserver) {
      state._sentinelObserver.disconnect();
      state._sentinelObserver = null;
    }
    // 不重置 _initialised: 下次再进 view-enter 直接复用 (避免重 bind)
    // 但状态会被 syncUrl 覆盖 — 由 init 解析 hash 重置
  });

  // 暴露到 window 便于 debug / onclick 引用
  window.initAllStocks = init;
})();

