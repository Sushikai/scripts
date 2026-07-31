// ────────────────────────────────────────────
// 市场概览 + ticker
// ────────────────────────────────────────────
// P-perf: localStorage 快照缓存 — 页面重载时免白屏
var _DASHBOARD_CACHE_KEY = 'tx3_dash_cache';
var _DASHBOARD_CACHE_TTL_MS = 120_000;  // 2min 过期

// R232: 自包含 ECharts 加载器 — view-dash 是首屏,view-stock 还没按需注入,
// 这里自己拉 vendor/echarts (SW 已 precache),避免 sparkline 永远是空白 div
var _dashEchartsPromise = null;
function _ensureDashEcharts() {
  if (typeof echarts !== 'undefined') return Promise.resolve(echarts);
  if (_dashEchartsPromise) return _dashEchartsPromise;
  _dashEchartsPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/echarts.min.js';
    s.onload = () => resolve(echarts);
    s.onerror = () => { _dashEchartsPromise = null; reject(new Error('echarts load fail')); };
    document.head.appendChild(s);
  });
  return _dashEchartsPromise;
}

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

async function refreshTicker(loadDash) {
  const bar = $('#tickerbar');
  try {
    const data = await api('/api/market/overview');
    lastRefreshTs = data.ts || Date.now() / 1000;
    const indices = data.indices || [];
    const fragments = indices.map(i => {
      const c = i.change_pct > 0 ? 'var(--up)' : i.change_pct < 0 ? 'var(--down)' : 'var(--ink-3)';
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
    // P-perf: 分阶段渲染 dashboard — 仅当位于 dash 页面时加载
    if (loadDash) _dashLoadPhased();
  } catch (e) {
    bar.innerHTML = '<div class="ticker-empty">市场数据暂不可达 · ' + e.message + '</div>';
    if (loadDash) _dashLoadPhased();
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
  // 大盘/板块分时走势 (sparkline grid)
  _dashRefreshIndexTrend();
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

// 大盘 + 板块分时 sparkline (Phase 1 异步,不再阻塞首屏)
async function _dashRefreshIndexTrend() {
  try {
    const r = await _fetchWithTimeout('/api/dashboard/index_trend', { timeout: 25000 });
    const env = await r.json();
    if (!env.ok) return;
    const d = env.data || {};
    _paintIndexTrend(d);
  } catch (e) {
    console.debug('[dash] index_trend refresh failed:', e.message);
    const idxHost = $('#index-trend-grid');
    const secHost = $('#sector-trend-grid');
    if (idxHost && /^加载中/.test(idxHost.textContent || '')) {
      idxHost.innerHTML = '<div class="hs-empty">板块数据暂不可达</div>';
    }
    if (secHost && /^加载中/.test(secHost.textContent || '')) {
      secHost.innerHTML = '<div class="hs-empty">板块数据暂不可达</div>';
    }
  }
}

var _dashTrendCharts = {};   // tile.id → echarts instance
var _dashTrendResizeHooked = false;

function _paintIndexTrend(d) {
  // 顶卡「板块·实时分时」: 5 大指数作为盘面整体走势代理(板块 tick 数据源 evening 不稳)
  // 底卡「板块·今日分时」: 4 热门板块 tick 折线(tick 可用时才出图)
  const indices = d.indices || [];
  const sectors = d.sectors || [];
  _paintTrendGrid('#index-trend-grid', indices, 'index-trend', (it, i) => `idx-${i}`);
  _paintTrendGrid('#sector-trend-grid', sectors.slice(0, 4), 'sector-trend', (it, i) => `sec-${i}`);
}

function _paintTrendGrid(sel, items, _ns, keyFn) {
  const host = document.querySelector(sel);
  if (!host) return;
  if (!items.length) {
    host.innerHTML = '<div class="hs-empty">暂无数据</div>';
    return;
  }
  host.innerHTML = items.map((it, i) => {
    const pct = Number(it.change_pct) || 0;
    const cls = pct > 0.05 ? 'up' : pct < -0.05 ? 'down' : '';
    const arrow = pct > 0 ? '+' : '';
    const last = it.last;
    const open = it.open;
    const ticks = it.ticks || [];
    const lastTime = ticks.length ? (ticks[ticks.length - 1].time || '').slice(0, 5) : '';
    const tileId = keyFn(it, i);
    // pulse_only 子行: 净流入 + 排名(板块·实时分时专用,无 tick 时也有数据)
    let pulseSub = '';
    if (it.pulse_only) {
      const parts = [];
      if (it.net_inflow_yi != null) {
        const ni = Number(it.net_inflow_yi);
        const sign = ni > 0 ? '+' : '';
        const cls = ni > 0 ? '' : (ni < 0 ? 'down' : '');
        parts.push(`<span class="${cls}">净流入 ${sign}${ni.toFixed(2)}亿</span>`);
      }
      if (it.rank_kind && it.rank_kind !== 'none') {
        const labels = [];
        if (it.rank_flow) labels.push(`流入#${it.rank_flow}`);
        if (it.rank_pct) labels.push(`涨幅#${it.rank_pct}`);
        if (labels.length) parts.push(`<span style="opacity:.7">${labels.join(' ')}</span>`);
      }
      pulseSub = parts.length ? `<div class="trend-tile-sub" style="margin-top:2px;font-size:11px">${parts.join('')}</div>` : '';
    }
    return `<div class="trend-tile ${cls}" data-tile-id="${escapeHtml(tileId)}">
      <div class="trend-tile-head">
        <span class="trend-tile-name">${escapeHtml(it.name || it.code || '—')}</span>
        <span class="trend-tile-pct ${cls}">${arrow}${pct.toFixed(2)}%</span>
      </div>
      <div class="trend-tile-chart" id="trend-chart-${escapeHtml(tileId)}"></div>
      <div class="trend-tile-sub">
        <span>开 ${open != null ? (+open).toFixed(2) : '—'}</span>
        <span>收 ${last != null ? (+last).toFixed(2) : '—'}</span>
        <span style="opacity:.65">${escapeHtml(lastTime)}</span>
      </div>
      ${pulseSub}
    </div>`;
  }).join('');
  // 渲染 sparkline — 先确保 echarts 已加载 (R232: 用 view-dash 自带 _ensureDashEcharts)
  if (typeof echarts === 'undefined') {
    _ensureDashEcharts().then(() => _renderTrendCharts(items, keyFn)).catch(() => {});
    return;
  }
  _renderTrendCharts(items, keyFn);
}

function _renderTrendCharts(items, keyFn) {
  items.forEach((it, i) => {
    const tileId = keyFn(it, i);
    const el = document.getElementById(`trend-chart-${tileId}`);
    if (!el) return;
    const up = (it.change_pct || 0) >= 0;
    const color = up ? 'var(--up)' : 'var(--down)';
    if (!it.ticks || !it.ticks.length) {
        // 兜底: 没分时数据画水平 sparkline (左→右 fill 宽度=变化幅度)
        const pct = Math.min(Math.abs(Number(it.change_pct) || 0), 5);  // cap 5%
        const widthPct = pct / 5 * 100;
        el.innerHTML = `<div style="position:relative;height:100%;display:flex;align-items:center">
          <div style="position:absolute;left:0;right:0;height:1px;background:var(--line);opacity:.5"></div>
          <div style="position:relative;height:6px;width:0;${up?'left:50%':'right:50%'};background:${color};border-radius:3px;opacity:.7;width:${widthPct}%;max-width:50%;transform-origin:${up?'left':'right'} center" title="${it.change_pct?.toFixed(2)}%"></div>
        </div>`;
        return;
    }
    const prices = it.ticks.map(t => t.price);
    const minP = Math.min(...prices), maxP = Math.max(...prices);
    // up/color 已在顶部声明
    const prevId = _dashTrendCharts[tileId];
    const opt = {
      animation: false,
      grid: { left: 0, right: 0, top: 2, bottom: 2 },
      xAxis: { type: 'category', show: false, data: it.ticks.map(t => t.time) },
      yAxis: { type: 'value', show: false, scale: true, min: minP, max: maxP },
      series: [{
        type: 'line', data: prices, showSymbol: false,
        smooth: true, lineStyle: { width: 1.6, color },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: up ? 'rgba(232,71,74,0.35)' : 'rgba(62,175,86,0.35)' },
              { offset: 1, color: 'rgba(0,0,0,0)' },
            ],
          },
        },
      }],
    };
    if (prevId && echartsCharts[prevId]) {
      echartsCharts[prevId].dispose();
    }
    const inst = echarts.init(el, null, { renderer: 'canvas' });
    inst.setOption(opt);
    _dashTrendCharts[tileId] = inst.id || tileId;
    if (!echartsCharts[inst.id]) echartsCharts[inst.id] = inst;
    // resize on dash toggle / theme change
    if (!_dashTrendResizeHooked) {
      _dashTrendResizeHooked = true;
      window.addEventListener('resize', () => {
        Object.values(_dashTrendCharts).forEach(id => {
          const c = echartsCharts[id];
          if (c && !c.isDisposed && c.getDom && c.getDom().isConnected) c.resize();
        });
      });
      // 主题切换 hook
      const observer = new MutationObserver(() => {
        Object.values(_dashTrendCharts).forEach(id => {
          const c = echartsCharts[id];
          if (c && !c.isDisposed) c.resize();
        });
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
    }
  });
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
    const tx = t.taxonomy || {};
    const l1Dot = tx.l1 && tx.l1_color
      ? `<span class="hs-tile-l1" style="display:inline-block;width:8px;height:8px;border-radius:4px;background:${escapeHtml(tx.l1_color)};margin-right:5px;vertical-align:middle;flex-shrink:0;" title="${escapeHtml(tx.l1)}"></span>`
      : '';
    return `<div class="hs-tile" title="${escapeHtml(t.name)} · 涨停 ${ztN} · 资金净流入 ${flowStr}">
      <span class="hs-tile-name">${l1Dot}${escapeHtml(t.name)}</span>
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

function _paintSignalCol(prefix, payload, animate) {
  if (!payload) return;
  const v = payload.verdict || 'cautious';
  const degraded = payload._degraded;
  const verdictEl = $(`#sig-${prefix}-verdict`);
  if (verdictEl) {
    const prev = verdictEl.className;
    verdictEl.className = `signal-verdict signal-${v}${animate ? ' fresh' : ''}${degraded ? ' signal-degraded' : ''}`;
    verdictEl.textContent = degraded ? '⚠' : (_VERDICT_LABEL[v] || '—');
    if (degraded) verdictEl.title = '数据暂不可达，最后已知值';
  }
  const pctEl = $(`#sig-${prefix}-pct`);
  if (pctEl) {
    const cp = Number(payload.change_pct) || 0;
    const sign = cp > 0 ? '+' : '';
    pctEl.className = `sig-pct ${degraded ? 'flat' : (cp > 0 ? 'up' : cp < 0 ? 'down' : 'flat')}`;
    pctEl.textContent = degraded ? '—' : `${sign}${cp.toFixed(2)}%`;
  }
  const headEl = $(`#sig-${prefix}-head`);
  if (headEl) {
    headEl.innerHTML = degraded
      ? `<span class="stale-label">${escapeHtml(payload.headline || payload.head || '—')} · 陈旧数据</span>`
      : (payload.headline || payload.head || '—');
  }
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

