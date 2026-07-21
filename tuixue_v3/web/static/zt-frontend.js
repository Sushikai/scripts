(function(){
'use strict';

const $mount = document.getElementById('zt-mount');
if (!$mount) return;

// ── State ──
let _params = null;
let _lastResult = null;
let _sortKey = 'weighted_score';
let _sortDir = 'desc';

// ── Load params on init ──
(async function init(){
  await _loadParams();
  render();
})();

async function _loadParams() {
  try {
    const r = await api('/api/zt/params');
    // api() 剥 envelope → r.data 已展平 → 直接 r.data?.params
    _params = (r && r.params) || (r && r.data && r.data.params) || r || null;
  } catch(e) {
    _params = null;
  }
}

async function _runBacktest(start, end) {
  const url = `/api/zt/backtest?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  try {
    const data = await api(url);
    // api() 已经剥过 envelope → 直接是 result dict
    _lastResult = data;
    _sortKey = 'weighted_score';
    _sortDir = 'desc';
    render();
  } catch(e) {
    _lastResult = {error: e.message};
    render();
  }
}

// ── 多因子排序列定义 ──
const _ZT_SORT_COLS = [
  { key: 'weighted_score',  label: '加权',     fmt: v => v==null?'—':v.toFixed(2) },
  { key: 'return_pct',      label: '主退场%',  fmt: v => v==null?'—':(v>=0?'+':'')+v.toFixed(2)+'%' },
  { key: 'close_t1',        label: 'T+1收%',   fmt: v => v==null?'—':(v>=0?'+':'')+v.toFixed(2)+'%', get: r => (r.exits_pct||{}).close_t1 },
  { key: 'trail_t2',        label: 'trail%',   fmt: v => v==null?'—':(v>=0?'+':'')+v.toFixed(2)+'%', get: r => (r.exits_pct||{}).trail_t2 },
  { key: 'best',            label: '理论最',   fmt: v => v==null?'—':(v>=0?'+':'')+v.toFixed(2)+'%', get: r => (r.exits_pct||{}).best },
  { key: 'gap_score',       label: 'gap',      fmt: v => v==null?'—':(v>=0?'+':'')+v.toFixed(2) },
  { key: 'trail_strength',  label: '捕获',     fmt: v => v==null?'—':(v*100).toFixed(0)+'%' },
  { key: 'efficiency',      label: '效率',     fmt: v => v==null?'—':(v*100).toFixed(0)+'%' },
  { key: 'streak_factor',   label: '连板强度', fmt: v => v==null?'—':(v*3).toFixed(1) },
  { key: 'streak',          label: '连板数',   fmt: v => v??'—' },
  { key: 'hold_days',       label: '持有天',   fmt: v => v??'—' },
  { key: 'continued_zt',    label: '续板',     fmt: v => v?'✓':'' },
  { key: 'code',            label: '代码',     fmt: v => v??'—' },
];

function _sortTrades(trades) {
  const col = _ZT_SORT_COLS.find(c => c.key === _sortKey) || _ZT_SORT_COLS[0];
  const getVal = col.get || (r => r[col.key]);
  const dir = _sortDir === 'asc' ? 1 : -1;
  return [...(trades||[])].sort((a, b) => {
    const va = getVal(a), vb = getVal(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string') return va.localeCompare(vb) * dir;
    return (va - vb) * dir;
  });
}

function _renderTradesCard(r) {
  const trades = r.trades || [];
  if (!trades.length) return '';
  const sorted = _sortTrades(trades);

  // 列定义 (代码+名称 + 因子列)
  const cols = [
    { render: (t, body) => `<b>${t.code||''}</b> ${(t.name||'').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'})[c])}` },
    _ZT_SORT_COLS.find(c => c.key==='weighted_score'),
    _ZT_SORT_COLS.find(c => c.key==='return_pct'),
    _ZT_SORT_COLS.find(c => c.key==='close_t1'),
    _ZT_SORT_COLS.find(c => c.key==='trail_t2'),
    _ZT_SORT_COLS.find(c => c.key==='best'),
    _ZT_SORT_COLS.find(c => c.key==='gap_score'),
    _ZT_SORT_COLS.find(c => c.key==='trail_strength'),
    _ZT_SORT_COLS.find(c => c.key==='streak'),
    _ZT_SORT_COLS.find(c => c.key==='continued_zt'),
  ].filter(Boolean);

  const sortInd = (k) => k === _sortKey ? (_sortDir === 'asc' ? '▲' : '▼') : '';
  const header = cols.map((c, i) => {
    if (i === 0) return `<th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--line-soft);font-weight:600;">代码 名称</th>`;
    return `<th data-key="${c.key}" style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);cursor:pointer;user-select:none;font-weight:600;">
      ${c.label} <span style="color:var(--accent-2);font-size:10px;">${sortInd(c.key)}</span>
    </th>`;
  }).join('');

  const rows = sorted.slice(0, 80).map(t => {
    const cells = cols.map((c, i) => {
      if (i === 0) return `<td style="padding:3px 8px;border-bottom:1px solid var(--line-soft);font-size:11.5px;">${c.render(t)}</td>`;
      const v = (c.get ? c.get(t) : t[c.key]);
      const txt = c.fmt(v);
      const color = (typeof v === 'number' && (c.key === 'return_pct' || ['close_t1','trail_t2','best'].includes(c.key)))
        ? (v > 0 ? 'var(--accent-2)' : v < 0 ? 'var(--color-danger)' : '')
        : '';
      return `<td style="text-align:right;padding:3px 8px;border-bottom:1px solid var(--line-soft);font-size:11.5px;color:${color};">${txt}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  return `
    <div class="card">
      <div class="card-head">
        <span>交易明细 <span style="font-size:11px;font-weight:400;color:var(--ink-4);">(${sorted.length} 笔 · 默认按加权降序 · 点击表头重排)</span></span>
      </div>
      <div style="padding:8px 12px;overflow-x:auto;">
        <table class="data-table" id="zt-trades-table" style="width:100%;font-size:12px;border-collapse:collapse;min-width:900px;">
          <thead><tr>${header}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${sorted.length > 80 ? `<div style="text-align:center;padding:8px;font-size:11px;color:var(--ink-3);">仅显示前 80 / ${sorted.length} 笔</div>` : ''}
      </div>
      <div style="padding:6px 12px;font-size:11px;color:var(--ink-3);border-top:1px solid var(--line-soft);">
        <b>加权 = 0.25·gap + 0.30·trail_capture + 0.25·efficiency + 0.20·streak</b><br>
        gap: 隔夜溢价质 (±5% 截尾) · trail_capture: trail_t2/best · efficiency: 主退场/best · streak: min(连板/3,1)
      </div>
    </div>
  `;
}

// ── Render ──
function render() {
  const html = `
    <div style="padding:12px;display:flex;flex-direction:column;gap:12px;max-width:1000px;margin:0 auto;">

      <!-- 策略说明 -->
      <div class="card">
        <div class="card-head">交易方式</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px;font-size:13px;">
          <div>
            <div style="font-weight:600;color:var(--accent-2);margin-bottom:4px;">买入</div>
            <div style="color:var(--ink-2);line-height:1.6;">
              <div><b>close_t0</b> — T日收盘价买入（14:57-15:00 集合竞价）</div>
              <div style="margin-top:4px;font-size:12px;color:var(--ink-3);">
                条件：当日涨停板 → 收盘集合竞价以涨停价买入<br>
                捕获隔夜溢价（T+1 跳空高开），T+0 已持仓
              </div>
            </div>
          </div>
          <div>
            <div style="font-weight:600;color:var(--accent-3);margin-bottom:4px;">卖出</div>
            <div style="color:var(--ink-2);line-height:1.6;">
              <div><b>trail_t2</b> — 分日 trailing stop</div>
              <div style="font-size:12px;color:var(--ink-3);margin-top:2px;">
                T+1 盘中触发止盈线(≥1%) → 当日退出<br>
                未触发 → T+2 继续 trailing<br>
                连板延续 → 自动持有
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 买卖时刻 -->
      <div class="card">
        <div class="card-head">买卖时刻提醒</div>
        <div style="padding:12px;font-size:13px;color:var(--ink-2);line-height:1.7;">
          <div><span class="badge" style="background:var(--bg-buy, #1a7f3a);">买</span> <b>14:57-15:00</b> — 收盘集合竞价，以涨停价买入今日涨停股</div>
          <div><span class="badge" style="background:var(--bg-sell, #b91c1c);">卖</span> <b>T+1 盘中</b> — trailing stop 自动追踪，≥1% 盈利则激活，回落 0.5% 退出</div>
          <div><span class="badge">持</span> <b>连板延续</b> — 若 T+1 继续涨停则自动持有，T+2 再判断</div>
          <div><span class="badge">停</span> <b>T+2收盘</b> — 最长持有 2 天，T+2 收盘前强制退出</div>
        </div>
      </div>

      <!-- 当前参数 -->
      <div class="card">
        <div class="card-head">
          <span>当前参数</span>
          <span style="font-size:11px;font-weight:400;color:var(--ink-4);">${_params ? '已加载' : '未加载'}</span>
        </div>
        <div style="padding:8px 12px;">
          ${_params ? _renderParams(_params) : '<div style="color:var(--ink-4);font-size:13px;">加载参数中...</div>'}
        </div>
      </div>

      <!-- 回测控制 -->
      <div class="card">
        <div class="card-head">回测</div>
        <div style="padding:12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
          <span class="label">开始</span>
          <input type="date" id="zt-bt-start" class="input-date" value="2026-05-01" style="font-size:12px;padding:2px 6px;">
          <span class="label">结束</span>
          <input type="date" id="zt-bt-end" class="input-date" value="2026-06-30" style="font-size:12px;padding:2px 6px;">
          <button class="btn" id="zt-bt-run" style="padding:3px 14px;font-size:12px;">▶ 运行</button>
          <span id="zt-bt-status" style="font-size:11px;color:var(--ink-4);"></span>
        </div>
      </div>

      <!-- 结果 -->
      ${_lastResult ? _renderResults(_lastResult) : ''}
      ${_lastResult ? _renderTradesCard(_lastResult) : ''}
    </div>
  `;
  $mount.innerHTML = html;
  _bindEvents();
}

function _renderParams(p) {
  const labels = {
    min_streak: '最小连板', max_streak: '最大连板', burst_max: '炸板容忍',
    sealed_before: '封板时间≤', mcap_min_yi: '最小市值(亿)', mcap_max_yi: '最大市值(亿)',
    turnover_min_pct: '最小换手%', turnover_max_pct: '最大换手%',
    limit_order_min_yi: '最小封单(亿)', top_n: '每日N只',
    trail_activate_pct: '止盈触发%', trail_pullback_pct: '回撤%', stop_loss_pct: '止损%',
  };
  return `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px;font-size:12px;font-family:monospace;">
    ${Object.entries(p).map(([k,v]) =>
      `<div><span style="color:var(--ink-4)">${labels[k]||k}:</span> <b>${v}</b></div>`
    ).join('')}
  </div>`;
}

function _renderResults(r) {
  if (r.error) {
    return `<div class="card" style="color:var(--color-danger);padding:12px;">错误: ${r.error}</div>`;
  }
  const summary = r.summary || {};
  const s = summary;
  if (!s.trades) {
    return `<div class="card"><div style="padding:12px;color:var(--ink-4);text-align:center;">无交易产生（数据源可能在盘后不可用）</div></div>`;
  }

  const sc = r.scenario_compare_full || {};
  const exitKeys = Object.keys(sc).sort();

  return `
    <!-- KPI -->
    <div class="card">
      <div class="card-head">回测结果 ${r.config?.start||''} → ${r.config?.end||''}</div>
      <kpi-group style="padding:8px 12px;">
        <kpi-item><span class="kpi-label">交易</span><span class="kpi-value">${s.trades}</span></kpi-item>
        <kpi-item><span class="kpi-label">胜率</span><span class="kpi-value">${_fmtPct(s.win_rate_pct)}</span></kpi-item>
        <kpi-item><span class="kpi-label">平均</span><span class="kpi-value">${_fmtPct(s.avg_return_pct)}</span></kpi-item>
        <kpi-item><span class="kpi-label">日均</span><span class="kpi-value">${_fmtPct(s.daily_avg_return_pct)}</span></kpi-item>
        <kpi-item><span class="kpi-label">总收益</span><span class="kpi-value">${_fmtPct(s.total_return_pct)}</span></kpi-item>
        <kpi-item><span class="kpi-label">回撤</span><span class="kpi-value" style="color:${(s.max_drawdown_pct||0) < -20 ? 'var(--color-danger)' : 'var(--ink-1)'}">${_fmtPct(s.max_drawdown_pct)}</span></kpi-item>
        <kpi-item><span class="kpi-label">Profit</span><span class="kpi-value">${s.profit_factor === Infinity ? '∞' : s.profit_factor}</span></kpi-item>
      </kpi-group>
    </div>

    <!-- 退场方案对比 -->
    ${exitKeys.length ? `
    <div class="card">
      <div class="card-head">退场方案对比</div>
      <div style="padding:8px 12px;overflow-x:auto;">
        <table class="data-table" style="width:100%;font-size:12px;border-collapse:collapse;">
          <thead><tr>
            <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--line-soft);">退场</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">笔数</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">胜率</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">平均%</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">累计%</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">回撤%</th>
          </tr></thead>
          <tbody>
            ${exitKeys.map(k => {
              const e = sc[k] || {};
              const label = {trail_t2:'Trail T2',close_t1:'T+1收盘',close_t2:'T+2收盘',gap_t1:'隔夜溢价',open_t2:'T+2开盘',best:'理论最佳',stop_t1:'T+1止损'}[k]||k;
              return `<tr>
                <td style="padding:4px 8px;border-bottom:1px solid var(--line-soft);">${k === 'trail_t2' ? '<b>'+label+'</b>' : label}</td>
                <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${e.trades||0}</td>
                <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(e.win_rate_pct)}</td>
                <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(e.avg_return_pct)}</td>
                <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(e.total_return_pct)}</td>
                <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(e.max_drawdown_pct)}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>` : ''}

    <!-- 月度收益 -->
    ${(r.monthly||[]).length ? `
    <div class="card">
      <div class="card-head">月度收益</div>
      <div style="padding:8px 12px;overflow-x:auto;">
        <table class="data-table" style="width:100%;font-size:12px;border-collapse:collapse;">
          <thead><tr>
            <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--line-soft);">月份</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">交易</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">胜率</th>
            <th style="text-align:right;padding:6px 8px;border-bottom:1px solid var(--line-soft);">平均%</th>
          </tr></thead>
          <tbody>
            ${(r.monthly||[]).map(m => `<tr>
              <td style="padding:4px 8px;border-bottom:1px solid var(--line-soft);">${m.month||''}</td>
              <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${m.trades||0}</td>
              <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(m.win_rate_pct)}</td>
              <td style="text-align:right;padding:4px 8px;border-bottom:1px solid var(--line-soft);">${_fmtPct(m.avg_return_pct)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>` : ''}
  `;
}

function _fmtPct(v) {
  if (v == null || v === undefined) return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return '—';
  if (Math.abs(n) >= 1000) return n.toFixed(0) + '%';
  if (Math.abs(n) >= 10) return n.toFixed(1) + '%';
  return n.toFixed(2) + '%';
}

function _bindEvents() {
  const btn = document.getElementById('zt-bt-run');
  if (btn) {
    btn.addEventListener('click', async () => {
      const start = document.getElementById('zt-bt-start')?.value || '2026-05-01';
      const end = document.getElementById('zt-bt-end')?.value || '2026-06-30';
      const status = document.getElementById('zt-bt-status');
      if (status) status.textContent = '运行中...';
      btn.disabled = true;
      await _runBacktest(start, end);
      btn.disabled = false;
    });
  }

  // 交易明细表头点击排序
  const tbl = document.getElementById('zt-trades-table');
  if (tbl) {
    tbl.querySelectorAll('th[data-key]').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.getAttribute('data-key');
        if (_sortKey === k) {
          _sortDir = _sortDir === 'desc' ? 'asc' : 'desc';
        } else {
          _sortKey = k;
          _sortDir = 'desc';
        }
        render();
      });
    });
  }
}

})();
