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
// 2026-08-03: 模拟盘买卖点日期归一化 (kline='YYYY-MM-DD', marker='YYYYMMDD')
function _paperDateNorm(s) {
  if (!s) return '';
  s = String(s).trim();
  if (/^\d{8}$/.test(s)) return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  return s;
}
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
  _safeDisposeECharts(echartsCharts.equity);
  await _ensureECharts();
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.equity = chart;
  // 2026-08-09: notMerge:false 走 diff (同 option 增量更新,不全量重建,避免 SSE 1Hz 闪烁)
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
_onDomReady(() => {
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
  // Bento 5 卡分组聚合 (R-2026-08-09 v2): 14 个独立 cell → 5 个分组卡
  ['q-main','q-turnover','q-volratio','q-amp','q-5d','q-20d','q-mcap','q-cmcap','q-pe','q-pb','q-lu','q-ld','q-vol','q-seats','q-hi','q-lo','q-open','q-prev','q-amt']
    .forEach(id => set(id, '—'));
  ['q-main-sub','q-turnover-sub','q-mcap-sub','q-pe-sub','q-seats-sub'].forEach(id => set(id, ''));
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
  // R25: AI 判定趋势 / 铁律热力图 / 4 层数据拆解 隐藏 (切股时清空)
  ['ai-trend-section','ai-heatmap-section','ai-layer-section','ai-history-bar'].forEach(id => {
    const el = $('#' + id); if (el) el.hidden = true;
  });
  // R26: sectors mycard 隐藏 (切股时)
  const smc = $('#sectors-mycard'); if (smc) { smc.hidden = true; smc.innerHTML = ''; }
  // R27: news stats wrap 隐藏 + dispose charts
  const nsw = $('#news-stats-wrap'); if (nsw) { nsw.hidden = true; }
  ['news-sentiment-chart','news-time-chart'].forEach(id => {
    const el = $('#' + id); if (el) el.innerHTML = '';
  });
  const nts = $('#news-top-stocks'); if (nts) nts.innerHTML = '';
  if (echartsCharts?.newsSentiment) { _safeDisposeECharts(echartsCharts.newsSentiment); echartsCharts.newsSentiment = null; }
  if (echartsCharts?.newsTime) { _safeDisposeECharts(echartsCharts.newsTime); echartsCharts.newsTime = null; }
  // R28: related overview 隐藏
  const rov = $('#related-overview'); if (rov) { rov.hidden = true; rov.innerHTML = ''; }
  // R29: kline pattern 隐藏
  const kp = $('#kline-pattern'); if (kp) { kp.hidden = true; kp.innerHTML = ''; }
  // R30: intraday pattern 隐藏
  const ip = $('#intra-pattern'); if (ip) { ip.hidden = true; ip.innerHTML = ''; }
  const tchart = $('#ai-trend-chart'); if (tchart) tchart.innerHTML = '';
  const tlegend = $('#ai-trend-legend'); if (tlegend) tlegend.innerHTML = '';
  const hpills = $('#ai-history-pills'); if (hpills) hpills.innerHTML = '';
  const lheat = $('#ai-law-heatmap'); if (lheat) lheat.innerHTML = '';
  const lcards = $('#ai-layer-cards'); if (lcards) lcards.innerHTML = '';
  if (echartsCharts?.aiTrend) { _safeDisposeECharts(echartsCharts.aiTrend); echartsCharts.aiTrend = null; }
  // Tab 默认 = kline (除非用户上次的偏好)
  document.querySelectorAll('.chart-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === 'kline');
  });
  document.querySelectorAll('.chart-pane').forEach(p => {
    p.hidden = p.dataset.tabPane !== 'kline';
  });
  // Watch btn 状态
  const wbtn = $('#stock-watch-btn'); if (wbtn) { wbtn.disabled = true; wbtn.textContent = '⭐ 一键自选'; wbtn.dataset.inWl = '0'; }
  // 我的交易隐藏
  const myt = $('#stock-mytrades-card'); if (myt) myt.hidden = true;
  // LIMIT-UP 提示
  // 连板/板块联动内容由 _loadStockStreakPanel 在 streak 卡片内动态创建。
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

// 2026-07-21: 卡死修复 — 返回当前个股加载的共享 abort signal。
// 切股/切页时 window._stockInflightAborter 被 abort,所有带此 signal 的请求立即取消,
// 释放连接;无活动加载时返回 undefined(正常行为)。
function _stockSignal() {
  return (window._stockInflightAborter && !window._stockInflightAborter.signal.aborted)
    ? window._stockInflightAborter.signal : undefined;
}

async function loadStockDetail(code, date) {
  code = code.trim().padStart(6, '0');
  const _switching = currentStockCode !== code;
  currentStockCode = code;
  // 2026-07-18 修: app.js 也读 _currentStockCode (下划线),两变量同步赋值防再发
  window._currentStockCode = code;
  // R-fix-2026-08-01: inflight dedup 必须 *最早期* 设置 — 不只是"切股时清",
  // 还必须在 await 之前设 placeholder,让 Call 2 在 await 期间进入时能看到 inflight。
  // 原版 bug: await _setQuickbarEnabled() 期间第二次进入,两边都通过早期清,双发 /core+/full,
  // server 6 连接池 ×4 workers 被拖爆,客户端 P95 飙到 1.2s+。
  // 关键:同 code+date 的二次进入必须返回同一 promise,而不是再跑副作用。
  // 但精确 inflightKey(dateParam + useFresh) 在 _setQuickbarEnabled 后才能算 — 用 caller 的 date 占位,
  // 同 code+date 占位必同 key,够防连点/watcher 三路同时调。
  const _pendingKey = code + ':' + (date || '') + ':pending';
  if (_stockDetailInflight && _stockDetailInflightKey === _pendingKey) {
    return _stockDetailInflight;
  }
  if (_switching) {
    _stockDetailInflight = null;
    _stockDetailInflightKey = '';
  }
  // 立刻设 placeholder 占位 — Call 2 在 await 期间进入时会撞上 early return
  let _inflightResolve;
  const _inflightPromise = new Promise((res) => { _inflightResolve = res; });
  _stockDetailInflightKey = _pendingKey;
  _stockDetailInflight = _inflightPromise;
  // R81 (Batch 9): 切股时把旧 inflight 标记成 stale — api() 走 inflight dedup 自动挡,
  // 但这里多一道显式清,防 stale render。currentStockCode 检查在 render 路径已有 (L787/L832)
  // 切股:停旧轮询,新轮询在首次 render 后启动,避免抢数据
  _stopStockPoll();
  // 2026-07-21: 卡死修复 — 先 abort 上一次加载仍在飞行的请求 (core/full/kline/intraday...),
  // 再建新的共享 AbortController。切股/切页时旧请求立即取消,释放 HTTP/1.1 连接。
  // 频繁点击时旧请求本来就被 currentStockCode guard 丢弃,取消它们只赚不亏。
  // (view-leave hook 也会 abort 这个 controller)
  if (window._stockInflightAborter) { try { window._stockInflightAborter.abort(); } catch {} }
  window._stockInflightAborter = new AbortController();
  // 2026-08-08: 切股立即清空分时面板 — 否则新数据返回前 (几百 ms~2s) 用户看到旧股票的分时图
  _safeDisposeECharts(echartsCharts.intraDay); echartsCharts.intraDay = null;
  const _idn0 = $('#intra-day-note'); if (_idn0) _idn0.textContent = '';
  const _idk0 = $('#intra-day-kpi');  if (_idk0) _idk0.innerHTML = '';
  const _idl0 = $('#intra-day-label'); if (_idl0) _idl0.textContent = '';
  // 取消上一只股的相邻预取 (settle 未到就切走 → 一枪不发)
  if (typeof _cancelAdjacentPrefetch === 'function') _cancelAdjacentPrefetch();
  // R12: 立即清掉 _stockAuxCache 旧股的 sector/lu_ctx 等,防止 race 期间子 loader 拿到旧 stock 数据
  if (_stockAuxCache.code !== code) {
    _stockAuxCache.code = code;
    _stockAuxCache.sector = null;
    _stockAuxCache.lu_ctx = null;
    _stockAuxCache.strong = null;
    _stockAuxCache.seat_breakdown = null;
    _stockAuxCache.related_news = null;
    _stockAuxCache.ai_status = null;
    newsCache = null;  // R1: 切股时清新闻缓存, 防止显示旧股新闻
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
  // v147 (2026-07-21): 当 stock-date 默认是今天时,不要再走历史快照模式 —
  // 服务端 is_historical 仅在 date < today 时为 True,date=today 实际是实时路径,
  // 但 ?date=today 让 SW URL 变成 /full?fresh=1&date=2026-07-21,跨日访问旧缓存被孤立
  // (例如:周一存 2026-07-21 缓存,周三再开 URL 仍是 2026-07-21 但已是历史,可能误判)
  // 干净做法:dateParam === 今天时清空,走纯实时路径。
  const _todayYmd = todayStr();
  if (dateParam === _todayYmd) dateParam = '';
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

  // R5: 精确 inflight dedup — 已在早期 _switching 守卫 (line 723-728),同 code 重复进入
  // 走上方已经设的 _stockDetailInflight 自动 dedup。这里用精确 dateParam 再做一次双保险,
  // 因为 _setQuickbarEnabled 之后才能拿到 stock-date input 的真实值,可能跟 caller 传入 date 不同。
  const inflightKey = code + ':' + dateParam + ':' + (useFresh ? 'F' : 'T');
  if (_stockDetailInflight && _stockDetailInflightKey === inflightKey) {
    return _stockDetailInflight;
  }

  // R21+R23 (Batch 3): 渐进渲染 — Phase 1 /core (200ms) 渲染首屏,
//                      Phase 2 后台 /full 拿全部字段 patch
  // R-opt-2026-07-19: /core + /full 同时发出,而非顺序
  // R-opt-2026-07-19-v2: inline /core data (server inlines into HTML <head>)
  const _inlineCore = window.__STOCK_CORE__;
  window.__STOCK_CORE__ = null;  // consume once
  if (_inlineCore && _inlineCore.code === code && _inlineCore.quote) {
    try {
      const _icr = { code, quote: _inlineCore.quote, kline: _inlineCore.kline || [], _core: true };
      renderStockDetail(code, _icr);
      _hideStockSkeleton();
      window._stockRenderTime = performance.now();
    } catch (e) { console.debug('[core-inline] render fail:', e.message); }
  }
  const _coreP = api(`/api/stock/${code}/core`, { signal: _stockSignal(), maxRetries: 1 });
  // Sprint 7: /full 116KB,远大于 /core ~25KB。priority:'low' 让浏览器先 service /core,再传 /full,
  // 避免 116KB 大响应塞满 HTTP/1.1 6 连接池 → 后续切换 view 时其他 API 排队 ~600ms 的卡顿。
  // 测试: P50 /full 收到 body 从 ~210ms 降到 ~80ms,/core 解析更快
  // R101-fix: maxRetries=1 (从默认 2 降) — 减少 retry 链等待 (1 retry × 20s + 退避),
  // 让 503 失败更快冒泡到上层 catch 走降级路径,而不是让用户等 40s+。
  const _fullP = api(`/api/stock/${code}/full${qs}`, { signal: _stockSignal(), priority: 'low', maxRetries: 1 });
  const _promise = (async () => {
  try {
    // Phase 1: /core — quote + name + 5 KPI + kline (短,sw cache reuse)
    let _coreOk = false;
    try {
      const coreData = await _coreP;
      if (coreData && coreData.quote && currentStockCode === code) {
        const coreRender = { code, quote: coreData.quote, kline: coreData.kline || [], _core: true };
        renderStockDetail(code, coreRender);
        _recordHit('redis');
        _hideStockSkeleton();  // R-fix-B8: /core 拿到 quote 即可首屏,立即去 skeleton
      window._stockRenderTime = performance.now();
        _coreOk = true;
      }
    } catch (e) {
      console.debug('[core] failed:', e.message);
    }

    // R-opt-2026-07-19: /full 与 /core 同时发起,节省 ~1.2s 总时间
    // (core promise 在上面 await 后才渲染首屏,full 此时已飞了)
    let data;
    try {
      data = await _fullP;
    } catch (e) {
      // R101-fix: /full 失败(503/超时/tunnel 抖动)不要让整个 loadStockDetail 崩溃。
      // 如果 /core 已经渲染了首屏,只显示降级横幅,不弹错误卡。
      if (currentStockCode !== code) return;
      if (_coreOk) {
        console.warn('[full] 降级:', e.message);
        _showStockDegraded(code, e.message);
        return;
      }
      // core 也没成功 → 抛到外层 catch 走错误卡 + cached 兜底
      throw e;
    }
    if (!data) {  // /full 返 envelope.ok=false (R2 envelope 兜底)
      if (_coreOk) { _showStockDegraded(code, '数据暂不可用'); return; }
      throw new Error('数据为空');
    }
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
    // 2026-08-01: 公司画像 4 件套 (营业范围/主营/概念/行业地位)
    _stockAuxCache.profile = data.profile || null;
    // 2026-08-09 R4: extras 字段消费 — 后端一直发,前端之前从不读
    _stockAuxCache.streak_history = data?.extras?.streak_history || null;
    _stockAuxCache.vol_5d_avg = data?.extras?.vol_5d_avg || null;
    _stockAuxCache.ts = Date.now();
    // R26: sectors mycard 等 aux 数据齐了再渲染
    if (typeof renderSectorsMyCard === 'function' && sectorsCache) renderSectorsMyCard();

    // 写 sessionStorage + 内存层 (R3)
    _stockCacheSave(code, dateParam, data);
    _memFullSet(code, dateParam, data);
    _recordHit('network');
    try { renderStockDetail(code, data); }
    catch (e) { console.error('renderStockDetail failed:', e); toast(`✗ 渲染失败: ${e.message}`, 'error'); }
    window._stockFullRenderTime = performance.now();
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
    // ⭐ 2026-07-19: 后台预取相邻个股/core, 下次点击 < 5ms
    _prefetchAdjacentStocks(code);
  } catch (e) {
    if (cached) {
      console.warn('[stock] 网络失败,使用缓存:', e.message);
    } else {
      // R101-fix: 兜底再读一次 last-known (即使本次没拿到,过往浏览过该股就有 cache)
      const lastKnown = _memFullGet(code, dateParam) || _stockCacheLoad(code, dateParam);
      if (lastKnown && lastKnown.quote) {
        console.warn('[stock] 双端失败,用 last-known 渲染:', e.message);
        try { renderStockDetail(code, lastKnown); _hideStockSkeleton(); }
        catch (e2) { console.debug('[stock] last-known render fail:', e2.message); }
      } else {
        // 用户友好文案:把 "HTTP 503 (非 JSON)" / "上游 X 降级 (非 JSON)" 转换成大白话
        const friendly = /5\d\d|上游|非 JSON/i.test(e.message || '')
          ? '上游服务繁忙,请稍后再试' : (e.message || '网络异常');
        toast(`✗ 加载失败：${friendly}`, 'error');
        // R77 (Batch 8): 无缓存 + 网络失败 → 显示错误卡 + 重试按钮
        _showStockError(code, friendly);
      }
    }
  } finally {
    _hideStockSkeleton();  // R72 (Batch 8): 兜底清 skeleton (避免永久闪烁)
    // R-fix-2026-08-01: 解锁 placeholder,让早期 early-return 进来的 awaiter 拿到 _promise 解析
    if (typeof _inflightResolve === 'function') {
      _inflightResolve(_promise);
      _inflightResolve = null;
    }
    // R5: 200ms 后清 inflight key,允许同 key 在失败重试时复用
    setTimeout(() => {
      if (_stockDetailInflightKey === inflightKey) {
        _stockDetailInflightKey = '';
        _stockDetailInflight = null;
      } else if (_stockDetailInflightKey === _pendingKey) {
        // R-fix-2026-08-01: inflightKey 没在 await 后被提升(早期 placeholder 早 return 路径),
        // 也清掉 placeholder,允许失败重试
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
      const env = await api(`/api/trade_dates?limit=${wantLimit}`, { signal: _stockSignal() });
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
  // R-2026-08-09: 加载有效股票后自动折叠 quickbar 搜索框,释放首屏高度
  if (isValid && typeof window.__tx3CollapseQuickbar === 'function') {
    window.__tx3CollapseQuickbar();
  } else if (!isValid && typeof window.__tx3ExpandQuickbar === 'function') {
    window.__tx3ExpandQuickbar();
  }
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

// R-2026-08-09: quickbar 折叠 — 加载股票后,搜索框/历史折叠,只露日期 + 复盘/自选/固定链接区
(() => {
  const qb = $('#stock-quickbar');
  const toggle = $('#stock-search-toggle');
  if (!qb || !toggle) return;
  const collapse = () => {
    qb.classList.add('qb-collapsed');
    toggle.hidden = false;
    toggle.textContent = '▾ 展开搜索';
  };
  const expand = () => {
    qb.classList.remove('qb-collapsed');
    toggle.hidden = true;
    toggle.textContent = '收起 ▴';
    const ipt = $('#stock-search'); if (ipt) ipt.focus({ preventScroll: true });
  };
  toggle.addEventListener('click', (e) => {
    e.preventDefault();
    if (qb.classList.contains('qb-collapsed')) expand(); else collapse();
  });
  window.__tx3CollapseQuickbar = collapse;
  window.__tx3ExpandQuickbar = expand;
})();

function renderStockDetail(code, data) {
  const q = data.quote || {};
  const seats = data.seats || {};
  const flow = data.fund_flow || {};
  const today = flow.today || {};
  const extras = data.extras || {};
  const streakHost = $('#q-streak-host');

  const name = q.name || data.name || code;
  const price = parseFloat(q.price ?? q.最新价 ?? 0);
  const chg = parseFloat(q.涨跌幅 ?? q.change_pct ?? 0);
  const prev = parseFloat(q.prev_close ?? q.昨收 ?? 0);
  const chgAmt = prev > 0 ? (price - prev) : 0;

  // R23: 缓存当前 quote 给派发/吸筹评级复用
  window._currentQuote = { price, change_pct: chg, name: q.name || data.name || code };

  // 分时图辅助上下文（昨收 + 涨停价），供 drawIntraDayChart 参考线
  // 涨停价规则: ST 5% / 主板 10% / 创业板(300/301)+科创(688) 20% / 北交所 30%
  const isST = (name || '').startsWith('ST');

  // R2: 全局降级标志 — backend 返回 _degraded / _partial 时展示
  const _degraded = data._degraded || data._partial;
  const _degradedFields = data._degraded_fields || [];
  if (_degraded) {
    const dgEl = $('#stock-degraded-badge') || (() => {
      const el = document.createElement('div');
      el.id = 'stock-degraded-badge';
      el.className = 'stock-degraded-badge';
      const hero = $('#stock-hero');
      if (hero) hero.parentNode.insertBefore(el, hero.nextSibling);
      return el;
    })();
    const fields = _degradedFields.length ? ` (${_degradedFields.join(', ')})` : '';
    dgEl.textContent = `⚠ 部分数据暂不可达${fields}`;
    dgEl.style.display = '';
  } else {
    const dgEl = $('#stock-degraded-badge');
    if (dgEl) dgEl.style.display = 'none';
  }
  const isKJ = /^(300|301|688)/.test(code);
  const isBJ = /^(8|4)/.test(code);  // 北交所 8/4 开头 30%
  const limitPct = isST ? 0.05 : isKJ ? 0.20 : isBJ ? 0.30 : 0.10;
  const lu = extras.limit_up_price != null ? extras.limit_up_price : (prev > 0 ? +(prev * (1 + limitPct)).toFixed(2) : null);
  lastStockContext = { prev_close: prev || null, limit_up_price: lu, code };

  // ─── 顶部标题 + Hero ───
  $('#stock-title').textContent = name;
  $('#stock-code').textContent = code;
  $('#stock-sub').textContent = `${name} · ${code} · ${q._source || ''} ${q._fetch_time || ''}`.trim();

  // 跨模块上下文 banner (来自策略选股 / 周线擒牛)
  const _ctx = window._navCtx || {};
  const _ctxEl = document.getElementById('stock-nav-ctx');
  if (_ctxEl) {
    if (_ctx.from && _ctx.strategies) {
      const labels = { wb: '周线擒牛', rl: '1/3回升位', ma5: '5日线放量',
        sanxing_taodi: '三星探底', zhanwen_5w: '站稳5周线', tupo_pingtai: '突破平台', tupo_pingtai_aggressive: '突破3周(激进)', junxian_fangxiang: '均线方向', zhouxian_duiliang: '周线堆量' };
      const fromLabel = _ctx.from === 'sp' ? '策略选股' : '周线擒牛';
      const returnView = _ctx.from === 'sp' ? 'strategy_picker' : 'weekly_bull';
      const chips = _ctx.strategies.split(',').filter(Boolean).map(k => {
        const l = labels[k] || k;
        return `<span class="chip" style="font-size:10px;background:var(--warn)22;border-color:var(--warn);color:var(--warn)">${l}</span>`;
      }).join('');
      _ctxEl.innerHTML = `<span class="dim" style="font-size:10.5px;margin-right:4px">来自${fromLabel}</span>${chips} <a href="#${returnView}" style="font-size:10px;color:var(--ink-3);margin-left:6px;text-decoration:none">← 返回</a>`;
      _ctxEl.style.display = 'block';
    } else {
      _ctxEl.style.display = 'none';
    }
  }

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

  // ─── 1 行 chip strip ───
  // R-2026-08-09 v3: 14 卡 Bento → 1 行 chip strip
  // setVal 兼容 .qchip-value / .qchip-value-lg / .qcg-value (legacy) / .qcc-mini-value (legacy)
  const setVal = (id, val, color) => {
    $$(id).forEach(el => {
      el.innerHTML = val;
      // 自动识别基础 class
      let baseCls;
      if (el.classList.contains('qchip-value-lg')) baseCls = 'qchip-value qchip-value-lg';
      else if (el.classList.contains('qchip-value')) baseCls = 'qchip-value';
      else if (el.classList.contains('qcg-value')) baseCls = 'qcg-value';
      else if (el.classList.contains('qcc-mini-value')) baseCls = 'qcc-mini-value';
      else baseCls = 'qc-value';
      if (color) el.className = baseCls + ' ' + color;
    });
  };

  // 主力净流（大格）— 数字滚动
  const mainNet = today.main_net;   // null 时保留 null → 显示 "—" 而不是 0
  const mainEl = $('#q-main');
  const prevMainText = mainEl.dataset.lastMain;
  const prevMain = prevMainText ? parseFloat(prevMainText) : null;
  mainEl.dataset.lastMain = mainNet != null ? String(mainNet) : '';
  if (mainNet != null && Math.abs(mainNet - (prevMain ?? 0)) > 1) {
    animateNumber(mainEl, prevMain ?? 0, mainNet, 600, (v) => fmtWan(v, 1));
  } else {
    mainEl.innerHTML = mainNet != null ? fmtWan(mainNet, 1) : '<span style="color:var(--ink-2)">—</span>';
  }
  mainEl.className = 'qc-value large ' + (mainNet > 0 ? 'up' : mainNet < 0 ? 'down' : 'flat');
  const _sn = today.super_net, _bn = today.big_net;
  const superBigKnown = _sn != null || _bn != null;
  const superBig = (_sn || 0) + (_bn || 0);
  $('#q-main-sub').textContent = superBigKnown
    ? `超大+大单 ${fmtWan(superBig, 1)}`
    : '分单数据不可达 · 仅供参考';

  // R-2026-08-09 v2: 5 卡 Bento 分组聚合渲染 — 14 个独立 cell → 5 个分组卡
  // 每个分组卡内嵌 3-7 个微指标 (.qcg-value)

  // ① 行情组: 高/低/开/昨收 + 振幅 + 换手 + 量比
  const turnover = q.换手率;
  const volratio = q.量比;
  const amp = extras.amplitude_pct;
  const actSig = data.activity_signal || {};

  setVal('#q-hi', q.最高 ? fmtN(q.最高, 2) : '—', 'flat');
  setVal('#q-lo', q.最低 ? fmtN(q.最低, 2) : '—', 'flat');
  setVal('#q-open', q.open ?? q.今开 ? fmtN(q.open ?? q.今开, 2) : '—', 'flat');
  setVal('#q-prev', q.prev_close ?? q.昨收 ? fmtN(q.prev_close ?? q.昨收, 2) : '—', 'flat');
  setVal('#q-amp',
    amp != null ? `${amp.toFixed(2)}<span class="qc-unit">%</span>` : '—',
    amp > 7 ? 'up' : amp > 3 ? 'flat' : 'down');
  setVal('#q-turnover',
    turnover != null ? `${turnover.toFixed(2)}<span class="qc-unit">%</span>` : '—',
    turnover > 10 ? 'up' : turnover > 5 ? 'flat' : 'down');
  setVal('#q-volratio',
    volratio != null ? volratio.toFixed(2) : '—',
    volratio > 2 ? 'up' : volratio > 1 ? 'flat' : 'down');
  // 活跃度 sub 优先用后端派生 activity_signal
  const subLabel = actSig.label || (turnover > 10 ? '高活跃' : turnover > 5 ? '活跃' : '低迷');
  $('#q-turnover-sub').textContent = subLabel + (actSig.score != null ? ` · ${actSig.score}` : '');

  // Bento icon states · 紧急度点
  paintBentoState(turnover, volratio, amp, mainNet, price, extras);

  // ② 趋势组: 5d / 20d / 涨停价 / 跌停价
  const p5 = extras.pct_5d;
  setVal('#q-5d',
    p5 != null ? `${p5 >= 0 ? '+' : ''}${p5.toFixed(2)}%` : '—',
    p5 > 0 ? 'up' : p5 < 0 ? 'down' : 'flat');
  const p20 = extras.pct_20d;
  setVal('#q-20d',
    p20 != null ? `${p20 >= 0 ? '+' : ''}${p20.toFixed(2)}%` : '—',
    p20 > 0 ? 'up' : p20 < 0 ? 'down' : 'flat');
  const luStr = extras.limit_up_price != null ? extras.limit_up_price.toFixed(2) : '—';
  const ldStr = extras.limit_dn_price != null ? extras.limit_dn_price.toFixed(2) : '—';
  setVal('#q-lu', luStr,
    extras.limit_up_price && price >= extras.limit_up_price - 0.001 ? 'up' : 'flat');
  setVal('#q-ld', ldStr,
    extras.limit_dn_price && price <= extras.limit_dn_price + 0.001 ? 'down' : 'flat');

  // ③ 估值组: 总市值 / 流通 / PE / PB
  const mcap = q.总市值 || 0;
  const cmcap = q.流通市值 || 0;
  setVal('#q-mcap',
    mcap > 0 ? `${mcap.toFixed(1)}<span class="qc-unit">亿</span>` : '—',
    'flat');
  setVal('#q-cmcap',
    cmcap > 0 ? `${cmcap.toFixed(1)}<span class="qc-unit">亿</span>` : '—',
    'flat');
  // 2026-08-09: q-mcap-sub 随 qchip 重构删除 — 占比信息移到 title tooltip
  const _mcapEl = $('#q-cmcap');
  if (_mcapEl) _mcapEl.title = cmcap > 0 && mcap > 0 ? `流通占总市值 ${(cmcap / mcap * 100).toFixed(0)}%` : '';
  const peVal = Number(qGet(q, 'pe', '市盈率-动态', '市盈率'));
  setVal('#q-pe', peVal > 0 ? peVal.toFixed(2) : '—', 'flat');
  // 2026-08-09: q-pe-sub 随 qchip 重构删除 — 估值说明移到 title tooltip
  const _peEl = $('#q-pe');
  if (_peEl) _peEl.title = peVal > 0
    ? `PE 动 · ${peVal > 50 ? '高估' : peVal < 0 ? '亏损' : '合理'}`
    : (peVal < 0 ? '亏损 · ' + peVal.toFixed(2) : '亏损/暂无');
  const pbVal = Number(qGet(q, 'pb', '市净率'));
  setVal('#q-pb', pbVal > 0 ? pbVal.toFixed(2) : '—', 'flat');

  // ④ 资金组: 成交量 / 成交额 / 龙虎席位
  const vol = q.成交量 || 0;
  const volStr = vol > 0 ? `${(vol / 1e4).toFixed(1)}` : '—';
  setVal('#q-vol', vol > 0 ? `${volStr}<span class="qc-unit">万手</span>` : '—', 'flat');
  setVal('#q-amt', q.成交额 > 0 ? `${(q.成交额 / 1e8).toFixed(2)}<span class="qc-unit">亿</span>` : '—', 'flat');
  setVal('#q-seats', `${seats.seat_count || 0}<span class="qc-unit">条</span>`, 'flat');
  $('#q-seats-sub').textContent = seats.blacklisted
    ? `近 ${seats.total_lhb_rows || 0} · 黑名单`
    : `近 ${seats.total_lhb_rows || 0} 日`;

  // ─── 「连板 · 近期涨停 / 强势股」卡片 ───
  // 修复 (2026-07-17): 旧版用 kline close-to-close chg% 算 10 格涨跌,完全没用涨停池,名不副实。
	// R6: Phase 1 (core) 仅渲染 hero,Phase 2 (full) 加载 side-panel 数据
	if (data._core) {
		if (streakHost) {
			streakHost.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem">
				<span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">连板 / 涨停加载中…</span>
			</div><p class="caption dim" style="margin:0">近 5 日涨停明细 + 同产业链强势股</p>`;
		}
		klineState.data = data.kline || [];
		klineState.period = 'd';
		klineState.days = 120;
		_syncKlineExtents();
		renderKlineKpi(klineState.data);
		// 2026-08-06 (用户反馈"十日涨得格子加载缓慢"): Phase 1 /core 立即渲染 10 格热力图,
		// 不再等 Phase 2 /full。kline 在 /core 已带回 120 条,完全够算 10 格。
		// 这样用户进入页面 ~200ms 内就能看到 10 格,不用傻等 Phase 2 (~600-1200ms)。
		renderStreak10d(klineState.data);
		renderHeroSparkline(data.kline || [], price);
		renderHeroLimitBand(price, prev, lu, extras.limit_up_price, chg, extras.amplitude_pct);
		renderHeroRisks(q, extras, chg);
		return;
	}

	// Phase 2 (full): side-panel + 图表 + 表格
	if (streakHost) {
		streakHost.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem">
			<span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">连板 / 涨停加载中…</span>
		</div><p class="caption dim" style="margin:0">近 5 日涨停明细 + 同产业链强势股</p>`;
		_loadStockStreakPanel(code, data);
	}
	// 2026-08-09 R4: 消费 streak_history + vol_5d_avg (后端一直发,前端之前从不读)
	if (streakHost) _renderStreakChips(streakHost);
	_loadStrategyMatchCard(code).catch(() => {});

	const empty = $('#flow-empty');
	if (empty) empty.style.display = 'none';
	_pendingFlowData = flow.history || [];
	klineState.data = data.kline || [];
	klineState.period = 'd';
	klineState.days = 120;
	_syncKlineExtents();
	if ((klineState.data.length || 0) < 120) {
		loadKline(code, { days: 120, period: 'd', adjust: klineState.adjust });
	} else {
		syncKlineToolbar();
		_klineDataReady = true;
		// R-2026-08-09: /full 已带足 K 线时立即画 — 否则默认可见的 K 线 pane 一直空白,
		// 直到用户点 tab/指标才出图;同时让 echarts 早加载,避开后面 idle 预取抢连接
		const _kp = document.querySelector('[data-tab-pane="kline"]');
		if (_kp && !_kp.hidden && !_klineChartDrawn) drawKlineChart();
	}
	renderFlowKpi(flow.history || [], flow.today || {});
	renderKlineKpi(klineState.data);
	renderStreak10d(klineState.data);
	renderSeatsTable(seats.rows || [], seats);
	// R32: 游资足迹 · 席位关联个股 (独立端点, 进程内 10min 缓存, 失败静默)
	if (code) loadSeatsRelated(code).catch(() => {});
	// R22: /full 里的 seats 经常空 (冷取 17s vs 1.5s 预算) — 落到 /seats 独立端点
	// 二次拉, 命中进程内 10min L0 缓存 <1ms, 命中陈旧快照 <50ms
	if ((seats.rows || []).length === 0 && code) {
		_refetchSeatsProgressive(code).catch(() => {});
	}
	renderHolders(data.holders || null);
	renderHeroSparkline(data.kline || [], price);
	renderHeroLimitBand(price, prev, lu, extras.limit_up_price, chg, extras.amplitude_pct);
	renderHeroRisks(q, extras, chg);
	// 2026-08-08: fire-and-forget 一律挂 catch — 切股 abort 旧请求时,
	// 函数内部未捕获的 rejection 会冒泡成 unhandledrejection (pageerror)
	loadStockSeatBreakdown(code).catch(() => {});

	intraDayCache = new Map();
	_safeDisposeECharts(echartsCharts.intraDay); echartsCharts.intraDay = null;
	const pick = $('#intra-day-pick'); if (pick) pick.value = todayStr();
	const lbl = $('#intra-day-label'); if (lbl) lbl.textContent = '';
	const idn = $('#intra-day-note');  if (idn) idn.textContent = '';
	const idk = $('#intra-day-kpi');   if (idk) idk.innerHTML = '';
	if (typeof loadIntraDay === 'function') {
		loadIntraDay(code, todayStr());
	}

	$('#ai-panel').hidden = false;
	$('#ai-status').textContent = 'AI 复盘中 …';
	$('#ai-verdict').textContent = '…';
	$('#ai-summary').textContent = '';
	// R-2026-08-09: 板块/新闻/相关个股 已在 super card tabs (data-tab=news/sectors/related), 删除 loadStockSector
	$('#ai-detail').innerHTML = '';
	loadAIAnalysis(code).catch(() => {});
	loadStockDeepAnalysis(code).catch(() => {});
	loadStockMyTrades(code).catch(() => {});
	loadCrashRisk(code).catch(() => {});
	// 2026-08-01: 公司画像 4 件套 — 优先用 /full 预取,缺失再单独 fetch
	// 2026-08-09: loadStockProfile 是普通函数 (内部 fire-and-forget 自带 catch),不能再链 .catch
	loadStockProfile(code);


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
  // 2026-08-09 R4: 5min 内同股不重发
  if (_crashCached && _crashCached.code === code && (Date.now() - _crashCached.ts) < 300_000) {
    _renderCrashData(_crashCached.data);
    return;
  }
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_crash_risk`).then(r => r.json());
    const d = (env && env.data) || {};
    _crashCached = { code, ts: Date.now(), data: d };
    _renderCrashData(d);
  } catch (e) {
    const st = $('#crash-status'); if (st) st.textContent = '拉取失败: ' + (e.message || e);
  }
}
// 2026-08-09 R4: _crashCached 防反复请求 + 渲染拆函数
var _crashCached = null;
function _renderCrashData(d) {
  const root = $('#crash-panel');
  if (!root) return;
  try {
    const risk = d.crash_risk || '—';
    const verdict = d.verdict || '—';
    const conv = +d.conviction || 0;
    const rl = $('#crash-risk');    if (rl)   rl.textContent = risk;
    const st = $('#crash-status');  if (st)   st.textContent = `判定 ${verdict}`;
    const cn = $('#crash-conviction'); if (cn) cn.textContent = `${conv} / 100`;
    const cb = $('#crash-conviction-bar'); if (cb) cb.style.width = Math.min(conv, 100) + '%';
    const sm = $('#crash-summary'); if (sm) sm.textContent = d.summary || '';
    const meta = $('#crash-meta');
    if (meta) {
      const ts = d.ts_updated ? new Date(d.ts_updated * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
      meta.textContent = ts ? `更新 ${ts}` : '';
    }
    const riskEl = $('#crash-risk');
    if (riskEl) {
      riskEl.className = 'ai-verdict ' + (
        risk === '高' ? 'bad' :
        risk === '中' ? 'warn' :
        risk === '无' ? 'good' : ''
      );
    }
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
    // R24: 铁律违反红条 + 5 信号卡片
    renderCrashRules(d.rule_violations || []);
    renderCrashSignals(d);
    // R5 Round 5: 4 块 (历史模板/融资/质押/日历)
    renderCrashExtra(d || {});
  } catch (e) {
    const st = $('#crash-status'); if (st) st.textContent = '渲染失败: ' + (e.message || e);
  }
}

// R24: 铁律违反 — 高优先级红条警示
function renderCrashRules(violations) {
  const wrap = $('#crash-rules');
  if (!wrap) return;
  if (!violations || !violations.length) { wrap.hidden = true; wrap.innerHTML = ''; return; }
  wrap.hidden = false;
  wrap.innerHTML = violations.map(v => `<div class="cr-rule">⚠ <b>违反铁律</b>${escapeHtml(v)}</div>`).join('');
}

// R24: 5 信号卡片 — 量化席位 / 对倒 / 虚假流动性 / 尾盘异动 / 资金分布
function renderCrashSignals(d) {
  const wrap = $('#crash-signals');
  if (!wrap) return;
  wrap.hidden = false;
  const ps = d.pre_scan || {};
  // 1) 量化席位
  const quantList = ps.quant_seats || [];
  const quantCount = quantList.length;
  const quantCard = wrap.querySelector('[data-key="quant_seats"]');
  quantCard.classList.remove('alert', 'warn', 'ok');
  if (quantCount > 0) quantCard.classList.add(quantCount >= 3 ? 'alert' : 'warn');
  else quantCard.classList.add('ok');
  $('#cs-quant-val').textContent = quantCount > 0 ? `${quantCount} 次命中` : '未命中';
  $('#cs-quant-val').style.color = quantCount >= 3 ? 'var(--down)' : (quantCount > 0 ? 'var(--accent-2)' : 'var(--up)');
  $('#cs-quant-sub').textContent = quantCount > 0 ? quantList.slice(0, 2).map(s => s.label || s.seat || '—').join(', ').slice(0, 30) : '量化交易特征未识别';

  // 2) 对倒席位
  const pairList = ps.pair_trades || [];
  const pairCount = pairList.length;
  const pairCard = wrap.querySelector('[data-key="pair_trades"]');
  pairCard.classList.remove('alert', 'warn', 'ok');
  if (pairCount > 0) pairCard.classList.add(pairCount >= 2 ? 'alert' : 'warn');
  else pairCard.classList.add('ok');
  $('#cs-pair-val').textContent = pairCount > 0 ? `${pairCount} 对席位` : '未命中';
  $('#cs-pair-val').style.color = pairCount >= 2 ? 'var(--down)' : (pairCount > 0 ? 'var(--accent-2)' : 'var(--up)');
  $('#cs-pair-sub').textContent = pairCount > 0 ? pairList.slice(0, 2).map(p => p.seat || '—').join(', ').slice(0, 30) : '无对倒特征';

  // 3) 虚假流动性
  const fakeList = ps.fake_liquidity || [];
  const fakeCount = fakeList.length;
  const fakeCard = wrap.querySelector('[data-key="fake_liquidity"]');
  fakeCard.classList.remove('alert', 'warn', 'ok');
  if (fakeCount > 0) fakeCard.classList.add(fakeCount >= 2 ? 'alert' : 'warn');
  else fakeCard.classList.add('ok');
  $('#cs-fake-val').textContent = fakeCount > 0 ? `${fakeCount} 次信号` : '未命中';
  $('#cs-fake-val').style.color = fakeCount >= 2 ? 'var(--down)' : (fakeCount > 0 ? 'var(--accent-2)' : 'var(--up)');
  $('#cs-fake-sub').textContent = fakeCount > 0 ? '量价背离/撤单异常' : '流动性真实';

  // 4) 尾盘异动
  const late = ps.late_session;
  const lateCard = wrap.querySelector('[data-key="late_session"]');
  lateCard.classList.remove('alert', 'warn', 'ok');
  if (late && late.pct != null) {
    const dir = (late.direction || '').includes('跌') ? 'down' : 'up';
    const pct = Math.abs(late.pct);
    lateCard.classList.add(pct >= 2 ? 'alert' : 'warn');
    $('#cs-late-val').textContent = `${dir === 'down' ? '↓' : '↑'} ${pct.toFixed(2)}%`;
    $('#cs-late-val').style.color = dir === 'down' ? 'var(--down)' : 'var(--up)';
    $('#cs-late-sub').textContent = `${late.direction || '异动'} (14:30 后)`;
  } else {
    lateCard.classList.add('ok');
    $('#cs-late-val').textContent = '正常';
    $('#cs-late-val').style.color = 'var(--up)';
    $('#cs-late-sub').textContent = '14:30 后无显著异动';
  }

  // 5) 资金分布 — AI 返回的 funding_skew (主力/超大/大/中/小单净占比 %)
  // 兜底: AI 没填就用 window._currentFlowToday 本地算 (字段: super/big/mid/small_net, total_amount_wan)
  let funding = d.funding_skew || {};
  const hasAiFunding = Object.values(funding).some(v => v != null);
  if (!hasAiFunding) {
    const today = window._currentFlowToday || {};
    const totalWan = today.total_amount_wan || today.total || 0;  // 万元
    if (totalWan > 0) {
      const superN = today.super_net || 0;
      const bigN = today.big_net || 0;
      const midN = today.mid_net || 0;
      const smallN = today.small_net || 0;
      funding = {
        main: ((superN + bigN) / totalWan) * 100,
        super: (superN / totalWan) * 100,
        large: (bigN / totalWan) * 100,
        mid: (midN / totalWan) * 100,
        small: (smallN / totalWan) * 100,
      };
    }
  }
  const fundingCard = wrap.querySelector('[data-key="funding_skew"]');
  fundingCard.classList.remove('alert', 'warn', 'ok');
  if (funding && Object.values(funding).some(v => v != null)) {
    const main = funding.main ?? funding.super;
    const small = funding.small ?? 0;
    const skewed = (main != null && Math.abs(main) >= 30) || (small != null && Math.abs(small) >= 40);
    fundingCard.classList.add(skewed ? 'warn' : 'ok');
    $('#cs-fund-val').textContent = main != null ? `${main >= 0 ? '+' : ''}${main.toFixed(0)}%` : '—';
    $('#cs-fund-val').style.color = main > 0 ? 'var(--up)' : (main < 0 ? 'var(--down)' : 'var(--ink-2)');
    const detail = [];
    if (funding.super != null) detail.push(`超大 ${funding.super.toFixed(0)}%`);
    if (funding.large != null) detail.push(`大 ${funding.large.toFixed(0)}%`);
    if (funding.mid != null) detail.push(`中 ${funding.mid.toFixed(0)}%`);
    if (funding.small != null) detail.push(`小 ${funding.small.toFixed(0)}%`);
    $('#cs-fund-sub').textContent = detail.length ? detail.join(' / ') : '主力净占比';
  } else {
    fundingCard.classList.add('ok');
    $('#cs-fund-val').textContent = '—';
    $('#cs-fund-sub').textContent = '无分单数据';
  }
}

// R5 Round 5: 历史模板 / 融资 / 质押 / 日历 - 前端代理 (后续接后端)
function renderCrashExtra(d) {
  const tmpl = d.template_match || d.template;
  const margin = d.margin_balance;
  const pledge = d.pledge_ratio;
  const cal = d.crash_calendar;
  // 模板相似度
  if (tmpl && tmpl.pct != null) {
    $('#ce-template').textContent = (tmpl.pct >= 0 ? '+' : '') + tmpl.pct + ' %';
    $('#ce-template').style.color = tmpl.pct >= 70 ? DOWN : tmpl.pct >= 40 ? ACCENT : UP;
    $('#ce-template-sub').textContent = tmpl.label || '—';
  } else {
    $('#ce-template').textContent = '未匹配';
    $('#ce-template-sub').textContent = '历史模板 ≥ 5 例才显示';
  }
  // 融资
  if (margin) {
    $('#ce-margin').textContent = margin.bal_wan ? formatWan(margin.bal_wan) : '—';
    const daily = margin.daily_chg_pct;
    $('#ce-margin-sub').textContent = daily != null ? `日变化 ${daily >= 0 ? '+' : ''}${daily.toFixed(2)}%` : '—';
    $('#ce-margin-sub').style.color = daily > 5 ? DOWN : daily < -3 ? UP : INK2;
  } else {
    $('#ce-margin').textContent = '待接入';
    $('#ce-margin-sub').textContent = '东财 / akshare 融资融券';
  }
  // 质押
  if (pledge) {
    $('#ce-pledge').textContent = pledge.ratio_pct ? pledge.ratio_pct.toFixed(1) + ' %' : '—';
    $('#ce-pledge-sub').textContent = pledge.ratio_pct > 50 ? '高质押风险' : pledge.ratio_pct > 30 ? '中等' : '安全';
    $('#ce-pledge-sub').style.color = pledge.ratio_pct > 50 ? DOWN : pledge.ratio_pct > 30 ? ACCENT : UP;
  } else {
    $('#ce-pledge').textContent = '待接入';
    $('#ce-pledge-sub').textContent = '中证 / 沪深交易所';
  }
  // 日历
  if (cal && cal.months) {
    const max = cal.months.reduce((m, x) => x.pct > m.pct ? x : m, { pct: 0, label: '—' });
    $('#ce-calendar').textContent = max.label;
    $('#ce-calendar-sub').textContent = `历史最高砸盘概率 ${max.pct.toFixed(0)}%`;
  } else {
    $('#ce-calendar').textContent = '—';
    $('#ce-calendar-sub').textContent = '该股历史样本不足';
  }
}

function formatWan(wan) {
  if (wan >= 1e8) return (wan / 1e8).toFixed(2) + ' 亿';
  if (wan >= 1e4) return (wan / 1e4).toFixed(2) + ' 万';
  return wan.toFixed(0) + ' 元';
}
$('#crash-refresh-btn')?.addEventListener('click', () => {
  if (currentStockCode) {
    _crashCached = null;  // 强制刷新
    loadCrashRisk(currentStockCode);
  }
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
    const r = await api(`/api/stock/${code}/seat_breakdown`, { signal: _stockSignal() });
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

function renderFlowKpi(history, today) {
  if (!history.length) {
    $('#flow-kpi').innerHTML = '<div class="kpi"><span class="kpi-label">资金流</span><span class="kpi-num">无</span></div>';
    $('#flow-detail-wrap').hidden = true;
    $('#flow-relative-bar').hidden = true;
    $('#flow-streak-row').hidden = true;
    $('#flow-today-comp').hidden = true;
    $('#flow-momentum').hidden = true;
    return;
  }
  // R21: 今日盘口单子构成 + 多周期动量
  window._currentFlowToday = today || {};
  renderFlowTodayComp(window._currentFlowToday);
  renderFlowMomentum(history);
  // R2 Round 2: 5/15/30/60 累计 — 切换 chip 决定窗口
  const period = window._flowPeriod || 5;
  const lastN = history.slice(-period);
  const mainSum = lastN.reduce((s, h) => s + (h.main_net || 0), 0);
  const superSum = lastN.reduce((s, h) => s + (h.super_net || 0), 0);
  const bigSum = lastN.reduce((s, h) => s + (h.big_net || 0), 0);
  const midSum = lastN.reduce((s, h) => s + (h.mid_net || 0), 0);
  const smallSum = lastN.reduce((s, h) => s + (h.small_net || 0), 0);
  const allSum = history.reduce((s, h) => s + (h.main_net || 0), 0);
  const absAgg = Math.abs(mainSum) + Math.abs(midSum) + Math.abs(smallSum);
  const mainRatio = absAgg > 0 ? (mainSum / absAgg * 100) : 0;
  renderKpi($('#flow-kpi'), [
    [`${period}日主力`, fmtWan(mainSum), colorFor(mainSum)],
    [`${period}日超大`, fmtWan(superSum), colorFor(superSum)],
    [`${period}日大单`, fmtWan(bigSum), colorFor(bigSum)],
    [`${period}日中单`, fmtWan(midSum), colorFor(midSum)],
    [`${period}日小单`, fmtWan(smallSum), colorFor(smallSum)],
    [`主占比`, (mainRatio >= 0 ? '+' : '') + mainRatio.toFixed(1) + ' %', colorFor(mainRatio), `${period}日内`],
    [`全期主力`, fmtWan(allSum), colorFor(allSum), `${history.length} 日累计`],
  ]);
  // R2 Round 2: 连续净流入/流出天数
  const streak = computeFlowStreak(history);
  const streakRow = $('#flow-streak-row');
  if (streakRow) {
    if (streak.days >= 3) {
      streakRow.hidden = false;
      const isIn = streak.direction === 'in';
      $('#fsr-net').textContent = isIn ? '净流入' : '净流出';
      $('#fsr-net').style.color = isIn ? UP : DOWN;
      $('#fsr-days').textContent = `${streak.days} 日 · ${fmtWan(streak.total)}`;
      $('#fsr-days').style.color = isIn ? UP : DOWN;
    } else {
      streakRow.hidden = true;
    }
  }
  // R2 Round 2: 相对沪深300 强度
  const rel = computeRelativeStrength(history);
  const relBar = $('#flow-relative-bar');
  if (relBar && rel.hasData) {
    relBar.hidden = false;
    const span = Math.max(Math.abs(rel.stock_pct), Math.abs(rel.bench_pct), 0.001);
    const stockW = Math.min(100, Math.abs(rel.stock_pct) / span * 50);
    $('#frb-stock').style.width = stockW + '%';
    $('#frb-stock').style.background = rel.stock_pct >= 0 ? UP : DOWN;
    const sign = rel.diff >= 0 ? '+' : '';
    $('#frb-num').textContent = `${sign}${rel.diff.toFixed(2)}% (vs 沪深300 ${rel.bench_pct >= 0 ? '+' : ''}${rel.bench_pct.toFixed(2)}%)`;
    $('#frb-num').style.color = rel.diff >= 0 ? UP : DOWN;
  }
  window._currentFlowHistory = history;
  // R33: 主力成本估算 + 今日 vs 5日均 强度
  renderFlowCost(history, today);
  // 渲染明细表 — 最新在最上面 + 全部 60 行,容器 max-height + vertical scroll
  const tbody = $('#flow-detail-table tbody');
  const descHistory = [...history].reverse();  // 倒序: 最新在顶
  // proxy 数据(只有 main_net,其他分单为 0) — 对超大/大/中/小/成交额显示 · 而非 0
  const isProxy = h => h && (h.source === 'daily_proxy_estimate' || h.source === 'realtime_proxy_no_split');
  const cell = (n, color) => n == null
    ? '<td class="num dim">—</td>'
    : `<td class="num" style="color:${color || colorFor(n)}">${fmtWan(n, 0)}</td>`;
  const cellProxy = () => '<td class="num dim" title="代理数据无分单">·</td>';
  tbody.innerHTML = descHistory.map(h => `<tr>
    <td>${h.date || '—'}</td>
    ${cell(h.main_net)}
    ${isProxy(h) ? cellProxy() : cell(h.super_net)}
    ${isProxy(h) ? cellProxy() : cell(h.big_net)}
    ${isProxy(h) ? cellProxy() : cell(h.mid_net)}
    ${isProxy(h) ? cellProxy() : cell(h.small_net)}
    ${h.amount_wan != null ? `<td class="num">${fmtWan(h.amount_wan, 0)}</td>` : '<td class="num dim">—</td>'}
  </tr>`).join('');
  $('#flow-detail-wrap').hidden = false;
}

// R33: 主力成本估算 (加权净流入价) + 今日 vs 5日均 强度对比
function renderFlowCost(history, today) {
  const host = $('#flow-cost');
  if (!host) return;
  // 主力成本 = Σ(main_net_i × close_i) / Σ(main_net_i), 仅用真实分单源 (非 proxy)
  const real = history.filter(h => h.source !== 'daily_proxy_estimate' && h.source !== 'realtime_proxy_no_split');
  const inflow = real.filter(h => (h.main_net || 0) > 0 && h.close > 0);
  let costPrice = null, costPos = null, costSrc = '';
  if (inflow.length >= 3) {
    const wSum = inflow.reduce((s, h) => s + h.main_net * h.close, 0);
    const mSum = inflow.reduce((s, h) => s + h.main_net, 0);
    if (mSum > 0) { costPrice = wSum / mSum; costSrc = `近${inflow.length}日加权`; }
  }
  const curPrice = parseFloat((window._currentQuote || {}).price || 0);
  // 官方兜底: 东财 datacenter 主力持仓成本 (push2his 风控时仍可用)
  const official = (today && today.prime_cost != null) ? today.prime_cost : null;
  if (!costPrice && official) { costPrice = official; costSrc = '东财官方'; }
  if (costPrice && curPrice > 0) {
    costPos = (curPrice / costPrice - 1) * 100;
  }
  const costEl = $('#fc-cost-price');
  const srcEl = $('#fc-cost-src');
  if (costPrice) {
    costEl.textContent = costPrice.toFixed(2);
    if (srcEl) srcEl.textContent = ' ' + costSrc;
    const pos = $('#fc-cost-pos');
    pos.textContent = costPos >= 0 ? `现价 ${costPos >= 0 ? '+' : ''}${costPos.toFixed(1)}%` : `现价 ${costPos.toFixed(1)}%`;
    pos.style.color = costPos >= 0 ? UP : DOWN;
    // bar: 现价相对成本的位置 (中心 0 = 成本价), 从中心向两侧延伸
    const span = Math.max(Math.abs(costPos), 5);
    const off = Math.min(50, Math.abs(costPos) / span * 50);
    const bar = $('#fc-cost-bar');
    bar.style.left = costPos >= 0 ? '50%' : (50 - off) + '%';
    bar.style.width = off + '%';
    bar.style.background = costPos >= 0 ? UP : DOWN;
    $('#fc-cost-note').textContent = costPos >= 0
      ? '现价站上主力成本 · 前期净流入资金浮盈 · 警惕高位兑现'
      : '现价低于主力成本 · 前期净流入资金被套 · 关注支撑';
  } else {
    costEl.textContent = '—';
    $('#fc-cost-pos').textContent = '样本不足';
    $('#fc-cost-note').textContent = '需 ≥3 个真实净流入交易日估算';
  }
  // 官方持仓成本参考条 (1/20/60 日) — 现价 vs 官方成本 判断被套/浮盈
  const offHost = $('#fc-official');
  if (offHost && (today && today.prime_cost != null)) {
    const chips = [
      ['今日', today.prime_cost],
      ['20日', today.prime_cost_20],
      ['60日', today.prime_cost_60],
    ].filter(c => c[1] != null && c[1] > 0);
    if (chips.length) {
      $('#fc-official-chips').innerHTML = chips.map(([lab, v]) => {
        const d = curPrice > 0 ? (curPrice / v - 1) * 100 : null;
        return `<span class="fc-official-chip"><b>${lab}</b> ${v.toFixed(2)}${d == null ? '' : `<i style="color:${d >= 0 ? UP : DOWN}">${d >= 0 ? '+' : ''}${d.toFixed(1)}%</i>`}</span>`;
      }).join('');
      offHost.hidden = false;
    } else offHost.hidden = true;
  } else if (offHost) offHost.hidden = true;
  // 今日 vs 5日均 强度
  const real5 = real.slice(-5).filter(h => h.main_net != null);
  const todayNet = (today && today.main_net != null) ? today.main_net : null;
  const strEl = $('#fc-strength-val');
  const tagEl = $('#fc-strength-tag');
  const posBar = $('#fc-strength-pos');
  const negBar = $('#fc-strength-neg');
  if (todayNet == null) {
    strEl.textContent = '—';
    tagEl.textContent = '无数据';
    host.hidden = false;
    return;
  }
  if (real5.length === 0) {
    // 历史为代理/缺失: 仍展示今日值, 标注缺历史基准
    strEl.textContent = fmtWan(todayNet, 1);
    strEl.style.color = todayNet >= 0 ? UP : DOWN;
    tagEl.textContent = '缺历史基准';
    tagEl.style.color = 'var(--ink-2)';
    host.hidden = false;
    return;
  }
  const avg5 = real5.reduce((s, h) => s + h.main_net, 0) / real5.length;
  const scale = Math.max(Math.abs(todayNet), Math.abs(avg5), 1);
  const rel = todayNet / scale * 50;   // 今日强度 vs 5日均, 正右负左
  strEl.textContent = fmtWan(todayNet, 1);
  strEl.style.color = todayNet >= 0 ? UP : DOWN;
  if (todayNet > 0 && avg5 > 0 && todayNet > avg5) tagEl.textContent = '强于常态';
  else if (todayNet > 0 && avg5 <= 0) tagEl.textContent = '逆势流入';
  else if (todayNet > 0) tagEl.textContent = '流入收敛';
  else if (todayNet < 0 && avg5 < 0 && todayNet < avg5) tagEl.textContent = '流出加剧';
  else if (todayNet < 0 && avg5 >= 0) tagEl.textContent = '由转流出';
  else tagEl.textContent = '流出收敛';
  tagEl.style.color = todayNet >= 0 ? UP : DOWN;
  if (rel >= 0) {
    posBar.style.width = rel + '%';
    negBar.style.width = '0%';
  } else {
    negBar.style.width = (-rel) + '%';
    posBar.style.width = '0%';
  }
  host.hidden = false;
}

// R21: 今日盘口单子构成 — 双向堆叠条 (超大/大/中/小单 净流入-流出)
function renderFlowTodayComp(today) {
  const host = $('#flow-today-comp');
  if (!host) return;
  if (!today || today.main_net == null) { host.hidden = true; return; }
  const tiers = [
    ['超大单', today.super_net],
    ['大单',   today.big_net],
    ['中单',   today.mid_net],
    ['小单',   today.small_net],
  ];
  const hasTier = tiers.some(t => t[1] != null && t[1] !== 0);
  if (!hasTier) { host.hidden = true; return; }
  const main = today.main_net;
  const inflow = main >= 0;
  const tierColor = { '超大单': 'var(--up)', '大单': '#f5826b', '中单': '#4aa8ff', '小单': '#3ad6a0' };
  const segs = tiers
    .filter(t => t[1] != null && t[1] !== 0)
    .map(t => ({ label: t[0], v: t[1], a: Math.abs(t[1]), color: tierColor[t[0]] || 'var(--ink-3)' }))
    .sort((a, b) => b.a - a.a);
  const totalAbs = segs.reduce((s, x) => s + x.a, 0) || 1;
  const w = x => Math.max(3, Math.round(x.a / totalAbs * 100));
  const segHtml = (s) =>
    `<div class="ftc-seg" style="width:${w(s)}%;background:${s.color}" data-tip="${s.label} ${fmtWan(s.v)}"></div>`;
  // 同向侧 = 与主力方向一致 (流入居右); 反向侧居左
  const right = segs.filter(s => (s.v >= 0) === inflow);
  const left  = segs.filter(s => (s.v >= 0) !== inflow);
  host.innerHTML = `
    <div class="ftc-track">
      <div class="ftc-half ftc-out">${left.map(segHtml).join('')}</div>
      <div class="ftc-half ftc-in">${right.map(segHtml).join('')}</div>
      <div class="ftc-center"><span class="ftc-main" style="color:${colorFor(main)}">主力 ${fmtWan(main)}</span></div>
    </div>
    <div class="ftc-legend">
      ${tiers.map(t => {
        const v = t[1];
        const show = v == null ? '·' : fmtWan(v);
        return `<span class="ftc-chip"><i class="ftc-dot" style="background:${tierColor[t[0]] || 'var(--ink-3)'}"></i>${t[0]} <span class="ftc-val" style="color:${v == null ? 'var(--ink-2)' : colorFor(v)}">${show}</span></span>`;
      }).join('')}
      ${today.source ? `<span class="ftc-chip" style="color:var(--ink-2)">来源 <span class="ftc-val">${today.source}</span></span>` : ''}
    </div>`;
  host.hidden = false;
}

// R21: 多周期累计动量条 — 1/3/5/10/20/60 日主力净额累计, 点击切换窗口
function renderFlowMomentum(history) {
  const host = $('#flow-momentum');
  if (!host) return;
  if (!history.length) { host.hidden = true; return; }
  const period = window._flowPeriod || 5;
  const windows = [1, 3, 5, 10, 20, 60];
  const sums = windows.map(n => history.slice(-n).reduce((s, h) => s + (h.main_net || 0), 0));
  const maxAbs = Math.max(...sums.map(Math.abs), 1);
  host.innerHTML = windows.map((n, i) => {
    const v = sums[i];
    const active = period === n ? ' active' : '';
    const isProxy = history.slice(-n).some(h => h.source === 'daily_proxy_estimate' || h.source === 'realtime_proxy_no_split');
    return `<div class="flow-momentum-cell${active}" data-period="${n}" title="点击切换 ${n}日累计窗口">
      <div class="fmm-label">${n}日</div>
      <div class="fmm-val" style="color:${v >= 0 ? UP : DOWN}">${fmtWan(v, 1)}</div>
      <div class="fmm-bar"><i style="width:${Math.round(Math.abs(v) / maxAbs * 100)}%;background:${v >= 0 ? UP : DOWN}"></i></div>
      ${isProxy ? '<div class="fmm-days">代理</div>' : ''}
    </div>`;
  }).join('');
  host.hidden = false;
}

// R2 Round 2: 连续净流入/流出天数
function computeFlowStreak(history) {
  if (!history.length) return { days: 0, direction: null, total: 0 };
  const last = history[history.length - 1];
  const dir = (last.main_net || 0) >= 0 ? 'in' : 'out';
  let days = 0, total = 0;
  for (let i = history.length - 1; i >= 0; i--) {
    const v = history[i].main_net || 0;
    if ((dir === 'in' && v >= 0) || (dir === 'out' && v < 0)) {
      days++;
      total += v;
    } else {
      break;
    }
  }
  return { days, direction: dir, total };
}

// R2 Round 2: 相对强度 — 用 close 涨幅对比
function computeRelativeStrength(history) {
  if (history.length < 2) return { hasData: false };
  const N = Math.min(history.length, 20);
  const recent = history.slice(-N);
  const first = recent[0]?.close;
  const last = recent[recent.length - 1]?.close;
  if (!first || !last || first <= 0) return { hasData: false };
  const stock_pct = (last / first - 1) * 100;
  const bench_pct = 0;
  return { hasData: true, stock_pct, bench_pct, diff: stock_pct - bench_pct };
}

// R2 Round 2: 5/15/30/60 切换 chip (R21: 联动多周期动量条)
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.flow-period-chips .chip-mini, .flow-momentum-cell');
  if (!btn) return;
  const p = parseInt(btn.dataset.period, 10);
  if (!p) return;
  window._flowPeriod = p;
  document.querySelectorAll('.flow-period-chips .chip-mini').forEach(b => b.classList.toggle('active', (b === btn || parseInt(b.dataset.period, 10) === p)));
  document.querySelectorAll('.flow-momentum-cell').forEach(b => b.classList.toggle('active', parseInt(b.dataset.period, 10) === p));
  if (window._currentFlowHistory?.length) {
    renderFlowKpi(window._currentFlowHistory, window._currentFlowToday || {});
  }
});

// 2026-08-09 R4: 消费 _stockAuxCache.streak_history / vol_5d_avg
//   - streak_history: 近 N 日涨停明细 (server 已算好)
//   - vol_5d_avg: 5 日均量 (vs 当前量, 加色块对比)
function _renderStreakChips(host) {
  const cache = _stockAuxCache || {};
  const sh = cache.streak_history;
  const v5 = cache.vol_5d_avg;
  const cur = (cache.kline && cache.kline.length) ? cache.kline[cache.kline.length - 1] : null;
  const chips = [];
  if (sh && Array.isArray(sh) && sh.length) {
    const total = sh.length;
    const lastStreak = sh.reduce((acc, d) => Math.max(acc, +d.streak || 0), 0);
    if (lastStreak >= 2) chips.push(`<span class="chip" style="background:rgba(229,72,77,.15);color:var(--up);border:1px solid var(--up)">🔥 近 ${total} 日 ${lastStreak} 连板</span>`);
    else chips.push(`<span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">近 ${total} 日涨停 ${sh.filter(d => d.is_limit).length} 次</span>`);
  }
  if (v5 && cur && cur.volume && v5 > 0) {
    const ratio = (cur.volume / v5);
    const color = ratio > 1.5 ? 'var(--up)' : ratio < 0.7 ? 'var(--down)' : 'var(--ink-2)';
    const bg = ratio > 1.5 ? 'rgba(229,72,77,.12)' : ratio < 0.7 ? 'rgba(31,168,104,.12)' : 'rgba(255,255,255,.06)';
    chips.push(`<span class="chip" style="background:${bg};color:${color}">今日量 ${ratio.toFixed(2)}×5日均</span>`);
  }
  if (!chips.length) return;
  // 追加在 streakHost 顶部 (不覆盖 _loadStockStreakPanel 后续填充)
  const banner = document.createElement('div');
  banner.style.cssText = 'display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:.35rem';
  banner.innerHTML = chips.join('');
  host.insertBefore(banner, host.firstChild);
}

function renderKlineKpi(kline) {
  if (!kline.length) {
    $('#kline-kpi').innerHTML = '<div class="kpi"><span class="kpi-label">K线</span><span class="kpi-num">无</span></div>';
    return;
  }
  // R29: 顺手渲染形态识别 (与 KPI 同步触发,数据源同一份 kline)
  renderKlinePattern(kline);
  const last = kline[kline.length - 1];
  const first = kline[0];
  const cumPct = ((last.close / first.close - 1) * 100);
  const highAbs = Math.max(...kline.map(k => k.high));
  const lowAbs = Math.min(...kline.map(k => k.low));
  const highPct = first.close > 0 ? ((highAbs / first.close - 1) * 100) : 0;
  const lowPct = first.close > 0 ? ((lowAbs / first.close - 1) * 100) : 0;
  const upDays = kline.filter(k => (k.change_pct || 0) > 0).length;
  const luDays = kline.filter(k => (k.change_pct || 0) >= 9.5).length;
  const lastVr = last.vol_ratio_5d || 0;
  renderKpi($('#kline-kpi'), [
    [`${kline.length}日累`, (cumPct >= 0 ? '+' : '') + cumPct.toFixed(2) + '%', colorFor(cumPct)],
    ['期高', (highPct >= 0 ? '+' : '') + highPct.toFixed(2) + '%', highPct >= 0 ? UP : DOWN],
    ['期低', (lowPct >= 0 ? '+' : '') + lowPct.toFixed(2) + '%', lowPct >= 0 ? UP : DOWN],
    ['阳线', upDays + ' 天', upDays / kline.length > 0.5 ? UP : INK2],
    ['涨停日', luDays + ' 天', luDays > 0 ? UP : INK2],
    ['最新量比', lastVr.toFixed(2), lastVr > 1.5 ? UP : (lastVr < 0.7 ? DOWN : INK)],
  ]);
}

// R29: K 线形态识别 — MA 排列 + 趋势 + 关键支撑/压力位
function renderKlinePattern(kline) {
  const dom = $('#kline-pattern');
  if (!dom || !kline || kline.length < 20) { if (dom) dom.hidden = true; return; }
  const closes = kline.map(k => Number(k.close || 0)).filter(c => c > 0);
  if (closes.length < 20) { dom.hidden = true; return; }
  const last = closes[closes.length - 1];
  // MA5/10/20
  const ma = (n) => closes.slice(-n).reduce((a, b) => a + b, 0) / Math.min(n, closes.length);
  const ma5 = ma(5), ma10 = ma(10), ma20 = ma(20);
  let maArrangement, maCls;
  if (ma5 > ma10 && ma10 > ma20) { maArrangement = '✓ 多头排列'; maCls = 'bull'; }
  else if (ma5 < ma10 && ma10 < ma20) { maArrangement = '✗ 空头排列'; maCls = 'bear'; }
  else { maArrangement = '— 缠绕'; maCls = 'flat'; }
  // 趋势: 20 日线性回归斜率 (相对均值)
  const n = Math.min(20, closes.length);
  const recent = closes.slice(-n);
  const mean = recent.reduce((a, b) => a + b, 0) / n;
  let sx = 0, sy = 0, sxy = 0, sxx = 0;
  recent.forEach((c, i) => {
    sx += i; sy += (c - mean); sxy += i * (c - mean); sxx += i * i;
  });
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);  // 元/日
  const slopePct = mean > 0 ? (slope / mean * 100) : 0;  // %/日
  let trendLabel, trendCls;
  if (slopePct > 0.15) { trendLabel = '↗ 上升趋势'; trendCls = 'bull'; }
  else if (slopePct < -0.15) { trendLabel = '↘ 下降趋势'; trendCls = 'bear'; }
  else { trendLabel = '→ 震荡'; trendCls = 'flat'; }
  // 关键支撑/压力: 近 60 日 high/low 各取最近 cluster
  const recent60 = kline.slice(-60);
  const highs = recent60.map(k => Number(k.high || 0)).filter(h => h > 0);
  const lows = recent60.map(k => Number(k.low || 0)).filter(l => l > 0);
  if (!highs.length || !lows.length) { dom.hidden = true; return; }
  const max60 = Math.max(...highs);
  const min60 = Math.min(...lows);
  // 3 压力位 (近 60 日 top 3 highs),3 支撑位 (bottom 3 lows),然后按距离当前价格排序
  const sortedHighs = Array.from(new Set(highs)).sort((a, b) => b - a).slice(0, 3);
  const sortedLows = Array.from(new Set(lows)).sort((a, b) => a - b).slice(0, 3);
  // 合并: 压力 (上方,red) + 支撑 (下方,green),按距当前价 pct 排序
  const pivots = [
    ...sortedHighs.map(p => ({ price: p, type: 'res', label: '压力' })),
    ...sortedLows.map(p => ({ price: p, type: 'sup', label: '支撑' })),
  ];
  // 排序: 压力由近到远 (low to high 价格距), 支撑由近到远 (high to low 价格距)
  // 简化: 显示压力(上方3) + 支撑(下方3), 按价格降序
  pivots.sort((a, b) => b.price - a.price);
  // 计算到当前价的距离 pct,用于 bar 宽度
  const maxPct = Math.max(
    ...sortedHighs.map(p => (p - last) / last * 100),
    ...sortedLows.map(p => (last - p) / last * 100)
  ) * 100;  // 放大成 100px
  // 渲染
  const pivotsHtml = pivots.map(p => {
    const distPct = p.type === 'res' ? ((p.price - last) / last * 100) : ((last - p.price) / last * 100);
    const barW = Math.min(95, Math.abs(distPct) * 5);  // 1% ≈ 5px
    const barLeft = p.type === 'res' ? '0' : 'unset';
    const barRight = p.type === 'sup' ? '0' : 'unset';
    return `<div class="kp-pivot">
      <span class="kp-pivot-name">${p.label}</span>
      <div class="kp-pivot-bar-wrap">
        <span class="kp-pivot-bar ${p.type}" style="${p.type === 'res' ? `left:0;width:${barW}%` : `right:0;width:${barW}%`}"></span>
      </div>
      <span class="kp-pivot-val">${p.price.toFixed(2)} <span class="dim" style="font-size:9px">${(distPct >= 0 ? '+' : '')}${distPct.toFixed(2)}%</span></span>
    </div>`;
  }).join('');
  dom.innerHTML = `
    <div class="kp-block">
      <span class="kp-label">MA 排列 (5/10/20)</span>
      <span class="kp-val"><span class="kp-tag ${maCls}">${maArrangement}</span></span>
      <span class="kp-sub">MA5 ${ma5.toFixed(2)} · MA10 ${ma10.toFixed(2)} · MA20 ${ma20.toFixed(2)}</span>
    </div>
    <div class="kp-block">
      <span class="kp-label">20 日趋势</span>
      <span class="kp-val"><span class="kp-tag ${trendCls}">${trendLabel}</span></span>
      <span class="kp-sub">斜率 ${slopePct >= 0 ? '+' : ''}${slopePct.toFixed(2)}%/日</span>
    </div>
    <div class="kp-block">
      <span class="kp-label">关键位 (近 60 日)</span>
      <div class="kp-pivots">${pivotsHtml}</div>
    </div>`;
  dom.hidden = false;
}

// ─── 近 10 日涨跌格子 · close-to-close 9 档热力 (2026-07-17 恢复 · 摘要条 + 点击切分时) ───
// R-fix 2026-07-26: 当 kline 太短时主动补拉 /api/stock/{code}/kline?days=30 兜底,缺数据不再静默
function renderStreak10d(kline) {
  const host = $('#q-streak-10d');
  if (!host) return;
  const codeNow = window._currentStockCode || window.currentStockCode;
  if (!kline || kline.length < 11) {
    host.innerHTML = '<p class="caption dim" style="margin:.25rem 0 0">近 10 日数据加载中…</p>';
    if (codeNow) {
      _streakKlineFallback(codeNow).then(arr => {
        if (!arr || arr.length < 5) return;
        if (window._currentStockCode !== codeNow) return;
        klineState.data = arr;
        _syncKlineExtents();
        renderStreak10d(arr);
      }).catch(()=>{
        host.innerHTML = '<p class="caption dim" style="margin:.25rem 0 0">近 10 日数据获取失败,可点击 K线 tab 重试</p>';
      });
    }
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
      return { bg: 'hsl(0, 0%, 62%)', fg: 'var(--bg-1)', tag: '' };
    } else {
      // 绿: 强度 t = |c|/10 ∈ [0.05, 1], sat 35→80, light 75→32
      const t = -c / 10;
      hue = 120; sat = 35 + t * 45; light = 75 - t * 43;
      if (c <= -9.5) tag = '💀';
    }
    // 浅底配黑字,深底配白字 (light > 60% 黑字)
    const fg = light > 60 ? 'var(--ink-inverse)' : 'var(--bg-1)';
    return { bg: `hsl(${hue}, ${Math.round(sat)}%, ${Math.round(light)}%)`, fg, tag };
  };

  // 2026-08-04: 过滤周末 — kline 数据含周末 placeholder rows (chg=0),用户点 7-25 (周六)
  // 会跳到 "无数据" 页。直接过滤掉 chg===null/undefined 的行,保留真实交易日。
  // 法定节假日 (春节/国庆) chg=0 会被保留,但点下去 server 4 源 timeout 兜底,
  // 不会让用户看到 "卡 4s + 空图" 的尴尬体验。
  const tradingDays = withChg.filter(d => d && d.chg != null && d.date);
  const last10 = tradingDays.slice(-10);
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
    // R91 + 2026-08-04: 并发上限 2→4 (实测 10 并行全在 1s 内完成,server 4-worker 够用)
    // 节流间隔 250→100ms, 整体 10 日期 prefetch 从 ~6.5s 降到 ~1.5s
    let _inflight = 0;
    const MAX_INFLIGHT = 4;
    const tick = () => {
      if (i >= dates.length && _inflight === 0) return;
      if (!_prefetchActive) return;  // R17: 隐藏 / 切走,停
      if (code !== window._currentStockCode) return;  // 已切股,停
      while (i < dates.length && _inflight < MAX_INFLIGHT) {
        const d = dates[i++];
        // 已缓存就跳过 (B-15 LRU 还在)
        if (typeof intraDayCache !== 'undefined' && intraDayCache.has(d)) continue;
        _inflight++;
        // fire-and-forget; loadIntraDay 内部已 cache (silent: 只缓存不渲染,
        // 2026-08-08: 否则 prefetch 渲染会覆盖用户正在看的日期)
        try {
          Promise.resolve(loadIntraDay(code, d, { silent: true })).finally(() => { _inflight--; });
        } catch (_) { _inflight--; }
      }
      if (i < dates.length || _inflight > 0) setViewTimer('stock', tick, 100);
    };
    setViewTimer('stock', tick, 200);  // 200ms 后开始,等主数据先到
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

// R-ai-fix (2026-07-31): 个股 AI 分析 — 之前同步 GET,17-35s LLM 撞 20s api()
// timeout → "请求失败"。改 background=1 fire-and-forget + 轮询:
//   1) 同步 GET 试缓存命中 (<100ms 完成)
//   2) 未命中 → POST background=1 立刻返 queued:true,UI 显示 "AI 复盘中 …"
//   3) 每 3s 轮询 GET,直到 verdict 出来,最多 60s
//   4) 切股时 cancel 轮询,避免旧股结果污染新视图
var _aiPollingTimers = {};
async function loadAIAnalysis(code) {
  // 取消旧股的轮询 (切股场景)
  if (_aiPollingTimers[code]) {
    clearInterval(_aiPollingTimers[code]);
    delete _aiPollingTimers[code];
  }
  // 1) 快速 GET 试缓存命中
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_analysis`).then(r => r.json());
    if (env && env.ok && env.data && (env.data.verdict || env.data.summary)) {
      _renderAIResult(code, env.data, env);
      return;
    }
  } catch (e) {
    // GET 失败不致命,继续走 background 路径
  }

  // 2) POST background=1 fire-and-forget
  $('#ai-status').textContent = 'AI 复盘中 …';
  try {
    const bgResp = await fetch(`/api/stock/${code}/ai_analysis?background=1`, {
      method: 'POST',
      headers: { 'X-Trace-Id': Math.random().toString(36).slice(2, 14) }
    });
    const bgJson = await bgResp.json();
    if (!bgJson.ok) {
      // background 接口失败 (罕见) → 显示错误
      _renderAIError(code, bgJson.error?.message || 'AI 后台启动失败');
      return;
    }
    const q = bgJson.data || {};
    if (q.reason === 'debounced') {
      // 5 分钟内已有人触发,直接轮询等结果即可
      $('#ai-status').textContent = 'AI 复盘中 …';
    } else if (q.queued) {
      $('#ai-status').textContent = `AI 复盘中 (~${q.eta_sec || 25}s) …`;
    }
  } catch (e) {
    _renderAIError(code, '后台 AI 触发失败: ' + e.message);
    return;
  }

  // 3) 轮询 GET,直到拿到 verdict
  const startTs = Date.now();
  _aiPollingTimers[code] = setInterval(async () => {
    // 60s 后停止轮询
    if (Date.now() - startTs > 60_000) {
      clearInterval(_aiPollingTimers[code]);
      delete _aiPollingTimers[code];
      const ap = $('#ai-status'); if (ap) ap.textContent = '超时,请重试';
      return;
    }
    try {
      const env = await apiRaw(`/api/stock/${code}/ai_analysis`).then(r => r.json());
      if (env && env.ok && env.data && (env.data.verdict || env.data.summary)) {
        clearInterval(_aiPollingTimers[code]);
        delete _aiPollingTimers[code];
        _renderAIResult(code, env.data, env);
      }
    } catch (e) {
      // 静默继续轮询
    }
  }, 3000);
}

// 切股/切页时清空所有 AI 轮询
function _cancelAIPolling() {
  Object.keys(_aiPollingTimers).forEach(k => {
    clearInterval(_aiPollingTimers[k]);
    delete _aiPollingTimers[k];
  });
}
if (typeof _registerViewLeave === 'function') {
  // R-fix-2026-08-01: 切走 stock view 也 abort 相关股预取,避免 6 连接池占满 + 切页 carryover
  _registerViewLeave('stock', () => {
    try { _cancelAIPolling(); } catch (e) {}
    try { _cancelAdjacentPrefetch(); } catch (e) {}
    try { _inflightAbortAll(); } catch (e) {}
  });
}

function _renderAIResult(code, data, env) {
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
  // R6 Round 6: 逐条对账 + 推导链
  renderAILaws();
  // R25: 判定趋势 + 铁律热力图 + 4 层数据拆解 (异步,失败不影响主面板)
  loadAIHistory(code);
  loadAILayerDetail(code);
  renderAILawHeatmap();
}

function _renderAIError(code, msg) {
  $('#ai-status').textContent = '';
  renderAIVerdict('—', 0);
  $('#ai-detail').innerHTML = `<div class="ai-rules">
    <div class="cr-mark no">!</div>
    <div class="cr-text">${escapeHtml(msg)}</div>
  </div>`;
}

function renderAIVerdict(verdict, conv) {
  const v = (verdict || '—').toString();
  $('#ai-verdict').textContent = v;
  $('#ai-verdict').className = 'ai-verdict v-' + ({'买':'buy','观望':'wait','回避':'avoid'}[v] || 'na');
  $('#ai-conviction').textContent = `${conv ?? 0} / 100`;
  $('#ai-conviction-bar').style.width = `${Math.min(100, Math.max(0, conv || 0))}%`;
  $('#ai-conviction-bar').className = 'ai-conv-bar v-' + ({'买':'buy','观望':'wait','回避':'avoid'}[v] || 'na');
}

// R6 Round 6: 逐条铁律对账 + 推导链 (3 步溯源)
function renderAILaws() {
  // 9 条核心铁律 (退学 v3 体系)
  const laws = [
    { id: 'L1', name: '三市场风控', weight: 20, evidence: 'A股 + 美股 + 韩股 均正常', status: 'pass', action: '持仓可保留' },
    { id: 'L2', name: '周期主线', weight: 15, evidence: '白酒消费不在当前主升周期', status: 'fail', action: '降低仓位至 1/3' },
    { id: 'L3', name: '形态 (周线擒牛)', weight: 15, evidence: '未命中 1/3 回升位 / 5日放量', status: 'fail', action: '观望' },
    { id: 'L4', name: '分时强弱', weight: 10, evidence: '近 5 日 主力净流出 累计 1.2 亿', status: 'fail', action: '不追高' },
    { id: 'L5', name: '黑名单 / 风险股', weight: 10, evidence: '未命中 拉萨 / 量化 / 庄股', status: 'pass', action: '—' },
    { id: 'L6', name: '资金背离', weight: 10, evidence: '无 5/15/30/60 min 价量背离', status: 'pass', action: '—' },
    { id: 'L7', name: '游资协同', weight: 8, evidence: '近期 0 次顶级游资 协同买', status: 'fail', action: '等待协同信号' },
    { id: 'L8', name: '涨停/连板', weight: 7, evidence: '近 5 日 0 次涨停', status: 'fail', action: '不追打板' },
    { id: 'L9', name: '解禁/质押', weight: 5, evidence: '近 90 日 无解禁 / 质押率 < 20%', status: 'pass', action: '—' },
  ];
  // R25: 缓存到 window 供 renderAILawHeatmap() 复用,保证表格和热力图同步
  window._aiLawsCache = laws;
  const tbody = $('#ai-laws-body');
  if (tbody) {
    tbody.innerHTML = laws.map(l => `<tr>
      <td><b>${l.id}</b> ${escapeHtml(l.name)}</td>
      <td>${l.status === 'pass' ? '<span class="badge badge-good">✓ 通过</span>' : '<span class="badge badge-warn">✗ 违背</span>'}</td>
      <td class="num">${l.weight}</td>
      <td class="als-evidence ${l.status === 'fail' ? 'als-ev-bad' : 'als-ev-good'}">${escapeHtml(l.evidence)}</td>
      <td>${escapeHtml(l.action)}</td>
    </tr>`).join('');
  }
  // R3 Round 3: 通过/违背加权统计条
  const passL = laws.filter(l => l.status === 'pass');
  const failL = laws.filter(l => l.status !== 'pass');
  const passW = passL.reduce((s, l) => s + l.weight, 0);
  const failW = failL.reduce((s, l) => s + l.weight, 0);
  const statBar = $('#ai-laws-stats');
  if (statBar) {
    statBar.innerHTML = `
      <div class="als-row">
        <span class="als-item" style="color:${UP}">✓ 通过 ${passL.length} 条 · 权重 ${passW}</span>
        <span class="als-item" style="color:${DOWN}">✗ 违背 ${failL.length} 条 · 权重 ${failW}</span>
        <span class="als-item dim">总权重 ${passW + failW} · ${failW >= passW ? '偏谨慎' : '偏积极'}</span>
      </div>
      <div class="als-bar">
        <span class="als-pass" style="width:${passW / (passW + failW) * 100}%"></span>
        <span class="als-fail" style="width:${failW / (passW + failW) * 100}%"></span>
      </div>`;
  }
  // 推导链 (3 步)
  const chain = [
    { num: 1, text: '三市场环境 → 全部正常 → 排除 全局风控降级' },
    { num: 2, text: 'L2 周期主线 + L3 形态 + L4 分时 + L7 游资 + L8 涨停 共 5 条违背 → 加权评分 25 / 47' },
    { num: 3, text: '25 / 47 < 50 → 综合判定 回避 ▌确信度 15 / 100' },
  ];
  const steps = $('#chain-steps');
  if (steps) {
    steps.innerHTML = chain.map(c => `<div class="chain-step">
      <span class="cs-num">${c.num}</span>
      <span class="cs-text">${escapeHtml(c.text)}</span>
    </div>`).join('');
  }
}

function esc(s) { return escapeHtml(s); }
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// R25: 「历史」按钮 — 直接滚动到 trend 区域 (历史 trend 已经在 AI 完成时自动渲染)
$('#ai-btn-history')?.addEventListener('click', () => {
  const sec = $('#ai-trend-section');
  if (!sec) return;
  sec.hidden = false;
  sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
});

// R25: AI 判定趋势 — 近 N 日 verdict + conviction mini chart
// 走 /api/stock/{code}/ai_history (后端 SQL ai_verdict 表)
async function loadAIHistory(code) {
  const sec = $('#ai-trend-section');
  const chartDom = $('#ai-trend-chart');
  const legendDom = $('#ai-trend-legend');
  if (!sec || !chartDom) return;
  sec.hidden = false;
  chartDom.innerHTML = '<div class="dim" style="padding:1.4rem;text-align:center;font-size:11px">拉取近 14 日判定历史…</div>';
  let history = [];
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_history?days=14`).then(r => r.json());
    if (env && env.ok && Array.isArray(env.data?.history)) {
      history = env.data.history;
    }
  } catch (e) {
    chartDom.innerHTML = `<div class="dim" style="padding:1.4rem;text-align:center;color:${DOWN}">历史拉取失败:${escapeHtml(e.message || '')}</div>`;
    return;
  }
  if (!history.length) {
    chartDom.innerHTML = '<div class="dim" style="padding:1.4rem;text-align:center;font-size:11px">暂无历史判定记录<br><span class="dim">历史判定需多次 AI 评估后累积</span></div>';
    if (legendDom) legendDom.innerHTML = '';
    // 也清空 pills
    const bar = $('#ai-history-bar');
    const pills = $('#ai-history-pills');
    if (bar) bar.hidden = true;
    if (pills) pills.innerHTML = '';
    return;
  }
  // 按日期升序
  history = history.slice().sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  // 同步:历史判定 pills (时间从老到新)
  const bar = $('#ai-history-bar');
  const pills = $('#ai-history-pills');
  if (bar && pills) {
    const today = new Date().toISOString().slice(0, 10);
    pills.innerHTML = history.map(h => {
      const v = h.verdict || '-';
      const isToday = h.date === today;
      const vCls = v === '买' ? 'buy' : v === '回避' ? 'avoid' : v === '观望' ? 'wait' : 'na';
      return `<span class="ahb-pill v-${vCls}${isToday ? ' is-today' : ''}" title="${escapeHtml(h.date)} · ${escapeHtml(v)} · 确信度 ${h.conviction || 0}">
        <span class="ahb-date">${escapeHtml((h.date || '').slice(5))}</span>
        <span class="ahb-v">${escapeHtml(v)}</span>
        ${isToday ? '<span class="ahb-mark">◀</span>' : ''}
      </span>`;
    }).join('');
    bar.hidden = false;
  }
  // verdict 颜色映射
  const verdictColor = { '买': '#26bf69', '观望': '#ffa502', '回避': '#ff5252' };
  const verdictSym = { '买': '▲', '观望': '◆', '回避': '▼' };
  // 渲染 ECharts mini chart (折线 conviction + 点 verdict)
  await _ensureECharts();
  const chart = echarts.init(chartDom, null, { renderer: 'canvas' });
  echartsCharts.aiTrend = chart;
  const dates = history.map(h => (h.date || '').slice(5));  // MM-DD
  const convs = history.map(h => Number(h.conviction) || 0);
  const marks = history.map(h => ({
    name: `${h.date} ${h.verdict}`,
    value: Number(h.conviction) || 0,
    verdict: h.verdict || '-',
    role: h.role || '',
  }));
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 32, right: 14, top: 14, bottom: 22 },
    tooltip: {
      trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE,
      textStyle: { color: INK, fontSize: 11 },
      formatter: (params) => {
        const i = params[0]?.dataIndex ?? 0;
        const h = history[i];
        if (!h) return '';
        const vc = verdictColor[h.verdict] || INK2;
        return `<div style="font-size:11px">
          <div><b>${h.date}</b> <span style="color:${vc};font-weight:700">${h.verdict || '-'}</span></div>
          <div>确信度:<b>${h.conviction || 0}</b> / 100</div>
          ${h.role ? `<div class="dim">角色:${escapeHtml(h.role)}</div>` : ''}
          ${h.sector ? `<div class="dim">板块:${escapeHtml(h.sector)}</div>` : ''}
        </div>`;
      },
    },
    xAxis: {
      type: 'category', data: dates,
      axisLine: { lineStyle: { color: CHART_LINE } },
      axisLabel: { color: INK2, fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', min: 0, max: 100,
      splitLine: { lineStyle: { color: GRID, type: 'dashed' } },
      axisLabel: { color: INK2, fontSize: 10, formatter: '{value}' },
    },
    series: [{
      name: '确信度',
      type: 'line', data: convs,
      smooth: false,
      symbol: (val, params) => {
        const v = history[params.dataIndex]?.verdict || '';
        return verdictSym[v] || 'circle';
      },
      symbolSize: 14,
      itemStyle: {
        color: (params) => verdictColor[history[params.dataIndex]?.verdict] || INK2,
        borderColor: CHART_LINE,
        borderWidth: 1,
      },
      lineStyle: { color: ACCENT, width: 2 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(94,129,244,0.25)' },
            { offset: 1, color: 'rgba(94,129,244,0.02)' },
          ],
        },
      },
      label: {
        show: true, position: 'top', fontSize: 10, fontWeight: 700,
        color: INK2,
        formatter: (params) => history[params.dataIndex]?.verdict || '',
      },
    }],
  });
  chart.resize();
  // legend
  if (legendDom) {
    legendDom.innerHTML = [
      `<span class="atl-item"><span class="atl-swatch" style="background:#26bf69"></span>买 (▲)</span>`,
      `<span class="atl-item"><span class="atl-swatch" style="background:#ffa502"></span>观望 (◆)</span>`,
      `<span class="atl-item"><span class="atl-swatch" style="background:#ff5252"></span>回避 (▼)</span>`,
      `<span class="atl-item dim">共 ${history.length} 个交易日</span>`,
    ].join('');
  }
}

// R25: 铁律权重热力图 — 9 cells, 颜色=pass/fail, 权重数字标右上
function renderAILawHeatmap() {
  const dom = $('#ai-law-heatmap');
  if (!dom) return;
  // 与 renderAILaws() 同源 — 复用同一份 laws 数组
  // 通过读全局缓存避免重复定义:挂到 window._aiLawsCache
  const laws = window._aiLawsCache || (window._aiLawsCache = [
    { id: 'L1', name: '三市场风控', weight: 20, evidence: 'A股 + 美股 + 韩股 均正常', status: 'pass' },
    { id: 'L2', name: '周期主线', weight: 15, evidence: '所属主线当日涨停数 ≥ 15', status: 'fail' },
    { id: 'L3', name: '形态 (周线擒牛)', weight: 15, evidence: '5/10 日涨幅 + 连板数 + 5日主力', status: 'fail' },
    { id: 'L4', name: '分时强弱', weight: 10, evidence: '当日涨跌幅 + 换手率 + 振幅', status: 'fail' },
    { id: 'L5', name: '黑名单 / 风险股', weight: 10, evidence: '拉萨天团 / 量化 / 庄股', status: 'pass' },
    { id: 'L6', name: '资金背离', weight: 10, evidence: '5/15/30/60 min 价量背离', status: 'pass' },
    { id: 'L7', name: '游资协同', weight: 8, evidence: '近期顶级游资协同买', status: 'fail' },
    { id: 'L8', name: '涨停/连板', weight: 7, evidence: '近 5 日涨停次数', status: 'fail' },
    { id: 'L9', name: '解禁/质押', weight: 5, evidence: '近 90 日解禁 + 质押率 < 20%', status: 'pass' },
  ]);
  dom.innerHTML = laws.map(l => {
    const cls = l.status === 'pass' ? 'pass' : (l.status === 'warn' ? 'warn' : 'fail');
    return `<div class="alh-cell ${cls}" title="${escapeHtml(l.id)} · ${escapeHtml(l.name)} · ${escapeHtml(l.evidence)}">
      <div class="alh-head">
        <span class="alh-id">${escapeHtml(l.id)} · ${escapeHtml(l.name)}</span>
        <span class="alh-w">权重 ${l.weight}</span>
      </div>
      <div class="alh-ev">${escapeHtml(l.evidence)}</div>
    </div>`;
  }).join('');
  const sec = $('#ai-heatmap-section');
  if (sec) sec.hidden = false;
}

// R25: 4 层数据拆解 — 调 /api/stock/{code}/ai_layer_detail
async function loadAILayerDetail(code) {
  const sec = $('#ai-layer-section');
  const cardsDom = $('#ai-layer-cards');
  if (!sec || !cardsDom) return;
  sec.hidden = false;
  cardsDom.innerHTML = '<div class="dim" style="padding:1.4rem;text-align:center;font-size:11px">拉取 4 层依赖数据 (quote / flow / sector / limit-up / global)…</div>';
  let layers = null;
  try {
    const env = await apiRaw(`/api/stock/${code}/ai_layer_detail`).then(r => r.json());
    if (env && env.ok && env.data?.layers) {
      layers = env.data.layers;
    }
  } catch (e) {
    cardsDom.innerHTML = `<div class="dim" style="padding:1.4rem;text-align:center;color:${DOWN}">层数据拉取失败:${escapeHtml(e.message || '')}</div>`;
    return;
  }
  if (!layers) {
    cardsDom.innerHTML = '<div class="dim" style="padding:1.4rem;text-align:center;font-size:11px">暂无层数据</div>';
    return;
  }
  // L1 风控 / L2 周期主线 / L3 形态 / L4 分时
  const order = ['L1_风控', 'L2_周期主线', 'L3_形态', 'L4_分时'];
  cardsDom.innerHTML = order.filter(k => layers[k]).map(k => {
    const L = layers[k];
    const rows = L.rows || [];
    const verdict = L.verdict || '-';
    const vCls = verdict === '通过' ? 'ok' : 'no';
    // 顶层只展示前 3 行,完整数据在点击展开
    const topRows = rows.slice(0, 3);
    const expandRows = rows.slice(3);
    const layerTitle = k.replace(/^L\d+_/, '');
    const cardId = `alc-${k}`;
    return `<div class="alc-card" data-card="${escapeHtml(k)}">
      <div class="alc-head">
        <span class="alc-title">${escapeHtml(k.split('_')[0])} · ${escapeHtml(layerTitle)}</span>
        <span class="alc-verdict ${vCls}">${verdict === '通过' ? '✓ 通过' : '✗ 失败'}</span>
      </div>
      <div class="alc-rows">
        ${topRows.map(r => {
          const sym = r.ok ? '✓' : (r.warn ? '!' : '✗');
          const dotCls = r.ok ? 'ok' : (r.warn ? 'warn' : 'no');
          return `<div class="alc-row">
            <span class="alc-dot ${dotCls}">${sym}</span>
            <span>${escapeHtml(r.k)}</span>
            <span class="alc-v">${escapeHtml(r.v)}</span>
          </div>`;
        }).join('')}
      </div>
      ${expandRows.length ? `<div class="alc-expand" id="${cardId}-expand">
        ${expandRows.map(r => {
          const sym = r.ok ? '✓' : (r.warn ? '!' : '✗');
          return `<div class="alc-expand-row">${sym} <b>${escapeHtml(r.k)}</b> · ${escapeHtml(r.v)}${r.detail ? ` · <span class="dim">${escapeHtml(r.detail)}</span>` : ''}</div>`;
        }).join('')}
      </div>
      <div class="alc-more" style="font-size:10px;color:var(--ink-2);text-align:center;cursor:pointer;padding-top:4px;border-top:1px dashed var(--line);margin-top:4px" data-toggle="${cardId}">▼ 展开 ${expandRows.length} 项</div>` : ''}
    </div>`;
  }).join('');
  // toggle handler
  cardsDom.querySelectorAll('[data-toggle]').forEach(el => {
    el.addEventListener('click', () => {
      const targetId = el.getAttribute('data-toggle') + '-expand';
      const exp = document.getElementById(targetId);
      if (!exp) return;
      const show = !exp.classList.contains('show');
      exp.classList.toggle('show', show);
      el.textContent = show ? '▲ 收起' : `▼ 展开 ${exp.querySelectorAll('.alc-expand-row').length} 项`;
    });
  });
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
  _safeDisposeECharts(echartsCharts.flow); echartsCharts.flow = null;
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
      { name: '超大单',   type: 'bar', data: history.map(h => h.super_net), itemStyle: { color: 'var(--accent-3)' } },
      { name: '大单',     type: 'bar', data: history.map(h => h.big_net),   itemStyle: { color: 'var(--accent-2)' } },
      { name: '中单',     type: 'bar', data: history.map(h => h.mid_net),   itemStyle: { color: 'var(--ink-3)' } },
      { name: '小单',     type: 'bar', data: history.map(h => h.small_net), itemStyle: { color: 'var(--ink-4)' } },
    ],
  });
  chart.resize();
}

// ──────────────────────────────────────────────────────────────
// K线状态 + 周期切换 + 指标计算 (MACD / KDJ / BOLL)
// ──────────────────────────────────────────────────────────────
var klineState = {
  // 2026-08-09: 周期从数字(days) 改为字符串 ("1m"|"5m"|"d"|"w"|"m") — 支持周/月/分钟
  period: 'd',               // 当前显示周期 · 默认 日
  days: 120,                  // 范围 (天) — period=d/w/m 时控制返回根数
  adjust: 'qfq',              // 复权: qfq|hfq|none
  indicators: { ma: true, macd: false, kdj: false, boll: false },
  data: [],                   // 当前缓存的 kline
  loading: false,
  // 2026-08-09: 无限延伸 — 拉过最老日期, 拖到左沿时自动追加
  oldestLoadedDate: null,     // 字符串 "YYYY-MM-DD" (周/月/日) 或 "YYYY-MM-DD HH:MM" (分钟)
  inflightOlder: false,       // 防抖锁
  inflightNewer: false,
};
// R-fix-2026-07-16: 周期 + 指标持久化到 localStorage,用户改了之后切股票不重置
(function _loadKlinePrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('tuixue_kline_prefs') || '{}');
    if (p && typeof p.period === 'string' && ['1m','5m','d','w','m'].includes(p.period)) {
      klineState.period = p.period;
    }
    if (p && typeof p.days === 'number' && [22, 66, 120, 132, 250, 400, 500, 1200, 2500].includes(p.days)) {
      klineState.days = p.days;
    } else if (p && typeof p.period === 'number') {
      // 旧版 days 持久化 — 数字 → 走 d + 对应 days
      klineState.period = 'd';
      klineState.days = p.period;
    }
    if (p && typeof p.adjust === 'string' && ['qfq','hfq','none'].includes(p.adjust)) {
      klineState.adjust = p.adjust;
    }
    if (p && p.indicators && typeof p.indicators === 'object') {
      Object.assign(klineState.indicators, p.indicators);
      // 互斥保证: macd 和 kdj 不能同时 true
      if (klineState.indicators.macd && klineState.indicators.kdj) klineState.indicators.kdj = false;
    }
  } catch (_) {}
})();
function _saveKlinePrefs() {
  try {
    localStorage.setItem('tuixue_kline_prefs', JSON.stringify({
      period: klineState.period,
      days: klineState.days,
      adjust: klineState.adjust,
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
    const rsv = h9 > l9 ? ((closes[i] - l9) / (h9 - l9)) * 100 : (closes[i] >= h9 ? 100 : 0);
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
    if (i < n - 1 || mid[i] == null) { upper.push(null); lower.push(null); continue; }
    let sum = 0;
    for (let p = i - n + 1; p <= i; p++) sum += (closes[p] - mid[i]) ** 2;
    const std = Math.sqrt(sum / n);
    upper.push(+(mid[i] + k * std).toFixed(3));
    lower.push(+(mid[i] - k * std).toFixed(3));
  }
  return { mid, upper, lower };
}

// R-fix-2026-08-09: 非 loadKline 路径 (如 /full 内嵌 K 线、streak 兜底) 也要同步
// oldestLoadedDate/_klineAtStart — 否则拖到最左时 dataZoom 处理器的 prepend 条件
// (oldestLoadedDate 为真) 永远不满足 → "左右滑动数据不会重新加载"
function _syncKlineExtents() {
  if (klineState.data && klineState.data.length) {
    const f = klineState.data[0];
    klineState.oldestLoadedDate = f.time ? `${f.date} ${f.time}` : f.date;
  }
  klineState._klineAtStart = false;
}

// 加载 K 线 (按周期)
// R4: 多并发 dedup — 同一 (code,days) 同时被调用多次,只发 1 个请求
var _klineInflight = {};
// R-fix 2026-07-26: 30 日 K 线兜底 — renderStreak10d 在原数据 <11 条时调用
var _streakKlineFallbackInflight = {};
async function _streakKlineFallback(code) {
  const key = code + ':30';
  if (_streakKlineFallbackInflight[key]) return _streakKlineFallbackInflight[key];
  const p = (async () => {
    try {
      const data = await api(`/api/stock/${code}/kline?days=30`, { timeout: 6000, priority: 'high' });
      return (data && data.kline) || [];
    } catch (e) {
      return [];
    } finally {
      setTimeout(() => { if (_streakKlineFallbackInflight[key] === p) delete _streakKlineFallbackInflight[key]; }, 800);
    }
  })();
  _streakKlineFallbackInflight[key] = p;
  return p;
}
async function loadKline(code, opts) {
  // 2026-08-09: opts 兼容 (code, days) 旧调用 — 改 (code, {days, period, adjust, before})
  let days, period, adjust, before;
  if (typeof opts === 'number') {
    days = opts;
    period = klineState.period;
    adjust = klineState.adjust;
  } else {
    days = opts?.days ?? klineState.days;
    period = opts?.period ?? klineState.period;
    adjust = opts?.adjust ?? klineState.adjust;
    before = opts?.before;
  }
  if (code !== window._currentStockCode) return Promise.resolve();
  const key = code + ':' + period + ':' + adjust + ':' + days + ':' + (before || '');
  if (_klineInflight[key]) return _klineInflight[key];
  const dom = $('#kline-chart');
  if (dom) dom.dataset.loading = '1';
  const p = (async () => {
    try {
      let url = `/api/stock/${code}/kline?period=${period}&adjust=${adjust}&days=${days}`;
      if (before) url += `&before=${encodeURIComponent(before)}`;
      const data = await api(url, { signal: _stockSignal() });
      if (code !== window._currentStockCode) return;
      const newRows = data.kline || [];
      if (before) {
        // 追加到老数据前面 (无限向左延伸)
        const seen = new Set(klineState.data.map(k => k.date + (k.time || '')));
        const older = newRows.filter(k => !seen.has(k.date + (k.time || '')));
        // R-fix-2026-08-09: 记录本次 prepend 了多少条 → drawKlineChart 重建后
        // 用 startValue+off 恢复窗口,避免用户视觉上看到"数据没动"
        klineState._olderAppended = older.length;
        klineState.data = older.concat(klineState.data);
        // 2026-08-09: 历史拉尽时打标记 — dataZoom 拖到最左不再空转请求
        // R3.3: _degraded (上游超时/缓存兜底) ≠ 历史拉尽 — 否则失败一次永久锁死 prepend
        if (data._degraded) {
          klineState._klineAtStart = false;
          if (!older.length) toast('更早 K 线暂不可用（上游超时），稍后再试', 'error');
        } else {
          klineState._klineAtStart = older.length === 0;
        }
        if (older.length && klineState.data.length) {
          const f = klineState.data[0];
          klineState.oldestLoadedDate = f.time ? `${f.date} ${f.time}` : f.date;
        }
      } else {
        klineState._olderAppended = 0;
        klineState._klineAtStart = false;
        klineState.data = newRows;
        if (newRows.length) {
          const f = newRows[0];
          klineState.oldestLoadedDate = f.time ? `${f.date} ${f.time}` : f.date;
        }
      }
      klineState.period = period;
      klineState.days = days;
      klineState.adjust = adjust;
      syncKlineToolbar();
      _klineDataReady = true;
      const klinePane = dom?.closest('[data-tab-pane]');
      if (klinePane && !klinePane.hidden && _klineDataReady) {
        drawKlineChart();
      }
      renderKlineKpi(klineState.data);
      renderHeroSparkline(klineState.data, $('#q-price')?.textContent ? Number($('#q-price')?.textContent) : null);
      renderStreak10d(klineState.data);
    } catch (e) {
      toast(`K线加载失败：${e.message}`, 'error');
      if (!before) {
        klineState.data = [];
        _klineDataReady = true;
        _klineChartDrawn = false;
      }
    } finally {
      if (dom) delete dom.dataset.loading;
      setTimeout(() => { if (_klineInflight[key] === p) delete _klineInflight[key]; }, 500);
    }
  })();
  _klineInflight[key] = p;
  return p;
}

async function drawKlineChart() {
  const dom = $('#kline-chart');
  if (!dom) return;
  // R6: 抢占式 token — 切 tab 频繁触发时,旧的 await 完成后不再覆盖新 chart
  const tk = _newChartToken('kline');
  // R-fix-2026-08-09: 保留旧 dataZoom 窗口 — 用户拖到中段时,加载更老数据
  // (loadKline before=) 会 dispose+重建 → 默认 start=0/end=100 → 视觉上"数据没动"
  // 实际是窗口被重置。先抓 (startValue/endValue 或 percent),重建后用 dispatchAction 恢复。
  const _prevDz = (() => {
    try {
      const c = echartsCharts.kline;
      if (!c) return null;
      const z = (c.getOption().dataZoom || [])[0];
      if (!z) return null;
      if (z.startValue != null || z.endValue != null) {
        return { sv: z.startValue ?? 0, ev: z.endValue ?? null };
      }
      return { sp: z.start ?? 0, ep: z.end ?? 100 };
    } catch (e) { return null; }
  })();
  _safeDisposeECharts(echartsCharts.kline); echartsCharts.kline = null;
  await _ensureECharts();
  if (_isChartTokenStale('kline', tk)) return;  // 新一轮已启动,放弃
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.kline = chart;
  _klineChartDrawn = true;
  let kline = klineState.data;
  if (!kline || !kline.length) {
    // R-fix 2026-07-26: 空数据兜底 — 主动拉 30 日 K 线 (走 /api/stock/{code}/kline,后端已 pre_cache 优先 + stale 兜底)
    const codeEmpty = window._currentStockCode || window.currentStockCode;
    if (codeEmpty) {
      const got = await _streakKlineFallback(codeEmpty);
      if (got && got.length && window._currentStockCode === codeEmpty) {
        klineState.data = got;
        _syncKlineExtents();
        kline = got;
        renderKlineKpi(kline);
        renderStreak10d(kline);
      }
    }
    if (!kline || !kline.length) {
      try { chart.setOption(emptyChartOption('暂无 K 线数据')); chart.resize(); } catch (e) {}
      return;
    }
  }
  const ind = klineState.indicators;
  const dates = kline.map(k => k.date);
  const _labelBg = document.documentElement.getAttribute("data-theme") === "light" ? "rgba(255,255,255,0.85)" : "rgba(10,9,8,0.6)";
  const ohlc = kline.map(k => [+k.open, +k.close, +k.low, +k.high]);
  const closes = kline.map(k => +k.close);
  const highs  = kline.map(k => +k.high);
  const lows   = kline.map(k => +k.low);
  // 全量检查: 服务器返回的 ma5/10/20 可能只有部分条目有,缺值则客户端自行计算
  const hasAll = (f) => kline.every(k => k[f] != null);
  const ma5  = hasAll('ma5')  ? kline.map(k => k.ma5)  : ma(closes, 5);
  const ma10 = hasAll('ma10') ? kline.map(k => k.ma10) : ma(closes, 10);
  const ma20 = hasAll('ma20') ? kline.map(k => k.ma20) : ma(closes, 20);
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
  // R-fix-2026-08-09: 不再用 全期间 极值锁死 y 轴 (会让"最低最高没法动" —
  // dataZoom 切到任意子区间时,左/右轴仍是全期间极值,体感"卡住不动";
  // 而且锁死 min/max 时,低位 bar 会贴轴/被裁掉)。
  // 改 min: 'dataMin'/max: 'dataMax' 让 ECharts 按 dataZoom 可见区间自动 fit。
  const yAxes = [
    // 主图左轴:价格 — 按可见区间 fit,留 5% 内边距避免 bar 贴轴
    { type: 'value', gridIndex: 0, position: 'left',
      min: 'dataMin', max: 'dataMax', scale: true, splitNumber: 6,
      splitLine: { lineStyle: { color: GRID } },
      axisLabel: { color: INK2, fontSize: 10 } },
    // 主图右轴:百分比 (相对 baseClose=期间首日收盘价) —
    // 同样按可见区间 fit,formatter 仍以 baseClose 算 %
    { type: 'value', gridIndex: 0, position: 'right',
      min: 'dataMin', max: 'dataMax', scale: true,
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
      axisLabel: { color: INK3, fontSize: 9, formatter: v => (v/1e4).toFixed(0)+'万手' },
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
            color: 'var(--bg-1)', fontSize: 10, fontWeight: 700,
            backgroundColor: lastColor, padding: [2, 6], borderRadius: 3,
            distance: 4,
          },
        }];
      })(),
    },
    // 2026-08-03: 模拟盘买卖点标记 (B 买入 / S 卖出),读 sessionStorage.paper_markers
    markPoint: (() => {
      const codeNow = window._currentStockCode || window.currentStockCode;
      if (!codeNow) return { data: [], symbol: 'pin', symbolSize: 36 };
      let markers = null;
      try { markers = JSON.parse(sessionStorage.getItem('paper_markers') || '{}'); } catch (_) {}
      const m = markers && markers[codeNow];
      if (!m) return { data: [], symbol: 'pin', symbolSize: 36 };
      const idxB = dates.indexOf(_paperDateNorm(m.buy_date));
      const idxS = m.sell_date ? dates.indexOf(_paperDateNorm(m.sell_date)) : -1;
      const pts = [];
      if (idxB >= 0 && m.buy_price > 0) {
        pts.push({
          name: 'B', coord: [idxB, +m.buy_price],
          symbol: 'pin', symbolSize: 38,
          itemStyle: { color: '#ff5252' },
          label: {
            show: true, formatter: 'B', color: '#fff', fontSize: 11, fontWeight: 700,
            offset: [0, -10], backgroundColor: '#ff5252',
            borderRadius: 4, padding: [1, 5],
          },
        });
      }
      if (idxS >= 0 && m.sell_price > 0) {
        pts.push({
          name: 'S', coord: [idxS, +m.sell_price],
          symbol: 'pin', symbolSize: 38,
          itemStyle: { color: '#26a69a' },
          label: {
            show: true, formatter: 'S', color: '#fff', fontSize: 11, fontWeight: 700,
            offset: [0, 14], backgroundColor: '#26a69a',
            borderRadius: 4, padding: [1, 5],
          },
        });
      }
      return { data: pts, symbol: 'pin', symbolSize: 38, animation: false };
    })(),
  });
  // MA 叠加（主图）— 2026-07-17: 加 endLabel 把 MA10/20/60 名直接打在每条线的右端,不占顶部空间
  if (ind.ma) {
    const lastIdx = ma10.length - 1;
    series.push({ name: 'MA10', type: 'line', data: ma10, smooth: true, lineStyle: { color: 'var(--accent-2)', width: 1 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA10', color: 'var(--accent-2)', fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: _labelBg } });
    series.push({ name: 'MA20', type: 'line', data: ma20, smooth: true, lineStyle: { color: ACCENT,  width: 1.2 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA20', color: ACCENT, fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: _labelBg } });
    series.push({ name: 'MA60', type: 'line', data: ma60, smooth: true, lineStyle: { color: 'var(--accent-3)', width: 1.2 }, symbol: 'none', connectNulls: true,
      endLabel: { show: true, formatter: 'MA60', color: 'var(--accent-3)', fontSize: 10, fontWeight: 600, padding: [0, 0, 0, 4], backgroundColor: _labelBg } });
  }
  // BOLL 叠加（主图）
  if (boll) {
    series.push({ name: 'BOLL上', type: 'line', data: boll.upper, smooth: true, lineStyle: { color: 'var(--accent)', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL中', type: 'line', data: boll.mid,   smooth: true, lineStyle: { color: 'var(--accent)', width: 0.8, opacity: 0.7 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'BOLL下', type: 'line', data: boll.lower, smooth: true, lineStyle: { color: 'var(--accent)', width: 0.8, opacity: 0.7, type: 'dashed' }, symbol: 'none', connectNulls: true });
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
      lineStyle: { color: 'var(--accent)', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'DEA', type: 'line', data: macdData.dea, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: 'var(--warn)', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'MACD', type: 'bar', data: macdData.hist.map(v => v == null ? 0 : v), xAxisIndex: 2, yAxisIndex: 2,
      barWidth: '50%',
      itemStyle: { color: p => (p.value >= 0 ? UP : DOWN) } });
  }
  // KDJ 副图（grid 2）
  if (subIndicator === 'kdj' && kdjData) {
    series.push({ name: 'K', type: 'line', data: kdjData.k, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: 'var(--accent)', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'D', type: 'line', data: kdjData.d, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: 'var(--warn)', width: 1 }, symbol: 'none', connectNulls: true });
    series.push({ name: 'J', type: 'line', data: kdjData.j, smooth: true, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: 'var(--accent-3)', width: 1 }, symbol: 'none', connectNulls: true });
    // KDJ 80/20 参考线 (放在 series 上而非 yAxis — markLine 不支持 yAxis 级)
    series.push({ name: 'KDJ_REF', type: 'line', data: [], xAxisIndex: 2, yAxisIndex: 2,
      markLine: {
        silent: true, symbol: 'none',
        data: [
          { yAxis: 80, label: { show: false }, lineStyle: { color: 'var(--ink-4)', type: 'dotted', width: 0.8 } },
          { yAxis: 20, label: { show: false }, lineStyle: { color: 'var(--ink-4)', type: 'dotted', width: 0.8 } },
        ],
      },
    });
  }

  // ── Tooltip ── THS 风格精确读数
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
  try { chart.setOption(option, { notMerge: false }); } catch (e) { console.warn('Kline setOption:', e); }
  try { chart.resize(); } catch (e) { console.warn('Kline resize:', e); }

  // R-fix-2026-08-09: 重建后恢复 dataZoom 窗口 — 否则用户拖到中段后加载
  // 更老数据会"窗口重置 → 数据没动 → 体感卡住"。percent 模式直接还原;
  // value 模式需把旧 startValue 加回前面追加的行数 (klineState._olderAppended)。
  if (_prevDz) {
    try {
      if (_prevDz.sv != null) {
        // value 模式:旧窗口 = [oldSv, oldEv],往前 prepend 了 _olderAppended 条
        const off = klineState._olderAppended || 0;
        chart.dispatchAction({
          type: 'dataZoom', xAxisIndex: hasSub ? [0,1,2] : [0,1],
          startValue: _prevDz.sv + off,
          endValue: (_prevDz.ev == null ? null : _prevDz.ev + off),
        });
      } else {
        chart.dispatchAction({
          type: 'dataZoom', xAxisIndex: hasSub ? [0,1,2] : [0,1],
          start: _prevDz.sp, end: _prevDz.ep,
        });
      }
    } catch (e) { console.debug('restore dataZoom:', e.message); }
    klineState._olderAppended = 0;  // 消费完清零
  }

  // ── 点击 K线柱子 → 切到分时 tab + 加载该日 (2026-07-15) ──
  chart.off('click');
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
  chart.off('updateAxisPointer');
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

  // ── 2026-08-09: 无限延伸 (TradingView 风格) — 拖到左沿自动拉更老数据 ──
  chart.off('dataZoom');
  let _dzDebounce = null;
  chart.on('dataZoom', (ev) => {
    if (_dzDebounce) clearTimeout(_dzDebounce);
    _dzDebounce = setTimeout(() => {
      if (!currentStockCode) return;
      const opt = chart.getOption();
      const z = opt && opt.dataZoom && opt.dataZoom[0];
      if (!z) return;
      // startValue/endValue 是数据 index; 缺省走 percent (0-100)
      const startPct = z.startValue != null ? (z.startValue / Math.max(1, klineState.data.length - 1)) * 100 : (z.start || 0);
      const endPct = z.endValue != null ? (z.endValue / Math.max(1, klineState.data.length - 1)) * 100 : (z.end || 100);
      if (startPct < 5 && !klineState.inflightOlder && klineState.oldestLoadedDate && !klineState._klineAtStart) {
        klineState.inflightOlder = true;
        const before = klineState.oldestLoadedDate;
        loadKline(currentStockCode, {
          days: Math.min(klineState.days * 2, 2500),
          period: klineState.period, adjust: klineState.adjust, before,
        }).finally(() => { klineState.inflightOlder = false; });
      } else if (endPct > 95 && !klineState.inflightNewer && klineState.period !== '1m' && klineState.period !== '5m') {
        klineState.inflightNewer = true;
        loadKline(currentStockCode, {
          days: klineState.days, period: klineState.period, adjust: klineState.adjust,
        }).finally(() => { klineState.inflightNewer = false; });
      }
    }, 280);
  });
}

// 同步工具栏高亮态 (周期 + 范围 + 复权 + 指标)
function syncKlineToolbar() {
  $$('#kline-period .kt-pill').forEach(btn => {
    // 兼容: 老 pill 用 data-days, 新 pill 用 data-period (默认 "d")
    const isOld = btn.dataset.days != null && btn.dataset.period == null;
    const active = isOld
      ? (+btn.dataset.days === klineState.days && klineState.period === 'd')
      : (btn.dataset.period === klineState.period);
    btn.classList.toggle('active', active);
  });
  $$('#kline-range .kt-pill').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.days === klineState.days);
  });
  $$('#kline-adjust .kt-pill').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.fqt === klineState.adjust);
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
    out.push(i >= n - 1 ? +(sum / n).toFixed(2) : null);
  }
  return out;
}

// R22: 渐进式 seats 二次拉 — /full 经常给空 (冷取 17s 超 1.5s 预算), 落到独立端点
// 命中进程内 L0 10min 缓存 <1ms 即可填上, 否则用陈旧快照 <50ms 兜底
async function _refetchSeatsProgressive(code) {
  const r = await api(`/api/stock/${code}/seats?days=30`, {
    signal: _stockSignal(), silent: true,
  });
  if (!r || !(r.rows || []).length) return;
  if (window._currentStockCode !== code) return;  // 切股了, 丢弃
  // 合并后渲染 — 同 (code) 时用最新结果覆盖
  renderSeatsTable(r.rows, r);
  // 同步更新 hero 角标 #q-seats (默认渲染用旧空 seats 已经写成 "0 条")
  const qSeats = document.querySelector('#q-seats');
  if (qSeats) {
    qSeats.innerHTML = `${r.seat_count || 0}<span class="qc-unit">条</span>`;
  }
  const qSub = document.querySelector('#q-seats-sub');
  if (qSub) {
    qSub.textContent = r.blacklisted
      ? `近 ${r.total_lhb_rows || 0} · 黑名单`
      : `近 ${r.total_lhb_rows || 0} 日`;
  }
}

function renderSeatsTable(rows, seats) {
  const tbody = $('#seats-table tbody');
  if (seats) renderSeatsKpi(seats);
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">近 30 日无龙虎席位</td></tr>';
    const portrait = $('#seats-portrait'); if (portrait) portrait.hidden = true;
    const correl = $('#seats-correl'); if (correl) correl.hidden = true;
    const nf = $('#seats-netflow'); if (nf) nf.hidden = true;
    return;
  }
  // R22: 龙虎榜净买趋势 — 先渲染, 记住日期过滤状态
  window._seatsRows = rows;
  renderSeatsNetflow(rows);
  const filtered = window._seatsDateFilter ? rows.filter(r => (r.date || '').indexOf(window._seatsDateFilter) === 0) : rows;
  const buyRows = filtered.filter(r => (r.direction || '').includes('买'));
  const sellRows = filtered.filter(r => (r.direction || '').includes('卖'));
  let html = '';
  if (buyRows.length) {
    html += `<tr><td colspan="6" class="dim" style="text-align:left;padding:8px 0 4px;background:transparent;border:none">▼ 买入席位 (${buyRows.length})</td></tr>`;
    html += buyRows.map(renderSeatRow).join('');
  }
  if (sellRows.length) {
    html += `<tr><td colspan="6" class="dim" style="text-align:left;padding:8px 0 4px;background:transparent;border:none">▲ 卖出席位 (${sellRows.length})</td></tr>`;
    html += sellRows.map(renderSeatRow).join('');
  }
  if (!filtered.length) {
    html = '<tr><td colspan="6" class="empty">该日无席位明细</td></tr>';
  }
  tbody.innerHTML = html;
  renderSeatsPortrait(filtered);
  renderSeatsCorrel(filtered);
}

// R22: 龙虎榜净买趋势 — 按日期双向条形 (绿买/红卖), 点击日期过滤席位表
function renderSeatsNetflow(rows) {
  const host = $('#seats-netflow');
  const body = $('#seats-netflow-body');
  if (!host || !body) return;
  const byDate = new Map();
  for (const r of rows) {
    if (!r.date) continue;
    if (!byDate.has(r.date)) byDate.set(r.date, { buy: 0, sell: 0 });
    const g = byDate.get(r.date);
    const amt = r.amount_wan || 0;
    if ((r.direction || '').includes('买')) g.buy += amt;
    else if ((r.direction || '').includes('卖')) g.sell += amt;
  }
  const dates = [...byDate.keys()].sort().reverse().slice(0, 12);
  if (!dates.length) { host.hidden = true; return; }
  const maxSide = Math.max(...dates.map(d => Math.max(byDate.get(d).buy, byDate.get(d).sell)), 1);
  const active = window._seatsDateFilter;
  body.innerHTML = dates.map(d => {
    const g = byDate.get(d);
    const net = g.buy - g.sell;
    const sw = Math.round(g.sell / maxSide * 50);
    const bw = Math.round(g.buy / maxSide * 50);
    const isActive = active && d.indexOf(active) === 0;
    return `<div class="snf-row${isActive ? ' active' : ''}" data-date="${d}" title="${d} · 买 ${fmtN(g.buy, 0)} 万 / 卖 ${fmtN(g.sell, 0)} 万">
      <span class="snf-date">${d.slice(5)}</span>
      <div class="snf-track">
        <div class="snf-half snf-left"><span class="snf-seg snf-sell" style="width:${sw}%"></span></div>
        <div class="snf-axis"></div>
        <div class="snf-half snf-right"><span class="snf-seg snf-buy" style="width:${bw}%"></span></div>
      </div>
      <span class="snf-net" style="color:${net >= 0 ? UP : DOWN}">${net >= 0 ? '+' : ''}${fmtN(net, 0)}</span>
    </div>`;
  }).join('');
  host.hidden = false;
}

// R22: 龙虎榜日期点击 — 过滤席位表 (再点同一日期取消)
document.addEventListener('click', (e) => {
  const row = e.target.closest('.snf-row[data-date]');
  if (!row) return;
  const d = row.dataset.date;
  if (window._seatsDateFilter === d) window._seatsDateFilter = '';
  else window._seatsDateFilter = d;
  renderSeatsNetflow(window._seatsRows || []);
  renderSeatsTable(window._seatsRows || [], null);
});

// R3 Round 3: 席位画像
function renderSeatsPortrait(rows) {
  const host = $('#seats-portrait');
  if (!host) return;
  const byLabel = new Map();
  for (const r of rows) {
    const key = (r.label || r.real_name || r.seat || '未知').trim();
    if (!key || key === '—') continue;
    if (!byLabel.has(key)) {
      byLabel.set(key, {
        label: key, tier: r.tier || '', group: r.group || '',
        count: 0, buy_amt: 0, sell_amt: 0, firstDate: r.date, lastDate: r.date,
      });
    }
    const g = byLabel.get(key);
    g.count++;
    const amt = r.amount_wan || 0;
    if ((r.direction || '').includes('买')) g.buy_amt += amt;
    else if ((r.direction || '').includes('卖')) g.sell_amt += amt;
    if (r.date && r.date < g.firstDate) g.firstDate = r.date;
    if (r.date && r.date > g.lastDate) g.lastDate = r.date;
  }
  const portraits = [...byLabel.values()]
    .filter(g => g.count >= 1)
    .sort((a, b) => (b.buy_amt + b.sell_amt) - (a.buy_amt + a.sell_amt))
    .slice(0, 8);
  if (!portraits.length) { host.hidden = true; return; }
  host.hidden = false;
  const tbody = $('#seats-portrait-body');
  // R22: 移除假随机 胜率/盈亏比 → 用真实买卖净额派生的 买入占比/净买额 (数据诚实)
  tbody.innerHTML = portraits.map(p => {
    const total = p.buy_amt + p.sell_amt;
    const hasAmt = total > 0;
    const buyRatio = hasAmt ? p.buy_amt / total : null;
    const netAmt = p.buy_amt - p.sell_amt;
    const mode = !hasAmt ? '—'
      : (p.tier === '顶级游资' || p.tier === '活跃游资')
        ? (buyRatio > 0.7 ? '打板型' : buyRatio > 0.4 ? 'T+1 一日游' : '短线出货')
        : (p.tier === '机构席位' ? '趋势型' : '论坛 ID');
    const buyCell = hasAmt
      ? `<td class="num" style="color:${buyRatio >= 0.6 ? UP : buyRatio >= 0.4 ? INK2 : DOWN}">${(buyRatio * 100).toFixed(0)}%</td>`
      : '<td class="num dim" title="无金额数据">—</td>';
    const netCell = hasAmt
      ? `<td class="num" style="color:${colorFor(netAmt)}">${netAmt >= 0 ? '+' : ''}${fmtN(netAmt, 0)}</td>`
      : '<td class="num dim">—</td>';
    return `<tr>
      <td><b>${escapeHtml(p.label)}</b></td>
      <td>${p.tier ? `<span class="badge badge-tier-${escapeHtml(p.tier)}">${escapeHtml(p.tier)}</span>` : (p.group || '—')}</td>
      <td class="num">${p.count}</td>
      ${buyCell}
      ${netCell}
      <td>${mode}</td>
      <td class="num">${fmtN(total, 0)} 万</td>
    </tr>`;
  }).join('');
}

// R3 Round 3: 协同度 / 关联股
function renderSeatsCorrel(rows) {
  const host = $('#seats-correl');
  if (!host) return;
  const byLabel = new Map();
  for (const r of rows) {
    const key = (r.label || r.real_name || r.seat || '').trim();
    if (!key || key === '—') continue;
    if (!byLabel.has(key)) byLabel.set(key, { label: key, count: 0, total: 0, lastDate: r.date, tier: r.tier });
    const g = byLabel.get(key);
    g.count++;
    g.total += r.amount_wan || 0;
    if (r.date && r.date > g.lastDate) g.lastDate = r.date;
  }
  const co = [...byLabel.values()]
    .filter(g => g.count >= 2)
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);
  if (!co.length) { host.hidden = true; return; }
  host.hidden = false;
  const tbody = $('#seats-correl-body');
  tbody.innerHTML = co.map(g => `<tr>
    <td><span class="badge badge-good">协同</span></td>
    <td><b>${escapeHtml(g.label)}</b>${g.tier ? ` <span class="badge badge-tier-${escapeHtml(g.tier)}">${escapeHtml(g.tier)}</span>` : ''}</td>
    <td class="num">${g.count}</td>
    <td class="num">${fmtN(g.total, 0)} 万</td>
    <td>${g.lastDate || '—'}</td>
  </tr>`).join('');
}

// R32: 游资足迹 · 席位关联个股 — 该股龙虎榜席位近 N 日还操作了哪些股
async function loadSeatsRelated(code) {
  const host = $('#seats-related');
  if (!host) return;
  if (!window._currentStockCode || window._currentStockCode !== code) return;
  const body = $('#seats-related-body');
  if (body) body.innerHTML = '<div class="dim" style="padding:.5rem 0">加载游资足迹…</div>';
  let d;
  try {
    d = await api(`/api/stock/${code}/seat_related`, { signal: _stockSignal() });
  } catch (e) {
    if (body && host) { host.hidden = true; }
    return;
  }
  if (window._currentStockCode !== code) return;
  renderSeatsRelated(d || {});
}

function renderSeatsRelated(d) {
  const host = $('#seats-related');
  if (!host) return;
  const seats = d.seats || [];
  if (!seats.length) { host.hidden = true; return; }
  host.hidden = false;
  const body = $('#seats-related-body');
  body.innerHTML = seats.map(s => {
    const tierBadge = s.tier ? `<span class="badge badge-tier-${escapeHtml(s.tier)}">${escapeHtml(s.tier)}</span>`
      : (s.group ? `<span class="badge badge-good">${escapeHtml(s.group)}</span>` : '');
    const name = s.label || (s.real_name ? s.real_name + ' 系' : '');
    const netCls = s.net_wan >= 0 ? 'pct-up' : 'pct-down';
    const netStr = fmtWan(s.net_wan, 1);
    const rel = s.related || [];
    const chips = rel.slice(0, 8).map(r => r.code
      ? `<a href="#" class="srl-chip" data-code="${escapeHtml(r.code)}" title="${escapeHtml(r.name)} · ${r.date || ''}">${escapeHtml(r.name)}</a>`
      : `<span class="srl-chip dim" title="${escapeHtml(r.name)} · ${r.date || ''}">${escapeHtml(r.name)}</span>`).join('');
    const more = rel.length > 8 ? `<span class="caption dim">+${rel.length - 8} 只</span>` : '';
    return `<div class="srl-item">
      <div class="srl-head">
        <b>${escapeHtml(name || s.seat)}</b>
        ${tierBadge}
        <span class="srl-net ${netCls}">${netStr}</span>
        <span class="caption dim">近 ${rel.length} 只关联 · 最近 ${s.last_date || '—'} · 买${s.buy_cnt}股/卖${s.sell_cnt}股</span>
      </div>
      <div class="srl-chips">${chips}${more}</div>
    </div>`;
  }).join('');
}

// 关联股 chip 跳转 (与 stock-link 同款事件委托)
document.addEventListener('click', (e) => {
  const chip = e.target.closest('.srl-chip[data-code]');
  if (!chip) return;
  e.preventDefault();
  const c = chip.dataset.code;
  const inp = $('#stock-search'); if (inp) inp.value = c;
  if (typeof showView === 'function') showView('stock');
  if (typeof loadStockDetail === 'function') loadStockDetail(c);
});

function renderHolders(holders) {
  _currentHolders = holders;  // R3 Round 3: 供持仓结构双图使用 (null 时图显示样本不足)
  const tbody = $('#holders-table tbody');
  if (!holders || !holders.holder_total) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">无最新季报数据</td></tr>';
    if ($('#holders-kpi')) $('#holders-kpi').innerHTML = '<div class="metric"><span class="m-num">—</span><span class="m-unit">暂无</span></div>';
    const extra = $('#holders-extra'); if (extra) extra.hidden = true;
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
  // R4 Round 4: 5 年分位 + 健康度 + 十大股东
  renderHoldersExtra(holders);
  renderHoldersAlerts(holders);
}

// R23: 筹码异动信号带 — 派发/吸筹评级 + 季报披露倒计时 + 户均市值 + 户数趋势 spark
function renderHoldersAlerts(holders) {
  const wrap = $('#holders-alerts');
  if (!wrap) return;
  const hist = (holders.history || []).slice().reverse();  // 旧→新
  if (!hist.length) { wrap.hidden = true; return; }
  wrap.hidden = false;
  const latest = hist[hist.length - 1];
  const prev = hist.length >= 2 ? hist[hist.length - 2] : null;
  const curTotal = latest.holder_total || 0;
  const prevTotal = prev ? (prev.holder_total || 0) : 0;

  // 1) 筹码异动 — 户数↑+价跌 = 派发 / 户数↓+价升 = 吸筹 / 其他 = 中性
  const price = parseFloat((window._currentQuote || {}).price || 0);
  const changePct = parseFloat((window._currentQuote || {}).change_pct || 0);
  // 近似: 户数变化率 vs 5d 涨跌
  const holderChg = prevTotal > 0 ? (curTotal - prevTotal) / prevTotal : 0;
  const sigCell = $('#ha-signal');
  const sigVal = $('#ha-signal-val');
  const sigSub = $('#ha-signal-sub');
  sigCell.classList.remove('alert-up', 'alert-down');
  let label = '中性', color = 'var(--ink-2)', detail = '';
  // 派发: 户数大涨 (≥+15%) 且 价跌 / 横盘 — 大资金派发筹码给散户
  if (holderChg > 0.15 && changePct <= 0) {
    label = '派发预警'; color = 'var(--down)'; detail = `户数 +${(holderChg * 100).toFixed(1)}% / 价 ${changePct.toFixed(2)}%`;
    sigCell.classList.add('alert-down');
  } else if (holderChg < -0.10 && changePct >= 0) {
    label = '吸筹迹象'; color = 'var(--up)'; detail = `户数 ${(holderChg * 100).toFixed(1)}% / 价 +${changePct.toFixed(2)}%`;
    sigCell.classList.add('alert-up');
  } else if (holderChg < -0.10) {
    label = '筹码集中'; color = 'var(--up)'; detail = `户数 ${(holderChg * 100).toFixed(1)}% (户均↑)`;
    sigCell.classList.add('alert-up');
  } else if (holderChg > 0.15) {
    label = '筹码松散'; color = 'var(--down)'; detail = `户数 +${(holderChg * 100).toFixed(1)}% (户均↓)`;
    sigCell.classList.add('alert-down');
  } else {
    label = '中性'; color = 'var(--ink-2)';
    detail = holderChg !== 0 ? `户数 ${holderChg > 0 ? '+' : ''}${(holderChg * 100).toFixed(1)}%` : '无显著异动';
  }
  sigVal.textContent = label;
  sigVal.style.color = color;
  sigSub.textContent = detail;

  // 2) 季报披露倒计时 — A 股年报 4/30, 一季报 4/30, 半年报 8/31, 三季报 10/31
  // 最新报告期决定下一个披露节点
  const today = new Date();
  const reportDate = (latest.report_date || '').slice(0, 10);
  const disclosed = reportDate ? new Date(reportDate) : null;
  const nextDiscl = _nextDisclosureDate(disclosed, today);
  if (nextDiscl) {
    const days = Math.ceil((nextDiscl - today) / 86400000);
    $('#ha-discl-val').textContent = days >= 0 ? `${days} 天` : '已披露';
    $('#ha-discl-val').style.color = days <= 14 ? 'var(--accent-2)' : 'var(--ink)';
    $('#ha-discl-sub').textContent = nextDiscl.toISOString().slice(0, 10) + (disclosed ? ` (现报 ${reportDate})` : '');
  } else {
    $('#ha-discl-val').textContent = '—';
    $('#ha-discl-sub').textContent = '';
  }

  // 3) 户均市值估算 = avg_shares × 当前价
  const avgShares = latest.avg_shares || 0;
  const avgAmtCell = $('#ha-avgamt');
  if (avgShares > 0 && price > 0) {
    const avgAmt = avgShares * price;
    const amtStr = avgAmt >= 1e8 ? (avgAmt / 1e8).toFixed(2) + ' 亿'
                  : avgAmt >= 1e4 ? (avgAmt / 1e4).toFixed(1) + ' 万'
                  : avgAmt.toFixed(0);
    $('#ha-avgamt-val').textContent = amtStr;
    $('#ha-avgamt-sub').textContent = `${avgShares.toLocaleString()} 股 × ¥${price.toFixed(2)}`;
  } else {
    $('#ha-avgamt-val').textContent = avgShares > 0 ? avgShares.toLocaleString() + ' 股' : '—';
    $('#ha-avgamt-sub').textContent = price > 0 ? `× ¥${price.toFixed(2)} (待补市值)` : '当前无价';
  }

  // 4) 户数趋势 sparkline + 极简箭头
  const series = hist.map(h => h.holder_total || 0);
  const minV = Math.min(...series), maxV = Math.max(...series);
  const span = maxV - minV || 1;
  const W = 100, H = 22;
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * W},${H - ((v - minV) / span) * H}`).join(' ');
  $('#ha-trend-val').textContent = `${series.length} 季`;
  const firstV = series[0], lastV = series[series.length - 1];
  const trendDelta = firstV > 0 ? ((lastV - firstV) / firstV) * 100 : 0;
  $('#ha-trend-sub').textContent = `${trendDelta > 0 ? '+' : ''}${trendDelta.toFixed(1)}% (${series.length} 季累计)`;
  $('#ha-trend-sub').style.color = trendDelta < 0 ? 'var(--up)' : (trendDelta > 0 ? 'var(--down)' : 'var(--ink-2)');
  // SVG spark (纯 inline, 不依赖 echarts)
  const sparkColor = trendDelta < 0 ? 'var(--up)' : (trendDelta > 0 ? 'var(--down)' : 'var(--ink-2)');
  $('#ha-trend-spark').innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:${H}px">
    <polyline points="${pts}" fill="none" stroke="${sparkColor}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

// R23: 下一个季报披露日 — A 股年报/季报披露规则
function _nextDisclosureDate(disclosed, today) {
  // disclosed 是最近一次季报截止日, 返回下一次披露截止
  if (!disclosed) return null;
  const m = disclosed.getMonth() + 1;  // 1-12
  // 季报截止 → 4/30(一季报+年报), 8/31(半年报), 10/31(三季报)
  // 简化: 一季报 3/31 截止 → 4/30 披露;半年报 6/30 → 8/31;三季报 9/30 → 10/31;年报 12/31 → 次年 4/30
  const y = disclosed.getFullYear();
  let next;
  if (m <= 3) next = new Date(y, 3, 30);          // 一季报 4/30
  else if (m <= 6) next = new Date(y, 7, 31);     // 半年报 8/31
  else if (m <= 9) next = new Date(y, 9, 31);     // 三季报 10/31
  else next = new Date(y + 1, 3, 30);             // 年报 次年 4/30
  if (next <= today) {
    // 已过 → 跳到下一个节点
    const tm = today.getMonth() + 1;
    if (tm <= 3) return new Date(today.getFullYear(), 3, 30);
    if (tm <= 6) return new Date(today.getFullYear(), 7, 31);
    if (tm <= 9) return new Date(today.getFullYear(), 9, 31);
    return new Date(today.getFullYear() + 1, 3, 30);
  }
  return next;
}

// R4 Round 4: 户数 5 年分位 + 健康度 + 十大股东（前端代理）
function renderHoldersExtra(holders) {
  const extra = $('#holders-extra');
  if (!extra) return;
  extra.hidden = false;
  // 5 年分位 = 当前 holder_total 在 history 数组中的位置
  const hist = (holders.history || []).map(h => h.holder_total).filter(Boolean);
  if (hist.length >= 2) {
    const sorted = [...hist].sort((a, b) => a - b);
    const current = holders.holder_total;
    const rank = sorted.findIndex(v => v >= current);
    const pct = Math.max(0, Math.min(100, ((rank + 1) / sorted.length) * 100));
    $('#he-bar-current').style.width = pct + '%';
    const pctLabel = pct.toFixed(0);
    $('#he-stat').textContent = `P${pctLabel} · ${current.toLocaleString()} 户 (近 ${hist.length} 季)`;
  } else {
    $('#he-bar-current').style.width = '50%';
    $('#he-stat').textContent = '样本不足';
  }
  // 健康度 = 0-100 综合评分
  const score = computeHolderHealth(holders);
  $('#he-score').textContent = score;
  $('#he-score').style.color = score >= 70 ? UP : score >= 50 ? ACCENT : DOWN;
  $('#he-sub').textContent = score >= 70 ? '筹码集中·稳定' : score >= 50 ? '一般·中性' : '松散·高波动';
  // R34: 股东类型画像 (堆叠条 + 类型 chip + 机构占比)
  renderHoldersType(holders);
  // R34: 十大流通股东 — 季报披露 (前端代理 — 真实接口在后续接入)
  const top10 = holders.top10_holders || holders.top10 || [];
  const tbody = $('#holders-top10-body');
  if (!top10.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">十大股东明细待下次季报披露接入</td></tr>';
  } else {
    tbody.innerHTML = top10.map((h, i) => {
      const chgColor = h.change === '增持' ? UP : h.change === '减持' || h.change === '退出' ? DOWN : 'var(--ink-2)';
      const pctColor = h.change_pct > 0 ? UP : h.change_pct < 0 ? DOWN : 'var(--ink-2)';
      const chgPct = (h.change_pct != null && Math.abs(h.change_pct) > 0.5)
        ? `<i style="color:${pctColor}">${h.change_pct >= 0 ? '+' : ''}${h.change_pct.toFixed(1)}%</i>`
        : `<i class="dim">—</i>`;
      const typeColor = _TYPE_COLOR[h.type] || 'var(--ink-2)';
      return `<tr>
        <td>${h.rank || (i + 1)}</td>
        <td><b>${escapeHtml(h.name || '—')}</b></td>
        <td class="num">${(h.shares_wan || 0).toLocaleString()}</td>
        <td class="num">${h.pct_free != null ? h.pct_free.toFixed(2) + ' %' : '—'}</td>
        <td><span style="color:${chgColor}">${h.change || '—'}</span> ${chgPct}</td>
        <td><span class="htype-chip" style="--chip-c:${typeColor}">${h.type || h.nature || '—'}</span></td>
      </tr>`;
    }).join('');
  }
}

// R34: 股东类型画像 — 堆叠条 + chip + 机构占比 KPI
const _TYPE_COLOR = {
  '北向/外资': '#4a8cff',
  '公募基金': '#f5826b',
  '社保基金': '#9d7cff',
  '险资':     '#36b3c2',
  '私募基金': '#ff9f43',
  'QFII':     '#3ad6a0',
  '券商':     '#ad7d3f',
  '信托':     '#c08bd0',
  '一般法人': '#94a3b8',
  '个人':     '#7a7e87',
  '其它':     '#5a5e66',
};
function renderHoldersType(holders) {
  const host = $('#holders-type');
  if (!host) return;
  const breakdown = holders.type_breakdown || {};
  const total = Object.values(breakdown).reduce((s, x) => s + (x.pct || 0), 0);
  if (!total || !Object.keys(breakdown).length) { host.hidden = true; return; }
  // 按占比排序的堆叠条
  const segs = Object.entries(breakdown)
    .filter(([k, v]) => v.pct > 0)
    .sort((a, b) => b[1].pct - a[1].pct);
  const html = segs.map(([k, v]) => {
    const w = Math.max(2, (v.pct / total) * 100);
    const color = _TYPE_COLOR[k] || '#94a3b8';
    return `<span class="htype-seg" style="width:${w}%;background:${color}" data-tip="${k} ${v.pct.toFixed(2)}% · ${v.count}个"></span>`;
  }).join('');
  const bar = $('#htype-bar'); if (bar) bar.innerHTML = html;
  // legend
  const legend = $('#htype-legend'); if (legend) {
    legend.innerHTML = segs.map(([k, v]) =>
      `<span class="htype-chip" style="--chip-c:${_TYPE_COLOR[k] || '#94a3b8'}">${k}<i>${v.pct.toFixed(1)}%</i>·${v.count}</span>`
    ).join('');
  }
  // KPI
  const instEl = $('#htype-inst');
  if (instEl) {
    instEl.textContent = (holders.inst_free_pct != null ? holders.inst_free_pct.toFixed(1) + '%' : '—');
    instEl.style.color = (holders.inst_free_pct || 0) >= 25 ? UP : (holders.inst_free_pct || 0) >= 10 ? ACCENT : DOWN;
  }
  const fundEl = $('#htype-fund');
  if (fundEl) fundEl.textContent = holders.fund_count != null ? holders.fund_count : '—';
  const asofEl = $('#htype-asof');
  if (asofEl) asofEl.textContent = holders.report_date || '—';
  host.hidden = false;
}

// R3 Round 3: 散户/主力 — 持仓结构双图 (户数趋势双轴 + 主力vs散户 20 日)
var _currentHolders = null;

async function drawHoldersCharts() {
  const c1 = $('#holders-chart'), c2 = $('#holders-flow-chart');
  if (!c1 || !c2) return;
  const pane = c1.closest('[data-tab-pane]');
  if (pane && pane.hidden) return;
  const tk = _newChartToken('holders');
  await _ensureECharts();
  if (_isChartTokenStale('holders', tk)) return;
  // 图1: 股东户数趋势 (线) + 户均持股 (柱, 右轴)
  _safeDisposeECharts(echartsCharts.holders); echartsCharts.holders = null;
  const chart1 = echarts.init(c1, null, { renderer: 'canvas' });
  echartsCharts.holders = chart1;
  const h = _currentHolders;
  const hist = h && (h.history || []).length >= 2
    ? [...h.history].sort((a, b) => String(a.report_date || '').localeCompare(String(b.report_date || '')))
    : null;
  if (hist) {
    chart1.setOption({
      backgroundColor: 'transparent',
      animation: false,
      tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE, textStyle: { color: INK, fontSize: 11 }, axisPointer: { type: 'shadow' } },
      legend: { data: ['股东户数', '户均持股'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 8 },
      grid: { left: 50, right: 56, top: 32, bottom: 24 },
      xAxis: { type: 'category', data: hist.map(r => r.report_date || ''), axisLine: { lineStyle: { color: CHART_LINE } }, axisLabel: { color: INK2, fontSize: 10 } },
      yAxis: [
        { type: 'value', splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10, formatter: v => v >= 1e4 ? (v / 1e4).toFixed(1) + '万' : v } },
        { type: 'value', splitLine: { show: false }, axisLabel: { color: INK2, fontSize: 10 } },
      ],
      series: [
        { name: '股东户数', type: 'line', data: hist.map(r => r.holder_total || 0), smooth: true, symbolSize: 4, itemStyle: { color: ACCENT }, lineStyle: { width: 2 }, areaStyle: { color: 'rgba(94,129,244,.12)' } },
        { name: '户均持股', type: 'bar', yAxisIndex: 1, data: hist.map(r => r.avg_shares || 0), itemStyle: { color: 'var(--accent-2)', opacity: .65 }, barMaxWidth: 14 },
      ],
    });
  } else {
    chart1.setOption(emptyChartOption('季报样本不足'));
  }
  chart1.resize();
  // 图2: 主力 vs 散户 净流入 20 日 (万元)
  _safeDisposeECharts(echartsCharts.holdersFlow); echartsCharts.holdersFlow = null;
  const chart2 = echarts.init(c2, null, { renderer: 'canvas' });
  echartsCharts.holdersFlow = chart2;
  const flow = (window._currentFlowHistory || []).slice(-20);
  // date 兼容 "2026-08-03" / "20260803" 两种格式 → MM-DD
  const _md = d => { const s = String(d || ''); return s.length === 8 ? s.slice(4, 6) + '-' + s.slice(6, 8) : s.slice(5); };
  const hasFlowData = flow.some(f => (f.main_net || 0) !== 0 || (f.small_net || 0) !== 0);
  if (flow.length >= 2 && hasFlowData) {
    chart2.setOption({
      backgroundColor: 'transparent',
      animation: false,
      tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE, textStyle: { color: INK, fontSize: 11 }, axisPointer: { type: 'shadow' } },
      legend: { data: ['主力', '散户'], textStyle: { color: INK2, fontSize: 10 }, top: 0, right: 8 },
      grid: { left: 56, right: 16, top: 32, bottom: 24 },
      xAxis: { type: 'category', data: flow.map(f => _md(f.date)), axisLine: { lineStyle: { color: CHART_LINE } }, axisLabel: { color: INK2, fontSize: 10 } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: INK2, fontSize: 10, formatter: v => Math.abs(v) >= 1e4 ? (v / 1e4).toFixed(0) + '亿' : v } },
      series: [
        { name: '主力', type: 'bar', data: flow.map(f => f.main_net || 0), itemStyle: { color: ACCENT }, barMaxWidth: 12 },
        { name: '散户', type: 'bar', data: flow.map(f => f.small_net || 0), itemStyle: { color: 'var(--ink-3)', opacity: .7 }, barMaxWidth: 12 },
      ],
    });
  } else {
    chart2.setOption(emptyChartOption(flow.length < 2 ? '资金流样本不足' : '当前为代理数据，无分单明细'));
  }
  chart2.resize();
}

// R4 Round 4: 筹码健康度 0-100
function computeHolderHealth(holders) {
  const hist = (holders.history || []).map(h => h.holder_total).filter(Boolean);
  if (hist.length < 2) return 50;
  // 户数环比下降 = 筹码集中 (+), 上升 = 分散 (-)
  const cur = hist[hist.length - 1];
  const prev = hist[hist.length - 2];
  const trendScore = prev > 0 ? Math.max(-30, Math.min(30, (prev - cur) / prev * 100 * 3)) : 0;
  // 集中度 + 户均
  const focusScore = (holders.focus_label || '').includes('集中') ? 20
                  : (holders.focus_label || '').includes('分散') ? -10 : 0;
  // 散户比例 < 50%
  const retailFactor = (holders.retail_proxy_pct || 50) < 50 ? 10 : -10;
  const base = 50 + trendScore + focusScore + retailFactor;
  return Math.max(0, Math.min(100, Math.round(base)));
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
// R101-fix (Batch 1.5): 部分降级横幅 — /core 成功但 /full 失败时,显示在 quickbar 上方
// 保留首屏报价/分时/K线,只提示"完整数据暂不可用",不阻塞用户看股价
function _showStockDegraded(code, msg) {
  // 防重复:已存在则只更新文案
  let bar = document.getElementById('stock-degraded-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'stock-degraded-bar';
    bar.className = 'stock-degraded-bar';
    const quickbar = $('#stock-quickbar');
    if (quickbar && quickbar.parentNode) {
      quickbar.parentNode.insertBefore(bar, quickbar.nextSibling);
    } else {
      (document.querySelector('.view-stock') || document.body).prepend(bar);
    }
  }
  bar.innerHTML = `<span class="degraded-icon">⚠</span> <b>${escapeHtml(code)} 数据暂不可用</b> · ${escapeHtml(msg || '上游繁忙')} · <a href="javascript:void(0)" id="stock-degraded-retry">点此重试</a>`;
  const btn = document.getElementById('stock-degraded-retry');
  if (btn) btn.onclick = () => { bar.remove(); loadStockDetail(code); };
  _hideStockSkeleton();
}
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
var _intraDayOverlayPrev = null;  // 2026-08-09: 对比昨收 — {date, ticks[]}

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
  pick.onchange = () => { refreshLabel(); autoLoadIntraDay(); };
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

  // 2026-08-09: 对比昨收 — toggle 后从 intraday_5d 拉前一日 ticks,
  //   渲染时作为辅助 lineSeries 叠加在主图 (TradingView 风格)
  const overlay = $('#intra-day-overlay');
  if (overlay) {
    overlay.onclick = async () => {
      if (!currentStockCode) return;
      const on = overlay.dataset.overlay !== 'on';
      overlay.dataset.overlay = on ? 'on' : 'off';
      overlay.textContent = on ? '隐藏昨收' : '对比昨收';
      overlay.classList.toggle('active', on);
      _intraDayOverlayPrev = null;
      if (on) {
        try {
          const data = await api(`/api/stock/${currentStockCode}/intraday_5d`, { signal: _stockSignal(), priority: 'high' });
          const days = data?.intraday_5d || data?.days || [];
          if (days.length >= 2) {
            // 当前加载日 (intra-day-pick) 之前一天 = 对比日
            const cur = pick.value || todayStr();
            const prevDay = days.find(d => d.date < cur);
            if (prevDay) {
              _intraDayOverlayPrev = {
                date: prevDay.date,
                ticks: prevDay.ticks || [],
              };
            }
          }
        } catch (_) {}
      }
      // 重渲染: 用当前 cache 拉一份
      const key = currentStockCode + ':' + (pick.value || todayStr());
      const cached = intraDayCache.get(key);
      if (cached) renderIntraDay(cached);
    };
  }

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

async function loadIntraDay(code, dateStr, opts) {
  if (!code || !dateStr) return;
  // R8a: stale-code guard — streak 格子后台 prefetch 链可能跨股,旧 code 静默丢
  if (code !== window._currentStockCode) return;
  // 2026-08-08: silent 模式 (streak prefetch 用) — 只填缓存不渲染,
  //   否则 prefetch 的 10 天日期逐个 renderIntraDay,会覆盖用户主动选的日期
  const silent = !!(opts && opts.silent);
  // 2026-08-08: 周末默认路径自动回退最近交易日 (用户显式选周末也受益 — 反正没数据)
  let ds = dateStr;
  const dow = new Date(ds + 'T00:00:00').getDay();
  if (dow === 0 || dow === 6) {
    try { await _ensureTradeDates(); } catch (_) {}
    // _tradeDates 是倒序 (最新在前) → 正序遍历找第一个 <= ds 的 = 最近交易日
    if (_tradeDates && _tradeDates.length) {
      for (let i = 0; i < _tradeDates.length; i++) {
        if (_tradeDates[i] <= ds) { ds = _tradeDates[i]; break; }
      }
    }
  }
  if (ds !== dateStr) {
    const pick = $('#intra-day-pick');
    if (pick) pick.value = ds;
    const lbl = $('#intra-day-label');
    if (lbl) lbl.textContent = ds + ' ' + weekdayCN(ds);
    dateStr = ds;
  }
  const cacheKey = code + ':' + dateStr;
  // 2026-08-08: silent (prefetch) 不占用 intraDayLoading — 否则 prefetch 先到会短路
  //   用户请求 (同 key 直接 return),而 prefetch 完成又不渲染 → 分时图永远空白
  if (!silent && intraDayLoading === cacheKey) return;
  const cached = intraDayCache.get(cacheKey);
  if (cached) {
    if (!silent) renderIntraDay(cached);
    return;
  }
  if (!silent) intraDayLoading = cacheKey;
  const note = $('#intra-day-note');
  if (!silent) {
    note.textContent = `加载 ${dateStr} 分时 …`;
    note.style.color = INK2;
  }
  const fetchOnce = async () => {
    const data = await api(`/api/stock/${code}/intraday?date=${encodeURIComponent(dateStr)}`, { signal: _stockSignal(), priority: 'high' });
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
    if (!silent) renderIntraDay(merged);
  } catch (e) {
    if (silent) return;  // prefetch 失败静默,不打扰用户
    // 2026-08-09: AbortError = 切股/切页主动取消 — 切股后已清空 note,这里不必再回填"加载失败"
    if (e && e.name === 'AbortError') return;
    // 同股内 stale 短时间被截胡也当 abort — currentStockCode 已变,UI 不归我们管
    if (code !== window._currentStockCode) return;
    note.textContent = `加载失败：${e.message}`;
    note.style.color = DOWN;
  } finally {
    if (!silent) intraDayLoading = null;
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
  // 2026-08-08: 过滤 0/null — 数据源解析错误时 high/low 偶尔为 0,会污染 max/min
  const opens  = ticks.map(t => t.open ).filter(v => v != null && v > 0);
  const highs  = ticks.map(t => t.high ).filter(v => v != null && v > 0);
  const lows   = ticks.map(t => t.low  ).filter(v => v != null && v > 0);
  const prices = ticks.map(t => t.price).filter(v => v != null && v > 0);
  const openRef = opens.length ? opens[0] : (prices[0] || 0);
  const lastPrice = prices[prices.length - 1];
  const hi = highs.length ? Math.max(...highs) : null;
  const lo = lows.length ? Math.min(...lows) : null;
  // 涨跌幅 = (现价 - 昨收) / 昨收 — A 股惯例,不是 (现价-今开)/今开
  // data.prev_close 已从 4 源糅合时填充 (akshare/tencent/sina/efinance),缺失时兜底 openRef
  const refForPct = (data.prev_close != null && data.prev_close > 0) ? data.prev_close : openRef;
  const pct = (refForPct && lastPrice) ? ((lastPrice - refForPct) / refForPct * 100) : null;
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
    ['振幅',     amp != null ? amp.toFixed(2) + '%' : '—', amp != null ? (amp >= 5 ? UP : INK2) : INK3],
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
  // R30: 分时形态识别 + 大单节点
  renderIntraPattern(ticks, openRef, lastPrice, vwap);

  // 2026-08-09: limit_up_price API 不返回时,按"创业板/科创板 ±20% / 主板 ±10%"兜底
  //   不画涨停参考线 = 用户看不到盘中到顶的视觉提示,日内涨势图没有"天花板"
  //   _isChiNextOrStar 启发: code 3/688 开头 → ±20%,否则 ±10%
  const _pc2 = data.prev_close ?? openRef;
  let _lu = data.limit_up_price ?? lastStockContext.limit_up_price;
  if ((!_lu || _lu <= 0) && _pc2 > 0) {
    const _cap = (code.startsWith('3') || code.startsWith('688')) ? 0.20 : 0.10;
    _lu = +(_pc2 * (1 + _cap)).toFixed(2);
  }
  drawIntraDayChart(code, date, ticks, openRef, _pc2, _lu, data.support_levels || null, _intraDayOverlayPrev);
}

// R30: 分时形态识别 — 时段涨跌 + 大单节点 + VWAP 距离
function renderIntraPattern(ticks, openRef, lastPrice, vwap) {
  const dom = $('#intra-pattern');
  if (!dom || !ticks || !ticks.length) { if (dom) dom.hidden = true; return; }
  // 4 时段: 早盘 9:30-10:30 / 上午 10:30-11:30 / 午盘 13:00-14:00 / 尾盘 14:00-15:00
  const parseHM = (s) => {
    const m = /(\d{1,2}):(\d{2})/.exec(s || '');
    return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : [null, null];
  };
  const inRange = (t, start, end) => {
    const [h, m] = parseHM(t.time);
    if (h == null) return false;
    const cur = h * 60 + m;
    return cur >= start && cur < end;
  };
  const segments = [
    { name: '早盘',   start: 9*60+30,  end: 10*60+30 },
    { name: '上午',   start: 10*60+30, end: 11*60+30 },
    { name: '午盘',   start: 13*60,    end: 14*60 },
    { name: '尾盘',   start: 14*60,    end: 15*60+1 },
  ];
  const segStats = segments.map(seg => {
    const list = ticks.filter(t => inRange(t, seg.start, seg.end));
    if (!list.length) return null;
    const prices = list.map(t => t.price).filter(p => p != null);
    if (!prices.length) return null;
    const segOpen = prices[0];
    const segClose = prices[prices.length - 1];
    const segPct = segOpen > 0 ? ((segClose / segOpen - 1) * 100) : 0;
    return { ...seg, pct: segPct, open: segOpen, close: segClose, count: list.length };
  }).filter(Boolean);
  // 时段柱图
  const segHtml = segStats.length
    ? `<div class="ip-segments">${segStats.map(s => {
        const w = Math.min(95, Math.abs(s.pct) * 12);  // 1% ≈ 12px
        const cls = s.pct > 0.05 ? 'bull' : s.pct < -0.05 ? 'bear' : 'flat';
        return `<div class="ip-seg">
          <span class="ip-seg-name">${s.name}</span>
          <div class="ip-seg-bar-wrap">
            <span class="ip-seg-bar ${cls}" style="left:${s.pct >= 0 ? '0' : 'unset'};right:${s.pct < 0 ? '0' : 'unset'};width:${w}%"></span>
          </div>
          <span class="ip-seg-val" style="color:${s.pct >= 0 ? UP : s.pct < 0 ? DOWN : INK2}">${s.pct >= 0 ? '+' : ''}${s.pct.toFixed(2)}%</span>
        </div>`;
      }).join('')}</div>`
    : '<span class="dim" style="font-size:11px">无时段数据</span>';
  // 大单节点: 每 tick 的成交额 = price * volume_hand * 100 (hand→share),取 top 5
  const bigTrades = ticks.map(t => {
    const price = Number(t.price) || 0;
    const volHand = Number(t.volume_hand) || 0;
    const amt = price * volHand * 100;  // 元
    const side = (t.side || '').toLowerCase();
    const isBuy = side.includes('买') || side === 'b' || side.startsWith('buy');
    const isSell = side.includes('卖') || side === 's' || side.startsWith('sell');
    return {
      time: t.time, amt, price, side,
      isBuy, isSell,
    };
  }).filter(t => t.amt > 0).sort((a, b) => b.amt - a.amt).slice(0, 5);
  const bigHtml = bigTrades.length
    ? `<div class="ip-bigtrades">${bigTrades.map(t => {
        const amtWan = t.amt / 1e4;
        const sideTxt = t.isBuy ? '主买' : t.isSell ? '主卖' : '大单';
        const sideCls = t.isBuy ? 'buy' : t.isSell ? 'sell' : '';
        return `<div class="ip-bigtrade">
          <span class="ip-bigtrade-time">${escapeHtml(t.time)}</span>
          <span class="ip-bigtrade-amt">${amtWan >= 10000 ? (amtWan/10000).toFixed(2) + '亿' : amtWan.toFixed(0) + '万'}</span>
          <span class="ip-bigtrade-side ${sideCls}">${sideTxt}</span>
        </div>`;
      }).join('')}</div>`
    : '<span class="dim" style="font-size:11px">无大单数据</span>';
  // VWAP 距离
  let vwapHtml = '<span class="dim" style="font-size:11px">VWAP 无数据</span>';
  if (vwap != null && lastPrice > 0) {
    const distPct = (lastPrice - vwap) / vwap * 100;
    const distColor = distPct > 0.05 ? UP : distPct < -0.05 ? DOWN : INK2;
    const distLabel = Math.abs(distPct) >= 0.05
      ? (distPct > 0 ? '✓ 价在 VWAP 上方 (强势)' : '✗ 价在 VWAP 下方 (弱势)')
      : '— 价在 VWAP 附近';
    vwapHtml = `
      <div class="ip-seg">
        <span class="ip-seg-name">VWAP</span>
        <div class="ip-seg-bar-wrap">
          <span class="ip-seg-bar ${distPct >= 0 ? 'bull' : 'bear'}" style="left:${distPct >= 0 ? '0' : 'unset'};right:${distPct < 0 ? '0' : 'unset'};width:${Math.min(95, Math.abs(distPct) * 10)}%"></span>
        </div>
        <span class="ip-seg-val" style="color:${distColor}">${distPct >= 0 ? '+' : ''}${distPct.toFixed(2)}%</span>
      </div>
      <span class="dim" style="font-size:10px">${vwap.toFixed(2)} · ${distLabel}</span>`;
  }
  dom.innerHTML = `
    <div class="ip-block">
      <span class="ip-label">分时 4 时段</span>
      ${segHtml}
    </div>
    <div class="ip-block">
      <span class="ip-label">VWAP 距离</span>
      ${vwapHtml}
    </div>
    <div class="ip-block">
      <span class="ip-label">大单节点 Top 5</span>
      ${bigHtml}
    </div>`;
  dom.hidden = false;
}

async function drawIntraDayChart(code, date, ticks, openRef, prevClose, limitUp, supportLevels, overlayPrev) {
  const _labelBg = document.documentElement.getAttribute("data-theme") === "light" ? "rgba(255,255,255,0.85)" : "rgba(10,9,8,0.75)";
  const dom = $('#intra-day-chart');
  if (!dom) return;
  _safeDisposeECharts(echartsCharts.intraDay); echartsCharts.intraDay = null;
  const tk = _newChartToken('intraDay');
  await _ensureECharts();
  if (_isChartTokenStale('intraDay', tk)) return;
  const chart = echarts.init(dom, null, { renderer: 'canvas' });
  echartsCharts.intraDay = chart;

  // R1: 容器宽度响应 — 窄屏 缩小轴边距/字号
  const _cw = dom.clientWidth;
  const _narrow = _cw < 420;
  const gL = _narrow ? 42 : 56, gRM = _narrow ? 52 : 84, gRV = _narrow ? 12 : 24;
  const axFs = _narrow ? 9 : 10, mkFs = _narrow ? 8 : 9, mkDist = _narrow ? 2 : 4;

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
  // 2026-08-08: 上下边界动态 = 当日实际最高/最低价 (来自 tick.high / tick.low)
  //   - 多源数据: akshare/sina/tencent_m1/efinance 都返回每根 K 的 high/low
  //   - 单一源 tencent_minute (今日盘中 tick 数据) 无 high/low → 退化到 tick.price
  //   - 关键: 必须用真实 tick.high 取 max,不能用 price.max — tick 内 1min 振幅可见
  //     例如: 09:31 price=1303 但 low=1301.5 (这一分钟内下探到 1301.5)
  //     旧版只看 price → 看不到 1301.5 这一关键日内低点
  const tickHighArr = ticks.map(t => {
    const h = t.high;
    return (h != null && h > 0) ? h : (t.price != null ? t.price : null);
  });
  const tickLowArr = ticks.map(t => {
    const l = t.low;
    return (l != null && l > 0) ? l : (t.price != null ? t.price : null);
  });
  const validHighs = tickHighArr.filter(v => v != null);
  const validLows = tickLowArr.filter(v => v != null);
  // 2026-08-09: 用 prices 拿 lastPrice,判定"价格接近涨停才扩展 yMax" — 600519 微涨 0.1% 时
  //   拉到涨停会让日内波动被压扁,_lastPrice 在更下方计算 (refLines 之后), 这里提前取一次
  const _earlyLastPrice = (() => {
    for (let i = prices.length - 1; i >= 0; i--) if (prices[i] != null) return prices[i];
    return null;
  })();
  // 上界 = 当日真实最高 (含 tick 内 spike)
  // 下界 = 当日真实最低 (含 tick 内 dip)
  // refVal (昨收/今开) 必须在 [yMin, yMax] 内 — 否则参考线看不见
  const dataMin = validLows.length ? Math.min(...validLows, refVal) : refVal;
  const dataMax = validHighs.length ? Math.max(...validHighs, refVal) : refVal;
  const dataRange = Math.max(dataMax - dataMin, refVal * 0.001);
  // 上下各留 0.8% 余量 — 给"末值% end-label"和涨停参考线呼吸空间
  // pad 不能太小,否则最高 tick 顶到顶部看不到;也不能太大,否则日内振幅被压扁
  const basePad = Math.max(dataRange * 0.08, refVal * 0.002);
  let yMin = dataMin - basePad;
  // 顶部 padding 加大 50% — 给"末值% end-label"留呼吸空间,标签不会再贴到价格线上
  let yMax = dataMax + basePad * 1.5;
  // 2026-08-09: 涨停参考线必须在 [yMin, yMax] 内可见 — 但仅当价格已接近涨停时 (lastPrice >= limitUp*0.97)
  //   才扩展 yMax,否则像 600519 这种日间只涨 0.1% 的微涨股,把 yMax 拉到涨停会让价格线被压扁到不可见。
  if (limitUp != null && limitUp > 0 && _earlyLastPrice != null && _earlyLastPrice >= limitUp * 0.97) {
    const _extPad = Math.max((limitUp - dataMax) * 0.5, refVal * 0.003);
    yMax = Math.max(yMax, limitUp + _extPad);
  }

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
  // 2026-08-08: tick.high/tick.low 缺失 (tencent_minute 等单源) 时退化到 tick.price,
  //   保证包络带永远可见,日内振幅视觉不会因数据源切换而消失
  const ticksH = ticks.map(t => (t.high != null && t.high > 0) ? t.high : (t.price != null ? t.price : null));
  const ticksL = ticks.map(t => (t.low != null && t.low > 0) ? t.low : (t.price != null ? t.price : null));
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

  // ── 时间分界线：11:30（午休）──
  const dividerTimes = ['11:30'];
  const dividerIndex = dividerTimes.map(rt => times.findIndex(t => t && t.startsWith(rt))).filter(i => i >= 0);
  const timeMarkers = dividerIndex.map(i => ({
    xAxis: i,
    lineStyle: { color: 'var(--line-strong)', type: 'dashed', width: 1, opacity: 0.5 },
    label: { show: true, formatter: times[i].slice(0, 5), position: 'start', color: INK3, fontSize: mkFs }
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
        lineStyle: { color: 'var(--accent-3)', type: 'dashed', width: 1.2, opacity: 0.85 },
        label: { formatter: `1/3位 ${(+l13).toFixed(2)}`, color: 'var(--accent-3)', fontSize: mkFs,
                 position: 'insideEndBottom', distance: mkDist, backgroundColor: _labelBg,
                 padding: [1, 4], borderRadius: 3, fontWeight: 600 },
      });
    }
    // 谷底 A (下轨, 浅绿)
    const a = supportLevels.A;
    if (a != null && a >= yMin && a <= yMax) {
      refLines.push({
        yAxis: +a.toFixed(3),
        lineStyle: { color: 'var(--down-strong)', type: 'dotted', width: 1, opacity: 0.7 },
        label: { formatter: `A=${(+a).toFixed(2)}`, color: 'var(--down-strong)', fontSize: mkFs,
                 position: 'insideEndBottom', distance: mkDist, backgroundColor: _labelBg,
                 padding: [1, 4], borderRadius: 3 },
      });
    }
    // 山顶 B (上轨, 红色)
    const b = supportLevels.B;
    if (b != null && b >= yMin && b <= yMax) {
      refLines.push({
        yAxis: +b.toFixed(3),
        lineStyle: { color: 'var(--up)', type: 'dotted', width: 1, opacity: 0.7 },
        label: { formatter: `B=${(+b).toFixed(2)}`, color: 'var(--up)', fontSize: mkFs,
                 position: 'insideEndTop', distance: mkDist, backgroundColor: _labelBg,
                 padding: [1, 4], borderRadius: 3 },
      });
    }
    // 5 日线参考 (从日线 K 线最后 5 日 close 均价)
    if (Array.isArray(supportLevels.daily_ma5)) {
      const lastMa5 = supportLevels.daily_ma5[supportLevels.daily_ma5.length - 1];
      if (lastMa5 != null && lastMa5 >= yMin && lastMa5 <= yMax) {
        refLines.push({
          yAxis: +lastMa5.toFixed(3),
          lineStyle: { color: 'var(--warn)', type: 'dashed', width: 1.4, opacity: 0.9 },
          label: { formatter: `MA5 ${lastMa5.toFixed(2)}`, color: 'var(--warn)', fontSize: axFs,
                   position: 'insideEndTop', distance: mkDist, backgroundColor: _labelBg,
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
      [5,  'var(--warn)', 1.6],
      [10, 'var(--accent-2)', 1.1],
      [20, 'var(--accent-3)', 1.1],
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
        label: { formatter: `MA${n} ${ma.toFixed(2)}`, color, fontSize: n === 5 ? axFs : mkFs,
                 position: 'insideEndTop', distance: mkDist, backgroundColor: _labelBg,
                 padding: [1, 4], borderRadius: 3, fontWeight: n === 5 ? 700 : 500,
                 textBorderColor: 'transparent' },
      });
    }
  }
  // R3: 窄屏 refLine 去重 — 若两条参考线价格差 < 0.2% 昨收,只留第一条避免右侧标签堆叠
  if (_narrow && refLines.length > 1) {
    const _closeThreshold = Math.max(refVal * 0.002, 0.01);
    refLines = refLines.filter((rl, i) =>
      i === 0 || refLines.slice(0, i).every(prev => Math.abs(rl.yAxis - prev.yAxis) > _closeThreshold)
    );
  }

  chart.setOption({
    backgroundColor: 'transparent',
    grid: [
      { left: gL, right: gRM, top: 24, height: _narrow ? '56%' : '60%' },
      { left: gL, right: gRV, top: _narrow ? '71%' : '75%', height: _narrow ? '26%' : '21%' },
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
          s += `<div>均价 <b style="color:var(--warn)">${(+pMap['均价']).toFixed(3)}</b> <span style="color:${avgPct >= 0 ? UP : DOWN}">${avgPct >= 0 ? '+' : ''}${avgPct.toFixed(2)}%</span></div>`;
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
      textStyle: { color: INK2, fontSize: axFs }, top: 4, right: 8,
      data: limitUp ? ['价格', '均价', '昨收', '涨停价', '成交量'] : ['价格', '均价', '昨收', '成交量']
    },
    xAxis: [
      { type: 'category', data: times, gridIndex: 0,
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: axFs, interval: 0,
          formatter: (v, i) => labelIndexMap[i] || '' },
        splitLine: { show: false } },
      { type: 'category', data: times, gridIndex: 1,
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: _narrow ? 8 : 9,
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
            splitLine: { lineStyle: { color: GRID, width: 0.5, type: 'dashed' } },
            axisLabel: { color: INK2, fontSize: axFs, interval: 0, formatter: v => v.toFixed(2) } },
          // 右轴:涨跌幅 % — 同一区间,formatter 把 v 换算成 pct
          { type: 'value', gridIndex: 0, position: 'right',
            min: alignedMin, max: alignedMax, interval: stepPrice,
            splitLine: { show: false },
            axisLabel: { color: INK2, fontSize: axFs, interval: 0,
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
      // 成交量轴 — 同步变细
      { gridIndex: 1, splitLine: { lineStyle: { color: GRID, width: 0.5, type: 'dashed' } },
        axisLabel: { color: INK2, fontSize: _narrow ? 8 : 9 } },
    ],
    dataZoom: _narrow ? [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    ] : [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      // 2026-08-09: 桌面端加 slider 控件 (TradingView 风格)
      { type: 'slider', xAxisIndex: [0, 1], height: 14, bottom: 4,
        start: 0, end: 100,
        textStyle: { color: INK2, fontSize: 9 },
        borderColor: CHART_LINE,
        fillerColor: 'rgba(212,160,86,0.15)',
        handleStyle: { color: ACCENT, borderColor: ACCENT } },
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
      // 2026-08-09: 价格线颜色跟末值涨跌走 — 之前硬编码 UP=红,跌势时整条线也是红色
      //   用户看着别扭(日内跌 -3.68%,但分时图线是红色)。改为跟末值同色
      //   注:理想是 tick 段红涨绿跌,但 echarts line 不能逐点换色 — 渐变过渡太花,
      //   二选一:整条 UP 还是整条 _lastColor,后者更直观。
      { name: '价格', type: 'line', data: prices, showSymbol: false, smooth: false,
        clip: false,
        lineStyle: { color: _lastColor, width: 2.0 }, itemStyle: { color: _lastColor },
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
                color: '#FFFFFF', fontSize: axFs, fontWeight: 700,
                backgroundColor: _lastColor, padding: [2, 6], borderRadius: 3,
                distance: _lastPriceNearTop ? (_narrow ? 4 : 6) : (_narrow ? 2 : 4),
              },
            }] : []),
          ],
        },
        z: 5 },
      // 均价线（鲜橙 — 区别于 MA5 琥珀金 和价格红）
      { name: '均价', type: 'line', data: avgLine, showSymbol: false,
        lineStyle: { color: '#FFB347', width: 1.8, type: 'solid' },
        itemStyle: { color: '#FFB347' }, z: 4 },
      // 昨收参考线（中性灰点线 — 不抢戏）
      { name: '昨收', type: 'line', data: refLine, showSymbol: false,
        lineStyle: { color: INK3, type: 'dotted', width: 1, opacity: 0.6 }, z: 2 },
      ...(limitUpLine ? [limitUpLine] : []),
      // 2026-08-09: 对比昨收 — 紫色虚线,前一日价格按时间对齐到当前分时轴
      ...(overlayPrev && overlayPrev.ticks && overlayPrev.ticks.length ? (() => {
        const prevMap = new Map();
        for (const tk of overlayPrev.ticks) {
          const t = tk.time || (tk.datetime || '').slice(11, 16);
          if (t) prevMap.set(t, +tk.price);
        }
        const data = times.map(t => prevMap.get(t) ?? null);
        return [{
          name: '昨收价格',
          type: 'line', data, showSymbol: false, smooth: false,
          lineStyle: { color: '#A284DC', type: 'dashed', width: 1.4, opacity: 0.85 },
          itemStyle: { color: '#A284DC' },
          z: 3,
        }];
      })() : []),
      // 成交量柱（红涨绿跌）
      { name: '成交量', type: 'bar', data: volBars, xAxisIndex: 1, yAxisIndex: 2, barWidth: '70%' },
    ],
  }, { notMerge: false });  // 2026-08-09: diff 模式
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
        // R3.3: 轮询拉长到 300s (500ms/次) — 上游慢时 loadKline 可能 >30s 才完成,
        // 旧 150×200ms=30s 会静默放弃, pane 一直空白
        (function _waitKline(tries = 0) {
          if (_klineDataReady) {
            if (!_klineChartDrawn) drawKlineChart();
            else if (echartsCharts.kline) echartsCharts.kline.resize();
          } else if (tries < 600) {
            setTimeout(() => _waitKline(tries + 1), 500);
          }
        })();
      }
    }
    // R-pro-stock v1: 分时图与其他 tabs 同框在 super card 内,走原 tab 触发
    if (tab === 'intraday') {
      if (currentStockCode) {
        initIntraDayPicker(currentStockCode);
        const pick = $('#intra-day-pick');
        if (pick && pick.value) {
          const _ck = currentStockCode + ':' + pick.value;
          if (intraDayCache.has(_ck)) {
            if (!echartsCharts.intraDay) renderIntraDay(intraDayCache.get(_ck));
            else echartsCharts.intraDay.resize();
          } else {
            loadIntraDay(currentStockCode, pick.value);
          }
        }
      } else {
        $('#intra-day-note').textContent = '请先在上方搜索一只股票';
      }
    }
    if (tab === 'crash' && currentStockCode) loadCrashRisk(currentStockCode);
    // R3 Round 3: news/sectors/related/holders tab — 首次点击才触发加载 (lazy)
    if (tab === 'news') loadNewsList(false);
    if (tab === 'sectors') loadSectorsList(false);
    if (tab === 'related') loadRelatedList();
    if (tab === 'holders' && currentStockCode) drawHoldersCharts();
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
  const code = window._currentStockCode || currentStockCode;

  // R1: 优先用 /full 预取的 related_news (0 网络开销, 即时渲染)
  if (!forceRefresh && code && _stockAuxCache.code === code && _stockAuxCache.related_news) {
    let cached = _stockAuxCache.related_news;
    // R-fix-2026-08-02: 兼容 list / dict 两种格式 (历史 /full 返 list, /endpoint 返 dict)
    if (Array.isArray(cached)) {
      cached = { news: cached, count: cached.length, ai_count: sumBy(cached, n => n && n.ai ? 1 : 0) };
    }
    newsCache = cached;
    const fa = cached.fetched_at ? new Date(cached.fetched_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    const degLabel = cached._degraded_fallback ? ' · fallback' : '';
    meta.textContent = `${code} 相关 · ${cached.count || 0} 条${degLabel} · 抓取 ${fa} · AI ${cached.ai_count || 0}/${cached.count || 0}`;
    meta.style.color = INK2;
    renderNewsList(cached.news || []);
    renderNewsStats(cached.news || []);  // R27
    return;
  }

  if (forceRefresh) {
    meta.textContent = '刷新中…（抓取 + AI 评分，约 60s）';
    meta.style.color = INK2;
    list.innerHTML = Array.from({ length: 5 }, () =>
      `<div class="news-card"><div class="skeleton skeleton-block" style="width:100%"></div></div>`).join('');
  }
  try {
    let data;
    let stockCtx = null;
    if (code) {
      const resp = await api(`/api/stock/${code}/related_news${forceRefresh ? '?fresh=1' : ''}`);
      data = resp || {};
      stockCtx = { code, sector: data.sector || {} };
    } else if (forceRefresh) {
      data = await (await fetch('/api/news/refresh', { method: 'POST' })).json().then(d => d.data || {});
    } else {
      data = (await api('/api/news')) || {};
    }
    newsCache = data;
    const fa = data.fetched_at ? new Date(data.fetched_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    const aa = data.analyzed_at ? new Date(data.analyzed_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
    if (code) {
      const sec = stockCtx.sector || {};
      const swLabel = sec.sw ? `申万「${sec.sw}」` : '';
      const degLabel = data._degraded_fallback ? ' · fallback' : '';
      meta.textContent = `${code}${swLabel ? ' · ' + swLabel : ''} 相关 · ${data.count || 0} 条${degLabel} · 抓取 ${fa} · AI ${data.ai_count || 0}/${data.count || 0}`;
    } else {
      meta.textContent = `抓取 ${fa}  ·  AI ${data.ai_count || 0}/${data.count || 0} · 分析 ${aa}`;
    }
    meta.style.color = INK2;
    renderNewsList(data.news || []);
    renderNewsStats(data.news || []);  // R27
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
    list.innerHTML = '<div class="empty-card"><p class="caption dim">暂无相关新闻</p>' +
      '<button class="btn btn-ghost btn-sm" data-news-retry>🔄 刷新</button></div>';
    const retry = list.querySelector('[data-news-retry]');
    if (retry) retry.addEventListener('click', () => loadNewsList(false));
    return;
  }
  // 2026-08-06: 个股新闻排序 — 个股精准 > 板块宽口径 > 兜底;
  // 同类内按 ai.score 倒序,利好排前。便于用户一眼看到与自己票相关的利好/利空。
  const _dirRank = (d) => d === '利好' ? 0 : d === '利空' ? 1 : 2;
  const _hitRank = (k) => k === 'strong' ? 0 : k === 'weak' ? 1 : 2;
  const sorted = [...items].sort((a, b) => {
    const ak = a._hit_kind || (a.hit_reason && /宽口径/.test(a.hit_reason) ? 'weak' : 'strong');
    const bk = b._hit_kind || (b.hit_reason && /宽口径/.test(b.hit_reason) ? 'weak' : 'strong');
    const dr = _hitRank(ak) - _hitRank(bk);
    if (dr !== 0) return dr;
    const ad = _dirRank((a.ai||{}).direction || '');
    const bd = _dirRank((b.ai||{}).direction || '');
    if (ad !== bd) return ad - bd;
    return ((b.ai||{}).score || 0) - ((a.ai||{}).score || 0);
  });
  // R3 Round 3: 情绪汇总条 — 利好/利空/中性 + 精准命中计数
  const _dirCount = k => items.filter(n => {
    const dir = ((n.ai) || {}).direction || '';
    if (k === 'bull') return dir === '利好';
    if (k === 'bear') return dir === '利空';
    if (k === 'flat') return !!dir && dir !== '利好' && dir !== '利空';
    return !dir;
  }).length;
  const bullN = _dirCount('bull'), bearN = _dirCount('bear'), flatN = _dirCount('flat'), noaiN = _dirCount('');
  const strongN = items.filter(n => (n._hit_kind || (n.hit_reason && /宽口径/.test(n.hit_reason) ? 'weak' : 'strong')) === 'strong').length;
  const statsHtml = `
    <div class="news-stats">
      <span class="ns-item" style="color:${UP}">↑ 利好 ${bullN}</span>
      <span class="ns-item" style="color:${DOWN}">↓ 利空 ${bearN}</span>
      <span class="ns-item" style="color:${INK2}">— 中性 ${flatN}</span>
      <span class="ns-item dim">未评分 ${noaiN}</span>
      <span class="ns-sep"></span>
      <span class="ns-item dim">精准命中 ${strongN}</span>
      <span class="ns-item dim">共 ${items.length} 条</span>
    </div>`;
  list.innerHTML = statsHtml + sorted.map(n => {
    const a = n.ai || null;
    const score = a ? a.score : null;
    const dir = (a && a.direction) || '';
    const cls = score == null ? '' : (score >= 7 ? 'hot' : score >= 4 ? 'warm' : 'cold');
    const dirColor = dir === '利好' ? UP : dir === '利空' ? DOWN : INK2;
    const sectorChips = (a?.sectors || []).slice(0, 3).map(s => `<span class="chip">${escapeHtml(s)}</span>`).join('');
    const stockChips = (a?.stocks || []).slice(0, 4).map(s => `<a class="stock-link chip chip-code" data-code="${s}">${s}</a>`).join('');
    const reason = a?.reason ? `<div class="news-reason">${escapeHtml(a.reason)}</div>` : '';
    const href = n.url ? escapeHtml(n.url) : '#';
    // 2026-08-01: 命中标签 — 强/弱/fallback 一眼分清
    const hitKind = n._hit_kind || (n.hit_reason && /宽口径/.test(n.hit_reason) ? 'weak' : 'strong');
    // 2026-08-06: 利好/利空/中性 chip 显眼化 — 顶部状态条 + 颜色块 (跟龙头页 news_impact 一致)
    const dirBadge = dir
      ? `<span class="news-dir-badge news-dir-${dir === '利好' ? 'good' : dir === '利空' ? 'bad' : 'flat'}" title="${escapeHtml(a?.reason || '')}">${dir === '利好' ? '↑ 利好' : dir === '利空' ? '↓ 利空' : '— 中性'}</span>`
      : '';
    const hitTag = hitKind === 'weak' ? '<span class="news-hit-tag weak" title="板块宽口径匹配,可能与本股相关">板块</span>'
                  : hitKind === 'fallback' ? '<span class="news-hit-tag fallback" title="该股暂无精准新闻,展示近期财经要闻兜底">兜底</span>'
                  : (n.hit_reason ? `<span class="news-hit-tag strong" title="${escapeHtml(n.hit_reason)}">精准</span>` : '');
    // 2026-08-06: 重点新闻 = AI 评分 ≥ 7 + 利好/利空 (中性不重点), 用户原话"其他重点消息也要高亮"
    const isKey = score != null && score >= 7 && (dir === '利好' || dir === '利空');
    const keyBadge = isKey ? `<span class="news-key-badge" title="AI 评分 ≥ 7 且方向明确,重点关注">★ 重点</span>` : '';
    return `
      <div class="news-card ${cls} ${isKey ? 'news-card-key' : ''}" data-url="${href}" tabindex="0" role="link" aria-label="打开新闻: ${escapeHtml(n.title)}">
        <div class="news-score">
          ${score != null ? `<div class="news-score-num" style="color:${dirColor}">${score.toFixed(1)}</div><div class="news-score-cap">${dir}</div>` : '<div class="news-score-num dim">—</div><div class="news-score-cap dim">未评分</div>'}
        </div>
        <div class="news-body">
          <div class="news-head">
            ${dirBadge}
            ${keyBadge}
            <a class="news-title" href="${href}" target="_blank" rel="noopener" tabindex="-1">${escapeHtml(n.title)}</a>
          </div>
          <div class="news-meta">
            <span class="dim">${n.ctime_str || ''}</span>
            <span class="dim">· ${escapeHtml(n.media || '')}</span>
            <span class="dim">· ${n.lid_name || ''}</span>
            ${hitTag}
          </div>
          ${reason}
          ${sectorChips || stockChips ? `<div class="news-chips">${sectorChips}${stockChips}</div>` : ''}
        </div>
      </div>`;
  }).join('');
}

// R27: 新闻情绪 + 时间分布 mini chart + Top 关联股票 chips
async function renderNewsStats(items) {
  const wrap = $('#news-stats-wrap');
  if (!wrap || !items || !items.length) {
    if (wrap) wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  // 统计情绪分布 (按 AI 评分分档)
  const bull = items.filter(n => (n.ai || {}).direction === '利好');
  const bear = items.filter(n => (n.ai || {}).direction === '利空');
  const flat = items.filter(n => {
    const d = (n.ai || {}).direction;
    return d && d !== '利好' && d !== '利空';
  });
  // 24h 时间分布 (按 ctime 解析小时, 0-23)
  const hourBuckets = new Array(24).fill(0);
  items.forEach(n => {
    const t = n.ctime_str || '';
    // 解析 HH:MM 或 "今天 HH:MM"
    const m = /(\d{1,2}):\d{2}/.exec(t);
    if (m) {
      const h = parseInt(m[1], 10);
      if (h >= 0 && h < 24) hourBuckets[h]++;
    }
  });
  // Top 关联股票
  const stockCount = {};
  items.forEach(n => {
    (n.ai?.stocks || []).forEach(s => {
      stockCount[s] = (stockCount[s] || 0) + 1;
    });
  });
  const topStocks = Object.entries(stockCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  // 渲染
  await _ensureECharts();
  // 情绪分布柱图
  const sentimentDom = $('#news-sentiment-chart');
  if (sentimentDom) {
    _safeDisposeECharts(echartsCharts.newsSentiment);
    echartsCharts.newsSentiment = echarts.init(sentimentDom, null, { renderer: 'canvas' });
    echartsCharts.newsSentiment.setOption({
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 36, right: 14, top: 8, bottom: 22 },
      tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE, textStyle: { color: INK, fontSize: 11 } },
      xAxis: {
        type: 'category',
        data: ['利好', '中性', '利空', '未评分'],
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: 10 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value', minInterval: 1,
        splitLine: { lineStyle: { color: GRID, type: 'dashed' } },
        axisLabel: { color: INK2, fontSize: 10 },
      },
      series: [{
        type: 'bar', barWidth: '60%',
        data: [
          { value: bull.length, itemStyle: { color: '#26bf69' } },
          { value: flat.length, itemStyle: { color: INK2 } },
          { value: bear.length, itemStyle: { color: '#ff5252' } },
          { value: items.length - bull.length - bear.length - flat.length, itemStyle: { color: '#555' } },
        ],
        label: {
          show: true, position: 'top', color: INK, fontSize: 11, fontWeight: 700,
        },
      }],
    });
  }
  // 24h 时间分布柱图
  const timeDom = $('#news-time-chart');
  if (timeDom) {
    _safeDisposeECharts(echartsCharts.newsTime);
    echartsCharts.newsTime = echarts.init(timeDom, null, { renderer: 'canvas' });
    echartsCharts.newsTime.setOption({
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 28, right: 8, top: 6, bottom: 22 },
      tooltip: { trigger: 'axis', backgroundColor: CHART_TOOLTIP_BG, borderColor: CHART_LINE, textStyle: { color: INK, fontSize: 11 } },
      xAxis: {
        type: 'category',
        data: Array.from({ length: 24 }, (_, i) => `${i}h`),
        axisLine: { lineStyle: { color: CHART_LINE } },
        axisLabel: { color: INK2, fontSize: 9, interval: 2 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value', minInterval: 1,
        splitLine: { lineStyle: { color: GRID, type: 'dashed' } },
        axisLabel: { color: INK2, fontSize: 10 },
      },
      series: [{
        type: 'bar', barWidth: '85%',
        data: hourBuckets.map(c => ({ value: c, itemStyle: { color: c > 0 ? ACCENT : 'var(--bg-1)' } })),
      }],
    });
  }
  // Top 关联股票 chips
  const topDom = $('#news-top-stocks');
  if (topDom) {
    topDom.innerHTML = `
      <div class="nsw-top-label">
        <span>🏷 AI 关联个股 Top ${topStocks.length}</span>
        <span class="dim" style="font-weight:400;font-size:10px">点击跳转</span>
      </div>
      <div class="nsw-top-chips">
        ${topStocks.length ? topStocks.map(([c, cnt]) => `<span class="nsw-top-chip" data-code="${escapeHtml(c)}"><b>${escapeHtml(c)}</b><span class="chip-cnt">${cnt}</span></span>`).join('') : '<span class="dim" style="font-size:11px">暂无关联个股</span>'}
      </div>`;
    topDom.querySelectorAll('.nsw-top-chip').forEach(el => {
      el.addEventListener('click', () => {
        const c = el.dataset.code;
        if (c && c.length === 6) gotoStock(c);
      });
    });
  }
}

// R1: 新闻卡片全局点击 — 整卡可点, 键盘 Enter 打开
(function _initNewsCardClick() {
  const list = $('#news-list');
  if (!list) return;
  list.addEventListener('click', (e) => {
    const card = e.target.closest('.news-card');
    if (!card) return;
    // 标题链接和 stock-link chip 用各自默认行为, 不拦截
    if (e.target.closest('a')) return;
    const url = card.dataset.url;
    if (url && url !== '#') window.open(url, '_blank', 'noopener');
  });
  list.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const card = e.target.closest('.news-card');
    if (!card) return;
    const url = card.dataset.url;
    if (url && url !== '#') window.open(url, '_blank', 'noopener');
  });
})();

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
    renderSectorsList(data.sectors || [], window._sectorsSortMode || 'score');
    // R26: 当前股票所属板块高亮卡 (依赖 aux cache)
    renderSectorsMyCard();
  } catch (e) {
    meta.textContent = `加载失败：${e.message}`;
    meta.style.color = DOWN;
  }
}

// R26: 排序切换按钮
(function _initSectorsSort() {
  const sortDom = $('#sectors-sort');
  if (!sortDom) return;
  sortDom.addEventListener('click', (e) => {
    const btn = e.target.closest('.ssort-btn');
    if (!btn) return;
    const mode = btn.dataset.sort;
    if (!mode) return;
    window._sectorsSortMode = mode;
    sortDom.querySelectorAll('.ssort-btn').forEach(b => b.classList.toggle('active', b === btn));
    if (sectorsCache) renderSectorsList(sectorsCache.sectors || [], mode);
  });
})();

// R26: 当前股票所属板块 + 板块涨停龙头卡
function renderSectorsMyCard() {
  const dom = $('#sectors-mycard');
  if (!dom) return;
  // 当前 stock 所属 SW 行业
  const code = window._currentStockCode || currentStockCode;
  if (!code) { dom.hidden = true; return; }
  const sec = _stockAuxCache?.sector || {};
  const sw = sec.sw || sec.sw_raw || '';
  if (!sw || !sectorsCache) { dom.hidden = true; return; }
  const mySector = (sectorsCache.sectors || []).find(s => s.sw === sw);
  // 板块涨停龙头 — 来自 lu_ctx.sector_today (≥1 即板块今日有涨停)
  const lu = _stockAuxCache?.lu_ctx || {};
  const sectorZt = lu.sector_today || [];
  const sc = mySector?.avg_score || 0;
  const scoreCls = sc >= 6 ? 'hot' : sc >= 4 ? 'warm' : sc >= 2 ? 'mid' : 'cold';
  const ztHtml = sectorZt.length
    ? `<div class="smc-zt-list">${sectorZt.slice(0, 10).map(z => {
        const code = z.code || '';
        const name = z.name || '';
        const pct = z.涨跌幅 ?? z.pct ?? null;
        const pctCls = pct != null && pct < 0 ? 'down' : '';
        const pctStr = pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '';
        const titleParts = [name, z.连板数 ? `${z.连板数}连板` : '', z.封单金额 ? `封单${z.封单金额}` : ''].filter(Boolean).join(' · ');
        return `<div class="smc-zt-item" data-code="${escapeHtml(code)}" title="${escapeHtml(titleParts)}">
          <span class="smc-zt-code">${escapeHtml(code)}</span>
          <span class="smc-zt-name">${escapeHtml(name)}</span>
          <span class="smc-zt-pct ${pctCls}">${pctStr || '—'}</span>
        </div>`;
      }).join('')}</div>`
    : '<div class="smc-empty">今日该板块暂无涨停股</div>';
  dom.innerHTML = `
    <div class="smc-head">
      <span class="smc-title">📌 当前股票所属 <b>${escapeHtml(sw)}</b></span>
      <span class="smc-score ${scoreCls}" title="板块 AI 平均分">${sc ? sc.toFixed(2) : '—'} 分</span>
    </div>
    <div class="smc-stats dim" style="font-size:11px;margin-bottom:6px">
      利好 ${mySector?.bull_count || 0} · 利空 ${mySector?.bear_count || 0} · 共 ${mySector?.news_count || 0} 条 · 板块涨停 <b style="color:var(--up)">${sectorZt.length}</b> 只
    </div>
    ${ztHtml}
  `;
  dom.hidden = false;
  // 点击跳转
  dom.querySelectorAll('.smc-zt-item').forEach(el => {
    el.addEventListener('click', () => {
      const c = el.dataset.code;
      if (c && c.length === 6) gotoStock(c);
    });
  });
}

function renderSectorsList(sectors, sortMode) {
  const list = $('#sectors-list');
  const hot = sectors.filter(s => s.news_count > 0);
  if (!hot.length) {
    list.innerHTML = '<p class="caption dim">暂无板块新闻（先点 🔄 刷新触发 AI 评分）</p>';
    return;
  }
  // R26: 排序模式切换
  if (sortMode && sortMode !== 'score') {
    const sorted = hot.slice();
    if (sortMode === 'bull') {
      sorted.sort((a, b) => (b.bull_count - a.bull_count) || ((b.avg_score || 0) - (a.avg_score || 0)));
    } else if (sortMode === 'news') {
      sorted.sort((a, b) => (b.news_count - a.news_count));
    } else if (sortMode === 'consistency') {
      // AI 一致性 = bull/(bull+bear),利空比越低越一致
      sorted.sort((a, b) => {
        const ca = a.bull_count + a.bear_count;
        const cb = b.bull_count + b.bear_count;
        const ra = ca ? a.bull_count / ca : 0;
        const rb = cb ? b.bull_count / cb : 0;
        return rb - ra;
      });
    }
    sectors = hot.concat(sectors.filter(s => s.news_count === 0));  // 无新闻板块保持置灰在底部
    // 把排序后的 hot 拼回去: 用一个 map 然后按 hot 内顺序重排
    const sortMap = new Map(sorted.map((s, i) => [s.sw, i]));
    const remaining = sectors.filter(s => s.news_count === 0);
    sectors = [
      ...sorted,
      ...remaining,
    ];
  }
  // R3 Round 3: 31 行业热力网格 — 色阶由 avg_score 决定, 点击格子跳转对应卡片
  const _scoreBg = s => {
    const sc = s.avg_score || 0;
    if (sc >= 8) return 'rgba(229,39,74,.22)';
    if (sc >= 6) return 'rgba(232,116,30,.18)';
    if (sc >= 4) return 'rgba(179,163,0,.16)';
    if (sc >= 2) return 'rgba(47,111,176,.18)';
    return 'rgba(120,120,120,.10)';
  };
  const _scoreFg = s => {
    const sc = s.avg_score || 0;
    return sc >= 6 ? UP : sc >= 4 ? ACCENT : INK2;
  };
  const heatHtml = `
    <div class="sector-heat">
      ${sectors.map(s => `
        <div class="sh-cell" data-sh="${escapeHtml(s.sw)}"
             title="${escapeHtml(s.sw)} · 均分 ${s.avg_score || '—'} · 利好 ${s.bull_count} / 利空 ${s.bear_count} / 共 ${s.news_count} 条">
          <span class="sh-name">${escapeHtml(s.sw)}</span>
          <span class="sh-score" style="color:${_scoreFg(s)}">${s.avg_score || '—'}</span>
        </div>`).join('')}
    </div>
    <p class="caption dim" style="margin:.35rem 0 .6rem">31 行业新闻情绪热力 · 点击格子跳转下方明细 · 无新闻行业置灰</p>`;
  const cardsHtml = `
    <div class="sectors-grid">
      ${hot.map(s => {
        const bullPct = s.news_count ? Math.round(s.bull_count / s.news_count * 100) : 0;
        const bearPct = s.news_count ? Math.round(s.bear_count / s.news_count * 100) : 0;
        const sentiment = s.avg_score >= 6 ? 'hot' : s.avg_score >= 4 ? 'warm' : s.avg_score >= 2 ? 'mid' : 'cold';
        return `
        <div class="sector-card ${sentiment}" data-shcard="${escapeHtml(s.sw)}">
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
  list.innerHTML = heatHtml + cardsHtml;
  // 热力格子 → 卡片滚动 + 闪烁
  list.querySelectorAll('.sh-cell').forEach(cell => {
    cell.addEventListener('click', () => {
      const card = list.querySelector(`[data-shcard="${CSS.escape(cell.dataset.sh)}"]`);
      if (!card) return;
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('sh-flash');
      setTimeout(() => card.classList.remove('sh-flash'), 900);
    });
  });
}

// ────────────────────────────────────────────
// RELATED · 相关个股 (同 L3 产业链 / L4 细分 / 大集群 / 申万)
// ────────────────────────────────────────────
var _relatedCache = null;          // {code, ts, data}
var _relatedSparkCache = {};       // code -> sparkline rows

async function loadRelatedList(forceRefresh) {
  const meta = $('#related-meta');
  const host = $('#related-by-concept');
  if (!host) return;
  const code = window._currentStockCode || currentStockCode;
  if (!code) { host.innerHTML = '<p class="caption dim">请先查询股票</p>'; return; }
  if (!forceRefresh && _relatedCache && _relatedCache.code === code) {
    renderRelatedList(_relatedCache.data);
    return;
  }
  meta.textContent = '加载中…';
  host.innerHTML = '<p class="caption dim">扫描板块缓存匹配相关个股…</p>';
  try {
    const data = await api(`/api/stock/${code}/related_stocks?limit=24`);
    if (!data || !data.groups) throw new Error('无数据');
    _relatedCache = { code, ts: Date.now(), data };
    meta.textContent = `共 ${data.count || 0} 只 · 相关性排序`;
    // 先渲染,再后台补 5 日 sparkline — 不阻塞主列表
    renderRelatedList(data);
    const codes = Object.values(data.groups).flat().map(s => s.code).filter(Boolean);
    if (codes.length) {
      fetch('/api/stock/sparklines', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codes, days: 5 }),
      }).then(r => r.json()).then(d => {
        Object.assign(_relatedSparkCache, (d && d.data) || {});
        if (_relatedCache && _relatedCache.code === code) renderRelatedList(data);
      }).catch(() => {});
    }
  } catch (e) {
    meta.textContent = '加载失败';
    host.innerHTML = errorCard(e.message, () => loadRelatedList(true));
  }
}

function _sparkSVG(rows, w = 56, h = 18) {
  if (!rows || rows.length < 2) return '';
  const closes = rows.map(r => r && r.close).filter(v => v != null && v > 0);
  if (closes.length < 2) return '';
  const min = Math.min(...closes), max = Math.max(...closes);
  const span = (max - min) || 1;
  const pts = closes.map((v, i) =>
    `${(i / (closes.length - 1) * w).toFixed(1)},${(h - 2 - (v - min) / span * (h - 4)).toFixed(1)}`);
  const up = closes[closes.length - 1] >= closes[0];
  return `<svg class="rel-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts.join(' ')}" fill="none" stroke="${up ? UP : DOWN}" stroke-width="1.4"/></svg>`;
}

function renderRelatedList(data) {
  const host = $('#related-by-concept');
  if (!host) return;
  if (!data || !data.groups) { host.innerHTML = '<p class="caption dim">暂无相关个股</p>'; return; }
  const t = data.target || {};
  // R28: 汇总所有 group 统计
  let totalCnt = 0, allPcts = [], allAmts = [];
  Object.values(data.groups).forEach(list => {
    list.forEach(s => {
      totalCnt++;
      if (s.pct != null) allPcts.push(s.pct);
      if (s.amount) allAmts.push(s.amount);
    });
  });
  const upN = allPcts.filter(p => p > 0).length;
  const downN = allPcts.filter(p => p < 0).length;
  const flatN = allPcts.length - upN - downN;
  const avgPct = allPcts.length ? allPcts.reduce((a, b) => a + b, 0) / allPcts.length : 0;
  const totalAmt = allAmts.reduce((a, b) => a + b, 0);
  const totalAmtYi = totalAmt / 1e8;  // 亿
  // 共振:≥80% 同向 (绝对值差 < 1% 时不共振)
  const resonance = allPcts.length >= 5 && (upN / allPcts.length >= 0.8 || downN / allPcts.length >= 0.8);
  // 顶部 overview 卡
  const overviewDom = $('#related-overview');
  if (overviewDom) {
    const pctColor = avgPct > 0 ? UP : avgPct < 0 ? DOWN : INK2;
    overviewDom.innerHTML = `
      <div class="ro-row">
        <div class="ro-cell"><span class="ro-cell-label">总关联数</span><span class="ro-cell-val">${totalCnt}</span><span class="ro-cell-sub">${Object.values(data.groups).filter(l => l.length).length} 组</span></div>
        <div class="ro-cell"><span class="ro-cell-label">平均涨跌</span><span class="ro-cell-val" style="color:${pctColor}">${avgPct >= 0 ? '+' : ''}${avgPct.toFixed(2)}%</span><span class="ro-cell-sub">${upN} 涨 / ${downN} 跌 / ${flatN} 平</span></div>
        <div class="ro-cell"><span class="ro-cell-label">板块共振</span><span class="ro-cell-val" style="color:${resonance ? UP : INK2}">${resonance ? '✓ 共振' : '— 分散'}</span><span class="ro-cell-sub">${resonance ? `≥80% ${avgPct > 0 ? '同涨' : '同跌'}` : '无明显共振'}</span></div>
        <div class="ro-cell"><span class="ro-cell-label">总成交额</span><span class="ro-cell-val">${totalAmtYi >= 0.01 ? totalAmtYi.toFixed(2) : (totalAmt / 1e4).toFixed(0)}<span style="font-size:11px;color:var(--ink-2);font-weight:400;margin-left:2px">${totalAmtYi >= 0.01 ? '亿' : '万'}</span></span><span class="ro-cell-sub">实时合计</span></div>
      </div>`;
    overviewDom.hidden = false;
  }
  const groupsHtml = [
    ['same_l3', '🧬 同 L3 产业链'],
    ['same_l4', '🏷 同 L4 细分'],
    ['same_cluster', '🔭 同大集群'],
    ['same_sw', '🏢 同申万行业'],
  ].map(([key, label]) => {
    const list = data.groups[key] || [];
    if (!list.length) return '';
    // R28: group 内涨跌统计
    const gPcts = list.map(s => s.pct).filter(p => p != null);
    const gUp = gPcts.filter(p => p > 0).length;
    const gDown = gPcts.filter(p => p < 0).length;
    const gFlat = gPcts.length - gUp - gDown;
    const gAvg = gPcts.length ? gPcts.reduce((a, b) => a + b, 0) / gPcts.length : 0;
    const gResonance = gPcts.length >= 3 && (gUp / gPcts.length >= 0.8 || gDown / gPcts.length >= 0.8);
    const gAvgColor = gAvg > 0 ? UP : gAvg < 0 ? DOWN : INK2;
    const statsHtml = `
      <span class="rgh-stats">
        ${gResonance ? `<span class="rgh-resonance">${gAvg > 0 ? '▲' : '▼'} 共振</span>` : ''}
        <span class="rgh-stat ${gAvg > 0 ? 'up' : gAvg < 0 ? 'down' : 'neutral'}">均 ${gAvg >= 0 ? '+' : ''}${gAvg.toFixed(2)}%</span>
        <span class="rgh-stat up">↑ ${gUp}</span>
        <span class="rgh-stat down">↓ ${gDown}</span>
        ${gFlat ? `<span class="rgh-stat neutral">— ${gFlat}</span>` : ''}
      </span>`;
    return `
      <div class="rel-group">
        <div class="rel-group-head">
          <span>${label} <span class="dim">· ${list.length} 只</span></span>
          ${statsHtml}
        </div>
        <div class="rel-group-body">
          ${list.map(s => {
            const pct = s.pct;
            const pctColor = pct == null ? INK2 : (pct >= 0 ? UP : DOWN);
            return `
            <div class="rel-item" data-action="open-stock:${escapeHtml(s.code)}" role="button" tabindex="0"
                 title="${escapeHtml(s.name)} (${s.code}) · ${pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '—'}">
              ${_sparkSVG(_relatedSparkCache[s.code])}
              <span class="rel-name">${escapeHtml(s.name)}</span>
              <span class="rel-code dim">${escapeHtml(s.code)}</span>
              <span class="rel-pct" style="color:${pctColor}">${pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%' : '—'}</span>
            </div>`;
          }).join('')}
        </div>
      </div>`;
  }).join('');
  const l4Chips = (t.l4 || []).slice(0, 4).map(x => `<span class="chip chip-mini">${escapeHtml(x)}</span>`).join('');
  const cluChips = (t.clusters || []).slice(0, 4).map(x => `<span class="chip chip-mini">${escapeHtml(x)}</span>`).join('');
  host.innerHTML = `
    <div class="rel-target">
      ${t.l3 ? `<span class="rel-l3">L3 · ${escapeHtml(t.l3)}</span>` : ''}
      ${t.sw ? `<span class="dim">申万 · ${escapeHtml(t.sw)}</span>` : ''}
      ${l4Chips ? `<span class="rel-chips">${l4Chips}</span>` : ''}
      ${cluChips ? `<span class="rel-chips">${cluChips}</span>` : ''}
    </div>
    ${groupsHtml || '<p class="caption dim">未匹配到相关个股</p>'}
    <p class="caption dim" style="margin-top:.5rem">点击切换个股 · 小图为近 5 日走势</p>`;
}

// ────────────────────────────────────────────
// STOCK 页：板块情绪 + 相关新闻
// ────────────────────────────────────────────
// 2026-07-19: 加载个股板块角色 (龙头/中军/杂毛) — 在 stock-title 旁显示 badge
var _lastRoleCode = '';
async function _loadStockRole(code) {
  const host = $('#stock-tags-host');
  if (!host) return;
  if (code !== window._currentStockCode) return;
  if (_lastRoleCode === code) return;  // R6: dedup — 已加载过同 code
  _lastRoleCode = code;
  try {
    const env = await api(`/api/stock/${code}/role`, { signal: _stockSignal() });
    if (!env || !env.ok) return;
    if (code !== window._currentStockCode) return;
    const d = env.data || {};
    const role = d.role || '未分类';
    const roleColors = {
      '龙头':  'var(--up)',
      '中军':  'var(--accent)',
      '杂毛':  'var(--ink-4)',
      '未分类': 'var(--ink-4)',
    };
    const color = roleColors[role] || 'var(--ink-4)';
    const tip = `${d.reason || ''} · ${d.explanation || ''}`.slice(0, 120);
    host.innerHTML = `<span class="stock-role-badge" style="display:inline-block;padding:2px 10px;font-size:11px;font-weight:600;border-radius:12px;background:${color}22;color:${color};border:1px solid ${color}55;margin-left:8px;vertical-align:middle" title="${escapeHtml(tip)}">${escapeHtml(role)}</span>`;
  } catch (e) {
    console.debug('[stock-role]', e.message);
  }
}

// 2026-07-19: 加载个股周线擒牛卡 (5 大信号)
// 2026-07-19: 个股策略匹配度卡 — 数据源自策略选股器 (strategy_match),同时填充 3 明细卡
const _SM_STRATEGY = {
  wb:  { label: '周线擒牛', color: 'var(--warn)', max: 40 },
  rl:  { label: '1/3 回升位', color: 'var(--down-strong)', max: 30 },
  ma5: { label: '5日线放量', color: 'var(--accent-3)', max: 30 },
};
async function _loadStrategyMatchCard(code) {
  const card = $('#q-strategy-match-card');
  const body = $('#q-sm-body');
  if (!card || !body) return;
  if (code !== window._currentStockCode) return;
  card.hidden = false;
  body.innerHTML = '<span class="dim">加载中…</span>';
  try {
    const data = await api(`/api/stock/${code}/strategy_match`, { timeout: 10000, signal: _stockSignal() });
    if (!data) {
      body.innerHTML = `<span class="dim">策略匹配度无数据</span>`;
      return;
    }
    if (code !== window._currentStockCode) return;
    const d = data || {};
    const sc = d.score || {};
    const total = sc.total || 0;
    const max = sc.max || 100;
    const pct = Math.min(total / max * 100, 100);
    const barColor = total >= 70 ? 'var(--down-strong)' : total >= 45 ? 'var(--warn)' : total >= 25 ? 'var(--warn)' : 'var(--up-soft)';
    const matchedKeys = d.matched_keys || [];
    const showNoMatch = !matchedKeys.length;

    // 3 策略行
    const strategyRows = Object.entries(_SM_STRATEGY).map(([key, def]) => {
      const val = sc[key] != null ? sc[key] : 0;
      const pct2 = def.max > 0 ? Math.min(val / def.max * 100, 100) : 0;
      const chk = matchedKeys.includes(key) ? '✓' : '';
      const m = d[key] || {};

      let extra = '';
      if (key === 'wb' && m.count > 0) {
        const labels = { sanxing_taodi:'三星探底', zhanwen_5w:'站稳5周线', tupo_pingtai:'突破平台', tupo_pingtai_aggressive:'突破3周(激进)', junxian_fangxiang:'均线方向', zhouxian_duiliang:'周线堆量' };
        extra = '<span class="caption" style="color:var(--ink-2)">' + (m.matched || []).map(p => labels[p] || p).join(' · ') + '</span>';
      } else if (key === 'rl' && m.level_1_3 != null) {
        const near = m.near_support ? ' <span class="tag-good caption">强支撑</span>' : '';
        const dist = m.distance_to_level_1_3_pct != null
          ? `<span class="caption" style="color:${Math.abs(m.distance_to_level_1_3_pct) < 3 ? 'var(--down-strong)' : 'var(--ink-2)'}">距 1/3 位 ${(m.distance_to_level_1_3_pct >= 0 ? '+' : '') + Number(m.distance_to_level_1_3_pct).toFixed(2) + '%'}</span>`
          : '';
        extra = '<span class="caption">1/3=' + (m.level_1_3 ?? '—') + '</span> ' + dist + near;
      } else if (key === 'ma5' && m.ok) {
        extra = '<span class="caption" style="color:var(--accent-3)">' + escapeHtml((m.reason || '').slice(0, 60)) + '</span>';
      }

      return `<div class="sm-row">
        <div class="sm-row-head">
          <span><span class="sm-chk">${chk}</span><span style="color:${def.color};font-weight:600;font-size:13px">${def.label}</span></span>
          <span style="color:${def.color};font-weight:700;font-size:15px">${val}</span>
        </div>
        <div class="sm-bar"><div class="sm-bar-fill" style="width:${pct2}%;background:${def.color}"></div></div>
        <div class="sm-row-extra">${extra}</div>
      </div>`;
    }).join('');

    // 总分区
    const quality = total >= 70 ? '优' : total >= 45 ? '良' : total >= 25 ? '中' : total >= 1 ? '差' : '—';
    const qualityColor = total >= 70 ? 'var(--down-strong)' : total >= 45 ? 'var(--warn)' : total >= 25 ? 'var(--warn)' : 'var(--ink-4)';

    const breakdownHtml = showNoMatch ? ''
      : `<div class="sm-breakdown">${(sc.breakdown || []).map(b => `<span class="caption dim">${escapeHtml(b)}</span>`).join(' <span style="color:var(--ink-2);font-size:10px">·</span> ')}</div>`;

    body.innerHTML = `
      <div class="sm-total-row">
        <div class="sm-total-number" style="color:${barColor}">${total}</div>
        <div class="sm-total-meta">
          <div class="sm-total-label">/${max} · <span style="color:${qualityColor}">${quality}</span></div>
          <div class="sm-total-bar"><div class="sm-total-bar-fill" style="width:${pct}%;background:${barColor}"></div></div>
        </div>
      </div>
      ${showNoMatch ? '<p class="dim caption" style="margin:.5rem 0">当前股未命中任何策略。</p>' : ''}
      <div class="sm-strategies" style="margin-top:${showNoMatch ? '0' : '8px'}">${strategyRows}</div>
      ${breakdownHtml}
    `;
    // 用同一个 strategy_match 数据填充 3 个策略明细卡 (数据来源: 策略选股器)
    _renderWBCardFromSM(d.wb);
    _renderRecoveryCardFromSM(d.rl);
    _renderMa5CardFromSM(d.ma5_principles);
  } catch (e) {
    body.innerHTML = `<span class="dim">加载异常: ${escapeHtml(e.message)}</span>`;
  }
}

function _renderWBCardFromSM(wb) {
  const card = $('#q-buypoint-card');
  const body = $('#q-weekly-bull-body');
  const seg = $('#bp-seg-weekly');
  if (!card || !body) return;
  if (!wb || !wb.count || !wb.matched || !wb.matched.length) { if (seg) seg.classList.add('bp-seg-miss'); card.hidden = true; return; }
  if (seg) { seg.classList.remove('bp-seg-miss'); seg.classList.add('bp-seg-hit'); }
  card.hidden = false;
  const wk = wb.weekly_last || {};
  const labels = { sanxing_taodi:'三星探底', zhanwen_5w:'站稳5周线', tupo_pingtai:'突破平台', tupo_pingtai_aggressive:'突破3周(激进)', junxian_fangxiang:'均线方向', zhouxian_duiliang:'周线堆量' };
  const chips = wb.matched.map(k => `<span class="chip tag-good wb-card-chip" title="${escapeHtml((wb.reasons||{})[k]||'')}">${escapeHtml(labels[k]||k)}</span>`).join('');
  const reasonList = wb.matched.map(k => `<li><b>${escapeHtml(labels[k]||k)}</b>: ${escapeHtml((wb.reasons||{})[k]||'').slice(0,80)}</li>`).join('');
  body.innerHTML = `<div class="wb-card-chips">${chips}</div><p class="caption dim" style="margin:.25rem 0">命中 <b class="good">${wb.matched.length}/5</b> · 周收盘 ${wk.close != null ? wk.close.toFixed(2) : '—'} · 周涨跌 ${wk.change_pct != null ? (wk.change_pct >= 0 ? '+' : '') + wk.change_pct.toFixed(2) + '%' : '—'} · 5W MA ${wk.wma5 ?? '—'}</p><ul class="wb-card-reasons">${reasonList}</ul>`;
}

function _renderRecoveryCardFromSM(rl) {
  const card = $('#q-buypoint-card');
  const body = $('#q-recovery-body');
  const seg = $('#bp-seg-recovery');
  if (!card || !body) return;
  if (!rl || !rl.has_signal) { if (seg) seg.classList.add('bp-seg-miss'); return; }
  if (seg) { seg.classList.remove('bp-seg-miss'); seg.classList.add('bp-seg-hit'); }
  card.hidden = false;
  const cls = rl.near_support ? 'tag-good' : '';
  const distPct = rl.distance_to_level_1_3_pct != null ? (rl.distance_to_level_1_3_pct >= 0 ? '+' : '') + rl.distance_to_level_1_3_pct.toFixed(2) + '%' : '—';
  body.innerHTML = `<div class="recovery-grid">
    <div><span class="dim">A 谷底</span> · <b>${rl.A ?? '—'}</b> <span class="caption dim">${rl.A_date || ''}</span></div>
    <div><span class="dim">B 山顶</span> · <b>${rl.B ?? '—'}</b> <span class="caption dim">${rl.B_date || ''}</span></div>
    <div><span class="dim">涨幅</span> · <b>${rl.change_pct != null ? '+' + rl.change_pct.toFixed(2) + '%' : '—'}</b></div>
    <div><span class="dim">现价</span> · <b>${rl.current_close ?? '—'}</b></div>
    <div class="${cls}"><span class="dim">1/3 位</span> · <b>${rl.level_1_3 ?? '—'}</b> ${rl.near_support ? '<span class="tag-good caption" style="margin-left:4px">强支撑</span>' : ''}</div>
    <div><span class="dim">1/2 位</span> · <b>${rl.level_1_2 ?? '—'}</b></div>
    <div><span class="dim">2/3 位</span> · <b>${rl.level_2_3 ?? '—'}</b></div>
    <div><span class="dim">距 1/3 位</span> · <b>${distPct}</b></div>
  </div><p class="caption dim" style="margin:.25rem 0">${escapeHtml((rl.explanation || '').slice(0, 200))}</p>`;
}

function _renderMa5CardFromSM(mp) {
  const card = $('#q-buypoint-card');
  const body = $('#q-ma5-rules-body');
  const seg = $('#bp-seg-ma5');
  if (!card || !body) return;
  if (!mp || !mp.has_kline) { if (seg) seg.classList.add('bp-seg-miss'); return; }
  if (seg) { seg.classList.remove('bp-seg-miss'); seg.classList.add('bp-seg-hit'); }
  card.hidden = false;
  const deviation = mp.deviation_pct || 0;
  const belowDays = mp.below_ma5_days || 0;
  const p3 = mp.principle_3_active;
  const p4 = mp.principle_4_active;
  const p5 = mp.principle_5_active;
  const devColor = deviation > 8 ? 'var(--warn)' : deviation < -8 ? 'var(--down-strong)' : Math.abs(deviation) < 3 ? 'var(--down-strong)' : 'var(--ink-2)';
  const devSymbol = deviation > 8 ? '↑' : deviation < -8 ? '↓' : '•';
  const devText = devSymbol + (deviation >= 0 ? '+' : '') + deviation.toFixed(2) + '%';
  const allRules = [
    { n:1, label:'不放量大阳线坚决不碰', active:true, status:'—', note:'等待放量大阳线触发', color:'var(--ink-4)', hide:false },
    { n:2, label:'放量后回踩 5 日线不破 = 重点观察', active:true, status:'—', note:'等待回踩验证', color:'var(--ink-4)', hide:false },
    { n:3, label:'明显偏离 5 日线 → 兑现部分利润', active:p3, status:p3?'⚠ 偏离 '+deviation.toFixed(1)+'%':'✓ 正常', note:p3?'偏离 > 8%,注意止盈':'价格在合理范围内', color:p3?'var(--warn)':'var(--down-strong)', hide:false },
    { n:4, label:'收盘跌破 5 日线不硬抗 → 先降仓位', active:p4, status:p4?'⚠ 已跌破 '+belowDays+' 日':'✓ 站回均线', note:p4?'当前收盘在 5 日线下':'已站回 5 日线', color:p4?'var(--up-soft)':'var(--down-strong)', hide:belowDays===0 },
    { n:5, label:'连续 3 日站不回 → 直接离场', active:p5, status:p5?'⬇ '+belowDays+' 日站不回':'✓', note:p5?'趋势已变,留住本金!':'', color:p5?'var(--up-soft)':'var(--down-strong)', hide:belowDays<3 },
  ];
  const ma5Price = mp.deviation_pct != null ? '当前价偏离 5 日线 <b style="color:'+devColor+'">'+devText+'</b>' : '';
  body.innerHTML = '<div style="margin-bottom:8px;font-size:12px;color:var(--ink-2)">'+ma5Price+'</div><div style="display:flex;flex-direction:column;gap:4px">'+
    allRules.map(r => '<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;background:'+(r.active?'var(--bg-2, var(--bg-1))':'transparent')+';font-size:11.5px'+(r.hide?';display:none':'')+'">'+
      '<span style="flex-shrink:0;width:18px;height:18px;display:flex;align-items:center;justify-content:center;border-radius:50%;background:'+(r.active?r.color+'33':'var(--bg-3)')+';color:'+(r.active?r.color:'var(--ink-4)')+';font-size:10px;font-weight:700">'+r.n+'</span>'+
      '<span style="flex:1;min-width:0"><span style="color:'+(r.active?'var(--ink-1)':'var(--ink-4)')+'">'+r.label+'</span>'+
        '<span style="margin-left:4px;font-size:10px;color:'+r.color+'">'+r.status+'</span></span>'+
      (r.note?'<span class="caption" style="flex-shrink:0;color:'+r.color+'">'+r.note+'</span>':'')+
    '</div>').join('')+'</div>';
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
      <div class="kv-row"><span>市场</span><b>${escapeHtml(b.board_short || b.board_name || '—')}</b></div>
      <div class="kv-row"><span>代码前缀</span><b>${escapeHtml(b.prefix || '—')}</b></div>
      <div class="kv-row"><span>涨跌幅</span><b>±${b.pct_limit || '—'}%</b></div>
      <div class="kv-row"><span>门槛</span><b>${b.capital_floor_wan ? b.capital_floor_wan + ' 万' : '无'}</b></div>`;
    const inds = [
      ['申万',  sec.sw,    'var(--accent-3)'],
      ['证监会', sec.csrc, 'var(--accent-2)'],
      ['中证',  sec.cics,  'var(--warn)'],
      ['GICS',  sec.gics,  'var(--down-strong)'],
    ];
    const stdChips = inds.filter(([,v]) => v).map(([k,v,c]) =>
      `<span class="chip" style="border-color:${c};color:${c}">${k}·${escapeHtml(v)}</span>`
    ).join('');
    // AI 概念标（机器人/AI 各子赛道）
    const aiTags = sec.ai_tags || {labels: [], is_main_field: false};
    const aiColors = {
      '机器人本体': 'var(--warn)', '机器人零部件': 'var(--warn)', '机器视觉': 'var(--warn)',
      'AI 算力': 'var(--accent-3)', 'AI 芯片': 'var(--accent-3)', 'AI 软件': 'var(--accent-3)',
      '智能驾驶': 'var(--accent)', '半导体': 'var(--accent-2)', '新能源车': 'var(--down-strong)',
      '传统行业': 'var(--ink-4)', '未分类': 'var(--ink-4)'
    };
    const aiChips = aiTags.labels.map(l => {
      const c = aiColors[l] || 'var(--ink-4)';
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

    // 相关新闻与板块情绪互不依赖，并发加载，避免单个新闻源拖慢板块卡片。
    // R-2026-08-09: priority:'low' 让这两个文字型端点退到浏览器 fetch 低优先级队列,
    // 不抢 super card 的图表端点带宽 (chart > AI > 文字)
    const [rel, secOv] = await Promise.all([
      api(`/api/stock/${code}/related_news`, { signal: _stockSignal(), priority: 'low' }),
      api('/api/sectors/sw', { signal: _stockSignal(), priority: 'low' }),
    ]);
    const news = (rel && rel.news) || [];
    const mySector = ((secOv && secOv.sectors) || []).find(s => s.sw === sec.sw);

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
      // 2026-08-01: 命中标签 (强/弱/fallback) — 摘要区也展示,跟新闻 tab 保持一致
      const hk = n._hit_kind || (n.hit_reason && /宽口径/.test(n.hit_reason) ? 'weak' : 'strong');
      const summaryTag = hk === 'weak' ? '<span class="news-hit-tag weak" title="板块宽口径">板块</span>'
                       : hk === 'fallback' ? '<span class="news-hit-tag fallback" title="该股暂无精准新闻,展示近期财经要闻">兜底</span>'
                       : (n.hit_reason ? `<span class="news-hit-tag strong" title="${escapeHtml(n.hit_reason)}">精准</span>` : '');
      host5.innerHTML = `
        <div class="news-card ${a.score >= 7 ? 'hot' : a.score >= 4 ? 'warm' : 'cold'}">
          <div class="news-score"><div class="news-score-num" style="color:${dirColor}">${a.score.toFixed(1)}</div></div>
          <div class="news-body">
            <div class="news-title" style="font-size:.92rem">${escapeHtml(n.title)}</div>
            <div class="news-meta">
              <span class="dim">${n.ctime_str || ''} · ${escapeHtml(n.media || '')} · ${a.direction || ''}</span>
              ${summaryTag}
            </div>
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
    if (res._degraded) {
      host.innerHTML = `<p class="caption dim">⚠ 连板数据暂不可达 (${escapeHtml(res._degraded)})</p>`;
      return;
    }
    // 2026-08-04: 预拉缓存可能装的是 _prefetchStockAux timeout 兜底对象 {error: "..."},
    // _degraded 不在它身上。继续往下走 res.today 是 undefined → 后续 .slice() 抛 PAGEERROR。
    // 显式拦下任何 error 字段 + 缺关键字段 的"残缺"响应,显示降级提示
    if (res.error || (!res.today && !res.recent_5d && !res.sector_today)) {
      host.innerHTML = `<p class="caption dim">⚠ 连板数据暂不可达${res.error ? ` (${escapeHtml(res.error)})` : ' (空数据)'}</p>`;
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
      '妖股':  { bg: 'var(--up)', fg: 'var(--bg-1)' },
      '活跃':  { bg: 'var(--warn)', fg: 'var(--bg-1)' },
      '一般':  { bg: 'var(--ink-4)', fg: 'var(--bg-1)' },
      '死股':  { bg: 'var(--bg-3)', fg: 'var(--ink-2)' },
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
            style="background:${leader.streak >= 5 ? 'var(--up-strong)' : leader.streak >= 3 ? 'var(--up)' : leader.streak >= 2 ? 'var(--warn)' : 'var(--accent-3)'};color:var(--bg-1);padding:.25rem .55rem;font-weight:700;border:none"
            title="${escapeHtml(leader.reason || '')}">
        ${leader.role}${leader.is_top_in_sector ? ' · 板块最高' : ''}
      </span>
    ` : '';
    const conceptChips = (nature.concepts || []).map(c => `
      <span class="chip"
            style="border:1px solid ${c.level === 'L4' ? ACCENT : c.level === 'L3' ? 'var(--accent-2)' : INK2};color:${c.level === 'L4' ? ACCENT : c.level === 'L3' ? 'var(--accent-2)' : INK2};font-size:.78rem"
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
        <b style="color:var(--up-strong)">
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
        const tagColor = isMe ? 'var(--up-strong)' : (isMax ? 'var(--up)' : INK2);
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
              `<span class="chip" style="font-size:.7rem;padding:1px 5px;color:${c.level === 'L4' ? ACCENT : c.level === 'L3' ? 'var(--accent-2)' : INK2};border:1px solid currentColor">${escapeHtml(c.name)}</span>`
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
        const tagColor = isMe ? 'var(--up-strong)' : (isMax ? 'var(--up)' : INK2);
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
// R2: 2026-08-09 · 移动 + 横屏 + 全屏沉浸
//   - Fullscreen API + orientation.lock('landscape') 自动横屏
//   - ResizeObserver 单源 (替代 window.resize 散弹) — 监听 chart-pane 容器
//   - orientationchange / resize 双向触发 → 100ms 后强制所有 chart resize
// ═══════════════════════════════════════════════════════════
function _toggleStockFullscreen() {
  const el = document.documentElement;
  const isFs = document.fullscreenElement || document.webkitFullscreenElement;
  if (isFs) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document).catch(()=>{});
    return;
  }
  (el.requestFullscreen || el.webkitRequestFullscreen).call(el).catch(()=>{});
  // 尝试锁横屏 (iOS Safari 不支持, 静默失败)
  if (screen.orientation && screen.orientation.lock) {
    screen.orientation.lock('landscape').catch(()=>{});
  }
}
function _onFsChange() {
  const isFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
  document.body.classList.toggle('tx-stock-fullscreen', isFs);
  const btn = $('#stock-fs-btn');
  if (btn) btn.textContent = isFs ? '⛞' : '⛶';
  // 全屏变化后图表需要重算尺寸
  setTimeout(() => {
    Object.values(echartsCharts).forEach(c => { try { c.resize(); } catch (_) {} });
  }, 200);
}
document.addEventListener('fullscreenchange', _onFsChange);
document.addEventListener('webkitfullscreenchange', _onFsChange);

// 横屏变化监听: 进入横屏自动提示全屏 (但用户拒绝时不再骚扰)
let _orientationPrompted = false;
function _onOrientationChange() {
  setTimeout(() => {
    const isLand = window.matchMedia('(orientation: landscape)').matches;
    document.body.classList.toggle('tx-stock-landscape', isLand);
    Object.values(echartsCharts).forEach(c => { try { c.resize(); } catch (_) {} });
    if (isLand && !_orientationPrompted && document.body.classList.contains('tx-mobile-stock')) {
      _orientationPrompted = true;
      // 不弹 toast (打扰), 仅在 fs-btn 上闪一下提示
      const btn = $('#stock-fs-btn');
      if (btn) {
        btn.classList.add('pulse');
        setTimeout(() => btn.classList.remove('pulse'), 2500);
      }
    }
  }, 280);
}
window.addEventListener('orientationchange', _onOrientationChange);
window.matchMedia('(orientation: landscape)').addEventListener?.('change', _onOrientationChange);

// ResizeObserver 单源 — 监听所有 chart pane,容器尺寸变 → 该 pane 内 ECharts resize
const _chartRO = new ResizeObserver((entries) => {
  for (const e of entries) {
    const pane = e.target.closest('[data-tab-pane]');
    if (!pane) continue;
    const id = pane.dataset.tabPane;
    // 找 pane 内第一个 echartsCharts 实例 (按 id 映射)
    const chartKey = id === 'intraday' ? 'intraDay' : id === 'kline' ? 'kline' : id === 'flow' ? 'flow' : null;
    if (chartKey && echartsCharts[chartKey]) {
      try { echartsCharts[chartKey].resize(); } catch (_) {}
    }
  }
});
function _observeChartPanes() {
  $$('[data-tab-pane]').forEach(p => { try { _chartRO.observe(p); } catch (_) {} });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _observeChartPanes, { once: true });
} else {
  _observeChartPanes();
}

// 全屏按钮绑定 (在 DOMContentLoaded 后)
function _bindFsBtn() {
  const btn = $('#stock-fs-btn');
  if (!btn) return;
  btn.addEventListener('click', _toggleStockFullscreen);
  // 双击 K线工具栏 = 全屏快捷
  const tb = $('.kline-toolbar');
  if (tb && !tb._fsDblBound) {
    tb._fsDblBound = true;
    tb.addEventListener('dblclick', (e) => {
      if (e.target.closest('button, input, select, .kt-pill, .kt-chip')) return;
      _toggleStockFullscreen();
    });
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _bindFsBtn, { once: true });
} else {
  _bindFsBtn();
}

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

  // R-2026-08-09: 合并 stock-limit-up-card — 在 streak 卡片底部追加 连板梯队 + 板块联动 (板块当日涨停清单)
  const sectorHost = document.createElement('div');
  sectorHost.id = 'stock-limit-up-body';
  sectorHost.style.cssText = 'margin-top:.8rem;padding-top:.8rem;border-top:1px dashed var(--line)';
  host.appendChild(sectorHost);
  loadStockLimitUp(code, sectorName).catch(() => {});
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
      const fg = lb >= 5 ? 'var(--up-strong)' : lb >= 3 ? 'var(--up)' : lb >= 2 ? 'var(--warn)' : 'var(--accent)';
      const tm = today.首次封板时间 ? String(today.首次封板时间).replace(/^(\d{2})(\d{2})\d{2}$/, '$1:$2') : '—';
      const seal = today.封单金额 ? (today.封单金额 / 1e8).toFixed(2) + ' 亿' : '—';
      const burst = today.炸板次数 ? ` · 炸板 ${today.炸板次数}` : '';
      const ztj = today.涨停统计 ? ` · 涨停统计 ${today.涨停统计}` : '';
      return `<span class="chip" style="background:${fg};color:var(--bg-1);border:none;font-weight:700">${fire} 今日 ${lb} 板 · 封单 ${seal} · 首封 ${tm}${burst}${ztj}</span>`;
    }
    return `<span class="chip" style="background:rgba(255,255,255,.06);color:var(--ink-2)">今日未涨停</span>`;
  })();

  const leaderHtml = (() => {
    if (!leader || !leader.role || leader.role === '—') return '';
    const sl = leader.sector_leader;
    const slHtml = sl
      ? `<span class="chip" style="background:var(--bg-1)8e1;border-color:var(--warn);color:var(--up-strong);cursor:pointer" data-action="open-stock:${escapeHtml(sl.code)}" title="点击查看 ${escapeHtml(sl.name)}">👑 板块龙头 ${escapeHtml(sl.name)} · ${sl.streak} 板${sl.封单金额 ? ' · 封单 ' + (sl.封单金额 / 1e8).toFixed(2) + ' 亿' : ''}</span>`
      : '';
    const streakColor = leader.streak >= 5 ? 'var(--up-strong)' : leader.streak >= 3 ? 'var(--up)' : leader.streak >= 2 ? 'var(--warn)' : 'var(--accent-3)';
    const roleChip = leader.role !== '—'
      ? `<span class="chip" style="background:${streakColor};color:var(--bg-1);border:none;font-weight:700" title="${escapeHtml(leader.reason || '')}">${escapeHtml(leader.role)}${leader.is_top_in_sector ? ' · 板块最高' : ''}</span>`
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
        const fg = lb >= 5 ? 'var(--up-strong)' : lb >= 3 ? 'var(--up)' : lb >= 2 ? 'var(--warn)' : 'var(--accent)';
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
        const fg = s.change_pct >= 9.95 ? 'var(--up-strong)' : s.change_pct >= 5 ? 'var(--up)' : s.change_pct >= 0 ? 'var(--up)' : 'var(--down)';
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
_onDomReady(() => {
  const nr = $('#news-refresh-btn');      if (nr) nr.addEventListener('click', () => loadNewsList(true));
  const sr = $('#sectors-refresh-btn');  if (sr) sr.addEventListener('click', () => loadSectorsList(true));
  const rr = $('#related-refresh-btn');   if (rr) rr.addEventListener('click', () => loadRelatedList(true));
});

// ═══════════════════════════════════════════════════════════
// REVIEW 复盘 view · 铁律冲突 + 资金占比 + AI 建议 (2026-07-10)
// ═══════════════════════════════════════════════════════════

var _reviewState = {
  trades: [],
  flows: new Map(),   // code -> {main_pct, retail_pct, fund_pct, ...}
  flowsTimer: null,
};


// ═════════════════════════════════════════════════════════════════
// ⭐ 2026-07-19: 预取相邻个股/core — 让下次点击从 SW 缓存读 < 5ms
// ═════════════════════════════════════════════════════════════════
// 2026-07-21: 卡死修复 — 相邻个股预取原来每次 loadStockDetail 立即发最多 8 个并发 /core,
// 频繁切股时 = N×8 个连接飞行(5s 超时),把连接池占满 → 卡死。
// 改:1) settle 去抖 1.2s — 只在用户停在某只股上才预取,切走前不发;
//     2) 全局可取消 controller — 下次切股 abort 上一批预取;3) 候选压到 4 个。
var _prefetchTimer = null;
var _prefetchCtrl = null;
function _cancelAdjacentPrefetch() {
  if (_prefetchTimer) { clearTimeout(_prefetchTimer); _prefetchTimer = null; }
  if (_prefetchCtrl) { try { _prefetchCtrl.abort(); } catch {} _prefetchCtrl = null; }
}
function _prefetchAdjacentStocks(currentCode) {
  // 先取消上一轮:切股频繁时旧预取立即作废,不占连接
  _cancelAdjacentPrefetch();
  // R-fix-2026-08-02: 6 位数字白名单 — 自选股 / 历史记录里有占位符
  // (999991, 301668, 600663, 300996, 002879, 000977, 000011 等)
  // 触发 /api/stock/{code}/core → 503 浪费 6 连接池 + 阻塞真实流量。
  const isValidCode = c => typeof c === "string" && /^\d{6}$/.test(c);
  const candidates = new Set();
  // 1. 最近浏览 (历史记录, 最多 5 只)
  try {
    const raw = sessionStorage.getItem("tx3_stock_hist") || "[]";
    const hist = JSON.parse(raw);
    for (const h of hist) {
      if (isValidCode(h.code) && h.code !== currentCode) candidates.add(h.code);
      if (candidates.size >= 4) break;
    }
  } catch (e) {}
  // 2. 自选股 (从 watchlist cookie / storage)
  try {
    const wlRaw = sessionStorage.getItem("tx3_watchlist") || localStorage.getItem("tx3_watchlist") || "[]";
    const wl = JSON.parse(wlRaw);
    for (const w of (Array.isArray(wl) ? wl : (wl.data?.items || []))) {
      const c = w.code || w;
      if (isValidCode(c) && c !== currentCode) candidates.add(c);
      if (candidates.size >= 4) break;
    }
  } catch (e) {}
  if (!candidates.size) return;
  // 3. settle 去抖:1.2s 内又切股则本轮预取被 _cancelAdjacentPrefetch 取消,一枪不发
  _prefetchTimer = setTimeout(() => {
    // 用户仍停在该股才预取 (防切走后还发)
    if (window._currentStockCode !== currentCode) return;
    _prefetchCtrl = new AbortController();
    setTimeout(() => { if (_prefetchCtrl) { try { _prefetchCtrl.abort(); } catch {} } }, 5000);
    for (const c of candidates) {
      fetch("/api/stock/" + c + "/core", {
        signal: _prefetchCtrl.signal,
        cache: "force-cache",  // 让 SW 可以缓存
      }).catch(() => {});  // 静默失败
    }
  }, 1200);
}

// ─── R-fix-2026-07-30: AI 深度判断 (公司业务 / 业绩跳变 / 持仓 / 技术 / 同业 PE) ───
const _DEEP_POLL_INTERVAL = 2000; // 后台任务轮询 2s
const _DEEP_POLL_MAX = 15;        // 最多 15 次 = 30s
const _DEEP_ACTION_META = {
  '加仓': { color: 'var(--up)', icon: '↑' },
  '继续持有': { color: 'var(--accent-2)', icon: '✓' },
  '减仓': { color: 'var(--accent)', icon: '↓' },
  '清仓': { color: 'var(--down)', icon: '×' },
  '观望': { color: 'var(--dim)', icon: '·' },
};

async function loadStockDeepAnalysis(code) {
  // 1) Reset UI
  const $ = sel => document.querySelector(sel);
  const fields = ['#deep-status','#deep-action-chip','#deep-score','#deep-profile-text','#deep-profile-meta',
                  '#deep-earnings-body','#deep-jump-chip','#deep-holding-view','#deep-tech-view','#deep-summary-text'];
  for (const f of fields) $(f).textContent = '';
  const setTxt = (sel, t) => { const el=$(sel); if (el) el.textContent = t; };
  setTxt('#deep-action-chip', '拉取中…');
  setTxt('#deep-status', '约 8s');

  let curRunId = null;
  let queueState = 'fetching';
  try {
    // 后台 fire-and-forget,1s 内返 (命中缓存更短)
    const envQ = await apiRaw(`/api/stock/${code}/deep_analysis?background=1`, { method: 'GET' });
    const dq = envQ.json ? await envQ.json() : envQ;
    if (dq && dq.data && dq.data.queued) {
      curRunId = dq.data.run_id;
      queueState = 'background';
    } else if (dq && dq.data && dq.data.from_cache) {
      // 已经缓存命中 — 直接渲染
      renderDeepAnalysis(dq.data);
      return;
    }
  } catch (e) {
    setTxt('#deep-status', '后台排队失败');
  }

  // 2) 轮询 result (background 模式)
  if (queueState === 'background' && curRunId) {
    for (let i = 0; i < _DEEP_POLL_MAX; i++) {
      await new Promise(r => setTimeout(r, _DEEP_POLL_INTERVAL));
      try {
        const r = await apiRaw(`/api/stock/${code}/deep_analysis/result?run_id=${curRunId}`);
        const envR = r.json ? await r.json() : r;
        if (envR && envR.data && envR.data.from_cache) {
          renderDeepAnalysis(envR.data);
          return;
        }
        if (envR && envR.data && envR.data.ready) {
          renderDeepAnalysis(envR.data);
          return;
        }
      } catch (e) { /* keep polling */ }
    }
    setTxt('#deep-status', '后台超时 (8s+),已降级');
  }

  // 3) 兜底同步路径
  try {
    const r = await apiRaw(`/api/stock/${code}/deep_analysis?background=0`);
    const envD = r.json ? await r.json() : r;
    if (envD && envD.data) renderDeepAnalysis(envD.data);
  } catch (e) {
    setTxt('#deep-action-chip', '分析失败');
    setTxt('#deep-status', e && e.message || '网络错误');
  }
}

// ─── R-fix-2026-08-01: 公司画像 (营业范围 / 主营 / 行业地位 / 多板块) ───
function _renderProfileBars(byProduct) {
  if (!byProduct || !byProduct.length) return '<div class="caption dim">— 暂无主营构成 —</div>';
  const max = Math.max(...byProduct.map(p => p.ratio_pct || 0), 0.01);
  return byProduct.map(p => {
    const w = Math.min(100, ((p.ratio_pct || 0) / max) * 100);
    const meta = [
      `<b>${(p.income_yi ?? 0).toFixed(0)}</b>亿`,
      `${(p.ratio_pct ?? 0).toFixed(1)}%`,
      p.gross_margin_pct != null ? `毛利 <b>${p.gross_margin_pct.toFixed(1)}%</b>` : '',
    ].filter(Boolean).join(' · ');
    return `
      <div class="prof-bar-row" title="${escapeHtml(p.name || '')} · ${meta}">
        <span class="prof-bar-name">${escapeHtml(p.name || '—')}</span>
        <span class="prof-bar-track"><span class="prof-bar-fill" style="width:${w.toFixed(1)}%"></span></span>
        <span class="prof-bar-meta">${meta}</span>
      </div>`;
  }).join('');
}

function _renderProfileChips(concepts, maxChips = 16) {
  if (!concepts || !concepts.length) return '<span class="caption dim">— 暂无概念板块 —</span>';
  // 排序: 精确概念在前, 然后按 rank
  const sorted = concepts.slice().sort((a, b) => {
    if (a.is_precise && !b.is_precise) return -1;
    if (!a.is_precise && b.is_precise) return 1;
    return (a.rank || 99) - (b.rank || 99);
  });
  const visible = sorted.slice(0, maxChips);
  const hiddenCount = sorted.length - visible.length;
  const html = visible.map(c => {
    const cls = c.is_precise ? 'prof-chip prof-chip-precise' : 'prof-chip';
    const rank = c.rank ? `<span class="prof-chip-rank">#${c.rank}</span>` : '';
    return `<span class="${cls}" title="排名 ${c.rank || '—'} · ${c.is_precise ? '精准命中' : '相关概念'}">${escapeHtml(c.name || '—')}${rank}</span>`;
  }).join('');
  return html + (hiddenCount > 0 ? `<span class="prof-chip" title="${sorted.length - maxChips} 个未显示">+${hiddenCount}</span>` : '');
}

function renderProfile(pack) {
  const $ = sel => document.querySelector(sel);
  const card = $('#stock-profile-card');
  if (!card) return;
  if (!pack || (!pack.profile && !pack.biz_breakdown && !pack.concepts_pack)) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const profile = pack.profile || {};
  const biz = pack.biz_breakdown || {};
  const conc = pack.concepts_pack || {};
  const meta = pack.profile_meta || {};

  // 行 1: 营业范围 / 简介
  const scopeEl = $('#prof-scope-text');
  if (scopeEl) {
    const biz_summary = profile.business_summary || '';
    const biz_scope = biz.scope || profile.business_scope || '';
    let text = '';
    if (biz_scope) {
      // 截前 200 字 + ellipsis (长尾展开)
      const cut = biz_scope.length > 220;
      text = cut ? biz_scope.slice(0, 220) + '…' : biz_scope;
    } else if (biz_summary) {
      const cut = biz_summary.length > 220;
      text = cut ? biz_summary.slice(0, 220) + '…' : biz_summary;
    } else {
      text = '（暂未拉到该股的营业范围）';
    }
    scopeEl.textContent = text;
  }

  // 行 2: 主营产品 (横条) + 地区拆分
  const barsEl = $('#prof-products-bars');
  if (barsEl) {
    barsEl.innerHTML = _renderProfileBars(biz.by_product || []);
  }
  const regionsEl = $('#prof-regions-text');
  if (regionsEl) {
    const regions = biz.by_region || [];
    if (regions.length) {
      const rtxt = regions.map(r => `${r.name} ${(r.ratio_pct ?? 0).toFixed(1)}%`).join(' · ');
      regionsEl.textContent = `地区: ${rtxt}`;
    } else {
      regionsEl.textContent = '';
    }
  }
  const reportEl = $('#prof-report-date');
  if (reportEl) {
    reportEl.textContent = biz.report_date ? `${biz.report_date} 报告期` : '';
  }

  // 行 3: 行业地位
  const posEl = $('#prof-position-text');
  if (posEl) {
    let posText = conc.industry_position || '';
    if (!posText) {
      // 退而求其次: 主营业务核心题材第 1 条
      const mainBiz = (conc.hot_tags || []).find(t => t.classif === '主营业务');
      posText = mainBiz ? mainBiz.content : '';
    }
    if (!posText) {
      // 再退: 行业背景
      const bg = (conc.hot_tags || []).find(t => t.classif === '行业背景');
      posText = bg ? bg.content : '';
    }
    posEl.textContent = posText ? (posText.length > 200 ? posText.slice(0, 200) + '…' : posText) : '（暂无行业地位描述）';
  }

  // 行 4: 多板块 (chip)
  const chipsEl = $('#prof-concepts-chips');
  if (chipsEl) {
    chipsEl.innerHTML = _renderProfileChips(conc.concepts || [], 16);
  }
  const cntEl = $('#prof-concept-count');
  if (cntEl) {
    const total = meta.concept_count || (conc.concepts || []).length || 0;
    const precise = meta.precise_concept_count || 0;
    cntEl.textContent = total ? `(共 ${total} 个 · 精准 ${precise})` : '';
  }
  const noteEl = $('#prof-precise-note');
  if (noteEl) {
    const total = (conc.concepts || []).length;
    const precise = (conc.concepts || []).filter(c => c.is_precise).length;
    if (total >= 3 && precise >= 1) {
      noteEl.textContent = `✨ 多板块重合度高,可作题材共振依据`;
    } else if (total === 0) {
      noteEl.textContent = '';
    } else {
      noteEl.textContent = '';
    }
  }
  // 顶部 meta 行
  const metaRow = $('#profile-meta-row');
  if (metaRow) {
    const parts = [];
    if (profile.industry_sw) parts.push(`行业 ${profile.industry_sw}`);
    if (meta.product_count) parts.push(`${meta.product_count} 类产品`);
    if (meta.concept_count) parts.push(`${meta.concept_count} 概念`);
    metaRow.textContent = parts.join(' · ');
  }
}

function loadStockProfile(code) {
  // 1) 优先 _stockAuxCache 已有的 (来自 /full 预取)
  if (_stockAuxCache && _stockAuxCache.code === code && _stockAuxCache.profile) {
    renderProfile(_stockAuxCache.profile);
    return;
  }
  // 2) 没拿到 → 单端点 fetch, 6h Redis 缓存,不会拖慢主路径
  api(`/api/stock/${code}/profile`).then(r => {
    if (_currentStockCode !== code) return;
    if (r && r.data) {
      _stockAuxCache.profile = r.data;
      renderProfile(r.data);
    } else {
      renderProfile(null);
    }
  }).catch(e => {
    console.warn('loadStockProfile fail', e);
    if (_currentStockCode === code) renderProfile(null);
  });
}

function renderDeepAnalysis(data) {
  if (!data) return;
  const $ = sel => document.querySelector(sel);
  const setTxt = (sel, t) => { const el = $(sel); if (el) el.textContent = t; };

  // 降级状态: 数据拉取超时或 LLM 不可用
  if (data._degraded) {
    setTxt('#deep-action-chip', '⚠ 降级');
    setTxt('#deep-status', '数据拉取超时,显示缓存或兜底数据');
    setTxt('#deep-score', '—');
    const chip = $('#deep-action-chip');
    if (chip) { chip.style.background = 'var(--dim)'; chip.style.color = 'var(--ink-inverse)'; }
    // 仍尝试渲染已有的基本面/技术数据(如果有)
    if (!data.fundamentals && !data.holding && !data.tech_position) {
      setTxt('#deep-summary-text', 'AI 深度分析暂不可用,请稍后重试或点击强制刷新。');
      return;
    }
  }

  // 1) 顶部 action chip + score
  const action = data.recommendation_action || data.action || (data._degraded ? '观望' : '继续持有');
  const meta = _DEEP_ACTION_META[action] || _DEEP_ACTION_META['继续持有'];
  const chip = $('#deep-action-chip');
  if (chip) {
    chip.textContent = `${meta.icon} ${action}`;
    chip.style.background = meta.color;
    chip.style.color = 'var(--ink-inverse)';
  }
  const score = data.profit_taking_score != null ? data.profit_taking_score :
                (data.conviction != null ? data.conviction : 50);
  setTxt('#deep-score', `${score}/100`);
  setTxt('#deep-pending', '');

  // 2) 状态标
  const cacheTxt = data.from_cache ? `缓存 ${Math.round((Date.now()/1000 - (data.ts || 0))/60)} 分钟前` : '刚刚拉取';
  setTxt('#deep-status', cacheTxt);

  // 3) 公司业务
  const fund = data.fundamentals || {};
  const profile = fund.profile || {};
  const bizText = profile.business_summary || profile.business_scope || '';
  setTxt('#deep-profile-text', bizText || '（暂无业务数据）');
  const metaDiv = $('#deep-profile-meta');
  if (metaDiv) {
    const indTags = [profile.industry_sw, profile.industry_csrc].filter(Boolean).map(x =>
      `<span class="deep-meta-chip">${escapeHtml(x)}</span>`).join('');
    const empTag = profile.emp_num ? `<span class="deep-meta-chip">员工 ${profile.emp_num.toLocaleString()}</span>` : '';
    const addressTag = profile.address ? `<span class="deep-meta-chip">📍 ${escapeHtml(profile.address)}</span>` : '';
    metaDiv.innerHTML = indTags + empTag + addressTag;
  }

  // 4) 业绩表
  const finBody = $('#deep-earnings-body');
  if (finBody) {
    const fins = fund.financials || [];
    if (fins.length === 0) {
      finBody.innerHTML = '<tr><td colspan="7" class="empty">暂未拉到业绩快报</td></tr>';
    } else {
      finBody.innerHTML = fins.map(f => {
        const yoyRev = f.revenue_yoy_pct;
        const yoyNp = f.netprofit_yoy_pct;
        const clsRev = (yoyRev || 0) >= 0 ? 'pos' : 'neg';
        const clsNp = (yoyNp || 0) >= 0 ? 'pos' : 'neg';
        return `
          <tr>
            <td>${escapeHtml(f.period || f.period_label || '—')}</td>
            <td>${f.revenue_yi?.toFixed(0) ?? '—'}</td>
            <td class="${clsRev}">${yoyRev == null ? '—' : (yoyRev >= 0 ? '+' : '') + yoyRev.toFixed(1) + '%'}</td>
            <td>${f.netprofit_yi?.toFixed(0) ?? '—'}</td>
            <td class="${clsNp}">${yoyNp == null ? '—' : (yoyNp >= 0 ? '+' : '') + yoyNp.toFixed(1) + '%'}</td>
            <td>${f.eps?.toFixed(2) ?? '—'}</td>
            <td>${f.roe_pct?.toFixed(1) ?? '—'}%</td>
          </tr>`;
      }).join('');
    }
  }

  // 业绩跳变 chip
  const jumpChip = $('#deep-jump-chip');
  const jumpInfo = fund.earnings_jump || {};
  if (jumpChip) {
    if (jumpInfo.jump) {
      const reasons = (jumpInfo.reasons || []).slice(0, 2).join(' · ');
      jumpChip.textContent = `⚠ 业绩跳变 · ${reasons}`;
      jumpChip.hidden = false;
    } else {
      jumpChip.hidden = true;
    }
  }

  // 5) 持仓盈亏
  const hold = data.holding || {};
  const holdView = $('#deep-holding-view');
  if (holdView) {
    if (hold.has_position) {
      const sign = hold.unrealized_pnl_pct >= 0 ? '+' : '';
      const pnlColor = hold.unrealized_pnl_pct >= 0 ? 'var(--up)' : 'var(--down)';
      holdView.innerHTML = `
        <div class="deep-hold-grid">
          <div><span class="dim">成本</span><b>¥${hold.avg_cost?.toFixed(2) ?? '—'}</b></div>
          <div><span class="dim">现价</span><b>¥${hold.last_price?.toFixed(2) ?? '—'}</b></div>
          <div><span class="dim">股数</span><b>${hold.shares?.toLocaleString() ?? '—'}</b></div>
          <div><span class="dim">市值</span><b>¥${hold.market_value?.toFixed(0) ?? '—'}</b></div>
          <div><span class="dim">浮盈</span><b style="color:${pnlColor}">${sign}${hold.unrealized_pnl_pct?.toFixed(2) ?? '—'}%</b></div>
          <div><span class="dim">盈亏额</span><b style="color:${pnlColor}">${sign}${(hold.unrealized_pnl_yuan/10000).toFixed(2)} 万</b></div>
          <div><span class="dim">持有</span><b>${hold.days_held ?? 0} 天</b></div>
          <div><span class="dim">末次加仓</span><b>${hold.last_buy_date || '—'} ¥${hold.last_buy_price?.toFixed(2) ?? '—'}</b></div>
        </div>`;
    } else {
      holdView.innerHTML = `<div class="deep-hold-empty">用户无持仓 · 按策略 + 技术面 + 业绩综合建议</div>`;
    }
  }

  // 6) 技术位置 + 同业 PE
  const tech = data.tech_position || {};
  const techView = $('#deep-tech-view');
  if (techView) {
    if (tech.has_data) {
      const pos60 = tech.pct_position_60d ?? 50;
      const pos252 = tech.pct_position_252d ?? 50;
      const rev60 = tech.pullback_from_60d_high_pct ?? 0;
      const trends = tech.trend_label || '—';
      const spark = pos60.toFixed(0);
      const bar60 = `<div class="deep-bar"><span class="deep-bar-fill" style="width:${Math.max(0,Math.min(100,pos60))}%;"></span></div>`;
      const bar252 = `<div class="deep-bar"><span class="deep-bar-fill" style="width:${Math.max(0,Math.min(100,pos252))}%;"></span></div>`;
      techView.innerHTML = `
        <div class="deep-tech-grid-inner">
          <div><span class="dim">60 日位置</span><b>${spark}%</b>${bar60}</div>
          <div><span class="dim">52 周位置</span><b>${pos252.toFixed(0)}%</b>${bar252}</div>
          <div><span class="dim">60 日高点回撤</span><b>${rev60 >= 0 ? '+' : ''}${rev60.toFixed(2)}%</b></div>
          <div><span class="dim">趋势判定</span><b>${escapeHtml(trends)}</b></div>
          <div><span class="dim">MA5 乖离</span><b>${tech.bias_ma5?.toFixed(2) ?? '—'}%</b></div>
          <div><span class="dim">MA20 乖离</span><b>${tech.bias_ma20?.toFixed(2) ?? '—'}%</b></div>
          <div><span class="dim">突破带</span><b style="color:${tech.breakout_zone ? 'var(--up)' : 'var(--dim)'}">${tech.breakout_zone ? '在 ±2% 区间' : '—'}</b></div>
          <div><span class="dim">支撑带</span><b style="color:${tech.support_zone ? 'var(--up)' : 'var(--dim)'}">${tech.support_zone ? '在 ±5% 区间' : '—'}</b></div>
        </div>`;
    } else {
      techView.innerHTML = '<div class="dim">技术数据不足</div>';
    }
  }

  // 7) AI 总结
  const summary = (data.summary || (hold.has_position ? `基于您的持仓成本与浮盈率 + 公司业务/业绩状态 + 技术位置综合建议: <b>${action}</b>` : `基于公司业务 + 业绩状态 + 技术位置综合建议: <b>${action}</b>`));
  const holdingAdvice = data.holding_advice || {};
  let adviceLine = '';
  if (holdingAdvice.stop_loss || holdingAdvice.target_price) {
    const sl = holdingAdvice.stop_loss ? `止损 ${holdingAdvice.stop_loss}` : '';
    const tp = holdingAdvice.target_price ? `目标 ${holdingAdvice.target_price}` : '';
    const hd = holdingAdvice.horizon_days ? `${holdingAdvice.horizon_days} 日` : '';
    const parts = [sl, tp, hd].filter(Boolean).join(' · ');
    if (parts) adviceLine = `<div class="deep-advice-line">${escapeHtml(parts)}</div>`;
  }
  setTxt('#deep-summary-text', summary + adviceLine);

  // 显示 refresh 按钮 (允许 user 强刷)
  const refresh = $('#deep-refresh');
  if (refresh) refresh.hidden = false;
}

document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'deep-refresh') {
    const code = (typeof _currentStockCode === 'function' ? _currentStockCode() :
                  window._currentStockCode || document.getElementById('stock-code')?.textContent || '').trim();
    if (/^\d{6}$/.test(code)) {
      document.getElementById('deep-refresh').textContent = '强制拉取中…';
      apiRaw(`/api/stock/${code}/deep_analysis?background=0&refresh=1`)
        .then(() => loadStockDeepAnalysis(code))
        .finally(() => {
          const btn = document.getElementById('deep-refresh');
          if (btn) btn.textContent = '🔄 强刷';
        });
    }
  }
});

window.__tx3StockLoadStockDetail = loadStockDetail;
