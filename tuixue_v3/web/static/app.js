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
  default: 8_000,
  '/api/screen':           60_000,  // 实时选股 50 只 11s, 留 5x 缓冲
  '/api/backtest':        120_000,  // 回测慢
  '/ai_analysis':          35_000,  // AI 分析 7 重兜底 + AI 重试
  '/api/stock/':           15_000,  // 个股综合接口
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
  $('#stock-search').value = q;
  showView('stock');
  doStockSearch();
}

// ────────────────────────────────────────────
// SCREEN — 同步 POST（轻量、20-60s 可接受；后面再切 SSE）
// ────────────────────────────────────────────
$('#run-screen')?.addEventListener('click', async () => {
  const date = $('#screen-date').value.trim() || null;
  const btn = $('#run-screen');
  btn.disabled = true;
  const originalLabel = btn.querySelector('span')?.textContent || '扫描';
  btn.querySelector('span').textContent = '扫描中…';
  $('#screen-count').textContent = '…';
  $('#screen-meta').textContent = '运行中，可能需要 10-30 秒 …';
  $('#screen-table tbody').innerHTML = '<tr><td colspan="8" class="empty">扫描中 …</td></tr>';

  // 进度心跳: 每 3s 更新一次提示, 让用户知道没卡死
  let elapsed = 0;
  const heartbeat = setInterval(() => {
    elapsed += 3;
    $('#screen-meta').textContent = `扫描中... 已 ${elapsed}s (后台并发多时会更慢)`;
  }, 3_000);

  try {
    const data = await api('/api/screen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, mode: 'live', pool_size: 50, top_n: 5 }),
    });
    renderScreenResults(data);
    toast(`扫描完成 · ${data.candidates?.length || 0} 只候选 · 用时 ${data.elapsed_sec || '?'}s`, 'success');
  } catch (e) {
    toast('扫描失败：' + e.message, 'error', 5_000);
    $('#screen-meta').textContent = '运行失败：' + e.message + ' (后端并发堵了, 等 30s 重试)';
    $('#screen-table tbody').innerHTML = '<tr><td colspan="8" class="empty">失败</td></tr>';
  } finally {
    clearInterval(heartbeat);
    btn.disabled = false;
    btn.querySelector('span').textContent = originalLabel;
  }
});

function renderScreenResults(data) {
  const tbody = $('#screen-table tbody');
  const cands = data.candidates || [];
  $('#screen-count').textContent = cands.length;
  const pre = data.stats_by_layer?.prefilter || {};
  const l1 = data.stats_by_layer?.l1 || {};
  const l2 = data.stats_by_layer?.l2 || {};
  const l3 = data.stats_by_layer?.l3 || {};
  const l4 = data.stats_by_layer?.l4 || {};
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
  $('#screen-meta').textContent =
    boardPart + prePart + `L1:${l1.passed || 0} → L2:${l2.passed || 0} → L3:${l3.passed || 0} → L4:${l4.passed || 0}` +
    ` · ${data.elapsed_sec || '?'}s`;

  if (!cands.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">当日无候选</td></tr>';
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
    return `<tr>
      <td><button class="linkbtn" data-stock="${code}">${code}</button></td>
      <td>${name}</td>
      <td>${sector} ${ztBadge}${hsBadge}</td>
      <td class="num">${typeof rr === 'number' ? rr.toFixed(2) : rr}</td>
      <td class="num">${typeof turnover === 'number' ? turnover.toFixed(2) + '%' : turnover}</td>
      <td class="num" style="color:${colorFor(chg)}">${fmtPct(chg)}</td>
      <td class="num">${typeof liuTong === 'number' ? fmtAmt(liuTong) : liuTong}</td>
      <td><button class="linkbtn" data-stock="${code}">详情 →</button></td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('button[data-stock]').forEach(btn => {
    btn.addEventListener('click', () => gotoStock(btn.dataset.stock));
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
      p.addEventListener('click', () => loadStockDetail(p.dataset.code)));
  } catch (e) {
    box.innerHTML = `<div class="dim">搜索失败：${e.message}</div>`;
  }
}

async function loadStockDetail(code) {
  code = code.trim().padStart(6, '0');
  toast(`加载 ${code} …`);
  try {
    const data = await api(`/api/stock/${code}`);
    renderStockDetail(code, data);
  } catch (e) {
    toast(`加载失败：${e.message}`, 'error');
  }
}

function renderStockDetail(code, data) {
  $('#stock-title').textContent = data.seats ? (data.quote?.name || code) : code;
  $('#stock-code').textContent = code;
  $('#stock-sub').textContent = `${data.quote?.name || '—'} · ${code}`;

  // quote
  const q = data.quote || {};
  const price = parseFloat(q.最新价 ?? q.price ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);
  $('#q-price').textContent = fmtN(price, 2);
  $('#q-change').textContent = fmtPct(chg);
  $('#q-change').style.color = colorFor(chg);

  const today = data.fund_flow?.today || {};
  const mainNet = today.main_net ?? 0;
  $('#q-main').textContent = fmtN(mainNet, 0);
  $('#q-main').style.color = colorFor(mainNet);

  const seats = data.seats || {};
  $('#q-seats').textContent = seats.seat_count || 0;
  $('#q-seats-sub').textContent = seats.blacklisted
    ? `近 ${seats.total_lhb_rows || 0} 条龙虎 · ⚠ 黑名单`
    : `近 ${seats.total_lhb_rows || 0} 条龙虎`;

  // charts
  drawFlowChart(data.fund_flow?.history || []);
  drawKlineChart(data.kline || []);
  renderSeatsTable(seats.rows || []);

  // AI 分析面板
  $('#ai-panel').hidden = false;
  $('#ai-status').textContent = 'AI 复盘中 …';
  $('#ai-verdict').textContent = '…';
  $('#ai-summary').textContent = '';
  $('#ai-detail').innerHTML = '';
  loadAIAnalysis(code);
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
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 50, right: 50, top: 16, bottom: 60 },
    tooltip: { trigger: 'axis', backgroundColor: '#15110d', borderColor: '#2a2825', textStyle: { color: INK }, axisPointer: { type: 'cross', lineStyle: { color: ACCENT } } },
    legend: { data: ['K线','MA20','MA60'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 8 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#2a2825' } }, axisLabel: { color: INK2, fontSize: 10 } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10 } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8, textStyle: { color: INK2 } }],
    series: [
      { name: 'K线', type: 'candlestick', data: ohlc, itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN } },
      { name: 'MA20', type: 'line', data: ma(ohlc.map(o => o[1]), 20), smooth: true, lineStyle: { color: ACCENT, width: 1.2 }, symbol: 'none' },
      { name: 'MA60', type: 'line', data: ma(ohlc.map(o => o[1]), 60), smooth: true, lineStyle: { color: '#a78bcf', width: 1.2 }, symbol: 'none' },
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

function renderSeatsTable(rows) {
  const tbody = $('#seats-table tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">近 30 日无龙虎席位</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.date || '—'}</td>
      <td>${escapeHtml(r.seat || '—')}</td>
      <td><span class="dir-${(r.direction || '').includes('买') ? 'buy' : 'sell'}">${r.direction || '—'}</span></td>
      <td>${r.group ? `<span class="badge badge-${r.group}">${r.group}</span>` : '<span class="dim">—</span>'}</td>
      <td>${escapeHtml(r.label || '') || '<span class="dim">—</span>'}</td>
    </tr>`).join('');
}

function emptyChartOption(msg) {
  return {
    backgroundColor: 'transparent',
    graphic: [{ type: 'text', left: 'center', top: 'middle',
      style: { text: msg, fill: INK2, font: '14px Manrope' } }],
  };
}

// ────────────────────────────────────────────
// STOCK 内部 tab
// ────────────────────────────────────────────
$$('.tab[data-tab]').forEach(t => {
  t.addEventListener('click', () => {
    const tab = t.dataset.tab;
    $$('.tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    $$('[data-tab-pane]').forEach(p => p.hidden = (p.dataset.tabPane !== tab));
    $$('[data-tab-title]').forEach(p => p.textContent = {
      flow: '资本动向', kline: 'K 线走势', seats: '游资席位', ai: 'AI 复盘'
    }[tab] || ' ');
    if (tab === 'flow'  && echartsCharts.flow)  echartsCharts.flow.resize();
    if (tab === 'kline' && echartsCharts.kline) echartsCharts.kline.resize();
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
          ${warn}
        </div>`;
    }).join('');
  }

  // STEP 4: 全部涨停 (默认折叠)
  $('#dragons-all-count').textContent = (d.all || []).length;
  const allBody = $('#dragons-all-table tbody');
  const allList = d.all || [];
  if (allList.length === 0) {
    allBody.innerHTML = '<tr><td colspan="10" class="empty">无数据</td></tr>';
  } else {
    allBody.innerHTML = allList.map(s => {
      const bd = s.score_breakdown || {};
      const bdNote = ['连板强度','资金认可','封成比','市值匹配','技术形态','题材纯度']
        .map(k => `${k}: ${(bd[k]?.note || '—')}`).join(' · ');
      const sealTxt = s.seal_ratio_pct != null ? `${s.seal_ratio_pct.toFixed(1)}%` : '—';
      const warnTxt = (s.warnings || []).length ? s.warnings.join('; ') : '—';
      return `<tr data-code="${s.code}">
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
      <tr class="bd-detail" data-bd-code="${s.code}" hidden>
        <td colspan="10" class="dim" style="padding-left: 2rem; font-size: .85rem">${bdNote}</td>
      </tr>`;
    }).join('');
  }

  // 点击行展开详情
  $('#dragons-all-table tbody').querySelectorAll('tr[data-code]').forEach(tr => {
    tr.addEventListener('click', () => {
      const code = tr.dataset.code;
      const detail = tr.nextElementSibling;
      if (detail && detail.dataset.bdCode === code) detail.hidden = !detail.hidden;
    });
  });
  // 联动到个股详情
  $('#dragons-all-table tbody').querySelectorAll('.stock-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      const code = a.dataset.code;
      $('#stock-code').value = code;
      showView('stock');
      loadStockDetail(code);
    });
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
