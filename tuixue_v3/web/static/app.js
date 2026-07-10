/* 退学 v3 · 操作台 前端逻辑
 * v2.0 — 信封 / 并发 / SSE / 心法 / AI
 */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const ACCENT = '#d4a056';
const UP     = '#4fb074';
const DOWN   = '#d97a6c';
const INK    = '#e8e3d8';
const INK2   = '#a8a39a';
const INK3   = '#6b6660';
const GRID   = 'rgba(232,227,216,0.06)';

const echartsCharts = {};
let lastRefreshTs = 0;

// ────────────────────────────────────────────
// fetch wrapper — 自动解包 {ok,data,error,ts}
// 每个请求都有 timeout + AbortController，避免后端慢导致前端卡死
// ────────────────────────────────────────────
const API_TIMEOUTS = {
  // 默认 8s; AI 分析和数据源密集型接口单独加长
  default: 12_000,
  '/api/screen':           60_000,  // 实时选股 50 只 11s, 留 5x 缓冲
  '/api/stream/screen':    90_000,  // SSE 长流:规则 + AI fan-out, 10-15 只 × 5-12s ≈ 60s
  '/api/backtest':        120_000,  // 回测慢
  '/ai_analysis':          35_000,  // AI 分析 7 重兜底 + AI 重试
  '/api/screen/ai_aggregate': 35_000,
  '/api/stock/':           20_000,  // 个股综合接口
  '/api/market/':          15_000,  // 大盘概览 (ngrok 域名 6-11s 常见)
  '/api/dragons':          20_000,  // 龙头榜 (冷启动 11s, 热后 0.1s)
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

async function _fetchWithTimeout(path, opts = {}) {
  const timeout = opts.timeout != null ? opts.timeout : _timeoutFor(path);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    return await fetch(path, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
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
  if (name === 'dash')    refreshTicker();
  if (name === 'optimize') loadReports();
  if (name === 'laws')    renderLawsOnce();
  if (name === 'screen')  loadGlobalSentiment(false);
}

// ────────────────────────────────────────────
// 全局情绪(美/韩 → A 股)加载 + 渲染
// ────────────────────────────────────────────
let _globalSentimentCache = null;
let _globalSentimentTs = 0;
async function loadGlobalSentiment(force = false) {
  const head = $('#global-sentiment-pill');
  if (!head) return;
  const now = Date.now();
  if (!force && _globalSentimentCache && (now - _globalSentimentTs) < 60_000) {
    renderGlobalSentiment(_globalSentimentCache);
    return;
  }
  try {
    const res = await api(`/api/global/sentiment${force ? '?force=true' : ''}`);
    if (res) {
      _globalSentimentCache = res;
      _globalSentimentTs = now;
      renderGlobalSentiment(res);
      paintTickerbarRisk(res.sentiment || 'neutral');
    }
  } catch (e) {
    const meta = $('#global-meta');
    if (meta) meta.textContent = '拉取失败:' + e.message;
  }
}

function renderGlobalSentiment(data) {
  if (!data) return;
  const score = data.sentiment_score ?? 50;
  const sent  = data.sentiment || 'neutral';
  const pill  = $('#global-sentiment-pill');
  const num   = $('#global-score');
  const stats = $('#global-stats');
  const impact = $('#global-sector-impact');
  const meta  = $('#global-meta');

  if (num) num.textContent = String(score);
  if (pill) {
    const label = sent === 'risk_on' ? '🟢 RISK-ON · 风险偏好' :
                  sent === 'risk_off' ? '🔴 RISK-OFF · 风险规避' : '⚪ NEUTRAL · 中性';
    pill.textContent = label;
    pill.className = `sentiment-pill ${sent === 'risk_on' ? 'sentiment-good' :
                                   sent === 'risk_off' ? 'sentiment-bad' : 'sentiment-neutral'}`;
  }

  // 指数/美/韩 leaders 摘要
  const idxTxt = (data.indices || []).slice(0, 5)
    .map(i => `${escapeHtml(i.name)} <b style="color:${i.change_pct >= 0 ? 'var(--up)' : 'var(--down)'}">${fmtPct(i.change_pct)}</b>`)
    .join(' · ');
  const usTxt = (data.us_leaders || []).slice(0, 3)
    .map(s => `${escapeHtml(s.ticker)} <b style="color:${(s.change_pct||0) >= 0 ? 'var(--up)' : 'var(--down)'}">${fmtPct(s.change_pct)}</b>`)
    .join(' · ');
  const krTxt = (data.kr_leaders || []).slice(0, 3)
    .map(s => `${escapeHtml(s.ticker)} <b style="color:${(s.change_pct||0) >= 0 ? 'var(--up)' : 'var(--down)'}">${fmtPct(s.change_pct)}</b>`)
    .join(' · ');
  if (stats) stats.innerHTML =
    `<span>📈 指数 ${idxTxt || '—'}</span>` +
    (usTxt ? `<span>🇺🇸 美股 ${usTxt}</span>` : '') +
    (krTxt ? `<span>🇰🇷 韩股 ${krTxt}</span>` : '');

  // 板块 impact: A 股受美/韩影响的板块
  const impacts = Object.entries(data.sector_impact || {})
    .filter(([_, v]) => v && v.score)
    .sort((a, b) => Math.abs(b[1].score) - Math.abs(a[1].score))
    .slice(0, 6);
  if (impact) impact.innerHTML = impacts.length ? impacts.map(([name, v]) => {
    const dir = v.direction === '+' ? 'good' : v.direction === '-' ? 'bad' : 'dim';
    const note = v.note ? `<div class="mainline-meta dim">${escapeHtml(v.note)}</div>` : '';
    return `<div class="mainline-card">
      <div class="mainline-name">${escapeHtml(name)}</div>
      <div class="mainline-meta"><span class="${dir}">${v.direction === '+' ? '↗ 利好' : v.direction === '-' ? '↘ 利空' : '· 中性'}</span></div>
      ${note}
    </div>`;
  }).join('') : '<div class="caption dim" style="grid-column:1/-1;text-align:center;padding:1rem 0">暂无显著板块影响</div>';

  if (meta) {
    const ts = data.ts ? new Date(data.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    const cached = data.cached ? '·缓存' : '';
    meta.textContent = `更新 ${ts} ${cached} · 60s 缓存`;
  }
}

function paintTickerbarRisk(sent) {
  const bar = $('.tickerbar');
  if (!bar) return;
  bar.classList.remove('risk-on', 'risk-off', 'risk-neutral');
  bar.classList.add(sent === 'risk_on' ? 'risk-on' :
                    sent === 'risk_off' ? 'risk-off' : 'risk-neutral');
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
    if ($('#m-zt')) $('#m-zt').textContent = data.limit_up || '—';
    if ($('#m-amt')) {
      const idx = indices[0] || {};
      $('#m-amt').textContent = idx.amount ? fmtAmt(idx.amount) : '—';
    }
    if ($('#m-cache')) {
      try {
        const h = await api('/api/health');
        $('#m-cache').textContent = `${h.cache?.spot?.hit_rate ?? 0}%`;
      } catch { $('#m-cache').textContent = '—'; }
    }
    // 顶部小时间戳
    if ($('#ts-stamp')) {
      const d = new Date(lastRefreshTs * 1000);
      $('#ts-stamp').textContent = `已刷新 ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
    }
  } catch (e) {
    bar.innerHTML = '<div class="ticker-empty">市场数据暂不可达 · ' + e.message + '</div>';
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
// SCREEN — SSE 流式 (rule_done → ai_done ×N → ai_aggregate → done → result)
// ────────────────────────────────────────────
$('#run-screen')?.addEventListener('click', () => {
  const date = $('#screen-date').value.trim() || '';
  const btn = $('#run-screen');
  if (btn.disabled) return;
  btn.disabled = true;
  btn.querySelector('span').textContent = '扫描中…';
  $('#screen-count').textContent = '…';
  $('#screen-meta').textContent = '运行中 …';
  $('#screen-table tbody').innerHTML = '<tr><td colspan="10" class="empty">扫描中 …</td></tr>';
  $('#ai-aggregate-card').hidden = true;

  const url = `/api/stream/screen?date=${encodeURIComponent(date)}&mode=live`;
  let resultData = null;
  const source = new EventSource(url);
  const aiCells = {}; // code -> ai cell element (供 ai_done 时增量更新)
  source.addEventListener('phase', (ev) => {
    let payload = {};
    try { payload = JSON.parse(ev.data); } catch {}
    if (payload.phase === 'start') {
      $('#screen-meta').textContent = '开始扫描 (L1-L4 规则) …';
    } else if (payload.phase === 'rule_done') {
      $('#screen-meta').textContent = `规则过完 · ${payload.n_picks} 只候选 · 进入 AI 阶段 (缓存命中跳过已通过) …`;
      $('#screen-count').textContent = payload.n_picks;
    } else if (payload.phase === 'ai_done' && payload.code) {
      const cell = aiCells[payload.code];
      if (cell) _renderAIBadgeCell(cell, payload.ai);
      const roleCell = document.querySelector(`td.role-cell[data-code="${payload.code}"]`);
      if (roleCell) _renderRoleCellAt(roleCell, payload.ai);
      // 累计 AI 完成数
      const doneCount = Object.values(aiCells).filter(c => c.querySelector('.ai-badge')?.dataset.loaded === '1').length;
      $('#screen-meta').textContent = `AI 阶段 · ${doneCount}/${Object.keys(aiCells).length} 完成`;
      renderStockAIInline(payload.code, payload.ai);
    } else if (payload.phase === 'ai_aggregate' && payload.aggregate) {
      renderAIAggregate(payload.aggregate);
    } else if (payload.phase === 'done') {
      $('#screen-meta').textContent = `完成 · ${payload.n_picks} 只候选 · 总耗时 ${payload.elapsed_sec || '?'}s`;
    }
  });

  source.addEventListener('result', (ev) => {
    try {
      resultData = JSON.parse(ev.data);
      // 注: SSE result 事件 payload 是 {ok,data,error,ts} 信封
      const data = resultData.data || resultData;
      renderScreenResults(data, aiCells);
      toast(`扫描完成 · ${(data.candidates || []).length} 只候选 · 用时 ${data.elapsed_sec || '?'}s`, 'success');
    } catch (e) {
      toast('结果解析失败：' + e.message, 'error');
    }
    source.close();
    btn.disabled = false;
    btn.querySelector('span').textContent = '扫描';
  });

  source.addEventListener('error', () => {
    // EventSource 默认 3 次重连;这里直接 close + 报错
    source.close();
    btn.disabled = false;
    btn.querySelector('span').textContent = '扫描';
    if (!resultData) {
      toast('SSE 连接失败 (检查 server 是否启动)', 'error', 5000);
      $('#screen-meta').textContent = 'SSE 连接失败';
      $('#screen-table tbody').innerHTML = '<tr><td colspan="10" class="empty">SSE 失败</td></tr>';
    }
  });
});

function _renderAIBadgeCell(cellEl, ai) {
  if (!cellEl) return;
  if (!ai) {
    cellEl.innerHTML = '<span class="ai-badge" style="background:rgba(255,255,255,0.05);color:var(--ink3)">…</span>';
    return;
  }
  const verdict = ai.verdict || '-';
  const conv = ai.conviction ?? 0;
  const fromCache = ai.from_cache ? '·缓存' : '';
  cellEl.innerHTML = `<span class="ai-badge v-${verdict}" data-loaded="1" title="${ai.summary || ''}">${verdict}·${conv}${fromCache ? '<sup style="font-size:.6em;opacity:.6">'+fromCache+'</sup>' : ''}</span>`;
}

// 角色定位(龙头/中军/杂毛) — 单独 cell 渲染
const _ROLE_LABELS = { '龙头': '龙', '中军': '中', '杂毛': '杂' };
function _roleToSlug(role) {
  if (role === '龙头') return 'dragon';
  if (role === '杂毛') return 'stray';
  return 'mid';
}
function _renderRoleCell(ai) {
  if (!ai) return '<span class="role-badge" data-loaded="0" title="AI 评估中">…</span>';
  const role = ai.role || '中军';
  const label = _ROLE_LABELS[role] || '中';
  return `<span class="role-badge role-${_roleToSlug(role)}" data-loaded="1" title="板块内角色:${role}">${label}</span>`;
}
function _renderRoleCellAt(cellEl, ai) {
  if (!cellEl) return;
  cellEl.innerHTML = _renderRoleCell(ai);
}

function renderScreenResults(data, aiCells = {}) {
  const tbody = $('#screen-table tbody');
  const cands = data.candidates || [];
  $('#screen-count').textContent = cands.length;
  const pre = data.stats_by_layer?.prefilter || {};
  const l1 = data.stats_by_layer?.l1 || {};
  const l2 = data.stats_by_layer?.l2 || {};
  const l3 = data.stats_by_layer?.l3 || {};
  const l4 = data.stats_by_layer?.l4 || {};
  const agg = data.ai_aggregate || null;
  // meta: 包含 prefilter 信息（如果跑了）
  const boardExcl = pre.board_excluded;
  const boardPart = (boardExcl && boardExcl.count > 0)
    ? `排除${boardExcl.count}只${(boardExcl.boards || []).join('/')} · `
    : '';
  const prePart = pre.skipped
    ? `Prefilter:跳过(${pre.reason || '-'}) · `
    : pre.after_filter !== undefined
      ? `Prefilter:${pre.input_size || '?'}→${pre.after_filter}(涨停∩热门板块) · `
      : '';
  const aiPart = agg ? ` · AI榜:${(agg.ranking || []).length} 条` : '';
  $('#screen-meta').textContent =
    boardPart + prePart + `L1:${l1.passed || 0} → L2:${l2.passed || 0} → L3:${l3.passed || 0} → L4:${l4.passed || 0}` +
    ` · ${data.elapsed_sec || '?'}s${aiPart}`;

  // 综合榜区块
  if (agg) renderAIAggregate(agg);

  if (!cands.length) {
    // 显示 block 原因(L2 高潮不开仓 / L1 全军覆没 / etc)
    const reason = data.reason || '';
    let detail = '';
    if (l2?.cycle_blocked > 0) {
      const cd = l2.cycle_detail || {};
      detail = `<br><span class="caption dim" style="margin-top:.5rem;display:inline-block">L2 block: 阶段=${cd.phase || '?'} · 情绪分=${cd.emotion_score || '?'} · 涨停=${cd.zt_count || '?'} · ${cd.block_reason || ''}</span>`;
    } else if (l1?.passed === 0) {
      detail = `<br><span class="caption dim" style="margin-top:.5rem;display:inline-block">L1 全军覆没: 低流动性 ${l1.low_liquidity || 0} · 量能下行 ${l1.vol_down || 0} · 黑名单 ${l1.blacklisted || 0} · 烂基本 ${l1.bad_fundamental || 0}</span>`;
    }
    tbody.innerHTML = `<tr><td colspan="10" class="empty center">
      <div style="padding:1.5rem 0">
        <div style="font-size:1.1rem;color:var(--ink-2,#a8a39a);margin-bottom:.5rem">🛑 当日无候选 (${reason || 'no_picks'})</div>
        <div class="caption dim">191 涨停池 → 38 热门板块交集 → L1:${l1.passed || 0} → L2:${l2.passed || 0} → L3:${l3.passed || 0} → L4:${l4.passed || 0}</div>
        ${detail}
        <div class="caption dim" style="margin-top:.8rem">💡 退学铁律:情绪高潮不开仓 · 等待次日"退潮"或"启动"信号</div>
      </div>
    </td></tr>`;
    return;
  }
  tbody.innerHTML = cands.map(c => {
    const code = c.code || '';
    const name = c.name || '';
    const sector = c.sector || '—';
    const rr = c.rr_ratio ?? c.RR ?? '—';
    const turnover = c.换手率 ?? c.turnover ?? '—';
    const chg = c.涨跌幅 ?? c.change_pct ?? 0;
    const liuTong = c.流通市值 ?? c.liutong ?? '—';
    // 2026-07 推荐池加分信息
    const zt = c.recent_zt_count ?? 0;
    const hsRank = c.recent_hot_sector_rank;
    const hsName = c.recent_hot_sector_name || '';
    const ztBadge = zt > 0
      ? `<span class="tag tag-zt" title="近 ${pre.recent_zt_days || 3} 天涨停次数">⚡${zt}</span>`
      : '';
    const hsBadge = hsRank
      ? `<span class="tag tag-hs" title="${hsName} 今日热门榜排名">🔥#${hsRank}</span>`
      : '';
    const ai = c.ai || null;
    const aiBadgeInitial = ai ? '' : 'data-loaded="0"';
    return `<tr class="pick-row">
      <td><button class="linkbtn" data-stock="${code}">${code}</button></td>
      <td>${name}</td>
      <td>${sector} ${ztBadge}${hsBadge}</td>
      <td class="num">${typeof rr === 'number' ? rr.toFixed(2) : rr}</td>
      <td class="num">${typeof turnover === 'number' ? turnover.toFixed(2) + '%' : turnover}</td>
      <td class="num" style="color:${colorFor(chg)}">${fmtPct(chg)}</td>
      <td class="num">${typeof liuTong === 'number' ? fmtAmt(liuTong) : liuTong}</td>
      <td class="role-cell" data-code="${code}" ${aiBadgeInitial}>${_renderRoleCell(ai)}</td>
      <td class="ai-cell" data-code="${code}" ${aiBadgeInitial}>${ai ? '' : '<span class="ai-badge" data-loaded="0" style="background:rgba(255,255,255,0.05);color:var(--ink3)">…</span>'}</td>
      <td><button class="linkbtn" data-stock="${code}">详情 →</button></td>
    </tr>
    <tr class="ai-row" data-code="${code}" hidden>
      <td colspan="10"><div class="ai-inline-host" data-code="${code}">${_aiInlineInitialHtml(ai)}</div></td>
    </tr>`;
  }).join('');

  // 收集 ai-cell / role-cell 引用,供 SSE ai_done 时增量填
  tbody.querySelectorAll('td.ai-cell[data-code]').forEach(cell => {
    aiCells[cell.dataset.code] = cell;
    // 如果 result 事件里已带 ai,直接填一次
    const c = cands.find(x => x.code === cell.dataset.code);
    if (c && c.ai) {
      _renderAIBadgeCell(cell, c.ai);
      const roleCell = tbody.querySelector(`td.role-cell[data-code="${c.code}"]`);
      if (roleCell) _renderRoleCellAt(roleCell, c.ai);
    }
  });

  tbody.querySelectorAll('button[data-stock]').forEach(btn => {
    btn.addEventListener('click', () => gotoStock(btn.dataset.stock));
  });

  // AI 行展开:点 ai-cell 的 badge 显示/隐藏 紧接的 ai-row
  tbody.querySelectorAll('td.ai-cell[data-code]').forEach(cell => {
    cell.addEventListener('click', () => {
      const code = cell.dataset.code;
      const row = tbody.querySelector(`tr.ai-row[data-code="${code}"]`);
      if (row) row.hidden = !row.hidden;
    });
  });
}

function _aiInlineInitialHtml(ai) {
  if (!ai) return '<p class="caption dim">AI 分析中 …</p>';
  const v = ai.verdict || '-';
  const c = ai.conviction ?? 0;
  const role = ai.role || '中军';
  return `<div class="ai-inline">
    <div class="ai-inline-head">
      <span class="ai-verdict v-${v}">${v}</span>
      <span class="role-badge role-${_roleToSlug(role)}" title="板块内角色定位">${role}</span>
      <span class="ai-conviction-num">${c} / 100</span>
      <span class="ai-status" style="font-size:.7rem;opacity:.6">${ai.from_cache ? '·缓存命中' : '·实时'}</span>
    </div>
    <p class="ai-summary">${ai.summary || ''}</p>
    <div class="ai-detail">${_renderAIRules(ai)}</div>
  </div>`;
}

function _renderAIRules(ai) {
  const passed = (ai.rules_passed || []).slice(0, 5);
  const failed = (ai.rules_failed || []).slice(0, 5);
  const risks  = (ai.key_risks  || []).slice(0, 4);
  const sec = (s, items, color) => `<div class="ai-rule-section">
    <span class="ai-rule-label" style="color:${color}">${s}</span>
    <ul>${(items || []).map(x => `<li>${escapeHtml(x)}</li>`).join('') || '<li class="dim">—</li>'}</ul>
  </div>`;
  return sec('✓ PASSED', passed, UP) + sec('✗ FAILED', failed, DOWN) + sec('! RISK', risks, '#d4a056');
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 龙头全涨停表 · 行展开 AI 6维评分卡(纯本地渲染,无 LLM)
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

// 全局 renderStockAIInline — 在 ai_done SSE 事件 + renderScreenResults 都用
function renderStockAIInline(code, ai) {
  const host = document.querySelector(`tr.ai-row[data-code="${code}"] .ai-inline-host`);
  if (!host) return;
  host.innerHTML = _aiInlineInitialHtml(ai);
}

function renderAIAggregate(agg) {
  const card = $('#ai-aggregate-card');
  if (!card) return;
  const list = $('#ai-aggregate-list');
  const overall = $('#ai-aggregate-overall');
  if (!agg || (!agg.ranking?.length && !agg.overall_view)) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  overall.textContent = agg.overall_view || '';
  list.innerHTML = (agg.ranking || []).map((r, i) => {
    const rec = r.recommendation || '观望';
    const cls = rec.includes('强烈') ? 'v-buy' : rec.includes('买入') ? 'v-buy' :
                rec.includes('回避') ? 'v-avoid' : 'v-wait';
    const role = r.role || '中军';
    return `<li class="ai-rank-item">
      <span class="ai-rank-num">${i + 1}</span>
      <button class="linkbtn ai-rank-code" data-code="${r.code}">${r.code}</button>
      <span class="ai-rank-name">${escapeHtml(r.name || '')}</span>
      <span class="role-badge role-${_roleToSlug(role)}" title="板块内角色:${role}">${role}</span>
      <span class="ai-badge ${cls}">${escapeHtml(rec)}</span>
      <span class="ai-rank-reason">${escapeHtml(r.reason || '')}</span>
    </li>`;
  }).join('');
  list.querySelectorAll('.ai-rank-code').forEach(btn => {
    btn.addEventListener('click', () => gotoStock(btn.dataset.code));
  });
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

async function loadStockDetail(code) {
  code = code.trim().padStart(6, '0');
  currentStockCode = code;
  toast(`加载 ${code} …`);
  try {
    const data = await api(`/api/stock/${code}`);
    renderStockDetail(code, data);
    // 记录到历史(从 stock 详情接口拿 name)
    const name = (data.quote && data.quote.name) || (data.name) || code;
    _addHist(code, name);
  } catch (e) {
    toast(`加载失败：${e.message}`, 'error');
  }
}

function renderStockDetail(code, data) {
  const q = data.quote || {};
  const seats = data.seats || {};
  const flow = data.fund_flow || {};
  const today = flow.today || {};
  const extras = data.extras || {};

  const name = q.name || data.name || code;
  const price = parseFloat(q.最新价 ?? q.price ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);

  $('#stock-title').textContent = name;
  $('#stock-code').textContent = code;
  $('#stock-sub').textContent = `${name} · ${code} · ${q._source || ''} ${q._fetch_time || ''}`.trim();

  // 板块/行业 tags
  const tags = [];
  if (extras.is_chinext_star) tags.push('<span class="badge badge-warn">创业板/科创</span>');
  if (seats.blacklisted) tags.push('<span class="badge badge-down">黑名单</span>');
  if (chg >= 9.7) tags.push('<span class="badge badge-up">涨停</span>');
  else if (chg <= -9.7) tags.push('<span class="badge badge-down">跌停</span>');
  const sectorTag = $('#stock-tags-host');
  if (sectorTag) sectorTag.innerHTML = tags.join(' ');

  // ─── 主指标 8 卡 ───
  $('#q-price').textContent = fmtN(price, 2);
  $('#q-change').textContent = fmtPct(chg) + (price && q.昨收 ? ` (${chg >= 0 ? '+' : ''}${(price - q.昨收).toFixed(2)})` : '');
  $('#q-change').style.color = colorFor(chg);

  const mainNet = today.main_net ?? 0;
  $('#q-main').textContent = fmtN(mainNet, 0);
  $('#q-main').style.color = colorFor(mainNet);
  const superBig = (today.super_net || 0) + (today.big_net || 0);
  $('#q-main-sub').textContent = `超大+大单 ${fmtN(superBig, 0)} 万`;

  $('#q-turnover').textContent = q.换手率 != null ? q.换手率.toFixed(2) : '—';
  $('#q-turnover').style.color = (q.换手率 || 0) > 10 ? UP : ((q.换手率 || 0) > 5 ? ACCENT : INK);
  $('#q-volratio').textContent = `量比 ${q.量比 != null ? q.量比.toFixed(2) : '—'}`;

  $('#q-amp').textContent = extras.amplitude_pct != null ? extras.amplitude_pct.toFixed(2) : '—';
  $('#q-amp').style.color = (extras.amplitude_pct || 0) > 7 ? UP : INK;
  const p5 = extras.pct_5d;
  $('#q-5d').textContent = `5日 ${p5 != null ? (p5 >= 0 ? '+' : '') + p5.toFixed(2) + '%' : '—'}`;
  $('#q-5d').style.color = colorFor(p5);

  const mcap = q.总市值 || 0;
  $('#q-mcap').textContent = mcap > 0 ? mcap.toFixed(1) : '—';
  const cmcap = q.流通市值 || 0;
  $('#q-mcap-sub').textContent = `流通 ${cmcap > 0 ? cmcap.toFixed(1) + ' 亿' : '—'}`;

  $('#q-pe').textContent = q.市盈率 > 0 ? q.市盈率.toFixed(2) : '—';
  $('#q-pe').style.color = (q.市盈率 > 0 && q.市盈率 < 0) ? DOWN : INK;
  $('#q-pe-sub').textContent = q.市盈率 > 0 ? `PE 动 · ${q.市盈率 > 50 ? '高估' : q.市盈率 < 0 ? '亏损' : '合理'}` : '亏损/暂无';

  $('#q-hl').innerHTML = `<span style="color:${UP}">${fmtN(q.最高, 2)}</span> / <span style="color:${DOWN}">${fmtN(q.最低, 2)}</span>`;
  $('#q-hl-sub').textContent = `开 ${fmtN(q.今开, 2)} · 昨收 ${fmtN(q.昨收, 2)}`;

  $('#q-seats').textContent = seats.seat_count || 0;
  $('#q-seats-sub').textContent = seats.blacklisted
    ? `近 ${seats.total_lhb_rows || 0} 条 · ⚠ 黑名单`
    : `近 ${seats.total_lhb_rows || 0} 条`;

  // ─── 当日明细 + 涨停/跌停 ───
  $('#q-open').textContent = fmtN(q.今开, 2);
  $('#q-prev').textContent = fmtN(q.昨收, 2);
  $('#q-high').textContent = fmtN(q.最高, 2);
  $('#q-low').textContent = fmtN(q.最低, 2);
  $('#q-lu').textContent = extras.limit_up_price != null ? extras.limit_up_price.toFixed(2) : '—';
  $('#q-lu').style.color = extras.limit_up_price && price >= extras.limit_up_price - 0.001 ? UP : INK;
  $('#q-ld').textContent = extras.limit_dn_price != null ? extras.limit_dn_price.toFixed(2) : '—';
  $('#q-ld').style.color = extras.limit_dn_price && price <= extras.limit_dn_price + 0.001 ? DOWN : INK;
  const vol = q.成交量 || 0;
  $('#q-vol').textContent = vol > 0 ? (vol / 1e4).toFixed(1) + ' 万手' : '—';
  $('#q-amt').textContent = q.成交额 > 0 ? (q.成交额 / 1e8).toFixed(2) + ' 亿' : '—';
  const p20 = extras.pct_20d;
  $('#q-20d').innerHTML = p20 != null ? `<span style="color:${colorFor(p20)}">${(p20 >= 0 ? '+' : '') + p20.toFixed(2)}%</span>` : '—';
  $('#q-v5').textContent = extras.vol_5d_avg ? (extras.vol_5d_avg / 1e4).toFixed(1) + ' 万手' : '—';

  // ─── 连板历史 ───
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
  drawKlineChart(data.kline || []);
  renderFlowKpi(flow.history || []);
  renderKlineKpi(data.kline || []);
  renderSeatsTable(seats.rows || [], seats);
  renderHolders(data.holders || null);

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

function drawKlineChart(kline) {
  const dom = $('#kline-chart');
  if (!dom) return;
  if (echartsCharts.kline) echartsCharts.kline.dispose();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.kline = chart;
  if (!kline.length) {
    chart.setOption(emptyChartOption('暂无 K 线数据'));
    return;
  }
  const dates = kline.map(k => k.date);
  const ohlc = kline.map(k => [k.open, k.close, k.low, k.high]);
  // 用后端预计算的 MA，回退到本地计算
  const ma5  = kline[0].ma5  != null ? kline.map(k => k.ma5)  : ma(ohlc.map(o => o[1]), 5);
  const ma10 = kline[0].ma10 != null ? kline.map(k => k.ma10) : ma(ohlc.map(o => o[1]), 10);
  const ma20 = kline[0].ma20 != null ? kline.map(k => k.ma20) : ma(ohlc.map(o => o[1]), 20);
  const ma60 = kline[0].ma60 != null ? kline.map(k => k.ma60) : ma(ohlc.map(o => o[1]), 60);
  // 成交量（涨绿跌红）
  const barColors = kline.map(k => (k.close >= k.open) ? UP : DOWN);
  const vols = kline.map(k => k.volume || 0);
  chart.setOption({
    backgroundColor: 'transparent',
    grid: [
      { left: 50, right: 50, top: 28, height: '60%' },
      { left: 50, right: 50, top: '75%', height: '18%' },
    ],
    tooltip: { trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK }, axisPointer: { type: 'cross', lineStyle: { color: ACCENT } } },
    legend: { data: ['K线','MA5','MA10','MA20','MA60','量'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 8 },
    xAxis: [
      { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 } },
      { type: 'category', gridIndex: 1, data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
      { gridIndex: 1, scale: true, splitNumber: 2, axisLabel: { color: INK2, fontSize: 9, formatter: v => (v/1e4).toFixed(1)+'万' }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0,1] }, { type: 'slider', height: 18, bottom: 8, textStyle: { color: INK2 }, xAxisIndex: [0,1] }],
    series: [
      { name: 'K线', type: 'candlestick', data: ohlc, itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN } },
      { name: 'MA5',  type: 'line', data: ma5,  smooth: true, lineStyle: { color: '#e8b75a', width: 1 }, symbol: 'none' },
      { name: 'MA10', type: 'line', data: ma10, smooth: true, lineStyle: { color: '#7b9bd1', width: 1 }, symbol: 'none' },
      { name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { color: ACCENT, width: 1.2 }, symbol: 'none' },
      { name: 'MA60', type: 'line', data: ma60, smooth: true, lineStyle: { color: '#a78bcf', width: 1.2 }, symbol: 'none' },
      { name: '量',   type: 'bar',  xAxisIndex: 1, yAxisIndex: 1, data: vols.map((v, i) => ({ value: v, itemStyle: { color: barColors[i] } })) },
    ],
  });
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
    ['股东户数', (holders.holder_total || 0).toLocaleString(), INK1, holders.report_date || ''],
    ['户均持股', avg > 0 ? avg.toLocaleString() + ' 股' : '—', INK1],
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
    drawIntraDayChart(code, date, [], null);
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

  drawIntraDayChart(code, date, ticks, openRef);
}

function drawIntraDayChart(code, date, ticks, openRef) {
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
  // 开盘基准虚线
  const refLine = times.map(_ => openRef);

  // 量能柱：来自 volume_hand（sina=股数, tencent=手数, akshare=手数）；按颜色涨跌分
  const volBars = ticks.map((t, i) => {
    const next = ticks[i + 1];
    const close = t.price;
    const open = (i === 0) ? openRef : (next ? next.price : close);
    const isUp = close >= open;
    return {
      value: t.volume_hand || 0,
      itemStyle: { color: isUp ? UP : DOWN, opacity: 0.55 },
    };
  });

  chart.setOption({
    backgroundColor: 'transparent',
    title: { text: `${code}  ${date}  分时`, textStyle: { color: INK2, fontSize: 11 }, left: 8, top: 4 },
    grid: [
      { left: 56, right: 24, top: 30, height: '58%' },
      { left: 56, right: 24, top: '74%', height: '22%' },
    ],
    tooltip: {
      trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK, fontSize: 11 },
      formatter: (params) => {
        if (!params || !params.length) return '';
        const t = params[0].axisValue;
        let s = `<div style="color:${INK2}">${t}</div>`;
        params.forEach(p => {
          const color = p.seriesName.includes('价') || p.seriesName === '价格' ? (p.data >= (openRef || 0) ? UP : DOWN) : INK2;
          const val = p.seriesName === '成交量'
            ? (p.value >= 1e4 ? (p.value / 1e4).toFixed(2) + ' 万' : p.value.toFixed(0))
            : (typeof p.value === 'number' ? p.value.toFixed(2) : p.value);
          s += `<div>${p.marker} ${p.seriesName}: <span style="color:${color};font-weight:600">${val}</span></div>`;
        });
        return s;
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { textStyle: { color: INK2, fontSize: 10 }, top: 4, right: 8, data: ['价格', '开盘', '成交量'] },
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 }, splitLine: { show: false } },
      { type: 'category', data: times, gridIndex: 1, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 9, interval: Math.max(1, Math.floor(times.length / 8)) }, splitLine: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
      { gridIndex: 1, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 9 } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ],
    series: [
      { name: '价格', type: 'line', data: prices, showSymbol: false, smooth: false, lineStyle: { color: ACCENT, width: 1.6 }, itemStyle: { color: ACCENT }, areaStyle: { color: 'rgba(212,160,86,0.08)' } },
      { name: '开盘', type: 'line', data: refLine, showSymbol: false, lineStyle: { color: INK2, type: 'dashed', width: 1 } },
      { name: '成交量', type: 'bar', data: volBars, xAxisIndex: 1, yAxisIndex: 1, barWidth: '70%' },
    ],
  });
}

// ────────────────────────────────────────────
// STOCK 内部 tab
// ────────────────────────────────────────────
let currentStockCode = null;
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
  $('#optimize-status').textContent = '正在跑网格扫描，预计 5-15 分钟 …';
  toast('开始参数调优，请耐心等待', 'info', 3000);
  try {
    const data = await api('/api/optimize', { method: 'POST' });
    $('#optimize-status').textContent = `完成 · 用时 ${data.elapsed_sec || '?'}s`;
    toast('调优完成，已写入报告目录', 'success');
    loadReports();
  } catch (e) {
    toast('调优失败：' + e.message, 'error');
    $('#optimize-status').textContent = '运行失败';
  } finally {
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  }
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
};
$$('[data-jump]').forEach(el => {
  el.addEventListener('click', () => showView(el.dataset.jump));
});

$('#refresh-ticker')?.addEventListener('click', () => {
  refreshTicker();
  toast('已刷新');
});

window.addEventListener('resize', () => {
  Object.values(echartsCharts).forEach(c => c && c.resize());
});

// 启动
refreshTicker();
setInterval(refreshTicker, 30 * 1000);

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

  try {
    const sec = await api(`/api/stock/${code}/sector`) || {};
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
                  ${(n.hit_reason || '').split(' · ').map(r => `<span class="chip" style="color:${ACCENT}">${escapeHtml(r)}</span>`).join('')}
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
  loadStockLimitUp(code, sec.sw || sec.csrc || sec.gics);
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

    host.innerHTML = `
      ${todayHtml}
      ${recentHtml}
      ${sectorHtml}
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

async function _reviewLoadList() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="15" class="dim center">加载中…</td></tr>';
  try {
    const r = await _fetchWithTimeout('/api/review/trades?limit=80&since_days=180');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    _reviewState.trades = (j.data || []).map(t => _reviewEnrichRow(t));
    _reviewRender();
    _reviewRefreshFlows();
    _reviewLoadStats();
    $('#review-ts').textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="15" class="dim center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function _reviewEnrichRow(t) {
  // 找该笔交易的最新价 / 盈亏 (用 last_review 不够,价格字段在 t 上)
  t._direction_cls = t.direction === 'buy' ? 'cell-up' : 'cell-down';
  return t;
}

function _reviewRender() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  if (!_reviewState.trades.length) {
    tbody.innerHTML = '<tr><td colspan="15" class="dim center">暂无交易 · 上面录入第一笔</td></tr>';
    return;
  }
  tbody.innerHTML = _reviewState.trades.map(t => {
    const flow = _reviewState.flows.get(t.code) || {};
    const main = _reviewPct(flow.main_pct);
    const retail = _reviewPct(flow.retail_pct);
    const fund = _reviewPct(flow.fund_pct);
    const rev = t.last_review || {};
    return `
      <tr data-trade-id="${t.id}">
        <td><code>${escapeHtml(t.code)}</code></td>
        <td>${escapeHtml(t.name || '—')}</td>
        <td class="${t._direction_cls}">${_reviewDirection(t.direction)}</td>
        <td class="cell-num">${_reviewFmtNum(t.price, 2)}</td>
        <td class="cell-num">${t.shares}</td>
        <td class="caption dim">${escapeHtml((t.occurred_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td class="cell-num ${main.cls}">${main.text}</td>
        <td class="cell-num ${retail.cls}">${retail.text}</td>
        <td class="cell-num ${fund.cls}">${fund.text}</td>
        <td>${_reviewRulePills(rev.rules_passed, 'pass')}</td>
        <td>${_reviewRulePills(rev.rules_failed, 'fail')}</td>
        <td>${_reviewConflictBadge(rev.rules_conflict_count)}</td>
        <td>${escapeHtml(rev.mistake_pattern || '—')}</td>
        <td class="caption" title="${escapeHtml(rev.ai_advice || '')}">${escapeHtml(rev.ai_advice || (rev.summary || '—').slice(0, 30))}</td>
        <td>
          <button class="btn-mini primary" onclick="_reviewRun(${t.id})">AI 复盘</button>
          <button class="btn-mini danger"  onclick="_reviewDelete(${t.id})">删</button>
        </td>
      </tr>
    `;
  }).join('');
}

async function _reviewRefreshFlows() {
  const codes = [...new Set(_reviewState.trades.map(t => t.code))].filter(Boolean);
  if (!codes.length) return;
  try {
    const r = await _fetchWithTimeout('/api/capital_flow?codes=' + codes.join(','));
    if (!r.ok) return;
    const j = await r.json();
    (j.data?.flows || []).forEach(f => {
      _reviewState.flows.set(f.code, f);
    });
    _reviewRender();
  } catch (e) {
    console.warn('capital_flow refresh failed', e);
  }
}

function _reviewStartFlowsPolling() {
  if (_reviewState.flowsTimer) clearInterval(_reviewState.flowsTimer);
  _reviewState.flowsTimer = setInterval(_reviewRefreshFlows, 10000);
}

async function _reviewRun(tradeId) {
  const btn = document.querySelector(`tr[data-trade-id="${tradeId}"] .btn-mini.primary`);
  if (btn) { btn.disabled = true; btn.textContent = 'AI 复盘中…'; }
  // 先开 toast 提示(后端 55s+ 跑)
  showToast(`复盘 #${tradeId} AI 调用中…约需 1 分钟`, 'info');
  // 直接走普通 POST(最稳,不走 SSE — SSE 30s timeout 不够,容易被代理切断)
  try {
    const r = await _fetchWithTimeout(`/api/review/trades/${tradeId}/review?force=true`, {
      method: 'POST',
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    if (j.ok) {
      showToast(`复盘完成: ${j.data?.verdict || '?'} ${j.data?.score || '?'}分`, 'success');
      _reviewLoadList();
    } else {
      showToast(`复盘失败: ${j.error || '未知错误'}`, 'error');
    }
  } catch (e) {
    showToast(`复盘超时/失败: ${e.message}`, 'error');
    console.error('review_trade failed', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'AI 复盘'; }
  }
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
  list.innerHTML = '<li class="caption dim">加载中 (走 screen 候选 + AI 错模式预警)…</li>';
  try {
    const r = await _fetchWithTimeout('/api/review/next_picks');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const d = j.data || {};
    if (!d.picks || !d.picks.length) {
      list.innerHTML = '<li class="caption dim">无候选 (可能 screen 失败,或没有交易记录)</li>';
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
      { lbl: '最佳', val: d.best ? (d.best.pnl_pct > 0 ? '+' : '') + d.best.pnl_pct.toFixed(2) + '%' : '—', cls: 'cell-up' },
      { lbl: '最差', val: d.worst ? d.worst.pnl_pct.toFixed(2) + '%' : '—', cls: 'cell-down' },
    ];
    $('#review-stats').innerHTML = tiles.map(t => `
      <div class="stat-tile">
        <div class="lbl">${t.lbl}</div>
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
    _reviewLoadList();
    _reviewStartFlowsPolling();
    _reviewLoadNextPicks();
    const btn = $('#review-next-pick-refresh');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', () => _reviewLoadNextPicks());
    }
  }
}

// 切到 review view 时加载 (已通过 showView 钩子触发,这里不重复)
// const _origJump = window.jumpTo; // 项目用 showView,不用 jumpTo — 之前的覆盖无效

// 初始绑定(用户直接打开 review 时)
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(_reviewOnViewEnter, 200);
  // 资金占比 10s 轮询
  setInterval(() => {
    if (document.querySelector('.view-review:not([hidden])')) {
      _reviewRefreshFlows();
    }
  }, 10000);
});
