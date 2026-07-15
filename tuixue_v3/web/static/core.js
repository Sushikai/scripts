/* 退学 v3 · 操作台 前端逻辑
 * v2.0 — 信封 / 并发 / SSE / 心法 / AI
 */

// ────────────────────────────────────────────────────────────
// B1: window.TX 命名空间 — 把顶层散落的 globals 收到 TX.core / TX.view.* 下
// 之前 25+ 个 let/const 直接挂 window,容易跟 view 文件冲突 (如 echartsCharts 被声明 2 次)
// 迁移路径: TX.core.* (跨 view 共享状态) / TX.view.{dash,stock,other,all_stocks,screener}.*
// view 文件目前仍直接读 TX.core.*,app.js 仍是 IIFE 但会逐步把 view 文件迁移为命名空间
// ────────────────────────────────────────────────────────────
window.TX = window.TX || { core: {}, view: { dash: {}, stock: {}, other: {}, all_stocks: {}, screener: {} } };
const TX = window.TX;

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
      case 'refresh-dashboard': if (typeof refreshDashboard === 'function') refreshDashboard(); break;
      case 'open-stock':        if (typeof showView === 'function') showView('stock'); if (typeof loadStockDetail === 'function') loadStockDetail(arg); break;
      case 'show-view':         if (typeof showView === 'function') showView(arg); break;
      case 'review-run':        if (typeof _reviewRun === 'function') _reviewRun(arg); break;
      case 'review-delete':     if (typeof _reviewDelete === 'function') _reviewDelete(arg); break;
      case 'ai-review':         if (typeof openAiReview === 'function') openAiReview(arg); break;
      case 'toggle-seat-detail':if (typeof toggleSeatDetail === 'function') toggleSeatDetail(el); break;
      case 'airv-rerun':        if (typeof _airvRunLLM === 'function') _airvRunLLM(true); break;
      case 'review-delete-position':
        if (typeof _reviewDeletePosition !== 'function') break;
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
  // R-a11y: Enter/Space 激活 sidebar 导航项
  if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('[data-jump]')) {
    e.preventDefault();
    const el = e.target.closest('[data-jump]');
    const view = el.dataset.jump;
    if (view && typeof showView === 'function') {
      showView(view);
      if (window.matchMedia('(max-width: 979px)').matches) _closeSidebar();
    }
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
var _debugPanelTimer = null;
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
var ACCENT = '#d4a056';
var UP     = '#e84545';
var DOWN   = '#34c759';
var INK    = '#e8e3d8';
var INK2   = '#a8a39a';
var INK3   = '#6b6660';
var GRID   = 'rgba(232,227,216,0.06)';

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
// ECharts 轴色 — 跟随主题
var CHART_LINE        = '#B8B0A8';
var CHART_TOOLTIP_BG  = '#FFFFFF';
var CHART_TOOLTIP_BORDER = '#DDD8D0';
function refreshChartColors() {
  CHART_LINE        = _cssVar('--chart-line')        || '#B8B0A8';
  CHART_TOOLTIP_BG  = _cssVar('--chart-tooltip-bg')  || '#FFFFFF';
  CHART_TOOLTIP_BORDER = _cssVar('--chart-tooltip-border') || '#DDD8D0';
}
refreshThemeColors();
refreshChartColors();

var echartsCharts = {};
TX.core.echartsCharts = echartsCharts;   // B1: 共享 chart 注册表,view 文件可直接读 TX.core.echartsCharts
var lastRefreshTs = 0;

// ────────────────────────────────────────────
// fetch wrapper — 自动解包 {ok,data,error,ts}
// 每个请求都有 timeout + AbortController，避免后端慢导致前端卡死
// ────────────────────────────────────────────
var API_TIMEOUTS = {
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

// R-sec-2026-07-15: 给 admin / destructive 端点自动注入 X-Admin-Token
// localStorage key 'tuixue-admin-token' — 用户首次访问被 401 时 console 提示
// 后端从 env TUIXUE_ADMIN_TOKEN 读,默认未配置时放行 (开发模式兼容)
const _ADMIN_PROTECTED_PREFIXES = [
  '/api/admin/',
  '/api/review/trades_all',
  '/api/review/positions/',
  '/api/review/trades/',     // DELETE 单笔 (PUT 不需要)
];
function _adminTokenHeader(path) {
  if (!path || !path.startsWith('/api/')) return null;
  // DELETE 才需要 token,GET/POST 由后端单独控制
  // 这里简化:命中前缀且 method 是 DELETE / admin 类 → 加 token
  const isProtected = _ADMIN_PROTECTED_PREFIXES.some(p => path.startsWith(p));
  if (!isProtected) return null;
  const tok = (() => { try { return localStorage.getItem('tuixue-admin-token') || ''; } catch { return ''; } })();
  if (!tok) {
    // 首次无 token 时 console 提示一次 (避免每次刷屏)
    if (!window._adminTokenWarned) {
      window._adminTokenWarned = true;
      console.warn('[auth] admin 端点需要 X-Admin-Token,后端已设 TUIXUE_ADMIN_TOKEN.\n设置方法: localStorage.setItem("tuixue-admin-token", "<env 值>")');
    }
    return null;
  }
  return { 'X-Admin-Token': tok };
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
          // R-sec-2026-07-15: admin / destructive 端点自动注入 X-Admin-Token
          // 后端从 env TUIXUE_ADMIN_TOKEN 读,前端从 localStorage 'tuixue-admin-token' 读
          // 首次访问被 401 时,console 提示用户设置
          ...(_adminTokenHeader(path) || {}),
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
var _ZT_CHAIN_CACHE_TTL_MS = 60 * 1000;
var _ztChainCache = new Map();   // code -> { ts, rows: [...] }
var _ztChainPendingPromise = null;   // 防止同时 N 个 page 触发 N 次 /api/limitup/per_code
var _ztChainPendingBuf = new Set();

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
  return 'var(--ink-3)';        // 0 灰
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
      ${r.is_mainline ? '<span style="color:var(--star-gold)">⚡</span>' : ''}
    </span>`;
  }).join('') + (more > 0 ? `<span style="font-size:10px;color:var(--ink-3);margin-right:4px">+${more}</span>` : '');
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
    // 请求成功 → 标记在线 (清除 pending 降级定时器)
    clearTimeout(_markOfflineOnApiErr._t);
    if (_kaState !== 'ok') _setNetworkStatus('ok');
  } catch (e) {
    clearTimeout(_bar); _hideTopProgress();
    // 网络错误 → 更新状态 (不抛到 toast,给 _markOfflineOnApiErr 处理)
    _markOfflineOnApiErr(path, e);
    // B10: 默认情况下统一 toast 错误,除非 opts.silent=true (需要精细控制时)
    if (!opts.silent && typeof toast === 'function') {
      const msg = e.name === 'AbortError'
        ? `请求超时: ${path}`
        : `加载失败: ${path}\n${e.message || e}`;
      toast(msg, 'error', 3500);
    }
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
var _topProgTimer = null;
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
var _kaState = 'idle';  // 'idle' | 'sending' | 'offline'
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
// 由 api() 内部调用,标记离线状态 (延迟降级防抖动)
function _markOfflineOnApiErr(_path, e) {
  if (_isRetryableError(e) || /timeout|网络|fetch/i.test(String(e?.message))) {
    _setNetworkStatus('switching');
    clearTimeout(_markOfflineOnApiErr._t);
    _markOfflineOnApiErr._t = setTimeout(() => _setNetworkStatus('offline'), 1500);
  }
}

// ────────────────────────────────────────────
// 网络状态条 — 把 _kaState / online 事件映射到 DOM pill
//   ok        绿色 dot + "在线"
//   switching 橙色 dot + "切换中"   (短抖动中,1.5s 没恢复就降为 offline)
//   offline   红色 dot + "断线"
// ────────────────────────────────────────────
var _netPill = () => document.getElementById('net-pill');
var _netText = () => document.getElementById('net-text');
var _NET_LABELS = {
  ok: '在线',
  switching: '切换中',
  offline: '断线 · 自动重连',
};
var _netHideTimer = null;
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
var toastEl = $('#toast');
var _toastQueue = [];
var _toastActive = false;
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
var _topLoadingEl = $('#top-loading');
var _topLoadingCount = 0;
var _topLoadingLabel = '';
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
// C3: 全屏 loading overlay — 长操作(>5s)用,比顶部条更明显
// 用法: showLoadingOverlay('回测中…', '最多 90s'); hideLoadingOverlay();
// 引用计数:多个并行操作叠加,全部 hide 后才真正隐藏
// ────────────────────────────────────────────
let _overlayCount = 0;
function showLoadingOverlay(label, sub) {
  _overlayCount++;
  const el = document.getElementById('loading-overlay');
  if (!el) return;
  if (label) {
    const lbl = document.getElementById('loading-overlay-label');
    if (lbl) lbl.textContent = label;
  }
  if (sub !== undefined) {
    const subEl = document.getElementById('loading-overlay-sub');
    if (subEl) subEl.textContent = sub;
  }
  el.hidden = false;
  el.setAttribute('aria-hidden', 'false');
}
function hideLoadingOverlay() {
  _overlayCount = Math.max(0, _overlayCount - 1);
  if (_overlayCount > 0) return;
  const el = document.getElementById('loading-overlay');
  if (!el) return;
  el.hidden = true;
  el.setAttribute('aria-hidden', 'true');
}
// 强制清零(出错路径 / cancel)— 不管 _overlayCount,直接隐藏
function forceHideLoadingOverlay() {
  _overlayCount = 0;
  const el = document.getElementById('loading-overlay');
  if (!el) return;
  el.hidden = true;
  el.setAttribute('aria-hidden', 'true');
}

// ────────────────────────────────────────────
// 视图切换
// ────────────────────────────────────────────
var _currentViewName = null;
var _currentStockCode = null;
// B1: 在 TX.core 上提供稳定别名 — 多个 view 文件用 `TX.core.currentViewName` 读,
// 不再用 window._currentViewName (老引用仍兼容)
TX.core.currentViewName  = () => _currentViewName;
TX.core.currentStockCode = () => _currentStockCode;
function showView(name) {
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
  if (name === 'dash' && typeof refreshTicker === 'function') refreshTicker();
  if (name === 'optimize' && typeof loadReports === 'function') loadReports();
  if (name === 'laws' && typeof renderLawsOnce === 'function') renderLawsOnce();
  if (name === 'review' && typeof _reviewOnViewEnter === 'function') _reviewOnViewEnter();
  if (name === 'ai-review' && typeof _airvOnViewEnter === 'function') _airvOnViewEnter();
  _currentViewName = name;
  // R5: 写 hash 便于深链 & 浏览器后退
  const curHash = location.hash.replace(/^#/, '');
  const want = (name === 'stock' && _currentStockCode) ? `stock=${_currentStockCode}` : name;
  if (curHash !== want && curHash.split('=')[0] !== name) {
    try { history.replaceState(null, '', '#' + want); } catch (e) {}
  }
  // 触发全局 view-enter 事件,R5 解耦各模块初始化
  document.dispatchEvent(new CustomEvent('view-enter', { detail: { name } }));
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// R-ui-012: view 离开钩子注册表 - { 'view-name': () => { ... cleanup ... } }
// 比让每个 enter hook 兼任 leave 更不容易漏 (之前 capTimer/flowsTimer
// 在反复切页时无限累加, +1s 一次拉取 → 服务端被拖垮)
var _VIEW_LEAVE_HOOKS = {};
TX.core.viewLeaveHooks = _VIEW_LEAVE_HOOKS;   // B1: view 文件用 TX.core.registerViewLeave 替代
function _registerViewLeave(name, fn) { _VIEW_LEAVE_HOOKS[name] = fn; }
TX.core.registerViewLeave = _registerViewLeave;

// R5: hash 路由已在 app.js 中完整实现(含 push:false 参数 + boot ?code= 深链)
// core.js 只保留 showView 供 app.js 覆盖,不注册 hashchange / setTimeout 以免双发
// (之前 core.js 和 app.js 各注册一次,导致 routeFromHash 跑 2 遍:首屏 dash→stock 闪烁 + 多余 API 请求)

// ────────────────────────────────────────────
// R-a11y-2026-07-15: 数据表无障碍 — 自动加 scope / caption / aria-sort
// 之前所有 <th> 缺 scope="col", 排序列缺 aria-sort, 屏幕阅读器读到一片无意义 TD
// 运行时增强 — 不需要手动改每个 <th>
// ────────────────────────────────────────────
function _enhanceTableA11y(table) {
  if (!table || table.dataset.a11yEnhanced === '1') return;
  table.dataset.a11yEnhanced = '1';
  // 1) caption: 用最近的 .card-eyebrow / h3 / table[aria-label] / table.id
  if (!table.querySelector('caption')) {
    const card = table.closest('.card');
    const eyebrow = card?.querySelector('.card-eyebrow')?.textContent?.trim();
    const h3 = card?.querySelector('.card-h, .card-title')?.textContent?.trim();
    const captionText = eyebrow || h3 || table.getAttribute('aria-label') || table.id || '数据表';
    const cap = document.createElement('caption');
    cap.className = 'sr-only';
    cap.textContent = captionText;
    table.insertBefore(cap, table.firstChild);
  }
  // 2) <th scope="col"> 缺则补
  const ths = table.querySelectorAll('thead th');
  ths.forEach(th => {
    if (!th.hasAttribute('scope')) th.setAttribute('scope', 'col');
    // 3) aria-sort — 由排序列的 .active.desc/.asc 类同步
    if (th.classList.contains('sortable') || th.dataset.sort) {
      if (!th.hasAttribute('aria-sort')) {
        const isActive = th.classList.contains('active');
        const isAsc = th.classList.contains('asc');
        th.setAttribute('aria-sort', isActive ? (isAsc ? 'ascending' : 'descending') : 'none');
      }
    }
  });
}
function _enhanceAllTables(root) {
  root = root || document;
  // 限定主内容,避免改到 sw / 离线提示里的 table
  const main = document.querySelector('main.canvas') || document.body;
  main.querySelectorAll('table.data-table, table.scr-table, table.stocks-table, table.bt-win-table, table.bt-trade-table, table.bd-cats, table#review-table, table#wl-table, table#scr-table, table#as-stocks-table, table#seats-table, table#holders-table, table#dragons-all-table, table#bt-monthly, table#sector-zt-table, table#sector-5d-table, table#flow-detail-table, table#snap-tbl').forEach(_enhanceTableA11y);
}
// 首屏跑一次 + 视图切换后跑一次 (有些表是动态渲染的,得在 DOM 就绪后增强)
_enhanceAllTables();
document.addEventListener('view-enter', () => setTimeout(_enhanceAllTables, 50));
// 监听动态插入的 table — MutationObserver
const _a11yObs = new MutationObserver(muts => {
  for (const m of muts) {
    for (const node of m.addedNodes) {
      if (node.nodeType !== 1) continue;
      if (node.tagName === 'TABLE') _enhanceTableA11y(node);
      else if (node.querySelectorAll) {
        node.querySelectorAll('table').forEach(_enhanceTableA11y);
      }
    }
  }
});
if (document.body) {
  _a11yObs.observe(document.body, { childList: true, subtree: true });
} else {
  document.addEventListener('DOMContentLoaded', () => _a11yObs.observe(document.body, { childList: true, subtree: true }));
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

var fmtN = (n, d = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
};
var fmtPct = (n, d = 2) => {
  if (n === null || n === undefined || isNaN(n)) return '—';
  const v = Number(n);
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(d)}%`;
};
var fmtAmt = (n) => {
  if (n === null || n === undefined) return '—';
  const v = Number(n);
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + ' 亿';
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + ' 万';
  return v.toFixed(0);
};
var colorFor = (v) => v > 0 ? UP : (v < 0 ? DOWN : INK2);

