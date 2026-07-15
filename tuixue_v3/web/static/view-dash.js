// ────────────────────────────────────────────
// 市场概览 + ticker
// ────────────────────────────────────────────
// P-perf: localStorage 快照缓存 — 页面重载时免白屏
var _DASHBOARD_CACHE_KEY = 'tx3_dash_cache';
var _DASHBOARD_CACHE_TTL_MS = 120_000;  // 2min 过期

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
      return `<span class="tk-item tk-clickable" data-code="${escapeHtml(i.code || '')}" title="点击查看 ${escapeHtml(i.name)} 详情" role="button" tabindex="0">
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
var _VERDICT_LABEL = { allow: '适合买入', cautious: '谨慎参与', block: '不适合买入' };
var _VERDICT_DOT   = { allow: '🟢', cautious: '🟡', block: '🔴' };

// P-perf: sessionStorage 个股缓存 — 切股/回退时免白屏
var _STOCK_CACHE_KEY_PREFIX = 'tx3_stock_';

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
var _MARKET_OF_PREFIX = { a: 'a', kr: 'kr', us: 'us' };

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

// Ticker 指数项点击 → 进个股详情页 (指数也走 stock view, 数据格式相同)
document.addEventListener('click', e => {
  const tk = e.target.closest('.tk-clickable');
  if (!tk) return;
  const code = tk.dataset.code;
  if (!/^\d{6}$/.test(code)) return;
  showView('stock');
  if (typeof loadStockDetail === 'function') loadStockDetail(code);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const tk = e.target?.closest?.('.tk-clickable');
  if (!tk) return;
  e.preventDefault();
  tk.click();
});

