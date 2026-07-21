(function(){
'use strict';

const $mount = document.getElementById('zt-mount');
if (!$mount) return;

const SELFSW_URL = '/static/zt-frontend.js';

// ── State ──
let _params = null;
let _lastResult = null;

// ── Load params on init ──
(async function init(){
  await _loadParams();
  render();
})();

async function _loadParams() {
  try {
    const r = await api('/api/zt/params');
    _params = r.params || r.data?.params || null;
  } catch(e) {
    _params = null;
  }
}

async function _runBacktest(start, end) {
  const url = `/api/zt/backtest?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  try {
    const data = await api(url);
    _lastResult = data;
    render();
  } catch(e) {
    _lastResult = {error: e.message};
    render();
  }
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
              <div>T+1 开盘价买入（09:30 集合竞价成交）</div>
              <div style="margin-top:4px;font-size:12px;color:var(--ink-3);">
                条件：涨停板次日 → 开盘即入场，不挂单不排队
              </div>
            </div>
          </div>
          <div>
            <div style="font-weight:600;color:var(--accent-3);margin-bottom:4px;">卖出</div>
            <div style="color:var(--ink-2);line-height:1.6;">
              <div><b>trail_t2</b> — 分日 trailing stop</div>
              <div style="font-size:12px;color:var(--ink-3);margin-top:2px;">
                T+1 盘中触发止盈线 → 当日退出<br>
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
          <div><span class="badge" style="background:var(--bg-buy, #1a7f3a);">买</span> <b>09:30</b> — 开盘第一秒检查涨停板候选，以开盘价买入</div>
          <div><span class="badge" style="background:var(--bg-sell, #b91c1c);">卖</span> <b>盘中实时</b> — trailing stop 自动追踪，T+1 或 T+2 触发则退出</div>
          <div><span class="badge">持</span> <b>连板延续</b> — 若 T+1/T+2 继续涨停则自动持有（理论最高收益）</div>
          <div><span class="badge">停</span> <b>T+2收盘</b> — 无论是否触发，T+2 强制退出（最长持有2天）</div>
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
  if (!btn) return;
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

})();
