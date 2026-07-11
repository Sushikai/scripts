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
      const resp = await fetch(path, { ...opts, signal: ctrl.signal });
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

async function api(path, opts) {
  opts = opts || {};
  let r;
  try {
    r = await _fetchWithTimeout(path, opts);
  } catch (e) {
    if (e.name === 'AbortError') {
      const t = (opts.timeout || _timeoutFor(path)) / 1000;
      throw new Error(`请求超时 (${t}s): ${path}`);
    }
    throw e;
  }
  let env;
  try { env = await r.json(); }
  catch { throw new Error(`HTTP ${r.status} (非 JSON)`); }
  if (!env.ok) throw new Error(env.error || `HTTP ${r.status}`);
  return env.data;
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
// toast
// ────────────────────────────────────────────
const toastEl = $('#toast');
let toastTimer = null;
function toast(msg, kind = 'info', ms = 2400) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.className = `toast toast-${kind}`;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, ms);
}

// ────────────────────────────────────────────
// 视图切换
// ────────────────────────────────────────────
function showView(name) {
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
}

// ────────────────────────────────────────────
// 数字 / 颜色格式化
// ────────────────────────────────────────────
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
async function refreshTicker() {
  const bar = $('#tickerbar');
  try {
    const data = await api('/api/market/overview');
    lastRefreshTs = data.ts || Date.now() / 1000;
    const indices = data.indices || [];
    const fragments = indices.map(i => {
      const c = colorFor(i.change_pct);
      return `<span class="tk-item">
        <span class="tk-name">${i.name}</span>
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
    // 并发拉三市场信号 + 热门板块(失败互不影响)
    Promise.allSettled([loadDashboardSignal(), loadHotSectors()]).catch(() => {});
  } catch (e) {
    bar.innerHTML = '<div class="ticker-empty">市场数据暂不可达 · ' + e.message + '</div>';
  }
}

// ── 三市场信号面板 (首页) ─────────────────────────────────
const _VERDICT_LABEL = { allow: '适合买入', cautious: '谨慎参与', block: '不适合买入' };
const _VERDICT_DOT   = { allow: '🟢', cautious: '🟡', block: '🔴' };

function _paintSignalCol(prefix, payload) {
  if (!payload) return;
  const v = payload.verdict || 'cautious';
  const verdictEl = $(`#sig-${prefix}-verdict`);
  if (verdictEl) {
    verdictEl.className = `signal-verdict signal-${v}`;
    verdictEl.textContent = _VERDICT_LABEL[v] || '—';
  }
  const pctEl = $(`#sig-${prefix}-pct`);
  if (pctEl) {
    const p = Number(payload.change_pct) || 0;
    pctEl.className = 'sig-pct ' + (p > 0.05 ? 'up' : p < -0.05 ? 'down' : 'flat');
    pctEl.textContent = (p > 0 ? '+' : '') + p.toFixed(2) + '%';
  }
  const headEl = $(`#sig-${prefix}-head`);
  if (headEl) headEl.textContent = payload.headline || '—';
  const listEl = $(`#sig-${prefix}-news`);
  if (listEl) {
    const ws = payload.warnings || [];
    listEl.innerHTML = ws.length
      ? ws.map(w => `<li class="warn">${escapeHtml(w)}</li>`).join('')
      : '<li>· 无不利信号</li>';
  }
}

async function refreshDashboard() {
  return Promise.allSettled([loadDashboardSignal(), loadHotSectors()]);
}

async function loadDashboardSignal() {
  // 用 fetch 直读 — 接受 ok=false 的降级数据,这样上游超时/限频时仍能看到 verdict;
  // 完全无数据时才显示"数据暂不可达 + 重试"。
  let env;
  try {
    const r = await _fetchWithTimeout('/api/dashboard/signal');
    env = await r.json();
  } catch (e) {
    _paintSignalError('a', e.message || '网络错误');
    _paintSignalError('kr', e.message || '网络错误');
    _paintSignalError('us', e.message || '网络错误');
    return;
  }
  const d = env.data || {};
  if (!env.ok || !d.a_share || !d.kr || !d.us) {
    const msg = (env.error || '数据暂不可达') + ' · 点此重试';
    _paintSignalError('a', msg);
    _paintSignalError('kr', msg);
    _paintSignalError('us', msg);
    return;
  }
  _paintSignalCol('a', d.a_share);
  _paintSignalCol('kr', d.kr);
  _paintSignalCol('us', d.us);
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
    headEl.innerHTML = `<span class="retry-link" onclick="refreshDashboard()">${escapeHtml(msg)}</span>`;
  }
  const listEl = $(`#sig-${prefix}-news`);
  if (listEl) listEl.innerHTML = '';
}

async function loadHotSectors() {
  // 直读 — 同 loadDashboardSignal 风格,接受 ok=false 的降级数据
  let env;
  try {
    const r = await _fetchWithTimeout('/api/dashboard/hot_sectors');
    env = await r.json();
  } catch (e) {
    const host = $('#hot-sectors-tiles');
    const sub = $('#hot-sectors-sub');
    if (host) host.innerHTML = `<div class="hs-empty"><span class="retry-link" onclick="refreshDashboard()">网络错误 · ${escapeHtml(e.message || '')} · 点此重试</span></div>`;
    if (sub) sub.textContent = '';
    return;
  }
  const d = env.data || {};
  const tiles = d.mainline || [];
  const host = $('#hot-sectors-tiles');
  const sub  = $('#hot-sectors-sub');
  if (!host) return;
  if (!env.ok || !tiles.length) {
    const msg = env.error || '暂无主线数据';
    host.innerHTML = `<div class="hs-empty"><span class="retry-link" onclick="refreshDashboard()">${escapeHtml(msg)} · 点此重试</span></div>`;
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
        <span class="hs-tile-rank">RANK ${t.rank_flow || ''}</span>
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
$('#run-bt')?.addEventListener('click', async () => {
  const body = {
    start:    $('#bt-start').value || '2025-01-01',
    end:      $('#bt-end').value   || '2026-06-30',
    top_n:    parseInt($('#bt-top').value || '3', 10),
    hold_days: parseInt($('#bt-hold').value || '5', 10),
    sample:   parseInt($('#bt-sample').value || '200', 10),
    sell_mode: $('#bt-sell').value || 'rule',
  };
  const btn = $('#run-bt');
  btn.disabled = true;
  btn.querySelector('span').textContent = '回测中…';
  $('#bt-kpis').innerHTML = '<div class="dim" style="padding:2rem;text-align:center">回测运行中 …</div>';
  toast(`开始回测 ${body.start} → ${body.end}，可能需要数分钟 …`, 'info', 4000);
  try {
    const data = await api('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    renderBacktestResults(data);
    toast(`回测完成 · ${data.summary?.trades || 0} 笔交易 · 用时 ${data.elapsed_sec || '?'}s`, 'success');
  } catch (e) {
    toast('回测失败：' + e.message, 'error');
    $('#bt-kpis').innerHTML = `<div class="dim" style="padding:2rem;text-align:center;color:${DOWN}">回测失败:${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始回测';
  }
});

function renderBacktestResults(data) {
  const s = data.summary || {};
  // KPI grid
  const kpis = [
    ['交易笔数',  s.trades ?? 0,         '笔'],
    ['胜率',      s.win_rate_pct ?? 0,   '%'],
    ['平均收益',  s.avg_return_pct ?? 0, '%'],
    ['月均收益',  s.monthly_avg_return_pct ?? 0, '%'],
    ['盈亏比',    s.profit_factor ?? 0,  ''],
    ['最大回撤',  s.max_drawdown_pct ?? 0, '%'],
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
  const cardHtml = `
    <div class="bt-risk">
      <span class="bt-risk-label">铁律三.4 · 回撤风控</span>
      <span class="bt-risk-state ${data.risk_state === 'reduced' ? 'on' : ''}">${data.risk_state === 'reduced' ? '已减仓' : '正常'}</span>
      <span class="bt-risk-stat">峰值 ${fmtN(data.peak_equity, 3)} → 当前 ${fmtN(data.final_equity, 3)}</span>
      <span class="bt-risk-stat">触发 <strong>${riskActions.length}</strong> 次 · 减仓日 <strong>${data.risk_reduced_days || 0}</strong></span>
    </div>`;
  $('#bt-risk-host').innerHTML = cardHtml;

  // equity curve with drawdown overlay
  const monthly = data.monthly || [];
  let cum = 0;
  const points = monthly.map(m => {
    cum += m.monthly_return_pct || 0;
    return [m.month + '-01', cum];
  });
  drawEquityChart(points);

  // 月度表
  const tbody = $('#bt-monthly tbody');
  if (!monthly.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">无交易</td></tr>';
  } else {
    tbody.innerHTML = monthly.map(m => `<tr>
      <td>${m.month}</td>
      <td class="num">${m.trades}</td>
      <td class="num">${m.win_rate_pct?.toFixed(1) || 0}%</td>
      <td class="num" style="color:${colorFor(m.avg_return_pct)}">${fmtPct(m.avg_return_pct)}</td>
      <td class="num" style="color:${colorFor(m.monthly_return_pct)}">${fmtPct(m.monthly_return_pct)}</td>
      <td class="num" style="color:${UP}">${fmtPct(m.max_return_pct)}</td>
      <td class="num" style="color:${DOWN}">${fmtPct(m.min_return_pct)}</td>
    </tr>`).join('');
  }

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

// ── 查询历史(localStorage 持久化,最多 10 条,最近在前) ──
const _STOCK_HIST_KEY = 'tuixue_stock_history_v1';
const _STOCK_HIST_MAX = 10;

function _loadHist() {
  try {
    const raw = localStorage.getItem(_STOCK_HIST_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}
function _saveHist(arr) {
  try { localStorage.setItem(_STOCK_HIST_KEY, JSON.stringify(arr.slice(0, _STOCK_HIST_MAX))); }
  catch (e) { console.warn('save stock history failed', e); }
}
function _addHist(code, name) {
  if (!code) return;
  code = String(code).padStart(6, '0');
  name = name || code;
  let arr = _loadHist();
  // 去重(同 code 提到最前)
  arr = arr.filter(x => x.code !== code);
  arr.unshift({ code, name, ts: Date.now() });
  arr = arr.slice(0, _STOCK_HIST_MAX);
  _saveHist(arr);
  _renderHist();
}
function _removeHist(code) {
  let arr = _loadHist().filter(x => x.code !== code);
  _saveHist(arr);
  _renderHist();
}
function _clearHist() {
  _saveHist([]);
  _renderHist();
}
function _renderHist() {
  const box = $('#stock-history');
  const list = $('#sh-list');
  if (!box || !list) return;
  const arr = _loadHist();
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
    const data = await api(`/api/stock/${code}?_fresh=1`);
    if (!data || code !== currentStockCode) return;
    _patchStockRealtime(code, data);
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

  // 主力净流(大格) — 数字滚动
  const mainNet = today.main_net ?? 0;
  const mainEl = $('#q-main');
  if (mainEl) {
    const prevMainText = mainEl.dataset.lastMain;
    const prevMain = prevMainText ? parseFloat(prevMainText) : 0;
    mainEl.dataset.lastMain = String(mainNet);
    if (Math.abs(mainNet - prevMain) > 1) {
      animateNumber(mainEl, prevMain, mainNet, 350, (v) => fmtN(v, 0));
    } else {
      mainEl.innerHTML = mainNet != null ? fmtN(mainNet, 0) : '—';
    }
    mainEl.className = 'qc-value large ' + (mainNet > 0 ? 'up' : mainNet < 0 ? 'down' : 'flat');
  }
  const mainSub = $('#q-main-sub');
  if (mainSub) {
    const superBig = (today.super_net || 0) + (today.big_net || 0);
    mainSub.textContent = `超大+大单 ${fmtN(superBig, 0)} 万`;
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
    drawFlowChart(data.fund_flow.history);
    renderFlowKpi(data.fund_flow.history);
  }
}

// visibility 切回页面 → 立即拉一次(避免用户切走 5min 后切回还看到旧价)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentStockCode) {
    _pollStockRealtime(currentStockCode);
  }
});

async function loadStockDetail(code) {
  code = code.trim().padStart(6, '0');
  currentStockCode = code;
  toast(`加载 ${code} …`);
  // 切股:停旧轮询,新轮询在首次 render 后启动,避免抢数据
  _stopStockPoll();
  // 启用快速工具栏 (一键复盘 / 一键自选 / 跳转) — 默认禁用,加载完启用
  _setQuickbarEnabled(code);
  try {
    // 进页面 ?_fresh=1 — 强制失效 quote / fund_flow 缓存,保证拿到最新
    const data = await api(`/api/stock/${code}?_fresh=1`);
    try { renderStockDetail(code, data); }
    catch (e) { console.error('renderStockDetail failed:', e); toast(`渲染失败:${e.message}`, 'error'); }
    // 记录到历史(从 stock 详情接口拿 name)
    const name = (data.quote && data.quote.name) || (data.name) || code;
    _addHist(code, name);
    // 把 name 也喂给快速栏,方便复盘/自选用
    _setQuickbarEnabled(code, name);
    // render 完再启轮询(避免和首次渲染撞车,patch 渲染前的 stale 值)
    _startStockPoll(code);
  } catch (e) {
    toast(`加载失败：${e.message}`, 'error');
  }
}

// ────────────────────────────────────────────
// 个股快速工具栏: 日期 / 一键复盘 / 一键自选 / 跳转
// ────────────────────────────────────────────
let _currentStockName = '';
let _tradeDates = [];        // ['YYYY-MM-DD', ...] 按时间倒序 (限 60 条)
let _tradeDatesSet = null;   // Set 加快 lookup
let _tradeDatesLoaded = false;
let _tradeDatesLoading = null;
let _lastTradeDate = null;   // 服务端给的"今日不是交易日时"的回退日

function _fmtYmd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

async function _ensureTradeDates() {
  if (_tradeDatesLoaded) return _tradeDates;
  if (_tradeDatesLoading) return _tradeDatesLoading;
  _tradeDatesLoading = (async () => {
    try {
      const env = await api('/api/trade_dates?limit=60');
      _tradeDates = env?.dates || [];
      _tradeDatesSet = new Set(_tradeDates);
      if (env?.last_trade_date) _lastTradeDate = env.last_trade_date;
      _tradeDatesLoaded = true;
    } catch (e) {
      console.warn('[quickbar] trade_dates 拉取失败,降级为工作日近似', e);
      _tradeDates = [];
      _tradeDatesSet = new Set();
      _tradeDatesLoaded = true;
    }
    return _tradeDates;
  })();
  return _tradeDatesLoading;
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

// 一键自选 → POST /api/watchlist
$('#stock-watch-btn')?.addEventListener('click', async () => {
  if (!currentStockCode) return;
  const btn = $('#stock-watch-btn');
  btn.disabled = true;
  btn.textContent = '⭐ 加入中…';
  try {
    const r = await _fetchWithTimeout('/api/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: currentStockCode, name: _currentStockName, tag: '自查' }),
    });
    const j = await r.json();
    if (j.ok || j.data?.ok) {
      toast(`✓ ${currentStockCode} 已加入自选`, 'success', 2200);
      btn.textContent = '✓ 已自选';
    } else {
      throw new Error(j.error || '加入失败');
    }
  } catch (e) {
    toast(`加入失败:${e.message}`, 'error', 3000);
    btn.textContent = '⭐ 一键自选';
    btn.disabled = false;
  }
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
  $('#stock-sub').textContent = `${name} · ${code} · ${q._source || ''} ${q._fetch_time || ''}`.trim();

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
  tagsHtml.push(`<span class="qh-tag">${q._source || '—'}</span>`);
  $('#qh-tags').innerHTML = tagsHtml.join(' ');

  // ─── 12 卡 Bento ───
  const setVal = (id, val, color) => {
    $$(id).forEach(el => {
      el.innerHTML = val;
      if (color) el.className = 'qc-value ' + color;
    });
  };

  // 主力净流（大格）— 数字滚动
  const mainNet = today.main_net ?? 0;
  const mainEl = $('#q-main');
  const prevMainText = mainEl.dataset.lastMain;
  const prevMain = prevMainText ? parseFloat(prevMainText) : 0;
  mainEl.dataset.lastMain = String(mainNet);
  if (Math.abs(mainNet - prevMain) > 1) {
    animateNumber(mainEl, prevMain, mainNet, 600, (v) => fmtN(v, 0));
  } else {
    mainEl.innerHTML = mainNet != null ? fmtN(mainNet, 0) : '—';
  }
  mainEl.className = 'qc-value large ' + (mainNet > 0 ? 'up' : mainNet < 0 ? 'down' : 'flat');
  const superBig = (today.super_net || 0) + (today.big_net || 0);
  $('#q-main-sub').textContent = `超大+大单 ${fmtN(superBig, 0)} 万`;

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

  // PE
  setVal('#q-pe', q.市盈率 > 0 ? q.市盈率.toFixed(2) : '—', 'flat');
  $('#q-pe-sub').textContent = q.市盈率 > 0
    ? `PE 动 · ${q.市盈率 > 50 ? '高估' : q.市盈率 < 0 ? '亏损' : '合理'}`
    : '亏损/暂无';

  // 当日高/低
  setVal('#q-hl', `${q.最高 ? fmtN(q.最高, 2) : '—'} / ${q.最低 ? fmtN(q.最低, 2) : '—'}`, 'flat');
  $('#q-hl-sub').textContent = `开 ${fmtN(q.今开, 2)} · 昨收 ${fmtN(q.昨收, 2)}`;

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

  // ─── 连板历史 (保留) ───
  const streakHost = $('#q-streak-host');
  if (extras.streak_history && extras.streak_history.length) {
    streakHost.innerHTML = extras.streak_history.map(s =>
      `<span class="streak-pill"><span class="pill-date">${s.date}</span><span class="pill-pct">+${s.change_pct.toFixed(2)}%</span></span>`
    ).join('');
  } else {
    streakHost.innerHTML = '<p class="caption dim" style="margin: 0">近 10 日无涨停记录</p>';
  }

  // ─── 图表 / 表格 ───
  const empty = $('#flow-empty');
  if (empty) empty.style.display = 'none';
  drawFlowChart(flow.history || []);
  klineState.data = data.kline || [];
  klineState.period = 66;
  syncKlineToolbar();
  drawKlineChart();
  renderFlowKpi(flow.history || []);
  renderKlineKpi(klineState.data);
  renderSeatsTable(seats.rows || [], seats);
  renderHolders(data.holders || null);

  // 资金成分 (6 类席位 + 占比 + 风险) — 异步
  loadStockSeatBreakdown(code);

  // 5 日分时：清空并标记需要 lazy load
  intraday5dCache = null;
  intraDayCache = new Map();
  $('#intraday5d-table tbody').innerHTML = '<tr><td colspan="13" class="empty">点击 5 日分时 标签加载 …</td></tr>';
  $('#intraday5d-note').textContent = '';
  if (echartsCharts.intraday5d) { echartsCharts.intraday5d.dispose(); echartsCharts.intraday5d = null; }
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
}

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
    const mainRow = `<tr class="bd-cat-row${expandable ? ' bd-expandable' : ''}"${zero ? ' style="opacity:.5"' : ''}${expandable ? ` data-detail="bd-detail-${idx}" onclick="toggleSeatDetail(this)"` : ''}>
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
        <button class="btn-mini primary" onclick="event.stopPropagation();_reviewRun(${t.id})">AI 复盘</button>
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
  for (const id of tradeIds) {
    try {
      const r = await _fetchWithTimeout(`/api/review/trades/${id}/review?force=true`, { method: 'POST' });
      const j = await r.json();
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
  // 渲染明细表
  const tbody = $('#flow-detail-table tbody');
  tbody.innerHTML = history.slice(-15).map(h => `<tr>
    <td>${h.date || '—'}</td>
    <td class="num" style="color:${colorFor(h.main_net || 0)}">${fmtN(h.main_net, 0)}</td>
    <td class="num" style="color:${colorFor(h.super_net || 0)}">${fmtN(h.super_net, 0)}</td>
    <td class="num" style="color:${colorFor(h.big_net || 0)}">${fmtN(h.big_net, 0)}</td>
    <td class="num" style="color:${colorFor(h.mid_net || 0)}">${fmtN(h.mid_net, 0)}</td>
    <td class="num" style="color:${colorFor(h.small_net || 0)}">${fmtN(h.small_net, 0)}</td>
    <td class="num">—</td>
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
  period: 66,                 // 当前显示周期 (天)
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

// ────────────────────────────────────────────
// STOCK · 5 日分时 + 封成比
// ────────────────────────────────────────────
let intraday5dCache = null;
let intraday5dLoading = false;

async function loadIntraday5d(code) {
  if (intraday5dLoading) return;
  if (intraday5dCache && intraday5dCache.code === code) {
    renderIntraday5d(intraday5dCache);
    return;
  }
  intraday5dLoading = true;
  const tbody = $('#intraday5d-table tbody');
  tbody.innerHTML = '<tr><td colspan="12" class="empty">加载 5 日分时 …</td></tr>';
  $('#intraday5d-note').textContent = '';
  try {
    const data = await api(`/api/stock/${code}/intraday_5d`);
    intraday5dCache = { code, ...data };
    renderIntraday5d(intraday5dCache);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty">加载失败：${e.message}</td></tr>`;
  } finally {
    intraday5dLoading = false;
  }
}

function renderIntraday5d(data) {
  const code = data.code;
  const rows = data.daily_5d || [];
  const tbody = $('#intraday5d-table tbody');
  const note = $('#intraday5d-note');
  const sum = data.summary_5d || {};

  // 5 日累计 KPI
  if (Object.keys(sum).length) {
    renderKpi($('#intra-kpi'), [
      ['5日累',      sum.cum_pct != null ? (sum.cum_pct >= 0 ? '+' : '') + sum.cum_pct.toFixed(2) + '%' : '—', colorFor(sum.cum_pct)],
      ['涨停天数',   (sum.limit_up_days || 0) + ' / 5', (sum.limit_up_days || 0) > 0 ? UP : INK2],
      ['最高连板',   (sum.max_streak || 0) + ' 板', (sum.max_streak || 0) > 0 ? UP : INK2],
      ['阳/阴',      (sum.up_days || 0) + ' / ' + (sum.down_days || 0), (sum.up_days || 0) > (sum.down_days || 0) ? UP : DOWN],
      ['平均涨幅',   sum.avg_change_pct != null ? (sum.avg_change_pct >= 0 ? '+' : '') + sum.avg_change_pct.toFixed(2) + '%' : '—', colorFor(sum.avg_change_pct)],
      ['平均封成比', sum.avg_seal_ratio != null ? sum.avg_seal_ratio.toFixed(1) + '%' : '—', (sum.avg_seal_ratio || 0) > 20 ? UP : INK2],
      ['5日高',      sum.high_5d != null ? sum.high_5d.toFixed(2) : '—', UP],
      ['5日低',      sum.low_5d  != null ? sum.low_5d.toFixed(2)  : '—', DOWN],
    ]);
  } else {
    $('#intra-kpi').innerHTML = '<div class="kpi"><span class="kpi-label">5日</span><span class="kpi-num">无数据</span></div>';
  }

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="13" class="empty">无 5 日数据</td></tr>';
    note.textContent = '未找到该股票最近 5 个交易日的日线 / 涨停池数据';
    if (echartsCharts.intraday5d) { echartsCharts.intraday5d.dispose(); echartsCharts.intraday5d = null; }
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const fc = r.was_limit_up ? (r.seal_ratio_pct != null ? r.seal_ratio_pct.toFixed(1) + '%' : '—') : '—';
    const sa = r.was_limit_up && r.sealed_amount ? (r.sealed_amount / 1e8).toFixed(2) : '—';
    const burst = r.was_limit_up ? (r.burst_count || 0) : '—';
    const streak = r.was_limit_up ? (r.streak || 1) : '—';
    const firstSeal = r.was_limit_up && r.first_seal_time ? r.first_seal_time : '—';
    const lu = r.was_limit_up
      ? '<span style="color:' + UP + '">✓</span>'
      : '<span style="color:' + INK3 + '">·</span>';
    return `<tr>
      <td>${r.date}</td>
      <td class="num">${fmtN(r.open, 2)}</td>
      <td class="num">${fmtN(r.high, 2)}</td>
      <td class="num">${fmtN(r.low, 2)}</td>
      <td class="num">${fmtN(r.close, 2)}</td>
      <td class="num" style="color:${colorFor(r.change_pct)}">${r.change_pct != null ? r.change_pct.toFixed(2) + '%' : '—'}</td>
      <td>${lu}</td>
      <td class="num">${streak}</td>
      <td class="num" style="color:${r.was_limit_up && r.seal_ratio_pct >= 20 ? UP : INK}">${fc}</td>
      <td class="num">${sa}</td>
      <td class="num" style="color:${r.burst_count > 0 ? DOWN : INK2}">${burst}</td>
      <td class="num">${firstSeal}</td>
      <td>${escapeHtml(r.sector || '—')}</td>
    </tr>`;
  }).join('');

  note.textContent = data.note || '';
  if (data.note) note.style.color = INK2;

  drawIntraday5dChart(code, rows, data.intraday_today, data.intraday_per_day);
}

function drawIntraday5dChart(code, daily, intraday) {
  const dom = $('#intraday5d-chart');
  if (!dom) return;
  if (echartsCharts.intraday5d) echartsCharts.intraday5d.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.intraday5d = chart;

  const dates = daily.map(r => r.date);
  const closes = daily.map(r => r.close);

  // ── 多日分时：每日的 ticks 拼成一条连续折线，x 用 "MM-DD HH:MM" ──
  const perDay = (typeof arguments[3] !== 'undefined') ? arguments[3] : null;
  const useMulti = perDay && perDay.days && perDay.days.some(d => d.ticks && d.ticks.length);

  if (useMulti) {
    const allPoints = [];
    const volPoints = [];
    const dayMarkers = [];
    const dayOpenRefs = {};  // date -> open price for that day (color ref)
    daily.forEach(r => { if (r.open) dayOpenRefs[r.date] = r.open; });

    perDay.days.forEach(day => {
      if (!day.ticks || !day.ticks.length) return;
      const d = day.date;
      day.ticks.forEach(t => {
        const x = `${d.slice(5)} ${(t.time || '').slice(0, 5)}`;
        allPoints.push([x, t.price, d]);
        if (t.volume_hand) volPoints.push([x, t.volume_hand, d]);
      });
    });

    // 找每天的中间位置做日分隔标记
    const dayFirstIdx = {};
    allPoints.forEach((p, i) => {
      if (!(p[2] in dayFirstIdx)) dayFirstIdx[p[2]] = i;
    });
    Object.entries(dayFirstIdx).forEach(([d, idx]) => {
      const open = dayOpenRefs[d] ?? null;
      const close = allPoints.filter(p => p[2] === d).slice(-1)[0]?.[1];
      dayMarkers.push({
        xAxis: allPoints[idx][0],
        label: { formatter: d.slice(5), color: INK2, fontSize: 10 },
        lineStyle: { color: '#3a3835', type: 'solid', width: 1, opacity: 0.6 },
      });
    });

    chart.setOption({
      backgroundColor: 'transparent',
      title: { text: `5 日连续分时  ·  ${daily.length} 日  ·  每 ${perDay.days.find(d => d.ticks?.length)?.source?.includes('tencent') ? '1' : '5'} min`, textStyle: { color: INK2, fontSize: 11 }, left: 8, top: 4 },
      grid: [
        { left: 56, right: 24, top: 32, height: '56%' },
        { left: 56, right: 24, top: '74%', height: '20%' },
      ],
      tooltip: {
        trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK, fontSize: 11 },
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        formatter: (params) => {
          if (!params?.length) return '';
          const t = params[0].axisValue;
          let s = `<div style="color:${INK2}">${t}</div>`;
          params.forEach(p => {
            const color = p.seriesName === '价格' ? ACCENT : (p.value > 0 ? UP : INK2);
            const val = p.seriesName === '成交量'
              ? (p.value >= 1e4 ? (p.value / 1e4).toFixed(2) + ' 万' : p.value.toFixed(0))
              : (typeof p.value === 'number' ? p.value.toFixed(2) : p.value);
            s += `<div>${p.marker} ${p.seriesName}: <span style="color:${color};font-weight:600">${val}</span></div>`;
          });
          return s;
        },
      },
      legend: { textStyle: { color: INK2, fontSize: 10 }, top: 4, right: 8, data: ['价格', '成交量'] },
      xAxis: [
        { type: 'category', data: allPoints.map(p => p[0]), gridIndex: 0,
          axisLine: { lineStyle: { color: '#2a2825' } },
          axisLabel: { color: INK2, fontSize: 9, interval: Math.max(1, Math.floor(allPoints.length / 8)), formatter: (v) => v.slice(0, 5) },
          splitLine: { show: true, interval: 0, lineStyle: { color: '#3a3835', type: 'dashed', opacity: 0.4 }, formatter: (v) => v.slice(0, 5) },
          markLine: { silent: true, symbol: 'none', data: dayMarkers },
        },
        { type: 'category', data: allPoints.map(p => p[0]), gridIndex: 1,
          axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
        { gridIndex: 1, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 9 } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], height: 14, bottom: 4, start: 0, end: 100,
          textStyle: { color: INK2, fontSize: 9 }, borderColor: '#2a2825', fillerColor: 'rgba(212,160,86,0.15)' },
      ],
      series: [
        { name: '价格', type: 'line', data: allPoints.map(p => p[1]), showSymbol: false, smooth: false,
          lineStyle: { color: ACCENT, width: 1.5 }, itemStyle: { color: ACCENT },
          areaStyle: { color: 'rgba(212,160,86,0.06)' },
          markLine: { silent: true, symbol: 'none',
            data: Object.entries(dayOpenRefs).map(([d, op]) => ({
              yAxis: op, label: { show: false }, lineStyle: { color: '#5a5852', type: 'dotted', width: 0.8, opacity: 0.5 },
            })),
          },
        },
        { name: '成交量', type: 'bar', data: volPoints.map(p => p[1]), xAxisIndex: 1, yAxisIndex: 1, barWidth: '60%',
          itemStyle: { color: UP, opacity: 0.45 },
        },
      ],
    });
    return;
  }

  // ── 旧版：仅日线 + 今日分时叠加（向后兼容） ──
  const limits = daily.map(r => r.limit_price).filter(x => x != null);
  const limitLine = dates.map((_, i) => limits[0] ?? null);
  const series = [
    { name: '收盘', type: 'line', data: closes, smooth: true, lineStyle: { color: ACCENT, width: 2 }, symbol: 'circle', symbolSize: 6, itemStyle: { color: ACCENT } },
  ];
  if (limits.length === daily.length && limits.every(x => x != null)) {
    series.push({ name: '涨停价', type: 'line', data: limitLine, lineStyle: { color: UP, type: 'dashed', width: 1 }, symbol: 'none' });
  }
  const lastDate = dates[dates.length - 1];
  const tickSeries = [];
  if (intraday && intraday.ticks && intraday.ticks.length) {
    const tickTimes = intraday.ticks.map(t => t.time);
    const tickPrices = intraday.ticks.map(t => t.price);
    tickSeries.push({ name: '今日分时', type: 'line', smooth: false, showSymbol: false, lineStyle: { color: '#a78bcf', width: 1.2 }, xAxisIndex: 1, yAxisIndex: 1,
      data: tickTimes.map((t, i) => [t, tickPrices[i]]) });
  }

  chart.setOption({
    backgroundColor: 'transparent',
    title: tickSeries.length ? { text: `5 日收盘  +  ${lastDate} 分时`, textStyle: { color: INK2, fontSize: 11 }, left: 8, top: 4 } : { show: false },
    grid: tickSeries.length
      ? [{ left: 50, right: '55%', top: 32, height: '40%' }, { left: '50%', right: 16, top: 32, height: '40%' }]
      : [{ left: 50, right: 16, top: 16, height: '70%' }],
    tooltip: { trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK } },
    legend: { textStyle: { color: INK2, fontSize: 10 }, top: 4, right: 8, data: tickSeries.length ? ['收盘', '涨停价', '今日分时'] : ['收盘', '涨停价'] },
    xAxis: tickSeries.length
      ? [
          { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 } },
          { gridIndex: 1, type: 'category', data: intraday.ticks.map(t => t.time), axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 9, interval: Math.max(1, Math.floor(intraday.ticks.length / 6)) } },
        ]
      : [{ type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 } }],
    yAxis: tickSeries.length
      ? [
          { scale: true, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
          { gridIndex: 1, scale: true, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
        ]
      : [{ scale: true, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } }],
    series: tickSeries.length ? [...series, ...tickSeries] : series,
  });
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
function animateNumber(el, from, to, dur = 500, fmt = (v) => v.toFixed(2)) {
  if (!el) return;
  const start = performance.now();
  const delta = to - from;
  el.classList.add('is-animating');
  function step(t) {
    const k = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - k, 3); // easeOutCubic
    el.textContent = fmt(from + delta * eased);
    if (k < 1) requestAnimationFrame(step);
    else el.classList.remove('is-animating');
  }
  requestAnimationFrame(step);
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
  prev.onclick = () => { pick.value = shiftDate(pick.value, -1); refreshLabel(); autoLoadIntraDay(); };
  next.onclick = () => { pick.value = shiftDate(pick.value, +1); refreshLabel(); autoLoadIntraDay(); };
  load.onclick = () => autoLoadIntraDay();

  function autoLoadIntraDay() {
    if (!currentStockCode) return;
    loadIntraDay(currentStockCode, pick.value);
  }
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
  const isSina = (data.source || '').startsWith('sina');
  const volStr = isSina
    ? (totalVol >= 1e8 ? (totalVol / 1e8).toFixed(2) + ' 亿股' : (totalVol / 1e4).toFixed(2) + ' 万股')
    : (totalVol >= 1e4 ? (totalVol / 1e4).toFixed(2) + ' 万手' : totalVol.toFixed(0) + ' 手');

  renderKpi(kpi, [
    ['开盘',     openRef != null ? openRef.toFixed(2) : '—', INK],
    ['最新',     lastPrice != null ? lastPrice.toFixed(2) : '—', colorFor(pct)],
    ['日内涨跌', pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '—', colorFor(pct)],
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
        markLine: { silent: true, symbol: 'none', data: timeMarkers } },
      { name: '均价', type: 'line', data: avgLine, showSymbol: false,
        lineStyle: { color: '#f5d77e', width: 1, type: 'solid' } },
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
        flow: '资本动向', kline: 'K 线走势', intraday5d: '5 日分时 · 封成比', seats: '游资席位',
        news: '📰 财经新闻 · AI 评分', sectors: '📊 申万 31 行业 · 新闻情绪', ai: 'AI 复盘'
      }[tab] || ' ');
    }
    if (tab === 'flow'  && echartsCharts.flow)  echartsCharts.flow.resize();
    if (tab === 'kline' && echartsCharts.kline) echartsCharts.kline.resize();
    if (tab === 'intraday5d') {
      if (echartsCharts.intraday5d) echartsCharts.intraday5d.resize();
      if (currentStockCode) {
        loadIntraday5d(currentStockCode);
        initIntraDayPicker(currentStockCode);
        // 首次进入 tab 自动加载当日分时
        const pick = $('#intra-day-pick');
        if (pick && pick.value && !intraDayCache.has(pick.value)) {
          loadIntraDay(currentStockCode, pick.value);
        }
      } else {
        $('#intraday5d-table tbody').innerHTML = '<tr><td colspan="12" class="empty">请先在上方搜索一只股票</td></tr>';
        $('#intraday5d-note').textContent = '';
      }
    }
    if (tab === 'news')   loadNewsList(false);
    if (tab === 'sectors') loadSectorsList(false);
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
      <td>${p.name}</td>
      <td>${p.type}</td>
      <td class="num">${p.size_kb} KB</td>
      <td>${p.mtime}</td>
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
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  });
  es.onerror = () => {
    // EventSource 不会自动重连 (server 不重试); 只显示错误
    $('#optimize-status').textContent = 'SSE 连接中断（可重试）';
    es.close();
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
      return `
        <div class="mainline-card">
          <div class="mainline-name">${m.name || '—'}</div>
          <div class="mainline-meta">
            <span class="${pct >= 0 ? 'good' : 'bad'}">${pct >= 0 ? '+' : ''}${pct}%</span>
            <span class="dim">净流入 ${inflow}亿</span>
          </div>
          <div class="mainline-badges">${flowBadge}${pctBadge}</div>
        </div>`;
    }).join('');
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
        <div class="dragon-card">
          <div class="dragon-head">
            <span class="dragon-rank">#${s.rank}</span>
            <span class="dragon-code">${s.code}</span>
            <span class="dragon-name">${s.name}</span>
            <span class="dragon-score">${s.score_total}</span>
          </div>
          <div class="dragon-meta">
            <span>${s.sector || '—'}</span> ${mainlineBadge}
            <span class="dim"> · ${s.streak}板 · 市值${s.market_cap_yi}亿 · 换手${s.turnover_pct}% · 封成${sealTxt}</span>
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
      const warnTxt = (s.warnings || []).length ? s.warnings.join('; ') : '—';
      const bd = s.score_breakdown || {};
      const bdHtml = _renderAIAnalysisCards(bd, s);
      return `<tr data-code="${s.code}" class="clickable ai-toggle">
        <td>${s.rank}</td>
        <td><a href="#" class="stock-link" data-code="${s.code}">${s.code}</a></td>
        <td>${s.name}</td>
        <td>${s.sector || '—'}</td>
        <td>${s.streak}板</td>
        <td>${s.market_cap_yi}亿</td>
        <td>${s.turnover_pct}%</td>
        <td>${sealTxt}</td>
        <td><b>${s.score_total}</b></td>
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
    $('#dragons-decision').innerHTML = `<p class="empty">${overall} (Top10 中无可执行标的)</p>`;
  } else {
    const playHtml = plays.length
      ? `<div class="decision-col">
          <div class="decision-title">🎯 尾盘打板 (${plays.length})</div>
          ${plays.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${p.code}"><b>${p.name}</b> ${p.code}</a>
            <span class="dim"> · ${p.sector} · 评分${p.score}</span>
            <div class="decision-reason">${p.reason}</div>
          </div>`).join('')}
        </div>`
      : '';
    const dipHtml = dips.length
      ? `<div class="decision-col">
          <div class="decision-title">📉 次日低吸 (${dips.length})</div>
          ${dips.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${p.code}"><b>${p.name}</b> ${p.code}</a>
            <span class="dim"> · ${p.sector} · ${p.streak}板 · 评分${p.score}</span>
            <div class="decision-reason">${p.reason}</div>
          </div>`).join('')}
        </div>`
      : '';
    const avoidHtml = avoids.length
      ? `<div class="decision-col">
          <div class="decision-title">⚠ 回避 (${avoids.length})</div>
          ${avoids.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${p.code}"><b>${p.name}</b> ${p.code}</a>
            <span class="dim"> · ${p.sector} · 评分${p.score}</span>
            <div class="decision-reason decision-warn">${p.reason}</div>
          </div>`).join('')}
        </div>`
      : '';
    $('#dragons-decision').innerHTML = `
      <p class="decision-overall">💡 <b>${overall}</b></p>
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
showView = function(name) {
  _origShowView(name);
  if (name === 'dragons' && !_dragonsLoaded) loadDragons(false);
  if (name === 'review') _reviewOnViewEnter();
  if (name === 'watchlist') _watchlistOnViewEnter();
};
$$('[data-jump]').forEach(el => {
  el.addEventListener('click', () => showView(el.dataset.jump));
});

$('#refresh-ticker')?.addEventListener('click', () => {
  refreshTicker();
  toast('已刷新');
});

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
  // 释放旧图表实例,让下一次 render 用新色
  Object.entries(echartsCharts).forEach(([k, c]) => {
    if (c) { try { c.dispose(); } catch {} }
    echartsCharts[k] = null;
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
  const btn = $('#tunnel-btn');
  const btnLabel = $('#tunnel-btn-label');
  const status = $('#tunnel-status');
  if (!dot) return;
  try {
    const r = await api('/api/tunnel/status');
    if (!r) return;
    if (r.running && r.url) {
      status.classList.add('online');
      status.classList.remove('offline');
      text.textContent = '公网已通';
      urlRow.hidden = false;
      urlEl.href = r.url;
      urlEl.textContent = r.url.replace(/^https?:\/\//, '');
      btnLabel.textContent = '重启';
    } else if (r.running && !r.url) {
      status.classList.remove('online');
      status.classList.add('offline');
      text.textContent = '启动中…';
      urlRow.hidden = true;
      btnLabel.textContent = '重启';
    } else {
      status.classList.remove('online');
      status.classList.add('offline');
      text.textContent = `局域网 http://${r.lan_ip}:${r.port}`;
      urlRow.hidden = true;
      btnLabel.textContent = '启动隧道';
    }
  } catch (e) {
    text.textContent = '状态读取失败';
  }
}
$('#tunnel-btn')?.addEventListener('click', async () => {
  const btn = $('#tunnel-btn');
  const btnLabel = $('#tunnel-btn-label');
  btn.disabled = true;
  btnLabel.textContent = '启动中…';
  try {
    const r = await api('/api/tunnel/start', { method: 'POST' });
    const d = r.data || r;
    if (d && d.url) {
      // 后端已经把 URL 写到 tunnel_url.txt,这里同步刷新状态即可
      await refreshTunnel();
      const tgMsg = d.tg_sent
        ? '✅ 已自动推到 Telegram'
        : `⚠ TG 推送失败 (${d.tg_err || 'DNS 阻断'}), URL 仍可访问`;
      toast(`✓ 公网入口 ${d.url.slice(8, 36)}… · ${tgMsg}`, d.tg_sent ? 'success' : 'warn', 4500);
    } else {
      toast(`启动失败:${(d && d.error) || r.error || '60s 内未拿到 URL'}`, 'error', 4500);
    }
  } catch (e) {
    toast('启动失败:' + e.message, 'error', 4500);
  } finally {
    btn.disabled = false;
    btnLabel.textContent = '重启';
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

    const todayHtml = today ? `
      <div class="kv-row">
        <span>今日</span>
        <b style="color:${today.连板数 >= 3 ? UP : today.连板数 >= 1 ? ACCENT : INK2}">
          ${today.连板数 >= 2 ? '🔥' : '✓'} ${today.连板数 || 0} 板
          · 封单 ${today.封单金额 ? (today.封单金额 / 1e8).toFixed(2) + ' 亿' : '—'}
          · 首次 ${today.首次封板时间 ? String(today.首次封板时间).replace(/^(\d{2})(\d{2})\d{2}$/, '$1:$2') : '—'}
        </b>
      </div>
      <div class="kv-row"><span>炸板</span><b>${today.炸板次数 || 0} 次</b></div>
      <div class="kv-row"><span>所属</span><b>${escapeHtml(today.所属行业 || '—')}</b></div>
    ` : `<div class="kv-row"><span>今日</span><b class="dim">未涨停</b></div>`;

    const recentHtml = recent5.length > 0 ? `
      <div class="caption" style="margin:.5rem 0 .25rem">📅 近 5 个交易日涨停</div>
      <div class="stock-tags">
        ${recent5.map(r => `
          <span class="chip" style="color:${ACCENT}">${r.date.slice(4,6)}/${r.date.slice(6,8)} · ${r.连板数 || 1}板</span>
        `).join('')}
      </div>
    ` : '';

    const sectorHtml = sectorZt.length > 0 ? `
      <div class="caption" style="margin:.5rem 0 .25rem">🔥 板块当日涨停 ${sectorZt.length} 只（取 ${Math.min(sectorZt.length, 10)}）</div>
      <table class="mini-table" style="width:100%;font-size:.85rem">
        <tr><th>名称</th><th>连板</th><th>封单</th><th>涨幅</th></tr>
        ${sectorZt.slice(0, 10).map(s => `
          <tr style="cursor:pointer" onclick="loadStockDetail('${s.代码}')">
            <td>${escapeHtml(s.名称)}</td>
            <td style="color:${(s.连板数||0) >= 2 ? UP : INK2};font-weight:bold">${s.连板数 || 0}</td>
            <td>${s.封单金额 ? (s.封单金额 / 1e8).toFixed(2) + '亿' : '—'}</td>
            <td style="color:${(s.涨跌幅||0) > 0 ? UP : DOWN}">${(s.涨跌幅||0).toFixed(1)}%</td>
          </tr>
        `).join('')}
      </table>
      <p class="caption dim" style="margin:.5rem 0 0">点击名称 → 切换个股</p>
    ` : `<p class="caption dim" style="margin:.5rem 0 0">板块今日无涨停</p>`;

    const relatedConHtml = relatedCon.length > 0 ? `
      <div class="caption" style="margin:.75rem 0 .25rem;border-top:.5px solid var(--line);padding-top:.5rem">🧬 相关概念当日涨停 (按 L3 产业链 / L4 细分聚合)</div>
      <div class="concepts-grid" style="display:flex;flex-wrap:wrap;gap:.4rem">
        ${relatedCon.map(c => `
          <span class="chip" style="cursor:default;border:1px solid ${c.zt_count >= 5 ? UP : c.zt_count >= 2 ? ACCENT : INK2};color:${c.zt_count >= 5 ? UP : c.zt_count >= 2 ? ACCENT : INK2}" title="${escapeHtml(c.concept)} (${c.level}) · ${c.zt_count} 只涨停 · 例: ${escapeHtml((c.samples || []).join(', '))}">
            <span class="cap">${c.level}</span>
            <b>${escapeHtml(c.concept)}</b>
            <span class="up">⚡${c.zt_count}</span>
          </span>
        `).join('')}
      </div>
      <p class="caption dim" style="margin:.5rem 0 0">同一产业链或细分标签下的涨停总数 · 颜色: ≥5 主线(红) / ≥2 二线(琥珀) / 其他杂毛(灰)</p>
    ` : '';

    host.innerHTML = `
      ${todayHtml}
      ${recentHtml}
      ${sectorHtml}
      ${relatedConHtml}
      ${summary ? `<div class="kv-row mt-8" style="border-top:.5px solid var(--line);padding-top:.5rem"><span>总结</span><b>${escapeHtml(summary)}</b></div>` : ''}
    `;
  } catch (e) {
    host.innerHTML = `<p class="caption down">连板数据加载失败: ${escapeHtml(e.message)}</p>`;
  }
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
  tbody.innerHTML = _reviewState.trades.map(t => {
    const live = t.live || {};
    const rev = t.last_review || {};
    const today = _reviewMoney(live.today_pnl);
    const cum = _reviewMoney(live.cum_pnl);
    const cumPct = _reviewPct(live.cum_pnl_pct);
    const dateStr = (t.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
    const timeStr = (t.occurred_at || '').replace('T', ' ').slice(11, 16) || '—';
    let mistake = rev.main_mistake || rev.mistake_pattern || '';
    const mistakeHtml = mistake
      ? `<span class="main-mistake-pill" title="${escapeHtml(mistake)}">${escapeHtml(mistake)}</span>`
      : '<span class="caption dim">未复盘</span>';
    const reviewed = !!t.last_review;
    return `
      <tr data-trade-id="${t.id}" data-code="${escapeHtml(t.code)}">
        <td class="rv-nm">
          <code class="np-code">${escapeHtml(t.code)}</code>
          <span class="np-name">${escapeHtml(t.name || '—')}</span>
          ${_reviewStatusPill(live)}
        </td>
        <td>${_reviewDirection(t.direction)}</td>
        <td class="caption">${escapeHtml(dateStr || '—')}</td>
        <td class="cell-num">${_reviewFmtNum(t.price, 2)}</td>
        <td class="caption">${escapeHtml(timeStr)}</td>
        <td class="cell-num">${t.shares}</td>
        <td class="cell-num ${today.cls}">${today.text}</td>
        <td class="cell-num ${cum.cls}">${cum.text}</td>
        <td class="cell-num ${cumPct.cls}">${cumPct.text}</td>
        <td>${mistakeHtml}</td>
        <td class="rv-act">
          <button class="btn-mini ${reviewed ? '' : 'primary'}" onclick="event.stopPropagation();openAiReview(${t.id})">${reviewed ? 'AI 复盘' : 'AI 复盘 ●'}</button>
          <button class="btn-mini danger" onclick="event.stopPropagation();_reviewDelete(${t.id})">删</button>
        </td>
      </tr>
    `;
  }).join('');
  tbody.querySelectorAll('.rv-nm').forEach(td => {
    td.style.cursor = 'pointer';
    td.addEventListener('click', () => {
      const c = td.closest('tr')?.dataset.code;
      if (c) loadStockDetail(c);
    });
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
  const totalSub = { text: `浮 ${_reviewMoney(d.unrealized_pnl).text} · 实 ${_reviewMoney(d.realized_pnl).text}`, cls: 'dim' };
  const ratio = d.total_pnl_pct != null
    ? _reviewPct(d.total_pnl_pct)
    : { text: '设总资金', cls: 'cell-flat' };
  bar.innerHTML =
    _capTile('总资金 (满仓)', total, { text: d.cash != null ? '可用 ' + yuan(d.cash) : '', cls: 'dim' }) +
    _capTile('仓位', { text: posText, cls: '' }, posRatio) +
    _capTile('今日盈亏', today, todaySub) +
    _capTile('总盈亏', total_pnl, totalSub) +
    _capTile('盈亏比', ratio, { text: '总盈亏 / 总资金', cls: 'dim' });
  _renderPositions(d.positions || []);
}

function _renderPositions(positions) {
  const box = $('#review-positions');
  if (!box) return;
  if (!positions.length) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="pos-title">当前持仓 · 实时</div>' +
    '<div class="pos-grid">' + positions.map(p => {
      const up = _reviewPct(p.unrealized_pct);
      const today = _reviewPct(p.prev_close ? (p.price - p.prev_close) / p.prev_close * 100 : null);
      return `<div class="pos-card" onclick="loadStockDetail('${escapeHtml(p.code)}')">
        <div class="pos-hd"><code>${escapeHtml(p.code)}</code> <span>${escapeHtml(p.name || '')}</span></div>
        <div class="pos-row"><span class="dim">现价</span><b class="${today.cls}">${_reviewFmtNum(p.price, 2)}</b> <span class="${today.cls}">${today.text}</span></div>
        <div class="pos-row"><span class="dim">${p.shares}股 @ ${_reviewFmtNum(p.avg_cost, 2)}</span></div>
        <div class="pos-row"><span class="dim">浮盈</span><b class="${up.cls}">${_reviewMoney(p.unrealized).text}</b> <span class="${up.cls}">${up.text}</span></div>
      </div>`;
    }).join('') + '</div>';
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
  // 在主表里找这笔交易(快速预览头部)
  _aiReviewState.tradeId = tradeId;
  _aiReviewState.trade = (_reviewState.trades || []).find(t => t.id === tradeId) || null;
  _aiReviewState.review = _aiReviewState.trade?.last_review || null;
  showView('ai-review');
}

// 当切到 ai-review view 时: 先看有没有缓存, 有就显示; 否则跑 LLM
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
  await _airvRunLLM();
}

async function _airvRunLLM(force = true) {
  const tid = _aiReviewState.tradeId;
  if (!tid || _aiReviewState.running) return;
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
        <button class="btn btn-mini primary" onclick="_airvRunLLM(true)">↻ 强制重跑</button>
        <button class="btn btn-mini" onclick="showView('review')">‹ 返回复盘</button>
      </div>
    </article>
  `;
}

// 简单 toast(用现成 alert 替代,避免再加组件)
function showToast(msg, type) {
  if (window.__toastBox) {
    window.__toastBox.remove();
  }
  const colors = { info: '#d4a056', success: '#4fb074', error: '#d97a6c' };
  const box = document.createElement('div');
  box.textContent = msg;
  box.style.cssText = `
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 12px 24px; background: rgba(20,18,14,0.95); color: ${colors[type] || colors.info};
    border: 1px solid ${colors[type] || colors.info}; border-radius: 8px;
    font-size: 14px; z-index: 9999; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    max-width: 80vw;
  `;
  document.body.appendChild(box);
  window.__toastBox = box;
  setTimeout(() => { if (box.parentNode) box.remove(); }, 6000);
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

async function _reviewLoadNextPicks() {
  const list = $('#review-next-pick-list');
  const meta = $('#review-next-meta');
  if (!list) return;
  list.innerHTML = '<li class="caption dim">加载中 (后端筛选 + AI 错模式预警)…</li>';
  try {
    const r = await _fetchWithTimeout('/api/review/next_picks');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const d = j.data || {};
    if (!d.picks || !d.picks.length) {
      list.innerHTML = '<li class="caption dim">无候选 (可能后端异常,或没有交易记录)</li>';
      meta.textContent = '';
      return;
    }
    if (d.user_patterns && d.user_patterns.length) {
      meta.innerHTML = `⚠ <span style="color:var(--accent)">你的常见错模式:</span> ${d.user_patterns.slice(0, 4).map(p => `<span class="rule-pill fail">${escapeHtml(p)}</span>`).join(' ')}`;
    } else {
      meta.textContent = '✅ 暂无历史错模式(继续积累交易后会有更精准预警)';
    }
    list.innerHTML = d.picks.map((p, i) => {
      const v = p.ai_verdict || '观望';
      const score = p.ai_score != null ? p.ai_score : '?';
      const risk = (p.risk_warnings || []).map(r => `<span class="rule-pill warn">${escapeHtml(r)}</span>`).join(' ');
      return `<li>
        <span class="np-idx">${i+1}</span>
        <code class="np-code" onclick="loadStockDetail('${p.code}')">${escapeHtml(p.code)}</code>
        <span class="np-name">${escapeHtml(p.name || '—')}</span>
        <span class="np-sector caption dim">${escapeHtml(p.sector || '')}</span>
        <span class="verdict-pill ${escapeHtml(v)}">${escapeHtml(v)} ${score}/100</span>
        <span class="np-risk">${risk}</span>
      </li>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<li class="caption dim">加载失败: ${escapeHtml(e.message)}</li>`;
  }
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
    const tradeClickable = t => t.code ? `onclick="loadStockDetail('${escapeHtml(t.code)}')" style="cursor:pointer"` : '';
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
    _reviewBindCapital();
    _reviewBindInfer();
    _reviewLoadSettings();
    _reviewLoadPortfolio();
    _reviewLoadList();
    _reviewLoadNextPicks();
    // 顶部资金栏 + 持仓每 10s 刷新 (报价实时)
    if (_reviewState.capTimer) clearInterval(_reviewState.capTimer);
    _reviewState.capTimer = setInterval(_reviewLoadPortfolio, 10000);
    const btn = $('#review-next-pick-refresh');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', () => _reviewLoadNextPicks());
    }
  }
}

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
  if (!confirm(`确认从自选中删除 ${code} ?`)) return;
  try {
    const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(code), { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
      showToast(`✓ 已删除 ${code}`, 'success');
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
  if (!confirm(`将对 ${items.length} 只股票触发 AI 判定 (预计 ${items.length * 25}s)。继续?`)) return;
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
});

// 初始绑定(用户直接打开 review 时)
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(_reviewOnViewEnter, 200);
  // 资金占比 10s 轮询
  setInterval(() => {
    if (document.querySelector('.view-review:not([hidden])')) {
      _reviewRefreshFlows();
    }
  }, 10000);

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
