// ────────────────────────────────────────────
// ECharts 懒加载工具 — 优先用本地 vendor, CDN 作为 fallback (2026-07-15)
// ────────────────────────────────────────────
let _echartsLoading = null;
function _ensureECharts() {
  if (typeof echarts !== 'undefined') return Promise.resolve();
  if (_echartsLoading) return _echartsLoading;
  _echartsLoading = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    // 本地 vendor 优先 — CDN 慢/挂不再静默白屏
    s.src = '/static/vendor/echarts.min.js';
    s.onload = () => resolve();
    s.onerror = () => {
      // 本地失败 → 兜底 CDN (用户首次访问时网络极差才会到这)
      const fb = document.createElement('script');
      fb.src = 'https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js';
      fb.onload = () => resolve();
      fb.onerror = () => { _echartsLoading = null; reject(new Error('ECharts 加载失败 (本地 + CDN)')); };
      document.head.appendChild(fb);
    };
    document.head.appendChild(s);
  });
  return _echartsLoading;
}

// R6: 防止 draw* 协程重叠导致 ECharts dispose 抢图
// 用法: const tk = _newChartToken('kline'); ... await ...; if (_isChartTokenStale('kline', tk)) return;
var _chartToken = {};
function _newChartToken(key) {
  _chartToken[key] = (_chartToken[key] || 0) + 1;
  return _chartToken[key];
}
function _isChartTokenStale(key, tk) {
  return _chartToken[key] !== tk;
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
  // C3: 全屏 overlay (回测最长 90s,用户体感上必须有 spinner)
  showLoadingOverlay('统计层回测中…', `${body.start} → ${body.end} · 最多 90s`);
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
    hideLoadingOverlay();
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

async function drawEquityChart(points) {
  const dom = $('#equity-chart');
  if (!dom) return;
  if (echartsCharts.equity) echartsCharts.equity.dispose();
  await _ensureECharts();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.equity = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 50, right: 24, top: 20, bottom: 36 },
    tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_TOOLTIP_BORDER, textStyle: { color: INK } },
    xAxis: {
      type: 'category', data: points.map(p => p[0]),
      axisLine: { lineStyle: { color: CHART_LINE } },
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
var searchTimer = null;
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
var _STOCK_HIST_KEY = 'tuixue_stock_history_v1';
var _STOCK_HIST_MAX = 50;          // 服务端上限
var _histCache = null;               // 当前已知的历史(防止 API 抖动时清空)

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
  // URL ?code=XXXXX 或 ?stock=XXXXX 自动切到个股页 (深链支持)
  const _bootParams = new URLSearchParams(location.search);
  const _bootCode = _bootParams.get('code') || _bootParams.get('stock');
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

// R-fix-2026-07-16: 进入 stock view 前清空所有 hero 残留文案 (上次查询的值会卡在 DOM)
function _resetStockHero() {
  // Hero 标题 + 价格
  const set = (id, v) => { const el = $('#' + id); if (el) el.textContent = v; };
  set('qh-name', '—'); set('qh-code', '——'); set('q-price', '—');
  set('q-change', '—'); set('q-chg-pct', '—'); set('q-arrow', '');
  set('q-time', '—'); set('stock-title', '个股'); set('stock-code', '—');
  set('stock-sub', '搜索 → 一只股票开始');
  // Hero tags / risks / lu-band / sparkline
  const tagsEl = $('#qh-tags'); if (tagsEl) tagsEl.innerHTML = '<span class="qh-tag" id="qh-source">数据源</span>';
  const risksEl = $('#qh-risks'); if (risksEl) { risksEl.innerHTML = ''; risksEl.hidden = true; }
  const luBand = $('#qh-lu-band'); if (luBand) luBand.hidden = true;
  const spark = $('#qh-spark-wrap'); if (spark) { spark.hidden = true; }
  // Sparkline 路径清空
  ['qh-spark-line','qh-spark-area','qh-spark-ma5','qh-spark-ma20'].forEach(id => {
    const el = $('#' + id); if (el) el.setAttribute('d', '');
  });
  const sparkMeta = $('#qh-spark-meta'); if (sparkMeta) sparkMeta.innerHTML = '';
  // Bento 14 格
  ['q-main','q-turnover','q-volratio','q-amp','q-5d','q-20d','q-mcap','q-pe','q-pb','q-hl','q-lu','q-ld','q-vol','q-seats']
    .forEach(id => set(id, '—'));
  ['q-main-sub','q-turnover-sub','q-mcap-sub','q-pe-sub','q-hl-sub','q-seats-sub'].forEach(id => set(id, ''));
  // Streak
  const streakHost = $('#q-streak-host'); if (streakHost) streakHost.innerHTML = '<p class="caption dim" style="margin: 0">查询股票后显示近 10 日涨跌</p>';
  // 分时
  const idn = $('#intra-day-note'); if (idn) idn.textContent = '';
  const idk = $('#intra-day-kpi');  if (idk) idk.innerHTML = '';
  const idl = $('#intra-day-label'); if (idl) idl.textContent = '';
  // AI panel
  const ap = $('#ai-panel'); if (ap) ap.hidden = true;
  const as = $('#ai-status'); if (as) as.textContent = '尚未调用';
  const av = $('#ai-verdict'); if (av) av.textContent = '—';
  const asum = $('#ai-summary'); if (asum) asum.textContent = '';
  // AI markets
  ['mkt-cn','mkt-us','mkt-kr'].forEach(id => {
    const card = $('#' + id); if (card) {
      const v = card.querySelector('.mkt-val'); if (v) v.textContent = '—';
      const s = card.querySelector('.mkt-status'); if (s) s.textContent = '未检测';
    }
  });
  // 资金流 / K线 KPI
  ['flow-kpi','kline-kpi','seats-kpi','holders-kpi'].forEach(id => {
    const el = $('#' + id); if (el) el.innerHTML = '';
  });
  // Tab 默认 = intraday (除非用户上次的偏好)
  document.querySelectorAll('.chart-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === 'intraday');
  });
  document.querySelectorAll('.chart-pane').forEach(p => {
    p.hidden = p.dataset.tabPane !== 'intraday';
  });
  // Watch btn 状态
  const wbtn = $('#stock-watch-btn'); if (wbtn) { wbtn.disabled = true; wbtn.textContent = '⭐ 一键自选'; wbtn.dataset.inWl = '0'; }
  // 我的交易隐藏
  const myt = $('#stock-mytrades-card'); if (myt) myt.hidden = true;
  // LIMIT-UP 提示
  const lub = $('#stock-limit-up-body'); if (lub) lub.innerHTML = '<p class="caption dim">查询股票后将显示连板 / 5 日涨停 / 板块涨停清单</p>';
  // Sector 合并卡
  const sectorCard = $('#stock-sector-card'); if (sectorCard) sectorCard.hidden = true;
  // News / Sectors / Related / Crash / AI 列表
  const clearList = (id, ph) => { const el = $('#' + id); if (el) el.innerHTML = `<p class="caption dim">${ph}</p>`; };
  clearList('news-list', '请先查询股票');
  clearList('sectors-list', '请先查询股票');
  clearList('related-by-concept', '请先查询股票');
  const cr = $('#crash-panel'); if (cr) {
    const rl = $('#crash-risk'); if (rl) rl.textContent = '—';
    const st = $('#crash-status'); if (st) st.textContent = '未检测';
    const cn = $('#crash-conviction'); if (cn) cn.textContent = '— / 100';
    const cb = $('#crash-conviction-bar'); if (cb) cb.style.width = '0%';
    const sm = $('#crash-summary'); if (sm) sm.textContent = '';
    const det = $('#crash-detail'); if (det) det.innerHTML = '<p class="caption dim">请先查询股票</p>';
  }
}

// ────────────────────────────────────────────
// STOCK · 实时轮询 — app.js:1489-1620 权威实现
// view-stock.js 这里不重复定义,直接用全局 (_startStockPoll / _stopStockPoll /
// _pollStockRealtime / _patchStockRealtime / visibilitychange listener 都在 app.js 里)。
// 修:之前 view-stock.js 重复一份导致 2 套 setInterval + 2 个 visibilitychange listener,
// 同时跑 → /api/stock/{code} 双倍流量 + 内存泄漏 (setInterval 失引用清不掉)。
// ────────────────────────────────────────────

// R5: loadStockDetail inflight dedup — 同 (code,date) 短时间内重复调用,共享同一个 promise
// 避免: 用户在搜索框连按 + URL hash 同步改 + watcher 3 路同时调,3 个 /full 请求并发
var _stockDetailInflight = null;
var _stockDetailInflightKey = '';
// R81 (Batch 9): inflight 已被 R5 dedup,这里显式 cache 切股时强制清掉防 stale render

// R3 (Batch 1): 内存层 LRU cache — 同 tab 内多次访问零延迟
// L1 (内存 ~0ms) > L2 (sessionStorage ~1ms) > L3 (SW ~5ms) > L4 (Redis 5s ~20ms) > L5 (server ~200ms+)
const _MEM_FULL_MAX = 50;
const _memFullCache = new Map();   // Map 保插入序,LRU 用 delete+set 模拟
const _MEM_FULL_TTL_MS = 30_000;   // 内存 30s (短于 sessionStorage 60s,避免脏)

// R4 (Batch 1): 缓存键规范化 — '2026-07-17' / '2026/07/17' / '20260717' 都视为同一 key
function _normalizeStockDate(d) {
  if (!d) return '';
  const s = String(d).trim();
  if (!s) return '';
  // 接受 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD / YYYY.MM.DD
  const m = s.match(/^(\d{4})[-/.]?(\d{1,2})[-/.]?(\d{1,2})$/);
  if (!m) return s;  // 不认识就原样
  return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
}

function _memFullKey(code, date) {
  return code + ':' + (_normalizeStockDate(date) || 'today');
}

function _memFullGet(code, date) {
  const k = _memFullKey(code, date);
  const v = _memFullCache.get(k);
  if (!v) return null;
  if (Date.now() - v.ts > _MEM_FULL_TTL_MS) {
    _memFullCache.delete(k);
    return null;
  }
  // LRU touch: 移到末尾
  _memFullCache.delete(k);
  _memFullCache.set(k, v);
  return v.data;
}

function _memFullSet(code, date, data) {
  const k = _memFullKey(code, date);
  if (_memFullCache.has(k)) _memFullCache.delete(k);
  _memFullCache.set(k, { ts: Date.now(), data });
  while (_memFullCache.size > _MEM_FULL_MAX) {
    const firstKey = _memFullCache.keys().next().value;
    _memFullCache.delete(firstKey);
  }
}

// R4: 在 sessionStorage / fetch 前规范化 dateParam,保证 L1/L2/SW/Server 都用同一 key
function _normDateParam(d) { return _normalizeStockDate(d); }

// R10 (Batch 1): 命中率埋点 — 验证 P95 < 100ms
const _cacheHits = { mem: 0, ss: 0, sw: 0, redis: 0, network: 0 };
function _recordHit(layer) { _cacheHits[layer] = (_cacheHits[layer] || 0) + 1; }

async function loadStockDetail(code, date) {
  code = code.trim().padStart(6, '0');
  currentStockCode = code;
  // 2026-07-18 修: app.js 也读 _currentStockCode (下划线),两变量同步赋值防再发
  window._currentStockCode = code;
  // R81 (Batch 9): 切股时把旧 inflight 标记成 stale — api() 走 inflight dedup 自动挡,
  // 但这里多一道显式清,防 stale render。currentStockCode 检查在 render 路径已有 (L787/L832)
  _stockDetailInflight = null;
  _stockDetailInflightKey = '';
  // 切股:停旧轮询,新轮询在首次 render 后启动,避免抢数据
  _stopStockPoll();
  // R12: 立即清掉 _stockAuxCache 旧股的 sector/lu_ctx 等,防止 race 期间子 loader 拿到旧 stock 数据
  if (_stockAuxCache.code !== code) {
    _stockAuxCache.code = code;
    _stockAuxCache.sector = null;
    _stockAuxCache.lu_ctx = null;
    _stockAuxCache.strong = null;
    _stockAuxCache.seat_breakdown = null;
    _stockAuxCache.related_news = null;
    _stockAuxCache.ai_status = null;
    _stockAuxCache.intraday = null;
    _stockAuxCache.inflight = null;
    _stockAuxCache.ts = 0;
  }
  // R71 (Batch 8): 冷启无任何缓存 → 显示 skeleton 占位,避免空白闪烁
  // 必须在 setQuickbarEnabled 之前 — quickbar 也要显示 skeleton 行
  _showStockSkeleton(code);
  // 启用快速工具栏 (含 default 日期),先 await 确保 stock-date 有值
  await _setQuickbarEnabled(code);
  // 日期参数优先级: 调用方传入 > 当前 stock-date input > 空(今日)
  const dateInput = $('#stock-date');
  let dateParam = _normDateParam(date || dateInput?.value || '');
  // R-fix-2026-07-18 A3: 切到 /full 单端点 — 服务端已预聚合 quote/kline/flow/seats/sector/
  // lu_ctx/strong_stocks/seat_breakdown/related_news/ai_status/intraday 11 个字段,Redis 5s 缓存。
  // 不再传 _fresh (full 有 ?fresh=1 单独控制),不再 _prefetchStockAux (字段已在 data 里)
  const useFresh = !!dateParam;  // 历史快照必须 fresh
  const qs = useFresh ? `?fresh=1&date=${encodeURIComponent(dateParam)}` : '';

  // P-perf: Phase 0 — 多层缓存取数渲染 (R3 内存 > R2 sessionStorage > fetch)
  let cached = _memFullGet(code, dateParam);
  if (cached) {
    _recordHit('mem');
    try { renderStockDetail(code, cached); }
    catch (e) { console.debug('[stock-cache] render fail:', e.message); }
    _hideStockSkeleton();  // R72 (Batch 8): 缓存命中也要清 skeleton
    return;  // 内存 hit 直接返,不再走 fetch (即时返回)
  }
  cached = _stockCacheLoad(code, dateParam);
  if (cached) {
    _recordHit('ss');
    _memFullSet(code, dateParam, cached);  // 顺手灌进 L1 内存,下次 0ms
    try { renderStockDetail(code, cached); }
    catch (e) { console.debug('[stock-cache] render fail:', e.message); }
    _hideStockSkeleton();  // R72 (Batch 8)
  }

  // R5: inflight dedup — 同 (code,date) 短时间内重复调,共用一个 promise
  const inflightKey = code + ':' + dateParam + ':' + (useFresh ? 'F' : 'T');
  if (_stockDetailInflight && _stockDetailInflightKey === inflightKey) {
    return _stockDetailInflight;
  }

  // R21+R23 (Batch 3): 渐进渲染 — Phase 1 /core (200ms) 渲染首屏,
//                      Phase 2 后台 /full 拿全部字段 patch
  const _promise = (async () => {
  try {
    // Phase 1: /core (1.5s 强超时) — quote + name + 5 KPI + kline (短)
    try {
      const coreData = await api(`/api/stock/${code}/core`);
      if (coreData && coreData.quote && currentStockCode === code) {
        const coreRender = { code, quote: coreData.quote, kline: coreData.kline || [], _core: true };
        renderStockDetail(code, coreRender);
        _recordHit('redis');
        _hideStockSkeleton();  // R-fix-B8: /core 拿到 quote 即可首屏,立即去 skeleton
      }
    } catch (e) {
      console.debug('[core] failed:', e.message);
    }

    // Phase 2: /full — 后台拿全部数据, 完成 patch 进 DOM
    const data = await api(`/api/stock/${code}/full${qs}`);
    if (currentStockCode !== code) return;  // 切股了, 丢弃
    // R-fix-2026-07-18 A3: 把 /full 的子包填进 _stockAuxCache,让现有 loadStockSector /
    // loadStockLimitUp / _loadStockStreakPanel 共享,无需各自 fetch
    _stockAuxCache.code = code;
    _stockAuxCache.sector = data.sector || null;
    _stockAuxCache.lu_ctx = data.limit_up_ctx || null;
    _stockAuxCache.strong = data.strong_stocks || null;
    _stockAuxCache.seat_breakdown = data.seat_breakdown || null;
    _stockAuxCache.related_news = data.related_news || null;
    _stockAuxCache.ai_status = data.ai_status || null;
    _stockAuxCache.intraday = data.intraday || null;
    _stockAuxCache.ts = Date.now();

    // 写 sessionStorage + 内存层 (R3)
    _stockCacheSave(code, dateParam, data);
    _memFullSet(code, dateParam, data);
    _recordHit('network');
    try { renderStockDetail(code, data); }
    catch (e) { console.error('renderStockDetail failed:', e); toast(`渲染失败:${e.message}`, 'error'); }
    _hideStockSkeleton();  // R72 (Batch 8): 首次成功 render 后移除 skeleton
    // 记录到历史
    const name = (data.quote && data.quote.name) || (data.name) || code;
    _addHist(code, name);
    _setQuickbarEnabled(code, name);
    if (!data.is_historical) {
      _startStockPoll(code);
    } else {
      $('#q-price')?.setAttribute('title', '历史快照,实时轮询已停');
    }
    // 异步检查自选状态,同步按钮 (未同步则 toggle 永远走"加")
    _updateStockWatchBtn();
    // A5: ai_status.ready=false → fire-and-forget 触发后台 LLM,完成后通过 SSE 推回
    if (data.ai_status && !data.ai_status.ready) {
      _maybeTriggerAiBackground(code).catch(e => console.debug('[ai-bg]', e.message));
    }

    // B3: open SSE 长连接,推 quote_patch + ai_ready + intraday (1Hz quote / 2s ai scan)
    _openStockStream(code);
  } catch (e) {
    if (cached) {
      console.warn('[stock] 网络失败,使用缓存:', e.message);
    } else {
      toast(`加载失败：${e.message}`, 'error');
      // R77 (Batch 8): 无缓存 + 网络失败 → 显示错误卡 + 重试按钮
      _showStockError(code, e.message || '网络异常');
    }
  } finally {
    _hideStockSkeleton();  // R72 (Batch 8): 兜底清 skeleton (避免永久闪烁)
    // R5: 200ms 后清 inflight key,允许同 key 在失败重试时复用
    setTimeout(() => {
      if (_stockDetailInflightKey === inflightKey) {
        _stockDetailInflightKey = '';
        _stockDetailInflight = null;
      }
    }, 200);
  }
  })();
  _stockDetailInflightKey = inflightKey;
  _stockDetailInflight = _promise;
  return _promise;
}

// R-fix-2026-07-18 A5: fire-and-forget LLM — 仅当 5 分钟内未触发过
async function _maybeTriggerAiBackground(code) {
  const lockKey = `ai_bg_lock:${code}`;
  const acked = sessionStorage.getItem(lockKey);
  if (acked && Date.now() - parseInt(acked) < 300_000) return;  // 5min cooldown
  sessionStorage.setItem(lockKey, String(Date.now()));
  try {
    await fetch(`/api/stock/${code}/ai_analysis?background=1`, { method: 'POST' });
  } catch (e) {
    // 静默 — 不影响主页面
  }
}

// R-fix-2026-07-18 B3: SSE 长连接订阅 /api/stock/{code}/stream
// 推 {quote_patch, ai_ready, ping, ready}。切股 / 离开 view 时关掉。
let _currentStockStream = null;
// R40 (Batch 4): SSE 延迟指标 — 收 quote_patch 时记录 (latency = now - ts)
var _sseMetrics = { quote_n: 0, ai_n: 0, ping_n: 0, err_n: 0,
                    sum_latency_ms: 0, max_latency_ms: 0,
                    last_log: 0 };

function _openStockStream(code) {
  // 关旧 stream
  _closeStockStream();
  if (!code) return;
  // R84 (Batch 9): hidden tab 不开 SSE — 切回时再开 (省带宽 + 后端 worker)
  if (document.hidden) {
    console.debug('[stream] hidden tab, defer open');
    return;
  }
  try {
    const es = new EventSource(`/api/stock/${code}/stream`);
    es.addEventListener('quote_patch', (e) => {
      try {
        const m = JSON.parse(e.data);
        if (m.code !== window._currentStockCode) return;
        // R40: 记录延迟 (server ts → client now)
        if (m.ts) {
          const lat = Date.now() - (m.ts * 1000);
          _sseMetrics.quote_n++;
          _sseMetrics.sum_latency_ms += lat;
          if (lat > _sseMetrics.max_latency_ms) _sseMetrics.max_latency_ms = lat;
          // 每 30s 打一次 summary
          if (Date.now() - _sseMetrics.last_log > 30000) {
            const avg = _sseMetrics.sum_latency_ms / _sseMetrics.quote_n;
            console.log(`[sse-metrics] quote_n=${_sseMetrics.quote_n} avg=${avg.toFixed(0)}ms max=${_sseMetrics.max_latency_ms}ms ai_n=${_sseMetrics.ai_n}`);
            _sseMetrics.last_log = Date.now();
          }
        }
        // SSE 只推 quote;走 _patchStockRealtime 用同一渲染路径(它接受 {quote})
        if (typeof _patchStockRealtime === 'function') {
          _schedulePatch(() => _patchStockRealtime(m.code, { quote: m.quote, fund_flow: { today: {} } }));
        }
      } catch (err) { console.debug('[stream quote_patch]', err.message); }
    });
    es.addEventListener('ai_ready', (e) => {
      try {
        const m = JSON.parse(e.data);
        if (m.code !== window._currentStockCode) return;
        _sseMetrics.ai_n++;
        // 刷新 AI verdict panel (后台 LLM 刚完成)
        if (typeof loadAIAnalysis === 'function') loadAIAnalysis(m.code).catch(() => {});
        // 更新全局状态
        if (typeof _stockAuxCache === 'object') {
          _stockAuxCache.ai_status = { ready: true, cached: true, model: 'MiniMax-M3',
                                       verdict: m.verdict, summary: m.summary, conviction: m.conviction, ts: m.ts };
        }
      } catch (err) { console.debug('[stream ai_ready]', err.message); }
    });
    es.addEventListener('error', () => {
      // R2: SSE 自动重连 — 但 EventSource 自带 retry 可能 0 退避,加 1s 最小间隔
      // 避免连续断开重连堆积多个 EventSource
      if (_currentStockStream !== es) return;
      // 不主动 close,让 EventSource 内置 retry 跑;但设个最小间隔防 reconnect 风暴
      const lastErr = _sseLastErrAt || 0;
      const now = Date.now();
      if (now - lastErr < 1000) {
        // 1s 内已重连过,主动 close 让上层重开 — 避免后台 retry 风暴
        try { es.close(); } catch {}
        setTimeout(() => { if (_currentStockStream == null) _openStockStream(code); }, 3000);
      }
      _sseLastErrAt = now;
      console.debug('[stock-stream] error/auto-reconnect');
    });
    _currentStockStream = es;
  } catch (err) {
    console.warn('[stock-stream] open fail:', err.message);
  }
}
var _sseLastErrAt = 0;
function _closeStockStream() {
  if (_currentStockStream) {
    try { _currentStockStream.close(); } catch {}
    _currentStockStream = null;
  }
}
// 切 stock 时关掉旧 stream
const _origLoadStockDetail = loadStockDetail;
// 已通过 _openStockStream 内部 _closeStockStream 处理

// R84 (Batch 9): visibilitychange — hidden 时关 SSE,visible 时重开 (R82 已加 R-stock refresh)
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    // 关 SSE 省资源
    _closeStockStream();
  } else if (window._currentStockCode && window._currentViewName === 'stock' && !_currentStockStream) {
    // 切回前台 → 重新订阅 SSE (没 R82 的 reload 必要,SSE 推的价格够新鲜)
    _openStockStream(window._currentStockCode);
  }
});

// 2026-07-17 性能: 个股 aux 数据共享缓存 (sector / lu_ctx / strong)
// 之前 3 个子 loader 各自 fetch,经常重复 2-3 次,导致切股/冷启 5-15s
// 现在 loadStockDetail 一次预拉,所有子 loader 共享
const _stockAuxCache = {
  code: null,
  sector: null,
  lu_ctx: null,
  strong: null,
  inflight: null,  // Promise (并行等待同一份)
  ts: 0,
};

function _auxFresh(code, maxAgeMs = 60000) {
  return _stockAuxCache.code === code && (Date.now() - _stockAuxCache.ts) < maxAgeMs;
}

async function _prefetchStockAux(code) {
  // 同 code 已在 inflight → 复用 (避免子 loader 多次重复 fetch)
  if (_stockAuxCache.code === code && _stockAuxCache.inflight) {
    return _stockAuxCache.inflight;
  }
  _stockAuxCache.code = code;
  const fetchWithTimeout = (url, ms = 4000) => Promise.race([
    api(url),
    new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms)),
  ]).catch(e => ({ error: e.message || String(e) }));
  const p = (async () => {
    // Step 1: 先拉 sector (因为 lu_ctx / strong_stocks 都依赖 sector.sw 来筛选)
    const sec = await fetchWithTimeout(`/api/stock/${code}/sector`);
    _stockAuxCache.sector = sec;
    // Step 2: 拿到 sector 后并行拉 lu_ctx (带 sector) + strong_stocks (带 sector)
    const sectorName = sec?.sw || sec?.csrc || sec?.gics || '';
    const sectorQs = sectorName ? `?sector=${encodeURIComponent(sectorName)}` : '';
    const [lu, strong] = await Promise.all([
      fetchWithTimeout(`/api/stock/${code}/limit_up_context${sectorQs}`),
      fetchWithTimeout(`/api/stock/${code}/strong_stocks${sectorQs}`),
    ]);
    _stockAuxCache.lu_ctx = lu;
    _stockAuxCache.strong = strong;
    _stockAuxCache.ts = Date.now();
    return _stockAuxCache;
  })();
  _stockAuxCache.inflight = p;
  try { await p; } finally { _stockAuxCache.inflight = null; }
  return _stockAuxCache;
}

// 暴露给子 loader 用: 先查 _stockAuxCache,没拿到才自己 fetch
async function _auxGet(code, key, fallbackUrl) {
  if (_auxFresh(code) && _stockAuxCache[key]) return _stockAuxCache[key];
  // R-fix-2026-07-17: 如果 _prefetchStockAux 已在飞, 等待它完成再决定要不要自己 fetch
  // (旧版 race condition: 预拉还没回来, _auxGet 自己又 fetch 一次 → 2 个相同 endpoint 并行)
  if (_stockAuxCache.inflight && _stockAuxCache.code === code) {
    try { await _stockAuxCache.inflight; } catch (_) { /* 预拉失败继续 */ }
  }
  if (_auxFresh(code) && _stockAuxCache[key]) return _stockAuxCache[key];
  if (_stockAuxCache.code !== code || !_stockAuxCache[key]) {
    // 不在缓存或已过期 → 自己 fetch 一次 (兜底,确保页面能渲染)
    try {
      const data = await api(fallbackUrl);
      _stockAuxCache[key] = data;
      _stockAuxCache.code = code;
      _stockAuxCache.ts = Date.now();
      return data;
    } catch (e) {
      return null;
    }
  }
  return _stockAuxCache[key];
}

// 自选按钮状态同步 — 拉 /api/watchlist 判断当前股是否已在自选
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

// ────────────────────────────────────────────
// 个股快速工具栏: 日期 / 一键复盘 / 一键自选 / 跳转
// ────────────────────────────────────────────
var _currentStockName = '';
var _tradeDates = [];        // ['YYYY-MM-DD', ...] 按时间倒序 (按需扩展)
var _tradeDatesSet = null;   // Set 加快 lookup
var _tradeDatesLoaded = false;
var _tradeDatesLoading = null;
var _tradeDatesLimit = 0;    // 当前已加载的 limit (用于按需扩展判断)
var _lastTradeDate = null;   // 服务端给的"今日不是交易日时"的回退日
var _TRADE_DATES_LIMIT_MAX = 1500;   // ≈ 6 年,够用且不会跑飞

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
// R-fix-2026-07-16: view-stock.js 与 app.js 重复绑定 stock-review-btn (双 handler bug)
// 权威实现在 app.js:1935, 这里删掉避免 sessionStorage 写两次 + toast 弹 2 次
// $('#stock-review-btn')?.addEventListener('click', () => {
//   if (!currentStockCode) return;
//   const date = $('#stock-date').value || new Date().toISOString().slice(0, 10);
//   sessionStorage.setItem('tuixue_review_seed', JSON.stringify({
//     code: currentStockCode,
//     name: _currentStockName,
//     date,
//   }));
//   showView('review');
//   toast(`已跳到复盘页 · ${currentStockCode} · ${date}`, 'info', 2200);
// });

// 一键自选 (toggle: 未自选→加,已自选→删) — 迁自已废弃的 app.js
// R-fix-2026-07-16: view-stock.js 与 app.js:1962 重复绑定 #stock-watch-btn (双 handler bug)
// 权威实现在 app.js — view-stock.js 里这份是历史 copy,删掉避免连点期间 2 套 timer + 重复 toast
// $('#stock-watch-btn')?.addEventListener('click', async () => {
//   ... (完整逻辑已迁至 app.js:1962)
// });

// R131: 跨页 watchlist 同步 — 监听 all_stocks / watchlist 页面的广播事件,
// 实时刷新当前 stock 页 ⭐ 按钮状态 (用户可能在多个视图同时打开股票)
document.addEventListener('watchlist-changed', (e) => {
  const detail = e.detail || {};
  if (!currentStockCode || detail.code !== currentStockCode) return;
  const btn = $('#stock-watch-btn');
  if (!btn) return;
  if (detail.action === 'add') {
    btn.dataset.inWl = '1';
    btn.textContent = '✓ 已自选';
  } else if (detail.action === 'remove') {
    btn.dataset.inWl = '0';
    btn.textContent = '⭐ 一键自选';
  }
});

// 一键跳转个股深查 (URL 锁定 code,方便分享)
// R-fix-2026-07-16: view-stock.js 与 app.js:2013 重复绑定 #stock-jump-stock → 删 view-stock.js 副本
// $('#stock-jump-stock')?.addEventListener('click', () => {
//   if (!currentStockCode) return;
//   history.replaceState(null, '', `?code=${currentStockCode}`);
//   toast(`URL 锁定 ${currentStockCode}`, 'info', 1500);
// });

function renderStockDetail(code, data) {
  const q = data.quote || {};
  const seats = data.seats || {};
  const flow = data.fund_flow || {};
  const today = flow.today || {};
  const extras = data.extras || {};

  const name = q.name || data.name || code;
  const price = parseFloat(q.price ?? q.最新价 ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);
  const prev = parseFloat(q.prev_close ?? q.昨收 ?? 0);
  const chgAmt = prev > 0 ? (price - prev) : 0;

  // 分时图辅助上下文（昨收 + 涨停价），供 drawIntraDayChart 参考线
  // 涨停价规则: ST 5% / 主板 10% / 创业板(300/301)+科创(688) 20% / 北交所 30%
  const isST = (name || '').startsWith('ST');
  const isKJ = /^(300|301|688)/.test(code);
  const isBJ = /^(8|4)/.test(code);  // 北交所 8/4 开头 30%
  const limitPct = isST ? 0.05 : isKJ ? 0.20 : isBJ ? 0.30 : 0.10;
  const lu = extras.limit_up_price != null ? extras.limit_up_price : (prev > 0 ? +(prev * (1 + limitPct)).toFixed(2) : null);
  lastStockContext = { prev_close: prev || null, limit_up_price: lu, code };

  // ─── 顶部标题 + Hero ───
  $('#stock-title').textContent = name;
  $('#stock-code').textContent = code;
  $('#stock-sub').textContent = `${name} · ${code} · ${q._source || ''} ${q._fetch_time || ''}`.trim();

  // 2026-07-19: 异步加载板块角色 (龙头/中军/杂毛) — 不阻塞首屏
  _loadStockRole(code);

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
    tagsHtml.push(`<span class="qh-tag" style="color:var(--accent);border-color:var(--accent)" title="实时数据无法回放,以下数据来自历史日线"> ${data.snapshot_date} 历史快照</span>`);
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
  // R-fix-2026-07-16: 保存 dateLabel 到 dataset,让 _patchStockRealtime 实时轮询时保留日期
  const subEl = $('#stock-sub');
  if (subEl && typeof dateLabel === 'string') subEl.dataset.dateLabel = dateLabel;

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

  // 当日高/低 + 开/昨收 (合到一行,Bento 一卡搞定 — 旧 TODAY 卡 2026-07-15 已删)
  setVal('#q-hl', `${q.最高 ? fmtN(q.最高, 2) : '—'} / ${q.最低 ? fmtN(q.最低, 2) : '—'}`, 'flat');
  $('#q-hl-sub').textContent = `开 ${fmtN(q.open ?? q.今开, 2)} · 昨收 ${fmtN(q.prev_close ?? q.昨收, 2)}`;

  // 涨停/跌停
  const luStr = extras.limit_up_price != null ? extras.limit_up_price.toFixed(2) : '—';
  const ldStr = extras.limit_dn_price != null ? extras.limit_dn_price.toFixed(2) : '—';
  setVal('#q-lu', luStr,
    extras.limit_up_price && price >= extras.limit_up_price - 0.001 ? 'up' : 'flat');
  setVal('#q-ld', ldStr,
    extras.limit_dn_price && price <= extras.limit_dn_price + 0.001 ? 'down' : 'flat');

  // 成交量 / 成交额 (Bento q-vol 一格两行: 主=成交量万手, 副=成交额)
  const vol = q.成交量 || 0;
  const volStr = vol > 0 ? `${(vol / 1e4).toFixed(1)}` : '—';
  setVal('#q-vol', vol > 0 ? `${volStr}<span class="qc-unit">万手</span>` : '—', 'flat');
  const amtHtml = q.成交额 > 0
    ? `<span class="qc-value flat" style="font-size:13px">${(q.成交额 / 1e8).toFixed(2)} 亿</span>`
    : '成交额 —';
  $$('#q-amt').forEach(el => { el.innerHTML = amtHtml; });

  // 龙虎席位
  setVal('#q-seats', `${seats.seat_count || 0}<span class="qc-unit">条</span>`, 'flat');
  $('#q-seats-sub').textContent = seats.blacklisted
    ? `近 ${seats.total_lhb_rows || 0} 条 ·  黑名单`
    : `近 ${seats.total_lhb_rows || 0} 条`;

  // ─── 「连板 · 近期涨停 / 强势股」卡片 ───
  // 修复 (2026-07-17): 旧版用 kline close-to-close chg% 算 10 格涨跌,完全没用涨停池,名不副实。
  // 新版分 3 段: ① 连板状态 (limit_up_context.today + leadership)
  //                ② 近 5 日涨停明细 (limit_up_context.recent_5d)
  //                ③ 板块强势股 (新 /api/stock/{code}/strong_stocks 按 L3/L4 industry 过滤)
  // 顶部骨架 placeholder,真实数据由 _loadStockStreakPanel 异步填充
  const streakHost = $('#q-streak-host');
  if (streakHost) {
    streakHost.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem">
        <span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">⏳ 连板 / 涨停加载中…</span>
      </div>
      <p class="caption dim" style="margin:0">🔍 近 5 日涨停明细 + 同产业链强势股</p>`;
    _loadStockStreakPanel(code, data);
  }

  // 2026-07-19: 加载 3 个买点策略卡 (周线擒牛 + 回升位 + MA5 原则)
  _loadWeeklyBullCard(code);
  _loadRecoveryCard(code);

  // ─── 图表 / 表格 ───
  const empty = $('#flow-empty');
  if (empty) empty.style.display = 'none';
  // 延迟绘制: 资金流 / K 线 chart 在对应 tab 首次激活时才初始化 ECharts
  _pendingFlowData = flow.history || [];
  klineState.data = data.kline || [];
  klineState.period = 120;
  if ((klineState.data.length || 0) < 120) {
    loadKline(code, 120);
  } else {
    syncKlineToolbar();
    // 不再立即 drawKlineChart() — 交由 tab 首次激活触发
    _klineDataReady = true;
  }
  renderFlowKpi(flow.history || []);
  renderKlineKpi(klineState.data);
  renderStreak10d(klineState.data);
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

  // 2026-07-15 修复分时图不显示: intraday tab 默认 active, tab click handler 不触发,
  // 必须在这里主动 loadIntraDay 一次 (否则只有点 sparkline / 切 tab 才加载)
  if (typeof loadIntraDay === 'function') {
    loadIntraDay(code, todayStr());
  }

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

// ─── Hero · sparkline (近 120 日 收 + MA5/MA20 + 现价竖线) ───
function renderHeroSparkline(kline, lastPrice) {
  const wrap = $('#qh-spark-wrap');
  if (!wrap) return;
  if (!kline || kline.length < 5) { wrap.hidden = true; return; }
  wrap.hidden = false;
  // 可点击 → 跳分时
  wrap.style.cursor = 'pointer';
  wrap.title = '点击查看当日分时';
  wrap.onclick = () => {
    if (!currentStockCode) return;
    const pick = $('#intra-day-pick');
    if (pick) pick.value = todayStr();
    const tabBtn = document.querySelector('.chart-tab[data-tab="intraday"]');
    if (tabBtn) tabBtn.click();
    loadIntraDay(currentStockCode, todayStr());
  };

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
  else if (price <= ld + 0.01)               { zClass = 'zone-ld';  zText = ' 跌停'; }
  else if (distLU_pct < 2)                   { zClass = 'zone-hot'; zText = '近涨停 <2%'; }
  else if (distLD_pct < 2)                   { zClass = 'zone-hot'; zText = ' 近跌停 <2%'; }
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
      tbody.innerHTML = `<tr><td colspan="8"><div class="error-card"><div class="er-msg"> <b>加载失败</b> · ${escapeHtml(e.message)}</div><button class="er-retry" id="bd-retry">↻ 重试</button></div></td></tr>`;
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
          <button class="btn-mini" id="mytr-bulk-review"> 一键复盘全部未评分 (${unreviewed.length})</button>
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

// ─── 近 10 日涨跌格子 · close-to-close 9 档热力 (2026-07-17 恢复 · 摘要条 + 点击切分时) ───
function renderStreak10d(kline) {
  const host = $('#q-streak-10d');
  if (!host) return;
  if (!kline || kline.length < 5) {
    host.innerHTML = '<p class="caption dim" style="margin:.25rem 0 0">近 10 日数据不足</p>';
    return;
  }
  // 取 11 条 → 10 个 chg% 值 (第 1 条无 prev_close 不算 chg)
  const last11 = kline.slice(-11);
  let prevC = null;
  const withChg = last11.map(k => {
    const cl = Number(k.close || k[1] || k.收盘价 || 0);
    if (prevC && prevC > 0) {
      const chg = (cl / prevC - 1) * 100;
      prevC = cl;
      return { date: k.date, close: cl, chg };
    }
    prevC = cl;
    return null;
  }).filter(Boolean);

  // 2026-07-18 改: 9 档离散 → HSL 连续渐变 (饱和度+亮度随涨跌幅度平滑插值)
//   涨停 +10% = hsl(0, 80%, 38%) 深红  🔥
//   大涨  +5% = hsl(0, 60%, 55%) 中红
//   小涨 +0.5% = hsl(0, 35%, 75%) 浅红
//   平  ±0%  = hsl(0, 0%, 62%) 灰
//   小跌 -0.5% = hsl(120, 35%, 75%) 浅绿
//   大跌  -5% = hsl(120, 60%, 50%) 中绿
//   跌停 -10% = hsl(120, 80%, 32%) 深绿  💀
// 优势: chg=3.7% 与 chg=3.8% 颜色差 0.01%,不再有"档位跳变"割裂感
  const colorOf = (chg) => {
    // 限幅 [-10, +10], 超出都按极值算 (10% + 1 = 仍 10%)
    const c = Math.max(-10, Math.min(10, chg));
    let hue, sat, light, tag = '';
    if (c >= 0.5) {
      // 红: 强度 t = c/10 ∈ [0.05, 1], sat 35→80, light 75→38
      const t = c / 10;
      hue = 0; sat = 35 + t * 45; light = 75 - t * 37;
      if (c >= 9.5) tag = '🔥';
    } else if (c > -0.5) {
      // 灰 (近 0): 平稳日
      return { bg: 'hsl(0, 0%, 62%)', fg: '#fff', tag: '' };
    } else {
      // 绿: 强度 t = |c|/10 ∈ [0.05, 1], sat 35→80, light 75→32
      const t = -c / 10;
      hue = 120; sat = 35 + t * 45; light = 75 - t * 43;
      if (c <= -9.5) tag = '💀';
    }
    // 浅底配黑字,深底配白字 (light > 60% 黑字)
    const fg = light > 60 ? '#000' : '#fff';
    return { bg: `hsl(${hue}, ${Math.round(sat)}%, ${Math.round(light)}%)`, fg, tag };
  };

  const last10 = withChg.slice(-10);
  if (!last10.length) {
    host.innerHTML = '<p class="caption dim" style="margin:.25rem 0 0">近 10 日无涨跌数据</p>';
    return;
  }

  // ── 10 格 (5 格 × 2 行, mobile 友好) ──
  const cells = last10.map(d => {
    const c = colorOf(d.chg);
    const date = String(d.date || '');
    const parts = date.split('-');
    const md = parts.length >= 3 ? `${parts[1]}/${parts[2]}` : date;
    const chgStr = `${d.chg >= 0 ? '+' : ''}${d.chg.toFixed(2)}%`;
    return `<div data-streak-date="${escapeHtml(date)}" title="${escapeHtml(date)} ${chgStr} · 点击看分时" style="flex:1 1 calc(20% - 3px);min-width:42px;background:${c.bg};color:${c.fg};padding:.35rem .2rem;border-radius:4px;text-align:center;font-size:.78rem;line-height:1.2;cursor:pointer;transition:transform .1s" onmousedown="this.style.transform='scale(.95)'" onmouseup="this.style.transform=''" onmouseleave="this.style.transform=''">
      <div style="font-size:.65rem;opacity:.85">${md}</div>
      <div style="font-weight:700">${c.tag}${chgStr}</div>
    </div>`;
  }).join('');

  // ── 10 格统计 ──
  const nUp = last10.filter(d => d.chg > 0.5).length;
  const nDn = last10.filter(d => d.chg < -0.5).length;
  const nFlat = last10.length - nUp - nDn;
  const nLimit = last10.filter(d => Math.abs(d.chg) >= 9.5).length;
  // 累计涨幅: ((末/初) - 1) × 100 — 真实复利,比简单求和准
  const cumChg = (last10.length >= 2 && last10[0].close > 0)
    ? ((last10[last10.length - 1].close / last10[0].close) - 1) * 100
    : 0;
  // 连阳 / 连阴 (从最新一根往前数)
  let streak = 0;
  if (last10.length) {
    const sign = last10[last10.length - 1].chg > 0.5 ? 'up'
               : last10[last10.length - 1].chg < -0.5 ? 'dn' : null;
    if (sign) {
      for (let i = last10.length - 1; i >= 0; i--) {
        if ((sign === 'up' && last10[i].chg > 0.5) ||
            (sign === 'dn' && last10[i].chg < -0.5)) streak++;
        else break;
      }
    }
  }
  const avgChg = last10.reduce((s, d) => s + d.chg, 0) / last10.length;
  const maxChg = last10.reduce((m, d) => d.chg > m.chg ? d : m, last10[0]);
  const minChg = last10.reduce((m, d) => d.chg < m.chg ? d : m, last10[0]);

  const fmtSigned = (v) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const cumColor = cumChg > 0.5 ? 'var(--up)' : cumChg < -0.5 ? 'var(--down)' : 'var(--ink-2)';
  const avgColor = avgChg > 0.5 ? 'var(--up)' : avgChg < -0.5 ? 'var(--down)' : 'var(--ink-2)';
  const streakLabel = streak === 0 ? '—'
                   : (last10[last10.length - 1].chg > 0.5 ? `${streak} 连阳` : `${streak} 连阴`);
  const streakColor = streak === 0 ? 'var(--ink-2)'
                   : (last10[last10.length - 1].chg > 0.5 ? 'var(--up)' : 'var(--down)');

  const summaryTop = `
    <span><b style="color:${cumColor}">累计 ${fmtSigned(cumChg)}</b></span>
    <span><b style="color:${streakColor}">${streakLabel}</b></span>
    <span>均日 <b style="color:${avgColor}">${fmtSigned(avgChg)}</b></span>
    <span>↑<b>${nUp}</b> ↓<b>${nDn}</b>${nFlat ? ' 平' + nFlat : ''}${nLimit ? ' · ' + nLimit + ' 板' : ''}</span>`;
  const summaryBot = `
    <span class="dim">区间最高 <b style="color:var(--up)">${fmtSigned(maxChg.chg)}</b> (${(maxChg.date||'').slice(5).replace('-','/')})</span>
    <span class="dim">区间最低 <b style="color:var(--down)">${fmtSigned(minChg.chg)}</b> (${(minChg.date||'').slice(5).replace('-','/')})</span>
    <span class="dim">点击格子 → 当日分时</span>`;
  const summary = `
    <div style="font-size:.78rem;color:var(--ink-2);margin-bottom:.4rem;display:flex;gap:.85rem;flex-wrap:wrap;line-height:1.55">${summaryTop}</div>
    <div style="font-size:.7rem;display:flex;gap:.85rem;flex-wrap:wrap;margin-bottom:.5rem">${summaryBot}</div>`;

  host.innerHTML = summary + `<div style="display:flex;gap:3px;flex-wrap:wrap">${cells}</div>`;

  // R17: 后台 prefetch 静默 chunk 数 — 切走 / 隐藏标签页时立即停止
  let _prefetchActive = true;
  function _stopPrefetch() { _prefetchActive = false; }
  document.addEventListener('visibilitychange', () => { if (document.hidden) _stopPrefetch(); });

  // 2026-07-18: 后台预缓存 10 天的分时数据 → 用户点格子秒开 (免去 1-3s 等待)
  // 节流: 每个 250ms 一个,避免瞬时 10 个并发压垮东财接口
  // R8b: 用 setViewTimer 注册,离开个股 view 自动 clearTimeout 全部
  // R17: 标签页隐藏或切走时停 prefetch
  const code = window._currentStockCode || window.currentStockCode;
  if (code) {
    const dates = last10.map(d => String(d.date || '')).filter(Boolean);
    let i = 0;
    // R91: 并发上限 — 最多 2 个未完成 prefetch,避免 10 串行→10 并发拖垮 server
    let _inflight = 0;
    const MAX_INFLIGHT = 2;
    const tick = () => {
      if (i >= dates.length && _inflight === 0) return;
      if (!_prefetchActive) return;  // R17: 隐藏 / 切走,停
      if (code !== window._currentStockCode) return;  // 已切股,停
      while (i < dates.length && _inflight < MAX_INFLIGHT) {
        const d = dates[i++];
        // 已缓存就跳过 (B-15 LRU 还在)
        if (typeof intraDayCache !== 'undefined' && intraDayCache.has(d)) continue;
        _inflight++;
        // fire-and-forget; loadIntraDay 内部已 cache
        try {
          Promise.resolve(loadIntraDay(code, d)).finally(() => { _inflight--; });
        } catch (_) { _inflight--; }
      }
      if (i < dates.length || _inflight > 0) setViewTimer('stock', tick, 250);
    };
    setViewTimer('stock', tick, 400);  // 400ms 后开始,等主数据先到
  }

  // 绑定点击 → 切换到分时 tab + 加载该日
  host.querySelectorAll('[data-streak-date]').forEach(el => {
    el.addEventListener('click', () => {
      const date = el.dataset.streakDate;
      // 2026-07-18 修 (双重):
      //   1. data-streak-10d-date → dataset.streak-10dDate (dash+数字不转 camelCase),
      //      改用 data-streak-date (dataset.streakDate 干净) 才能读到值
      //   2. app.js 用的是 _currentStockCode,loadStockDetail 必须同步赋值 (line 646)
      if (!date || !window._currentStockCode) return;
      const pick = $('#intra-day-pick');
      if (pick) pick.value = date;
      const tabBtn = document.querySelector('.tab[data-tab="intraday"], .chart-tab[data-tab="intraday"]');
      if (tabBtn) tabBtn.click();
      if (typeof loadIntraDay === 'function') loadIntraDay(window._currentStockCode, date);
    });
  });
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
    ['黑名单', seats.blacklisted ? ' 是' : '否', seats.blacklisted ? DOWN : UP],
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
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function drawFlowChart(history) {
  const dom = $('#flow-chart');
  if (!dom) return;
  // 延迟绘制: flow pane 隐藏时不浪费 ECharts init,存数据等切 tab 时画
  const pane = dom.closest('[data-tab-pane]');
  if (pane && pane.hidden) {
    _pendingFlowData = history;
    return;
  }
  if (echartsCharts.flow) { echartsCharts.flow.dispose(); echartsCharts.flow = null; }
  const tk = _newChartToken('flow');
  await _ensureECharts();
  if (_isChartTokenStale('flow', tk)) return;
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.flow = chart;
  _flowChartDrawn = true;
  if (!history.length) {
    chart.setOption(emptyChartOption('暂无资金流数据'));
    chart.resize();
    return;
  }
  const dates = history.map(h => h.date);
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,  // R13: SSE 1Hz 重画时不要动画,直接跳到目标值
    legend: { data: ['主力','超大单','大单','中单','小单'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 12 },
    grid: { left: 50, right: 16, top: 36, bottom: 50 },
    tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE, textStyle: { color: INK }, axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: CHART_LINE } }, axisLabel: { color: INK2, fontSize: 10 } },
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
  chart.resize();
}

// ──────────────────────────────────────────────────────────────
// K线状态 + 周期切换 + 指标计算 (MACD / KDJ / BOLL)
// ──────────────────────────────────────────────────────────────
var klineState = {
  period: 120,                // 当前显示周期 (天) · 默认 近 120 日 (与 hero sparkline meta 对齐)
  indicators: { ma: true, macd: false, kdj: false, boll: false },
  data: [],                   // 当前缓存的 kline
  loading: false,
};
// R-fix-2026-07-16: 周期 + 指标持久化到 localStorage,用户改了之后切股票不重置
(function _loadKlinePrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('tuixue_kline_prefs') || '{}');
    if (p && typeof p.period === 'number' && [22, 66, 120, 132, 250, 400].includes(p.period)) {
      klineState.period = p.period;
    }
    if (p && p.indicators && typeof p.indicators === 'object') {
      Object.assign(klineState.indicators, p.indicators);
    }
  } catch (_) {}
})();
function _saveKlinePrefs() {
  try {
    localStorage.setItem('tuixue_kline_prefs', JSON.stringify({
      period: klineState.period,
      indicators: klineState.indicators,
    }));
  } catch (_) {}
}
// 延迟绘制标记: 个股首次加载时不初始化 ECharts (隐藏容器宽高为 0),
// 等用户切到对应 tab 再创建图表实例,避免卡主线程 + 避免 0 尺寸初始化.
var _klineDataReady = false;
var _klineChartDrawn = false;
var _pendingFlowData = null;
var _flowChartDrawn = false;

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
// R4: 多并发 dedup — 同一 (code,days) 同时被调用多次,只发 1 个请求,其余等同一个 Promise
var _klineInflight = null;
var _klineInflightKey = '';
async function loadKline(code, days) {
  if (klineState.loading) return _klineInflight || Promise.resolve();
  const key = code + ':' + days;
  if (_klineInflightKey === key && _klineInflight) return _klineInflight;
  _klineInflightKey = key;
  klineState.loading = true;
  const dom = $('#kline-chart');
  if (dom) dom.dataset.loading = '1';
  _klineInflight = (async () => {
    try {
      const data = await api(`/api/stock/${code}/kline?days=${days}`);
      klineState.data = data.kline || [];
      klineState.period = days;
      syncKlineToolbar();
      // K 线数据就绪标记 — 无论 tab 是否可见,先标记数据已到达,
      // tab 激活时由 tab handler 触发绘制 (避免 race: tab 先点但数据后到)
      _klineDataReady = true;
      const klinePane = dom?.closest('[data-tab-pane]');
      if (klinePane && !klinePane.hidden && _klineDataReady) {
        drawKlineChart();
      }
      renderKlineKpi(klineState.data);
      renderHeroSparkline(klineState.data, $('#q-price')?.textContent ? Number($('#q-price')?.textContent) : null);
      // 2026-07-18 修: renderStockDetail 先调 renderStreak10d,此时 /full 的 kline 可能是空 cache;
      // loadKline 后到时再补一次,避免 streak 格子永远停在 "数据不足"
      renderStreak10d(klineState.data);
    } catch (e) {
      toast(`K线加载失败：${e.message}`, 'error');
      klineState.data = [];
      _klineDataReady = true;
    } finally {
      klineState.loading = false;
      if (dom) delete dom.dataset.loading;
      // R4: 完成后清掉 inflight key,但保留 promise 一小段时间给迟到 caller 复用
      setTimeout(() => {
        if (_klineInflightKey === key) {
          _klineInflightKey = '';
          _klineInflight = null;
        }
      }, 500);
    }
  })();
  return _klineInflight;
}

async function drawKlineChart() {
  const dom = $('#kline-chart');
  if (!dom) return;
  // R6: 抢占式 token — 切 tab 频繁触发时,旧的 await 完成后不再覆盖新 chart
  const tk = _newChartToken('kline');
  if (echartsCharts.kline) { echartsCharts.kline.dispose(); echartsCharts.kline = null; }
  await _ensureECharts();
  if (_isChartTokenStale('kline', tk)) return;  // 新一轮已启动,放弃
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.kline = chart;
  _klineChartDrawn = true;
  const kline = klineState.data;
  if (!kline || !kline.length) {
    chart.setOption(emptyChartOption('暂无 K 线数据'));
    chart.resize();
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
    { left: 56, right: 76, top: 12, height: hasSub ? '58%' : '70%' },
    { left: 56, right: 56, top: hasSub ? '72%' : '74%', height: hasSub ? '14%' : '20%' },
  ];
  if (hasSub) grids.push({ left: 56, right: 56, top: '88%', height: '10%' });

  const xAxes = [
    { type: 'category', data: dates, gridIndex: 0,
      axisLine: { lineStyle: { color: CHART_LINE } },
      // 2026-07-17: 主图 x 轴 label 隐藏,日期统一到下方 volume x 轴,避免两轴标签叠加
      axisLabel: { show: false },
      splitLine: { show: false } },
    { type: 'category', data: dates, gridIndex: 1,
      axisLine: { lineStyle: { color: CHART_LINE } },
      axisLabel: { color: INK2, fontSize: 10, hideOverlap: true },
      splitLine: { show: false } },
  ];
  // yAxes 配置:
  //   [0] 主图左轴:价格 — 与右轴 % 严格共享同一价格区间 (像素级对齐)
  //   [1] 主图右轴:百分比 — refVal=期间首日收盘价,formatter 把价格换算成 % chg
  //   [2] 量能网格轴:成交量 — 在量能 grid 内左侧
  // 2026-07-17 修 bug #5: 用户反馈 K 线图右轴"应有百分比"且显示不对,
  //                    之前右轴是 volume (171.9万/100.0万/12.5万),现在改为 %
  const baseClose = closes.length ? +closes[0] : null;
  const priceHi = Math.max(...highs);
  const priceLo = Math.min(...lows);
  // 选择 stepPct 让 4-8 段覆盖全幅度 (跟分时图 Round 3 同算法)
  let kStepPct = 5, kStepPrice = null, kAlignedMin = null, kAlignedMax = null;
  if (baseClose && baseClose > 0) {
    const totalPctRange = ((priceHi - priceLo) / baseClose) * 100;
    const candidates = [1, 2, 5, 10, 20, 30, 50];
    for (const s of candidates) {
      const segs = totalPctRange / s;
      if (segs >= 4 && segs <= 8) { kStepPct = s; break; }
      if (segs < 4) { kStepPct = s; break; }
    }
    kStepPrice = kStepPct * baseClose / 100;
    const kMin = Math.max(0, Math.floor((baseClose - priceLo - 0.5 * kStepPrice) / kStepPrice));
    const kMax = Math.max(0, Math.ceil((priceHi - baseClose - 0.5 * kStepPrice) / kStepPrice));
    kAlignedMin = baseClose - kMin * kStepPrice;
    kAlignedMax = baseClose + kMax * kStepPrice;
  }
  const yAxes = [
    // 主图左轴:价格 (与右轴 % 严格对齐)
    { type: 'value', gridIndex: 0, position: 'left',
      ...(kAlignedMin != null ? { min: kAlignedMin, max: kAlignedMax, interval: kStepPrice } : { scale: true, splitNumber: 6 }),
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: INK2, fontSize: 10 } },
    // 主图右轴:百分比 (相对 baseClose=期间首日收盘价)
    // refVal 来自 K线周期起点,让"0%"对齐到首根蜡烛的收盘价
    { type: 'value', gridIndex: 0, position: 'right',
      ...(kAlignedMin != null ? { min: kAlignedMin, max: kAlignedMax, interval: kStepPrice } : { scale: true }),
      splitLine: { show: false },
      axisLabel: { color: INK2, fontSize: 10,
        formatter: v => {
          if (!baseClose) return '';
          const pct = ((v - baseClose) / baseClose) * 100;
          const sign = pct >= 0 ? '+' : '';
          return Math.abs(pct) < 0.01 ? '0.0%' : sign + pct.toFixed(1) + '%';
        } },
      axisLine: { show: true, lineStyle: { color: CHART_LINE } } },
    // 量能网格轴:成交量 (gridIndex:1,放左侧,不再与 date 列冲突)
    { gridIndex: 1, scale: true, splitNumber: 2,
      min: 'dataMin', max: 'dataMax',
      position: 'right',
      axisLabel: { color: INK3, fontSize: 9, formatter: v => (v/1e4).toFixed(0)+'万' },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: CHART_LINE } } },
  ];
  if (hasSub) {
    xAxes.push({ type: 'category', data: dates, gridIndex: 2,
      axisLine: { lineStyle: { color: CHART_LINE } },
      axisLabel: { show: false },
      splitLine: { show: false } });
    yAxes.push({ gridIndex: 2, scale: true, splitNumber: 2,
      axisLabel: { color: INK2, fontSize: 9 },
      splitLine: { lineStyle: { color: GRID } },
      axisLine: { lineStyle: { color: CHART_LINE } } });
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
    // 2026-07-18: 同花顺风格 — 右轴外贴最新收盘涨跌百分比标签,涨跌染色
    // candlestick 没有 markLine 默认友好,直接加 markPoint 也行; 用 markLine 跨整图更稳
    // 用户反馈: 标签只显示涨跌百分比,不要再带价格 (右轴已经显示价格了)
    markLine: {
      silent: true, symbol: 'none',
      data: (() => {
        const lastIdx = kline.length - 1;
        const lastK = kline[lastIdx];
        if (!lastK) return [];
        const lc = +lastK.close;
        const lp = +lastK.prev_close || (kline[lastIdx-1] ? +kline[lastIdx-1].close : 0);
        const lastColor = lc >= lp ? UP : DOWN;
        const lastPct = lp > 0 ? ((lc - lp) / lp * 100) : 0;
        return [{
          name: '最新',
          yAxis: lc,
          lineStyle: { color: lastColor, type: 'dashed', width: 1, opacity: 0.6 },
          label: {
            show: true, position: 'end',
            // 只显示涨跌百分比,不再重复价格 (右轴刻度已经显示价格)
            formatter: `${(lastPct >= 0 ? '+' : '') + lastPct.toFixed(2)}%`,
            color: '#fff', fontSize: 10, fontWeight: 700,
            backgroundColor: lastColor, padding: [2, 6], borderRadius: 3,
            distance: 4,
          },
        }];
      })(),
    },
  });
  // MA 叠加（主图）— 2026-07-17: 加 endLabel 把 MA10/20/60 名直接打在每条线的右端,不占顶部空间
  if (ind.ma) {
    const lastIdx = ma10.length - 1;
    series.push({ name: 'MA10', type: 'line', data: ma10, smooth: true, lineStyle: { color: '#7b9bd1', width: 1 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA10', color: '#7b9bd1', fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: 'rgba(10,9,8,0.6)' } });
    series.push({ name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { color: ACCENT,  width: 1.2 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA20', color: ACCENT, fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: 'rgba(10,9,8,0.6)' } });
    series.push({ name: 'MA60', type: 'line', data: ma60, smooth: true, lineStyle: { color: '#a78bcf', width: 1.2 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA60', color: '#a78bcf', fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: 'rgba(10,9,8,0.6)' } });
  }
  // BOLL 叠加（主图）
  if (boll) {
    series.push({ name: 'BOLL上', type: 'line', data: boll.upper, smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL中', type: 'line', data: boll.mid,   smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL下', type: 'line', data: boll.lower, smooth: true, lineStyle: { color: '#5b8def', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
  }
  // 量（grid 1）
  series.push({
    name: '量', type: 'bar', xAxisIndex: 1, yAxisIndex: 2,
    data: vols.map((v, i) => ({ value: v, itemStyle: { color: barColors[i] } })),
    barWidth: '60%',
  });

  // MACD 副图（grid 2）
  if (subIndicator === 'macd' && macdData) {
    series.push({ name: 'DIF', type: 'line', data: macdData.dif, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#5b8def', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'DEA', type: 'line', data: macdData.dea, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#f0c075', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MACD', type: 'bar', data: macdData.hist.map(v => v == null ? 0 : v), xAxisIndex: 2, yAxisIndex: 2,
      barWidth: '50%',
      itemStyle: { color: p => (p.value >= 0 ? UP : DOWN) } });
  }
  // KDJ 副图（grid 2）
  if (subIndicator === 'kdj' && kdjData) {
    series.push({ name: 'K', type: 'line', data: kdjData.k, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#5b8def', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'D', type: 'line', data: kdjData.d, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#f0c075', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'J', type: 'line', data: kdjData.j, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#a78bcf', width: 1 }, symbol: 'none', connectNulls: true });
  }

  // ── Tooltip ── THS 风格精确读数
  const allNames = ['K线', 'MA10','MA20','MA60', ...(boll ? ['BOLL上','BOLL中','BOLL下'] : []), '量'];
  if (subIndicator === 'macd') allNames.push('DIF','DEA','MACD');
  if (subIndicator === 'kdj')  allNames.push('K','D','J');

  const option = {
    backgroundColor: 'transparent',
    animation: false,  // R14: K线图重画时无动画,SSE/poll 直接切到新值
    grid: grids,
    // 2026-07-17: 加 MA10/20 图例,之前没有 legend 用户分不清线是 MA 几
    // 2026-07-17 v3: legend 关闭,改用 series.endLabel 把 MA10/20/60 直接画在每条线的右端,既不挡价格轴也不挡百分比轴
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: ACCENT, width: 0.6, opacity: 0.6 }, lineStyle: { color: ACCENT, width: 0.6, opacity: 0.6 } },
      backgroundColor: 'rgba(20, 18, 14, 0.96)',
      borderColor: CHART_LINE,
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
            ['MA10', ma10[idx]],
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
    // 显式 start/end = 0/100 确保初始状态显示完整数据范围(覆盖最高/最低点)
    dataZoom: [
      { type: 'inside', xAxisIndex: hasSub ? [0,1,2] : [0,1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: hasSub ? [0,1,2] : [0,1], height: 18, bottom: 4,
        start: 0, end: 100,
        textStyle: { color: INK2, fontSize: 9 },
        borderColor: CHART_LINE,
        fillerColor: 'rgba(212,160,86,0.15)',
        handleStyle: { color: ACCENT, borderColor: ACCENT } },
    ],
    series,
  };
  chart.setOption(option);
  // 确保尺寸正确 (容器在 tab 激活后才可见,但 ECharts init 可能读到 0 宽高)
  chart.resize();

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

  // ── 点击 K线柱子 → 切到分时 tab + 加载该日 (2026-07-15) ──
  chart.on('click', (params) => {
    if (!currentStockCode) return;
    let date = null;
    if (params && params.dataIndex != null && kline[params.dataIndex] && kline[params.dataIndex].date) {
      date = String(kline[params.dataIndex].date);
    } else {
      date = todayStr();
    }
    const pick = $('#intra-day-pick');
    if (pick) pick.value = date;
    const tabBtn = document.querySelector('.chart-tab[data-tab="intraday"]');
    if (tabBtn) tabBtn.click();
    loadIntraDay(currentStockCode, date);
  });

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
  // 切指标时 K 线 tab 必须可见才立即重绘,否则延迟更新
  const pane = $('#kline-chart')?.closest('[data-tab-pane]');
  if (pane && !pane.hidden) {
    drawKlineChart();
  } else {
    _klineDataReady = true;
  }
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
    <div class="er-msg"> <b>加载失败</b> · ${escapeHtml(msg)}</div>
    ${retry}
  </div>`;
}

// R71 (Batch 8): 个股页骨架屏 — 冷启时注入 5 个 card shimmer 占位
// 高度匹配实际渲染,避免 CLS (R74)
// 被 _hideStockSkeleton 移除;首次 render 或任意缓存命中后自动消失
function _showStockSkeleton(code) {
  // 避免重复注入
  if ($('#stock-skeleton')) return;
  // 检查是否有任何 cache hit — 有就直接不显示 skeleton
  const cached = _memFullGet(code, '') || _stockCacheLoad(code, '');
  if (cached) return;
  const host = $('.view-stock') || document.body;
  const skel = document.createElement('div');
  skel.id = 'stock-skeleton';
  skel.className = 'stock-skeleton';
  // 5 张占位 card: hero / quote / sector / chart / news (按实际比例)
  skel.innerHTML = `
    <article class="card mt-16 stock-skel-hero">
      <div class="skeleton skeleton-line xl" style="width:40%;margin-bottom:.6rem"></div>
      <div class="skeleton skeleton-line lg" style="width:60%"></div>
      <div class="skeleton skeleton-line" style="width:80%;margin-top:.4rem"></div>
    </article>
    <article class="card mt-16 stock-skel-quote">
      <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);gap:.6rem">
        ${Array.from({length:4}, () => `<div><div class="skeleton skeleton-line" style="width:60%"></div><div class="skeleton skeleton-line lg" style="width:80%;margin-top:.4rem"></div></div>`).join('')}
      </div>
    </article>
    <article class="card mt-16 stock-skel-sector" style="min-height:140px">
      <div class="skeleton skeleton-line" style="width:35%;margin-bottom:.8rem"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${Array.from({length:6}, () => '<div class="skeleton skeleton-line" style="width:60px;height:24px;border-radius:12px"></div>').join('')}
      </div>
    </article>
    <article class="card mt-16 stock-skel-chart" style="min-height:240px">
      <div class="skeleton skeleton-line" style="width:25%;margin-bottom:.8rem"></div>
      <div class="skeleton skeleton-block" style="height:200px;width:100%"></div>
    </article>
    <article class="card mt-16 stock-skel-news" style="min-height:80px">
      <div class="skeleton skeleton-line" style="width:30%;margin-bottom:.6rem"></div>
      <div class="skeleton skeleton-line" style="width:90%"></div>
      <div class="skeleton skeleton-line" style="width:75%;margin-top:.4rem"></div>
    </article>
  `;
  // 插入到 quickbar 之后(避免遮盖搜索栏)
  const quickbar = $('#stock-quickbar');
  if (quickbar && quickbar.parentNode) {
    quickbar.parentNode.insertBefore(skel, quickbar.nextSibling);
  } else {
    host.appendChild(skel);
  }
}

function _hideStockSkeleton() {
  const skel = $('#stock-skeleton');
  if (skel) skel.remove();
  // 移除 error card 也算 hide (成功路径)
  const err = $('#stock-error-card');
  if (err) err.remove();
}

// R77 (Batch 8): 全页失败错误卡 + 重试 (网络挂 + 无缓存兜底)
let _stockRetryHandler = null;
function _showStockError(code, msg) {
  _hideStockSkeleton();
  const host = $('.view-stock') || document.body;
  const err = document.createElement('div');
  err.id = 'stock-error-card';
  err.className = 'stock-error-card';
  err.innerHTML = `
    <article class="card mt-16 error-card">
      <div class="er-msg">
        <b>加载 ${escapeHtml(code)} 失败</b><br/>
        ${escapeHtml(msg || '网络异常,请稍后重试')}<br/>
        <span class="caption dim">提示: 网络不通时可稍后再试,历史浏览过的股票会从缓存秒开</span>
      </div>
      <button class="er-retry" id="er-retry-btn">↻ 重试</button>
    </article>
  `;
  const quickbar = $('#stock-quickbar');
  if (quickbar && quickbar.parentNode) {
    quickbar.parentNode.insertBefore(err, quickbar.nextSibling);
  } else {
    host.appendChild(err);
  }
  const btn = $('#er-retry-btn');
  if (btn) btn.addEventListener('click', () => {
    err.remove();
    loadStockDetail(code);
  });
}

// R76 (Batch 8): 重试 + exponential backoff 封装 (用于子 loader)
async function _retryWithBackoff(fn, maxRetries = 2, baseMs = 500) {
  let lastErr;
  for (let i = 0; i <= maxRetries; i++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (i < maxRetries) {
        await new Promise(r => setTimeout(r, baseMs * Math.pow(2, i)));
      }
    }
  }
  throw lastErr;
}
// 数字滚动（首次显示或大变化时使用，~500ms 平滑过渡）
// R16: 全局追踪正在运行的 animateNumber RAF — 切股时全部取消
var _animateNumberRaf = new Set();
function _cancelAnimateNumbers() {
  for (const id of _animateNumberRaf) try { cancelAnimationFrame(id); } catch {}
  _animateNumberRaf.clear();
}
function animateNumber(el, from, to, dur = 500, fmt = (v) => v.toFixed(2), dir) {
  if (!el) return;
  if (dir == null) dir = to > from ? 'up' : to < from ? 'down' : 'flat';
  const start = performance.now();
  const delta = to - from;
  el.classList.add('is-animating', `flash-${dir}`);
  let myId = null;
  function step(t) {
    const k = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - k, 3); // easeOutCubic
    el.textContent = fmt(from + delta * eased);
    if (k < 1) {
      myId = requestAnimationFrame(step);
      _animateNumberRaf.add(myId);
    } else {
      el.classList.remove('is-animating');
      if (myId != null) _animateNumberRaf.delete(myId);
    }
  }
  myId = requestAnimationFrame(step);
  _animateNumberRaf.add(myId);
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
var INTRADAY_CACHE_MAX = 200;   // B-15: LRU 上限
var intraDayCache = new Map();  // date -> data
var intraDayLoading = null;

function _intraDayCacheSet(key, val) {
  if (intraDayCache.size >= INTRADAY_CACHE_MAX) {
    const it = intraDayCache.keys();
    const n_to_drop = intraDayCache.size - INTRADAY_CACHE_MAX + 1;
    for (let i = 0; i < n_to_drop; i++) {
      const k = it.next().value;
      if (k != null) intraDayCache.delete(k);
    }
  }
  intraDayCache.set(key, val);
}

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
  // R8a: stale-code guard — streak 格子后台 prefetch 链可能跨股,旧 code 静默丢
  if (code !== window._currentStockCode) return;
  const cacheKey = code + ':' + dateStr;
  if (intraDayLoading === cacheKey) return;
  const cached = intraDayCache.get(cacheKey);
  if (cached) {
    renderIntraDay(cached);
    return;
  }
  intraDayLoading = cacheKey;
  const note = $('#intra-day-note');
  note.textContent = `加载 ${dateStr} 分时 …`;
  note.style.color = INK2;
  const fetchOnce = async () => {
    const data = await api(`/api/stock/${code}/intraday?date=${encodeURIComponent(dateStr)}`);
    return data;
  };
  try {
    let data = await fetchOnce();
    // 2026-07-18: 凌晨四源 race 时偶尔返空 ticks → 1.5s 后单次 retry (避免用户看到假"无数据")
    if (!(data?.ticks && data.ticks.length) && !data?.note) {
      await new Promise(r => setTimeout(r, 1500));
      try { data = await fetchOnce(); } catch (_) { /* 落到下方 catch */ }
    }
    const merged = { code, date: dateStr, ...data };
    _intraDayCacheSet(cacheKey, merged);
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
    drawIntraDayChart(code, date, [], null, null, null, null);
    return;
  }

  // 计算日内 KPI
  const opens = ticks.map(t => t.open).filter(v => v != null);
  const highs = ticks.map(t => t.high).filter(v => v != null);
  const lows  = ticks.map(t => t.low).filter(v => v != null);
  const prices = ticks.map(t => t.price).filter(v => v != null);
  const openRef = opens.length ? opens[0] : (prices[0] || 0);
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

  // 振幅 = (最高 - 最低) / 昨收 — 永远用 data.prev_close 或 openRef,不用 lastStockContext (那是今日的)
  const refForAmp = (data.prev_close ?? openRef);
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
    data.prev_close ?? openRef,
    data.limit_up_price ?? lastStockContext.limit_up_price,
    data.support_levels || null);
}

async function drawIntraDayChart(code, date, ticks, openRef, prevClose, limitUp, supportLevels) {
  const dom = $('#intra-day-chart');
  if (!dom) return;
  if (echartsCharts.intraDay) { echartsCharts.intraDay.dispose(); echartsCharts.intraDay = null; }
  const tk = _newChartToken('intraDay');
  await _ensureECharts();
  if (_isChartTokenStale('intraDay', tk)) return;
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.intraDay = chart;

  if (!ticks.length) {
    chart.setOption(emptyChartOption('暂无分时数据'));
    return;
  }

  const times = ticks.map(t => t.time);
  const prices = ticks.map(t => t.price);
  const validPrices = prices.filter(p => p != null);
  if (validPrices.length < 2) {
    chart.setOption(emptyChartOption('分时数据点数不足'));
    return;
  }
  const refVal = (prevClose != null && prevClose > 0) ? prevClose : (openRef || validPrices[0]);
  const refLine = times.map(_ => refVal);

  // ── Y 轴覆盖数据范围 + 上下余量 ──
  // 同花顺风格: 顶部用实际最高价 (不能裁掉突发 spike 到日内最高的 1min tick),
  //            底部用 2% 百分位避免开盘瞬间噪声;顶部额外 1.5x padding 给末值标呼吸空间.
  // 旧版 pIdx(0.98) 看似温和,但单根 1min tick 冲到日内最高 → 落在 top 2% → 被裁掉看不到。
  const sortedPrices = [...validPrices].sort((a, b) => a - b);
  const pIdx = (p) => sortedPrices[Math.max(0, Math.min(sortedPrices.length - 1, Math.floor(p * sortedPrices.length)))];
  const dataMin = Math.min(pIdx(0.02), refVal);  // 底部用 2% 百分位
  const dataMax = Math.max(...validPrices, refVal);  // 顶部用真实最高 (含 spike)
  const dataRange = Math.max(dataMax - dataMin, refVal * 0.001);
  const basePad = Math.max(dataRange * 0.08, refVal * 0.0015);
  const yMin = dataMin - basePad;
  // 顶部 padding 加大 50% — 给"末值% end-label"留呼吸空间,标签不会再贴到价格线上
  const yMax = dataMax + basePad * 1.5;

  // ── 均价线（量加权 rolling）──
  const avgLine = [];
  let cumPV = 0, cumV = 0;
  for (let i = 0; i < ticks.length; i++) {
    const p = ticks[i].price;
    const v = ticks[i].volume_hand || 0;
    if (p != null) { cumPV += p * v; cumV += v; }
    avgLine.push(cumV > 0 ? +(cumPV / cumV).toFixed(3) : null);
  }

  // ── 量能柱：按涨跌方向着色（红涨绿跌）──
  const volBars = ticks.map(t => {
    const v = t.volume_hand || 0;
    const p = t.price;
    const color = p != null && p > refVal ? UP : (p != null && p < refVal ? DOWN : INK3);
    return { value: v, itemStyle: { color, opacity: 0.5 } };
  });

  // ── 涨跌区域填充 ──
  const upArea = prices.map(p => p != null && p >= refVal ? p : refVal);
  const dnArea = prices.map(p => p != null && p < refVal ? p : refVal);

  // ── 高低包络带: 半透明填充 high-low 区间,日内振幅视觉放大 ──
  const ticksH = ticks.map(t => t.high);
  const ticksL = ticks.map(t => t.low);
  const hasHL = ticksH.some(v => v != null) && ticksL.some(v => v != null);
  const hlHigh = hasHL ? ticksH.map((h, i) => h != null ? h : (ticksL[i] != null ? ticksL[i] : null)) : [];
  const hlLow  = hasHL ? ticksL.map((l, i) => l != null ? l : (ticksH[i] != null ? ticksH[i] : null)) : [];

  // ── X 轴时间标签（精选时刻）──
  const labelTimes = ['09:30', '10:30', '11:30', '13:00', '14:00', '15:00'];
  const labelIndexMap = {};
  for (const lt of labelTimes) {
    const idx = times.findIndex(t => t && t.startsWith(lt));
    if (idx >= 0) labelIndexMap[idx] = lt;
  }

  // ── 时间分界线：11:30（午休）和 15:00（收盘）──
  const dividerTimes = ['11:30', '15:00'];
  const dividerIndex = dividerTimes.map(rt => times.findIndex(t => t && t.startsWith(rt))).filter(i => i >= 0);
  const timeMarkers = dividerIndex.map(i => ({
    xAxis: i,
    lineStyle: { color: '#3a3735', type: 'dashed', width: 1, opacity: 0.5 },
    label: { show: true, formatter: times[i].slice(0, 5), position: 'start', color: INK3, fontSize: 9 }
  }));

  // ── 涨停价参考线 ──
  const limitUpLine = (limitUp != null && limitUp > 0 && limitUp >= yMin && limitUp <= yMax)
    ? { name: '涨停价', type: 'line', data: times.map(_ => limitUp),
        showSymbol: false, lineStyle: { color: UP, type: 'dashed', width: 1, opacity: 0.6 },
        tooltip: { show: false } }
    : null;

  // ── 日线均线参考 (MA5/MA10/MA20) ──
  let refLines = [];

  // ── 支撑/压力线 (1/3 回升位 + 谷底A + 山顶B + 5日线参考) ──
  // 2026-07-19: 用户要求分时图标注关键支撑/压力位,跟个股页个股分析联动
  if (supportLevels) {
    // 1/3 回升位 (强支撑, 1px 紫色虚线)
    const l13 = supportLevels.level_1_3;
    if (l13 != null && l13 >= yMin && l13 <= yMax) {
      refLines.push({
        yAxis: +l13.toFixed(3),
        lineStyle: { color: '#9a8cff', type: 'dashed', width: 1.2, opacity: 0.85 },
        label: { formatter: `1/3位 ${(+l13).toFixed(2)}`, color: '#9a8cff', fontSize: 9,
                 position: 'insideEndBottom', distance: 4, backgroundColor: 'rgba(10,9,8,0.75)',
                 padding: [1, 4], borderRadius: 3, fontWeight: 600 },
      });
    }
    // 谷底 A (下轨, 浅绿)
    const a = supportLevels.A;
    if (a != null && a >= yMin && a <= yMax) {
      refLines.push({
        yAxis: +a.toFixed(3),
        lineStyle: { color: '#52c85a', type: 'dotted', width: 1, opacity: 0.7 },
        label: { formatter: `A=${(+a).toFixed(2)}`, color: '#52c85a', fontSize: 9,
                 position: 'insideEndBottom', distance: 4, backgroundColor: 'rgba(10,9,8,0.75)',
                 padding: [1, 4], borderRadius: 3 },
      });
    }
    // 山顶 B (上轨, 红色)
    const b = supportLevels.B;
    if (b != null && b >= yMin && b <= yMax) {
      refLines.push({
        yAxis: +b.toFixed(3),
        lineStyle: { color: '#ff4d4f', type: 'dotted', width: 1, opacity: 0.7 },
        label: { formatter: `B=${(+b).toFixed(2)}`, color: '#ff4d4f', fontSize: 9,
                 position: 'insideEndTop', distance: 4, backgroundColor: 'rgba(10,9,8,0.75)',
                 padding: [1, 4], borderRadius: 3 },
      });
    }
    // 5 日线参考 (从日线 K 线最后 5 日 close 均价)
    if (Array.isArray(supportLevels.daily_ma5)) {
      const lastMa5 = supportLevels.daily_ma5[supportLevels.daily_ma5.length - 1];
      if (lastMa5 != null && lastMa5 >= yMin && lastMa5 <= yMax) {
        refLines.push({
          yAxis: +lastMa5.toFixed(3),
          lineStyle: { color: '#ff9f43', type: 'dashed', width: 1.4, opacity: 0.9 },
          label: { formatter: `MA5 ${lastMa5.toFixed(2)}`, color: '#ff9f43', fontSize: 10,
                   position: 'insideEndTop', distance: 4, backgroundColor: 'rgba(10,9,8,0.75)',
                   padding: [1, 4], borderRadius: 3, fontWeight: 700 },
        });
      }
    }
  }

  // ── 同花顺风格末值标 (右轴外贴彩色标签) ──
  const _lastIdx = (() => { for (let i = prices.length - 1; i >= 0; i--) if (prices[i] != null) return i; return -1; })();
  const _lastPrice = _lastIdx >= 0 ? prices[_lastIdx] : null;
  const _lastColor = _lastPrice != null && _lastPrice >= refVal ? UP : (_lastPrice != null && _lastPrice < refVal ? DOWN : INK2);
  const _lastPct = _lastPrice != null && refVal > 0 ? ((_lastPrice - refVal) / refVal * 100) : 0;
  const _lastLabel = _lastPrice != null ? `${_lastPrice.toFixed(2)} ${(_lastPct >= 0 ? '+' : '') + _lastPct.toFixed(2)}%` : '';
  // R-fix-2026-07-18: 末值在 y 轴上半区时 (距离 yMax < 25% yRange),把末值标从 end+distance:4 翻到 insideEndBottom —
  // 否则 end 标签占右上角,跟 MA5/10/20 的 insideEndTop 标签堆在同一像素,互相遮挡
  const _yRange = yMax - yMin;
  const _lastPriceNearTop = _lastPrice != null && _yRange > 0 && (yMax - _lastPrice) < _yRange * 0.25;
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
      if (ma < yMin || ma > yMax) continue;
      refLines.push({
        yAxis: +ma.toFixed(3),
        lineStyle: { color, type: n === 5 ? 'solid' : 'dashed', width: w, opacity: 0.75 },
        // 2026-07-17: 标签 insideEndTop (右上方) + 半透背,在右轴刻度列内部清晰可见
        label: { formatter: `MA${n} ${ma.toFixed(2)}`, color, fontSize: n === 5 ? 10 : 9,
                 position: 'insideEndTop', distance: 4, backgroundColor: 'rgba(10,9,8,0.75)',
                 padding: [1, 4], borderRadius: 3, fontWeight: n === 5 ? 700 : 500,
                 textBorderColor: 'transparent' },
      });
    }
  }

  chart.setOption({
    backgroundColor: 'transparent',
    grid: [
      { left: 56, right: 76, top: 30, height: '58%' },
      { left: 56, right: 24, top: '74%', height: '22%' },
    ],
    tooltip: {
      trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE,
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
          s += `<div>价 <b style="${upCls}">${price.toFixed(2)}</b> <span style="${upCls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span></div>`;
        }
        if (pMap['均价'] != null) {
          const avgPct = refVal ? ((+pMap['均价'] - refVal) / refVal * 100) : 0;
          s += `<div>均价 <b style="color:#ff9f43">${(+pMap['均价']).toFixed(3)}</b> <span style="color:${avgPct >= 0 ? UP : DOWN}">${avgPct >= 0 ? '+' : ''}${avgPct.toFixed(2)}%</span></div>`;
        }
        s += `<div style="color:${INK3}">昨收 ${refVal.toFixed(2)}</div>`;
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
      { type: 'category', data: times, gridIndex: 0,
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: 10, interval: 0,
          formatter: (v, i) => labelIndexMap[i] || '' },
        splitLine: { show: false } },
      { type: 'category', data: times, gridIndex: 1,
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: 9,
          interval: Math.max(1, Math.floor(times.length / 8)),
          formatter: (v, i) => labelIndexMap[i] || '' },
        splitLine: { show: false } },
    ],
    yAxis: [
      // 左轴 + 右轴 必须严格共享同一价格区间,确保左右刻度像素级对齐
      // 2026-07-17 修 bug #4: 之前 yMin/yMax 用数据原始范围,右轴用 alignedMin/alignedMax,
      //                    两者不一致 → 左轴 9.20~9.63 映射右轴 -3% ~ +2% 范围错位
      ...(function() {
        const totalPctRange = ((yMax - yMin) / refVal) * 100;
        // R-fix-2026-07-17 (bug #5): 之前 segs ∈ [4,8] 仍会出现 label 被自动隐藏 (002891 7% 区间 → stepPct=1 → 8 个 label, 图表高度只够 6 个, 自动漏 +3.0% / 30.54)
        // 收紧: segs ∈ [3,6] → 4-7 个 label, 留余量给 axisLabel fontSize=10 + padding
        const candidates = [0.05, 0.1, 0.2, 0.5, 1, 2, 3, 5];
        let stepPct = 5;
        for (const s of candidates) {
          const segs = totalPctRange / s;
          if (segs >= 3 && segs <= 6) { stepPct = s; break; }
          if (segs < 3) { stepPct = s; break; }
        }
        const stepPrice = stepPct * refVal / 100;
        const k = Math.max(0, Math.floor((refVal - yMin + 0.5 * stepPrice) / stepPrice));
        const alignedMin = refVal - k * stepPrice;
        const kMax = Math.max(0, Math.ceil((yMax - refVal - 0.5 * stepPrice) / stepPrice));
        const alignedMax = refVal + kMax * stepPrice;
        return [
          // 左轴:价格 — 用 alignedMin/alignedMax + interval,刻度跟右轴像素级对齐
          // interval: 0 强制显示所有刻度 (echarts 默认 'auto' 会因空间不足漏掉中间 label)
          { type: 'value', gridIndex: 0, position: 'left',
            min: alignedMin, max: alignedMax, interval: stepPrice,
            splitLine: { lineStyle: { color: GRID } },
            axisLabel: { color: INK2, fontSize: 10, interval: 0, formatter: v => v.toFixed(2) } },
          // 右轴:涨跌幅 % — 同一区间,formatter 把 v 换算成 pct
          { type: 'value', gridIndex: 0, position: 'right',
            min: alignedMin, max: alignedMax, interval: stepPrice,
            splitLine: { show: false },
            axisLabel: { color: INK2, fontSize: 10, interval: 0,
              formatter: v => {
                if (!refVal) return '';
                const pct = ((v - refVal) / refVal) * 100;
                const sign = pct >= 0 ? '+' : '';
                return Math.abs(pct) < 0.001 ? '0.00%' : sign + pct.toFixed(1) + '%';
              } },
            axisLine: { show: true, lineStyle: { color: CHART_LINE } },
          },
        ];
      })(),
      // 成交量轴
      { gridIndex: 1, splitLine: { lineStyle: { color: GRID } },
        axisLabel: { color: INK2, fontSize: 9 } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ],
    series: [
      // 高低包络带 (low → high 半透明面积,日内振幅放大可视)
      // z:0 在最底,避免遮盖主价格线
      ...(hasHL ? [
        { name: '高', type: 'line', data: hlHigh, showSymbol: false, silent: true,
          lineStyle: { width: 0 }, stack: 'hl_env', z: 0 },
        { name: '低-高带', type: 'line', data: hlLow.map((l, i) => {
          const h = hlHigh[i]; if (l == null || h == null) return null;
          return +(h - l).toFixed(3);
        }), showSymbol: false, silent: true,
          lineStyle: { width: 0 }, stack: 'hl_env',
          areaStyle: { color: 'rgba(155,140,255,0.10)' }, z: 0 },
      ] : []),
      // 涨区域填充（红色半透明）
      { name: '涨区域', type: 'line', data: upArea, showSymbol: false, silent: true,
        lineStyle: { width: 0 }, areaStyle: { color: 'rgba(232,69,69,0.05)' }, z: 1 },
      // 跌区域填充（绿色半透明）
      { name: '跌区域', type: 'line', data: dnArea, showSymbol: false, silent: true,
        lineStyle: { width: 0 }, areaStyle: { color: 'rgba(52,199,89,0.05)' }, z: 1 },
      // 价格线（红色主线条 — 2026-07-16 修白底白线看不见 bug）
      // 2026-07-16 顺手:开 smooth 0.35 让 1min tick 之间不那么硬折角(免费数据源极限
      // 1min 颗粒度,平滑曲线视觉上更接近「秒级」观感;价格真值不变,纯渲染层)
      // 2026-07-18: 用户反馈"太平滑了,看不到 tick 级波动" → 改 smooth:false 让每根 1min
      //              tick 折角都出来,日内真实波动可见; 同时加 high-low 半透明包络带
      //              强化日内振幅视觉 (不改价格真值,纯渲染层)
      // 2026-07-18: 加最新价右轴标签 (markLine + label.position='end'),同花顺风格
      //              用户反馈: 标签只显示涨跌百分比,价格由右轴承担 (避免双重视觉)
      { name: '价格', type: 'line', data: prices, showSymbol: false, smooth: false,
        // R-fix-2026-07-18: 关掉 grid clip,价格线 / markLine 在 yMax 之外仍然可见(确保 spike 到日内最高不被裁)
        clip: false,
        lineStyle: { color: UP, width: 1.8 }, itemStyle: { color: UP },
        markLine: {
          silent: true, symbol: 'none',
          data: [
            ...timeMarkers,
            ...refLines,
            // 同花顺风格末值标 — 右轴外贴彩色标签,涨跌染色
            ...(_lastPrice != null && _lastIdx >= 0 ? [{
              name: '最新',
              xAxis: _lastIdx, yAxis: _lastPrice,
              lineStyle: { color: _lastColor, type: 'solid', width: 1, opacity: 0.7 },
              label: {
                show: true,
                // R-fix-2026-07-18: 接近 yMax 时翻到 insideEndBottom,避免跟 MA5 insideEndTop 右上角遮挡
                position: _lastPriceNearTop ? 'insideEndBottom' : 'end',
                // 只显示涨跌百分比,价格由左轴 + 折线本身承担
                formatter: _lastPrice != null && refVal > 0
                  ? `${((_lastPrice - refVal) / refVal * 100 >= 0 ? '+' : '') + ((_lastPrice - refVal) / refVal * 100).toFixed(2)}%`
                  : '',
                color: '#fff', fontSize: 10, fontWeight: 700,
                backgroundColor: _lastColor, padding: [2, 6], borderRadius: 3,
                distance: _lastPriceNearTop ? 6 : 4,
              },
            }] : []),
          ],
        },
        z: 5 },
      // 均价线（橙色）
      { name: '均价', type: 'line', data: avgLine, showSymbol: false,
        lineStyle: { color: '#ff9f43', width: 1.6, type: 'solid' },
        itemStyle: { color: '#ff9f43' }, z: 4 },
      // 昨收参考线（灰色虚线）
      { name: '昨收', type: 'line', data: refLine, showSymbol: false,
        lineStyle: { color: '#6b6660', type: 'dashed', width: 1 }, z: 2 },
      ...(limitUpLine ? [limitUpLine] : []),
      // 成交量柱（红涨绿跌）
      { name: '成交量', type: 'bar', data: volBars, xAxisIndex: 1, yAxisIndex: 2, barWidth: '70%' },
    ],
  });
}

// ────────────────────────────────────────────
// STOCK 内部 tab
// ────────────────────────────────────────────
var currentStockCode = null;
// 当日分时辅助上下文（renderStockDetail 时填充，loadIntraDay 使用）
var lastStockContext = { prev_close: null, limit_up_price: null, code: null };
$$('.chart-tab[data-tab]').forEach(t => {
  // 2026-07-16 R97 A11y: 让 tab 可聚焦 + 键盘左右切换 (W3C tabs pattern)
  t.setAttribute('role', 'tab');
  t.setAttribute('tabindex', '0');
  const tabGroup = t.parentElement?.querySelectorAll('.chart-tab[data-tab]');
  if (tabGroup) {
    t.setAttribute('aria-controls', `tab-pane-${t.dataset.tab}`);
  }
  t.addEventListener('keydown', (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const siblings = Array.from(t.parentElement?.querySelectorAll('.chart-tab[data-tab]') || []);
    if (!siblings.length) return;
    const idx = siblings.indexOf(t);
    let next = idx;
    if (e.key === 'ArrowLeft') next = (idx - 1 + siblings.length) % siblings.length;
    else if (e.key === 'ArrowRight') next = (idx + 1) % siblings.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = siblings.length - 1;
    siblings[next].focus();
    siblings[next].click();
  });
  t.addEventListener('click', () => {
    const tab = t.dataset.tab;
    // 限定到当前 tab 所在的 view，避免影响其它视图
    const view = t.closest('.view');
    if (view) {
      view.querySelectorAll('.chart-tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      view.querySelectorAll('[data-tab-pane]').forEach(p => p.hidden = (p.dataset.tabPane !== tab));
      // 2026-07-16: 每个 tab pane 现在是独立 article.card + 自带 card-eyebrow title
      // → 不再需要动态 title 替换,顶替的 titleEl.textContent 逻辑删掉
    }
    if (tab === 'flow') {
      if (!_flowChartDrawn && _pendingFlowData) {
        drawFlowChart(_pendingFlowData);
        _flowChartDrawn = true;
      } else if (echartsCharts.flow) {
        echartsCharts.flow.resize();
      }
    }
    if (tab === 'kline') {
      if (_klineDataReady) {
        // 数据已就绪 → 画图(若尚未画)或 resize
        if (!_klineChartDrawn) drawKlineChart();
        else if (echartsCharts.kline) echartsCharts.kline.resize();
      } else {
        // 数据尚未到达 → 注册一次性监听,数据到后自动画
        // 后续 timer 轮询等待 _klineDataReady (最长 30s)
        (function _waitKline() {
          if (_klineDataReady) {
            if (!_klineChartDrawn) drawKlineChart();
            else if (echartsCharts.kline) echartsCharts.kline.resize();
          } else {
            setTimeout(_waitKline, 200);
          }
        })();
      }
    }
    if (tab === 'intraday') {
      if (echartsCharts.intraDay) echartsCharts.intraDay.resize();
      if (currentStockCode) {
        initIntraDayPicker(currentStockCode);
        // 首次进入 tab 自动加载当日分时
        const pick = $('#intra-day-pick');
        if (pick && pick.value && currentStockCode && !intraDayCache.has(currentStockCode + ':' + pick.value)) {
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
// NEWS · 全局新闻 tab
// ────────────────────────────────────────────
var newsCache = null;

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
    // 2026-07-16 R46: 列表为空时给一个手动刷新按钮 (避免 "暂无新闻" 死局)
    list.innerHTML = '<div class="empty-card"><p class="caption dim">暂无新闻</p>' +
      '<button class="btn btn-ghost btn-sm" data-news-retry>🔄 刷新</button></div>';
    const retry = list.querySelector('[data-news-retry]');
    if (retry) retry.addEventListener('click', () => loadNewsList(false));
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
var sectorsCache = null;

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
              <span style="color:${n.direction === '利好' ? UP : n.direction === '利空' ? DOWN : INK2}">${n.score != null ? n.score.toFixed(1) : '—'}</span>
              <span class="dim">·</span>
              <span>${escapeHtml((n.title || '').slice(0, 38))}</span>
              ${n.ctime_str ? `<span class="dim" style="margin-left:6px;font-size:.7rem">${n.ctime_str}</span>` : ''}
            </div>
          `).join('')}
        </div>`;
      }).join('')}
    </div>`;
}

// ────────────────────────────────────────────
// STOCK 页：板块情绪 + 相关新闻
// ────────────────────────────────────────────
// 2026-07-19: 加载个股板块角色 (龙头/中军/杂毛) — 在 stock-title 旁显示 badge
async function _loadStockRole(code) {
  const host = $('#stock-tags-host');
  if (!host) return;
  // 防 stale: 切股后旧 role 不显示
  if (code !== window._currentStockCode) return;
  try {
    const env = await api(`/api/stock/${code}/role`);
    if (!env || !env.ok) return;
    if (code !== window._currentStockCode) return;
    const d = env.data || {};
    const role = d.role || '未分类';
    const roleColors = {
      '龙头':  '#e84545',  // 红
      '中军':  '#5b8def',  // 蓝
      '杂毛':  '#888',     // 灰
      '未分类': '#666',    // 暗灰
    };
    const color = roleColors[role] || '#666';
    const tip = `${d.reason || ''} · ${d.explanation || ''}`.slice(0, 120);
    host.innerHTML = `<span class="stock-role-badge" style="display:inline-block;padding:2px 10px;font-size:11px;font-weight:600;border-radius:12px;background:${color}22;color:${color};border:1px solid ${color}55;margin-left:8px;vertical-align:middle" title="${escapeHtml(tip)}">${escapeHtml(role)}</span>`;
  } catch (e) {
    console.debug('[stock-role]', e.message);
  }
}

// 2026-07-19: 加载个股周线擒牛卡 (5 大信号)
const _WB_CARD_LABELS = {
  sanxing_taodi:     '三星探底',
  zhanwen_5w:        '站稳5周线',
  tupo_pingtai:      '突破震荡平台',
  junxian_fangxiang: '均线方向',
  zhouxian_duiliang: '周线堆量',
};
async function _loadWeeklyBullCard(code) {
  const card = $('#q-weekly-bull-card');
  const body = $('#q-weekly-bull-body');
  if (!card || !body) return;
  if (code !== window._currentStockCode) return;
  card.hidden = false;
  body.innerHTML = '<span class="dim">加载中…</span>';
  try {
    const env = await api(`/api/stock/${code}/weekly_bull`);
    if (!env || !env.ok) {
      body.innerHTML = `<span class="dim">未命中 5 大信号 — ${escapeHtml(env?.error || '加载失败')}</span>`;
      return;
    }
    if (code !== window._currentStockCode) return;
    const d = env.data || {};
    const matched = d.matched || [];
    const reasons = d.reasons || {};
    const wk = d.weekly_last || {};
    if (!matched.length) {
      body.innerHTML = `
        <p class="dim" style="margin:.25rem 0">当前未命中 5 大信号。</p>
        <p class="caption dim" style="margin:0">周收盘 ${wk.close != null ? wk.close.toFixed(2) : '—'} · 周涨跌 ${wk.change_pct != null ? (wk.change_pct >= 0 ? '+' : '') + wk.change_pct.toFixed(2) + '%' : '—'} · 5W MA ${wk.wma5 ?? '—'}</p>
      `;
      return;
    }
    const chips = matched.map(k => {
      const reason = reasons[k] || '';
      return `<span class="chip tag-good wb-card-chip" data-action="show-view:weekly_bull?pattern=${escapeHtml(k)}" title="${escapeHtml(reason)}">${escapeHtml(_WB_CARD_LABELS[k] || k)}</span>`;
    }).join('');
    const reasonList = matched.map(k =>
      `<li><b>${escapeHtml(_WB_CARD_LABELS[k] || k)}</b>: ${escapeHtml((reasons[k] || '').slice(0, 80))}</li>`
    ).join('');
    body.innerHTML = `
      <div class="wb-card-chips">${chips}</div>
      <p class="caption dim" style="margin:.25rem 0">命中 <b class="good">${matched.length}/5</b> · 周收盘 ${wk.close != null ? wk.close.toFixed(2) : '—'} · 周涨跌 ${wk.change_pct != null ? (wk.change_pct >= 0 ? '+' : '') + wk.change_pct.toFixed(2) + '%' : '—'} · 5W MA ${wk.wma5 ?? '—'}</p>
      <ul class="wb-card-reasons">${reasonList}</ul>
    `;
  } catch (e) {
    body.innerHTML = `<span class="dim">加载异常: ${escapeHtml(e.message)}</span>`;
  }
}

// 2026-07-19: 加载个股 1/3 回升位卡
async function _loadRecoveryCard(code) {
  const card = $('#q-recovery-card');
  const body = $('#q-recovery-body');
  if (!card || !body) return;
  if (code !== window._currentStockCode) return;
  card.hidden = false;
  body.innerHTML = '<span class="dim">加载中…</span>';
  try {
    const env = await api(`/api/stock/${code}/recovery_level`);
    if (!env || !env.ok) {
      body.innerHTML = `<span class="dim">回升位加载失败 — ${escapeHtml(env?.error || '未知')}</span>`;
      return;
    }
    if (code !== window._currentStockCode) return;
    const d = env.data || {};
    if (!d.has_signal) {
      body.innerHTML = `<p class="dim" style="margin:.25rem 0">${escapeHtml(d.explanation || '未找到明显的上一轮上涨 (K 线不足或单边)')}</p>`;
      return;
    }
    const cls = d.near_support ? 'tag-good' : '';
    const distPct = d.distance_to_level_1_3_pct != null ? (d.distance_to_level_1_3_pct >= 0 ? '+' : '') + d.distance_to_level_1_3_pct.toFixed(2) + '%' : '—';
    body.innerHTML = `
      <div class="recovery-grid">
        <div><span class="dim">A 谷底</span> · <b>${d.A}</b> <span class="caption dim">${d.A_date || ''}</span></div>
        <div><span class="dim">B 山顶</span> · <b>${d.B}</b> <span class="caption dim">${d.B_date || ''}</span></div>
        <div><span class="dim">涨幅</span> · <b>${d.change_pct != null ? '+' + d.change_pct.toFixed(2) + '%' : '—'}</b></div>
        <div><span class="dim">现价</span> · <b>${d.current_close ?? '—'}</b></div>
        <div class="${cls}"><span class="dim">1/3 位</span> · <b>${d.level_1_3}</b> ${d.near_support ? '<span class="tag-good caption" style="margin-left:4px">强支撑</span>' : ''}</div>
        <div><span class="dim">1/2 位</span> · <b>${d.level_1_2 ?? '—'}</b></div>
        <div><span class="dim">2/3 位</span> · <b>${d.level_2_3 ?? '—'}</b></div>
        <div><span class="dim">距 1/3 位</span> · <b>${distPct}</b></div>
      </div>
      <p class="caption dim" style="margin:.25rem 0">${escapeHtml((d.explanation || '').slice(0, 200))}</p>
    `;
  } catch (e) {
    body.innerHTML = `<span class="dim">加载异常: ${escapeHtml(e.message)}</span>`;
  }
}

async function loadStockSector(code) {
  const host1 = $('#q-sector-board');
  const host2 = $('#q-sector-industries');
  const host3 = $('#q-sector-source');
  const host4 = $('#q-sector-sentiment');
  const host5 = $('#q-related-news');
  const card = $('#stock-sector-card');
  if (!host1) return;
  // R-fix-2026-07-16: sector card 加载时 unhide (_resetStockHero 会默认隐藏)
  if (card) card.hidden = false;

  host1.innerHTML = '<div class="kv-row"><span>加载中…</span></div>';
  host2.innerHTML = '';
  host3.textContent = '';
  host4.innerHTML = '';
  host5.innerHTML = '';

  let sec = {};
  try {
    // 2026-07-17 性能: 优先用 _prefetchStockAux 的 sector 缓存 (避免重复 fetch)
    sec = (await _auxGet(code, 'sector', `/api/stock/${code}/sector`)) || {};
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
      return `<span class="chip" style="border-color:${c};color:${c};font-weight:bold">${escapeHtml(l)}${warn}</span>`;
    }).join('');
        // 行业归类 + AI 战场 — 分两行,层次清晰
    const stdSection = stdChips
      ? `<div class="section-label" style="font-size:10px;color:var(--ink2);margin-bottom:4px">📋 行业归类</div><div class="chip-row" style="display:flex;flex-wrap:wrap;gap:5px">${stdChips}</div>`
      : '';
    const aiSection = aiChips
      ? `<div class="section-label" style="font-size:10px;color:var(--ink2);margin-top:8px;margin-bottom:4px">🤖 AI 战场</div><div class="chip-row" style="display:flex;flex-wrap:wrap;gap:5px">${aiChips}</div>`
      : '';
    host2.innerHTML = stdSection + aiSection || '<span class="dim">行业分类待补</span>';
    host3.textContent = `数据来源：${sec.source || '—'}${sec.fresh ? '（刚拉到）' : ''}`;

    // ─── 顶部 hero 也带行业 chip（顶级 UX：扫一眼就懂是哪条赛道）──
    const heroTags = $('#qh-tags');
    if (heroTags) {
      const heroHtml = stdChips + aiChips;
      heroTags.insertAdjacentHTML('beforeend', heroHtml ? `<span class="qh-tag-sep">·</span>${heroHtml}` : '');
    }

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

    // 紧凑：只显示 AI 评分最高 1 条新闻 + 跳新闻 tab 的入口
    if (news.length) {
      const n = news[0];
      const a = n.ai;
      const dirColor = a.direction === '利好' ? UP : a.direction === '利空' ? DOWN : INK2;
      host5.innerHTML = `
        <div class="news-card ${a.score >= 7 ? 'hot' : a.score >= 4 ? 'warm' : 'cold'}">
          <div class="news-score"><div class="news-score-num" style="color:${dirColor}">${a.score.toFixed(1)}</div></div>
          <div class="news-body">
            <div class="news-title" style="font-size:.92rem">${escapeHtml(n.title)}</div>
            <div class="news-meta"><span class="dim">${n.ctime_str || ''} · ${escapeHtml(n.media || '')} · ${a.direction || ''}</span></div>
          </div>
        </div>
        <p class="caption dim" style="margin:.4rem 0 0">
          命中 ${news.length} 条相关新闻 ·
          <a href="#" id="go-news-tab" style="color:var(--accent)">查看全部 → 📰 新闻 tab</a>
        </p>`;
      const goNews = $('#go-news-tab');
      if (goNews) goNews.addEventListener('click', (e) => {
        e.preventDefault();
        const tabBtn = document.querySelector('.chart-tab[data-tab="news"]');
        tabBtn?.click();
      });
    } else {
      host5.innerHTML = '<p class="caption dim">暂无与该股直接相关的新闻（AI 评分按申万行业 / 涉及股票过滤）</p>';
    }
  } catch (e) {
    // R73 (Batch 8): per-card 错误状态 + 重试按钮
    const retryBtnId = `sec-retry-${code}`;
    host1.innerHTML = `
      <div class="kv-row"><span class="down">板块加载失败</span><b>${escapeHtml(e.message)}</b></div>
      <div class="kv-row"><button class="btn-mini" id="${retryBtnId}">↻ 重试</button></div>
    `;
    document.getElementById(retryBtnId)?.addEventListener('click', () => loadStockSector(code));
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
    // 2026-07-17 性能: _prefetchStockAux 已用 sectorName 预拉 lu_ctx,直接复用
    // 兜底: 缓存未命中 (冷启竞态) 才自己 fetch
    let res;
    if (_stockAuxCache.code === code && _stockAuxCache.lu_ctx && _stockAuxCache.lu_ctx.error == null) {
      res = _stockAuxCache.lu_ctx;
    } else {
      // 等待预拉完成 (最多等 4s)
      try {
        await _prefetchStockAux(code);
        res = _stockAuxCache.lu_ctx || {};
      } catch (_) {
        // 最后兜底: 自己 fetch
        res = await api(sectorName
          ? `/api/stock/${code}/limit_up_context?sector=${encodeURIComponent(sectorName)}`
          : `/api/stock/${code}/limit_up_context`) || {};
      }
    }
    if (res.error && res.error.includes('超时')) {
      host.innerHTML = `<p class="caption down">${escapeHtml(res.error)}</p>`;
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
      sectorLabel = `板块当日涨停 ${sectorZt.length} 只（取 ${Math.min(sectorZt.length, 10)}）`;
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
          // 2026-07-17: 改为直接从 s.taxonomy 读 (后端 _enrich_sector_zt_taxonomy 已补),
          // 不再依赖 window._taxCache (那个 cache 没被填充 → 永远显示 "—").
          const sTax = s.taxonomy || null;
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

// R29: RAF 节流 + visibility resize — 旋转屏/锁屏/切 TAB 回来图表尺寸正确
var _resizeRaf = null;
window.addEventListener('resize', () => {
  if (_resizeRaf) return;
  _resizeRaf = requestAnimationFrame(() => {
    _resizeRaf = null;
    Object.values(echartsCharts).forEach(c => { try { c.resize(); } catch (_) {} });
  });
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) setTimeout(() => {
    Object.values(echartsCharts).forEach(c => { try { c.resize(); } catch (_) {} });
  }, 100);
});

// ═══════════════════════════════════════════════════════════
// 「连板 · 近期涨停 / 强势股」卡片 (q-streak-host)
// 2026-07-17: 替换旧版 kline chg% 格子, 真用 limit_up_context + strong_stocks
// ═══════════════════════════════════════════════════════════

async function _loadStockStreakPanel(code, data) {
  const host = $('#q-streak-host');
  if (!host) return;
  // 2026-07-17 性能: 直接复用 _prefetchStockAux 已预拉的 lu_ctx + strong,
  // 不再单独 fetch (避免和 loadStockLimitUp 重复拉 limit_up_context,
  // 和 loadStockSector 重复拉 sector)。3 个 endpoint → 1 次合并请求
  try {
    await _prefetchStockAux(code);
  } catch (_) { /* 预拉失败不影响,继续用兜底 */ }

  const ctxRes     = _stockAuxCache.lu_ctx || {};
  const strongRes  = _stockAuxCache.strong || {};
  const sec        = _stockAuxCache.sector || {};
  const sectorName = sec?.sw || sec?.csrc || sec?.gics || '';

  const today      = (ctxRes && ctxRes.today) || null;
  const recent5    = (ctxRes && ctxRes.recent_5d) || [];
  const leader     = (ctxRes && ctxRes.leadership) || {};
  const nature     = (ctxRes && ctxRes.stock_nature) || {};
  const strongRows = (strongRes && strongRes.rows) || [];

  // 匹配"同板块/同产业链"强势股 — 按 L3 (产业链) + L4 (细分) + 所属行业宽松匹配
  const myTaxL3 = (ctxRes && ctxRes.taxonomy_l3) || (strongRes && strongRes.tax_l3) || '';
  const myTaxL4 = (ctxRes && ctxRes.taxonomy_l4) || (strongRes && strongRes.tax_l4) || [];
  const myIndustry = sectorName || (strongRes && strongRes.tax_l2) || '';
  const relatedStrong = matchRelatedStrong(strongRows, myTaxL3, myTaxL4, myIndustry, code);

  host.innerHTML = renderStreakPanel(today, recent5, leader, nature, relatedStrong, strongRows, code);
}

function matchRelatedStrong(rows, l3, l4List, industry, currentCode) {
  // 2026-07-17: 修 — 去掉 "近期多次涨停" 兜底 (这会让所有股票都看到同样的云创退/贵绳股份 5 只,
  // 用户报"连板梯队不是本股票的"). 改为只在 L4/L3/申万一级 真正匹配时返回,否则空数组.
  if (!rows || !rows.length) return [];
  const l4Set = new Set(l4List || []);
  const isMe = (c) => String(c).padStart(6, '0') === String(currentCode).padStart(6, '0');

  const l4Hits  = rows.filter(r => !isMe(r.code) && (l4Set.has(r.industry) || (l3 && r.industry === l3)));
  const l3Hits  = rows.filter(r => !isMe(r.code) && !l4Hits.includes(r) && r.industry && l3 && (r.industry.includes(l3) || l3.includes(r.industry)));
  const indHits = rows.filter(r => !isMe(r.code) && !l4Hits.includes(r) && !l3Hits.includes(r) && industry && r.industry && (r.industry.includes(industry) || industry.includes(r.industry)));

  // 合并去重,每组最多 5 条 — 不再 fallback 到无关 "近期多次涨停"
  return [...l4Hits, ...l3Hits, ...indHits].slice(0, 5);
}

function renderStreakPanel(today, recent5, leader, nature, relatedStrong, allStrong, code) {
  // ── 段 1: 连板状态 (今日 + 龙头位) ──
  const todayHtml = (() => {
    if (today && (today.连板数 || 0) >= 1) {
      const lb = today.连板数;
      const fire = lb >= 2 ? '🔥' : '✓';
      const fg = lb >= 5 ? '#b71c1c' : lb >= 3 ? '#d32f2f' : lb >= 2 ? '#f57c00' : 'var(--accent)';
      const tm = today.首次封板时间 ? String(today.首次封板时间).replace(/^(\d{2})(\d{2})\d{2}$/, '$1:$2') : '—';
      const seal = today.封单金额 ? (today.封单金额 / 1e8).toFixed(2) + ' 亿' : '—';
      const burst = today.炸板次数 ? ` · 炸板 ${today.炸板次数}` : '';
      const ztj = today.涨停统计 ? ` · 涨停统计 ${today.涨停统计}` : '';
      return `<span class="chip" style="background:${fg};color:#fff;border:none;font-weight:700">${fire} 今日 ${lb} 板 · 封单 ${seal} · 首封 ${tm}${burst}${ztj}</span>`;
    }
    return `<span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">今日未涨停</span>`;
  })();

  const leaderHtml = (() => {
    if (!leader || !leader.role || leader.role === '—') return '';
    const sl = leader.sector_leader;
    const slHtml = sl
      ? `<span class="chip" style="background:#fff8e1;border-color:#f57c00;color:#b71c1c;cursor:pointer" data-action="open-stock:${escapeHtml(sl.code)}" title="点击查看 ${escapeHtml(sl.name)}">👑 板块龙头 ${escapeHtml(sl.name)} · ${sl.streak} 板${sl.封单金额 ? ' · 封单 ' + (sl.封单金额 / 1e8).toFixed(2) + ' 亿' : ''}</span>`
      : '';
    const streakColor = leader.streak >= 5 ? '#b71c1c' : leader.streak >= 3 ? '#d32f2f' : leader.streak >= 2 ? '#f57c00' : '#7b1fa2';
    const roleChip = leader.role !== '—'
      ? `<span class="chip" style="background:${streakColor};color:#fff;border:none;font-weight:700" title="${escapeHtml(leader.reason || '')}">${escapeHtml(leader.role)}${leader.is_top_in_sector ? ' · 板块最高' : ''}</span>`
      : '';
    return roleChip + slHtml;
  })();

  // ── 段 2: 近 5 日涨停明细 ──
  const recentHtml = recent5.length > 0 ? `
    <div style="display:flex;flex-wrap:wrap;gap:.3rem">
      ${recent5.map(r => {
        const date = String(r.date || '');
        const md = date.length >= 8 ? `${date.slice(4,6)}/${date.slice(6,8)}` : date;
        const lb = r.连板数 || 1;
        const fire = lb >= 2 ? '🔥' : '✓';
        const fg = lb >= 5 ? '#b71c1c' : lb >= 3 ? '#d32f2f' : lb >= 2 ? '#f57c00' : 'var(--accent)';
        const burst = r.炸板次数 ? ` · 炸${r.炸板次数}` : '';
        // 用 data-streak-jump 自定义属性 (不依赖 data-action,因为 open-stock 会切个股)
        return `<span class="chip streak-jump" style="background:${fg}1a;color:${fg};border:1px solid ${fg};cursor:pointer" data-streak-jump="${escapeHtml(date)}" title="${escapeHtml(date)} · 连板 ${lb}${burst} · ${escapeHtml(r.所属行业 || '')} · ${escapeHtml(r.涨停统计 || '')} · 点击切到分时">${fire} ${md} · ${lb} 板${burst}</span>`;
      }).join('')}
    </div>` : `<p class="caption dim" style="margin:.25rem 0">近 5 日无涨停</p>`;

  // ── 段 3: 强势股 (匹配同板块/产业链) ──
  const strongHtml = relatedStrong.length > 0 ? `
    <div style="display:flex;flex-wrap:wrap;gap:.3rem">
      ${relatedStrong.map(s => {
        const fire = s.change_pct >= 9.95 ? '🔥' : (s.is_new_high ? '⚡' : '');
        const fg = s.change_pct >= 9.95 ? '#b71c1c' : s.change_pct >= 5 ? '#d32f2f' : s.change_pct >= 0 ? 'var(--up)' : 'var(--down)';
        const burst = s.zt_stats && s.zt_stats !== '0/0' ? ` ${s.zt_stats}` : '';
        const reasonIcon = s.is_new_high && /近期多次涨停/.test(s.reason || '') ? '⚡🔥'
                          : s.is_new_high ? '⚡新高'
                          : /近期多次涨停/.test(s.reason || '') ? '📈'
                          : '';
        return `<span class="chip" style="background:${fg}1a;color:${fg};border:1px solid ${fg};cursor:pointer" data-action="open-stock:${escapeHtml(s.code)}" title="${escapeHtml(s.name)} (${s.code}) · ${escapeHtml(s.industry || '')} · ${escapeHtml(s.reason || '')} · 涨 ${(s.change_pct||0).toFixed(2)}%${burst}">${reasonIcon} ${escapeHtml(s.name)} ${s.change_pct >= 0 ? '+' : ''}${(s.change_pct||0).toFixed(1)}%${burst}</span>`;
      }).join('')}
    </div>` : (allStrong.length > 0
      ? `<p class="caption dim" style="margin:.25rem 0"> ${escapeHtml(code)} 所在板块(${escapeHtml(nature?.taxonomy_l2 || nature?.industry || '')})今日无强势股入选 · 全市场共 ${allStrong.length} 只</p>`
      : `<p class="caption dim" style="margin:.25rem 0"> 强势股数据暂不可达</p>`);

  return `
    <div style="display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.5rem;align-items:center">
      ${todayHtml}${leaderHtml}
    </div>
    <div style="font-size:.7rem;color:var(--ink-2);margin:.4rem 0 .25rem"> 近 5 日涨停 (${recent5.length} 次 · 点 chip 看当日分时)</div>
    ${recentHtml}
    <div style="font-size:.7rem;color:var(--ink-2);margin:.55rem 0 .25rem;border-top:.5px solid var(--line);padding-top:.4rem">🚀 板块强势股 (${relatedStrong.length}${allStrong.length && relatedStrong.length < allStrong.length ? ` / 全市场 ${allStrong.length}` : ''} · 点 chip 切换个股)</div>
    ${strongHtml}
  `;
}

// 卡片 chip 点击 → 跳分时
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-streak-jump]');
  if (!el) return;
  const date = el.dataset.streakJump;
  if (!date || !currentStockCode) return;
  const pick = $('#intra-day-pick');
  if (pick) pick.value = date;
  const tabBtn = document.querySelector('.chart-tab[data-tab="intraday"]');
  if (tabBtn) tabBtn.click();
  loadIntraDay(currentStockCode, date);
});

// 按钮绑定
document.addEventListener('DOMContentLoaded', () => {
  const nr = $('#news-refresh-btn');      if (nr) nr.addEventListener('click', () => loadNewsList(true));
  const sr = $('#sectors-refresh-btn');  if (sr) sr.addEventListener('click', () => loadSectorsList(true));
});

// ═══════════════════════════════════════════════════════════
// REVIEW 复盘 view · 铁律冲突 + 资金占比 + AI 建议 (2026-07-10)
// ═══════════════════════════════════════════════════════════

var _reviewState = {
  trades: [],
  flows: new Map(),   // code -> {main_pct, retail_pct, fund_pct, ...}
  flowsTimer: null,
};
