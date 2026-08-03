// 策略选股器 · 3 大策略全市场扫描 (R150: token 化)
// 整合: 周线擒牛 (≥ N/5) + 1/3 回升位 + 5日线放量
let _spLoaded = false;
let _spLoading = false;
let _spData = null;
let _spOpts = {
  wb_min: 1,
  rl_near: true,
  ma5: true,
  mode: 'or',  // 'and' / 'or'
  min_matched: 2,  // 默认「≥2 策略」,全满足太严,任一太宽
};
let _spSortKey = 'score';
let _spSortDir = 'desc';
let _spMinScore = 0;

// strategy 配色 — 全部 token
const _SP_STRATEGY_TOKENS = {
  wb:  'var(--wb-pattern-default)',
  rl:  'var(--down-strong)',
  ma5: 'var(--wb-pattern-sanxing)',
};

function _spScoreToken(sc) {
  if (sc >= 70) return 'var(--down-strong)';
  if (sc >= 45) return 'var(--wb-score-mid)';
  if (sc >= 25) return 'var(--warn)';
  return 'var(--ink-4)';
}

let _spWarmRetries = 0;
let _spWarmTimer = null;
const _SP_WARM_MAX = 6;  // 最多轮询 6 次, ~30s

async function loadStrategyPicker(refresh = false) {
  if (_spLoading && !refresh) return;
  _spLoading = true;
  const status = $('#sp-status');
  const list = $('#sp-list');
  if (status) status.innerHTML = '<span class="dim">扫描中…</span>';
  if (list) list.innerHTML = '';
  try {
    const qs = new URLSearchParams({
      wb_min: String(_spOpts.wb_min),
      rl_near: _spOpts.rl_near ? '1' : '0',
      ma5: _spOpts.ma5 ? '1' : '0',
      mode: _spOpts.mode,
      min_matched: String(_spOpts.min_matched),
    });
    if (refresh) qs.set('refresh', '1');
    // 端点永不阻塞,5s 超时足够
    const data = await api('/api/strategies/scan?' + qs.toString(), { timeout: 5_000 });
    if (!data) {
      if (status) status.innerHTML = '<span class="bad">加载失败: 返回空数据</span>';
      return;
    }
    // warming 占位 → 显示预热提示并轮询
    if (data._warming && (!data.signals || data.signals.length === 0)) {
      _spWarming(status, data);
      return;
    }
    if (data.signals || data._skip) {
      _spData = data;
      _spLoaded = true;
      _spWarmRetries = 0;
      if (_spWarmTimer) { clearTimeout(_spWarmTimer); _spWarmTimer = null; }
      renderStrategyPicker();
    } else {
      if (status) status.innerHTML = '<span class="bad">加载失败: 返回数据格式异常</span>';
    }
  } catch (e) {
    if (status) status.innerHTML = '<span class="bad">加载异常: ' + escapeHtml(e.message) + '</span>';
  } finally {
    _spLoading = false;
  }
}

function _spWarming(status, data) {
  if (!status) return;
  const last = data.ts ? new Date(data.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
  status.innerHTML = `<span class="dim">⏳ 首次预热中… 上次成功 ${last} · 预计 ${data.expected_matched || '?'} 只命中</span>`;
  if (_spWarmRetries >= _SP_WARM_MAX) {
    status.innerHTML += ` <button class="btn btn-mini" id="sp-retry-now">重试</button>`;
    const btn = document.getElementById('sp-retry-now');
    if (btn) btn.onclick = () => { _spWarmRetries = 0; loadStrategyPicker(true); };
    return;
  }
  _spWarmRetries++;
  _spWarmTimer = setTimeout(() => loadStrategyPicker(false), 5000);
}

function _spPct(v, goodUp = true) {
  if (v == null) return '—';
  const s = (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
  // 走 class 而非 inline
  let cls;
  if (goodUp) {
    if (v >= 3)        cls = 'wb-pct-strong-up';
    else if (v >= 0)   cls = 'wb-pct-up';
    else if (v >= -3)  cls = 'wb-pct-down';
    else               cls = 'wb-pct-strong-down';
  } else {
    if (v <= -3)       cls = 'wb-pct-strong-up';
    else if (v <= 0)   cls = 'wb-pct-up';
    else if (v >= 3)   cls = 'wb-pct-down';
    else               cls = 'wb-pct-strong-down';
  }
  return '<span class="' + cls + '">' + s + '</span>';
}

function renderStrategyPicker() {
  if (!_spData) return;
  const filtersHost = $('#sp-filters');
  const statusHost = $('#sp-status');
  const listHost = $('#sp-list');

  if (filtersHost) {
    filtersHost.innerHTML = `
      <div class="sp-filter-row">
        <label class="sp-toggle">
          <input type="checkbox" id="sp-wb" ${_spOpts.wb_min > 0 ? 'checked' : ''} />
          <span>周线擒牛</span>
          <select id="sp-wb-min" class="sp-mini-select">
            <option value="1" ${_spOpts.wb_min === 1 ? 'selected' : ''}>≥1</option>
            <option value="2" ${_spOpts.wb_min === 2 ? 'selected' : ''}>≥2</option>
            <option value="3" ${_spOpts.wb_min === 3 ? 'selected' : ''}>≥3</option>
          </select>
        </label>
        <label class="sp-toggle">
          <input type="checkbox" id="sp-rl" ${_spOpts.rl_near ? 'checked' : ''} />
          <span>1/3 回升位</span>
        </label>
        <label class="sp-toggle">
          <input type="checkbox" id="sp-ma5" ${_spOpts.ma5 ? 'checked' : ''} />
          <span>5日线放量</span>
        </label>
        <span class="sp-mode-group">
          <label><input type="radio" name="sp-mode" value="and" ${_spOpts.mode === 'and' ? 'checked' : ''}/> 全满足 AND</label>
          <label><input type="radio" name="sp-mode" value="or" ${_spOpts.mode === 'or' ? 'checked' : ''}/> 任一 OR</label>
        </span>
        <select id="sp-sort" class="sp-mini-select">
          <option value="score" ${_spSortKey === 'score' ? 'selected' : ''}>评分排序</option>
          <option value="matched_count" ${_spSortKey === 'matched_count' ? 'selected' : ''}>策略数</option>
          <option value="code" ${_spSortKey === 'code' ? 'selected' : ''}>代码</option>
        </select>
        <select id="sp-min-score" class="sp-mini-select">
          <option value="0" ${_spMinScore === 0 ? 'selected' : ''}>全部</option>
          <option value="25" ${_spMinScore === 25 ? 'selected' : ''}>≥25分</option>
          <option value="45" ${_spMinScore === 45 ? 'selected' : ''}>≥45分</option>
          <option value="70" ${_spMinScore === 70 ? 'selected' : ''}>≥70分</option>
        </select>
        <button class="btn btn-mini" id="sp-apply">应用</button>
        <button class="btn btn-mini" id="sp-refresh">刷新</button>
      </div>
    `;
    $('#sp-apply').onclick = () => {
      _spOpts.wb_min = $('#sp-wb').checked ? Number($('#sp-wb-min').value) : 0;
      _spOpts.rl_near = $('#sp-rl').checked;
      _spOpts.ma5 = $('#sp-ma5').checked;
      const m = document.querySelector('input[name="sp-mode"]:checked');
      if (m) _spOpts.mode = m.value;
      _spSortKey = $('#sp-sort').value;
      _spMinScore = Number($('#sp-min-score').value);
      if (!_spOpts.wb_min && !_spOpts.rl_near && !_spOpts.ma5) {
        $('#sp-status').innerHTML = '<span class="bad">至少勾选 1 个策略</span>';
        return;
      }
      loadStrategyPicker(false);
    };
    $('#sp-refresh').onclick = () => loadStrategyPicker(true);
  }

  if (statusHost) {
    const total = _spData.total_scanned || 0;
    const matched = _spData.matched_count || 0;
    const took = _spData.took_ms || 0;
    const ts = _spData.ts || '';
    const byStrat = _spData.by_strategy || {};
    const stale = _spData._stale ? ' <span class="dim caption">(stale)</span>' : '';
    statusHost.innerHTML = `
      <span class="caption dim">扫描 <b>${total}</b> 只 · 命中 <b class="good">${matched}</b> 只${stale}</span>
      <span class="caption dim sp-strat-counts">
        <span>周线 <b class="sp-cnt-wb">${(byStrat.wb || []).length}</b></span>
        <span>回升 <b class="sp-cnt-rl">${(byStrat.rl || []).length}</b></span>
        <span>MA5 <b class="sp-cnt-ma5">${(byStrat.ma5 || []).length}</b></span>
      </span>
      <span class="caption dim">${took}ms · ${escapeHtml(ts)}</span>
    `;
  }

  if (!listHost) return;
  let signals = _spData.signals || [];

  if (_spMinScore > 0) {
    signals = signals.filter(s => (s.score || {}).total >= _spMinScore);
  }

  signals = signals.slice().sort((a, b) => {
    const key = _spSortKey;
    let va, vb;
    if (key === 'score') { va = (a.score || {}).total || 0; vb = (b.score || {}).total || 0; }
    else if (key === 'matched_count') { va = a.matched_count || 0; vb = b.matched_count || 0; }
    else { va = a.code || ''; vb = b.code || ''; }
    if (va < vb) return _spSortDir === 'desc' ? 1 : -1;
    if (va > vb) return _spSortDir === 'desc' ? -1 : 1;
    return 0;
  });

  if (!signals.length) {
    listHost.innerHTML = '<div class="empty-state"><span class="dim">当前条件无命中股 · 试试调低阈值或评分过滤</span></div>';
    return;
  }

  const STRAT_LABEL = { wb: '周线擒牛', rl: '1/3 回升位', ma5: '5日线放量' };

  const cardHtml = (s) => {
    const chips = (s.matched_keys || []).map(k => {
      const colorTok = _SP_STRATEGY_TOKENS[k] || _SP_STRATEGY_TOKENS.wb;
      const label = STRAT_LABEL[k] || k;
      return `<span class="chip sp-strat-chip" style="--sp-strat-accent:${colorTok}">${escapeHtml(label)}</span>`;
    }).join('');
    const code = escapeHtml(s.code || '');
    const name = escapeHtml(s.name || '');

    const details = [];
    if (s.wb && s.wb.count > 0) {
      const patLabels = { sanxing_taodi: '三星探底', zhanwen_5w: '站稳5周线', tupo_pingtai: '突破平台', tupo_pingtai_aggressive: '突破3周(激进)', junxian_fangxiang: '均线方向', zhouxian_duiliang: '周线堆量' };
      const pats = (s.wb.matched || []).map(p => patLabels[p] || p);
      details.push(`<span class="sp-detail-wb">周线${s.wb.count}/5</span><span class="dim sp-detail-sub">${pats.join(', ')}</span>`);
    }
    if (s.rl) {
      const near = s.rl.near_support ? '<span class="tag-good sp-tag-near">强支撑</span>' : '';
      const dist = s.rl.distance_to_level_1_3_pct != null
        ? `<span class="${Math.abs(s.rl.distance_to_level_1_3_pct) < 3 ? 'sp-dist-close' : 'sp-dist-far'}">距 1/3 位 ${(s.rl.distance_to_level_1_3_pct >= 0 ? '+' : '') + Number(s.rl.distance_to_level_1_3_pct).toFixed(2) + '%'}</span>`
        : '';
      details.push(`<span class="sp-detail-rl">1/3 位 ${s.rl.level_1_3 || '?'}</span>${dist}${near}`);
    }
    if (s.ma5 && s.ma5.ok) {
      details.push(`<span class="sp-detail-ma5">MA5 放量</span><span class="dim sp-detail-sub">${escapeHtml((s.ma5.reason || '').slice(0, 50))}</span>`);
    }

    return `<div class="card sp-card" data-code="${code}">
      <div class="sp-card-row">
        <div class="sp-card-main">
          <div class="card-head">
            <a href="#" class="stock-link sp-stock-link" data-code="${code}" data-strategies="${(s.matched_keys || []).join(',')}">${code}</a>
            <span class="sp-stock-name">${name}</span>
            <span class="sp-stock-close">${s.rl && s.rl.current_close != null ? Number(s.rl.current_close).toFixed(2) : '—'} ¥</span>
            <button class="wl-toggle-btn" data-wl-code="${code}" data-wl-name="${name}" title="加入自选">⭐</button>
          </div>
          <div class="sp-card-chips">${chips}</div>
          <div class="sp-card-details">${details.join('<span class="sp-detail-sep">|</span>')}</div>
          <div class="sp-score-bar"></div>
          <div class="sp-breakdown"></div>
          <div><a href="#" class="sp-bt-link" data-code="${code}">回测此股</a></div>
        </div>
        <span class="sp-score-badge"><span class="sp-score-num-empty">--</span><span class="sp-score-max">/100</span></span>
      </div>
    </div>`;
  };

  // 命中数过多时一次性塞进 DOM 会把 view 撑到 5 万 px,分批渲染。
  const SP_RENDER_LIMIT = 50;
  const totalCount = signals.length;
  let rendered = Math.min(SP_RENDER_LIMIT, totalCount);

  const moreBarHtml = () => rendered < totalCount
    ? `<div class="sp-more-bar" id="sp-more-bar">
         <button class="btn btn-mini" id="sp-show-more">再显示 ${Math.min(SP_RENDER_LIMIT, totalCount - rendered)} 条 (已显示 ${rendered}/${totalCount})</button>
       </div>`
    : '';

  listHost.innerHTML = signals.slice(0, rendered).map(cardHtml).join('') + moreBarHtml();
  _spDecorate(listHost, signals.slice(0, rendered));
  _spBind(listHost);

  const bindMore = () => {
    const btn = listHost.querySelector('#sp-show-more');
    if (!btn) return;
    btn.onclick = () => {
      const next = Math.min(rendered + SP_RENDER_LIMIT, totalCount);
      const batch = signals.slice(rendered, next);
      const bar = listHost.querySelector('#sp-more-bar');
      bar.insertAdjacentHTML('beforebegin', batch.map(cardHtml).join(''));
      rendered = next;
      bar.outerHTML = moreBarHtml();
      _spDecorate(listHost, batch);
      _spBind(listHost);
      bindMore();
    };
  };
  bindMore();
}

function _spDecorate(listHost, signals) {
  signals.forEach((s) => {
    const sc = s.score || {};
    const total = sc.total || 0;
    const colorTok = _spScoreToken(total);
    const badge = listHost.querySelector(`.card[data-code="${CSS.escape(s.code)}"] .sp-score-badge`);
    if (badge) {
      badge.innerHTML = `<span class="sp-score-num" style="--sp-score-accent:${colorTok}">${total}</span><span class="sp-score-max">/100</span>`;
    }
    const bar = listHost.querySelector(`.card[data-code="${CSS.escape(s.code)}"] .sp-score-bar`);
    if (bar) {
      bar.innerHTML = `<div class="sp-score-track"><div class="sp-score-fill" style="width:${Math.min(total / (sc.max || 100) * 100, 100)}%;--sp-score-accent:${colorTok}"></div></div>`;
    }
    const bd = listHost.querySelector(`.card[data-code="${CSS.escape(s.code)}"] .sp-breakdown`);
    if (bd && sc.breakdown) {
      bd.innerHTML = sc.breakdown.map(b => `<span class="sp-breakdown-item">${escapeHtml(b)}</span>`).join(' <span class="sp-breakdown-sep">·</span> ');
    }
  });
}

function _spBind(listHost) {
  listHost.querySelectorAll('.sp-bt-link').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      location.hash = '#screener&bt&idx=1&sec=1&periods=1周,2周,1月,2月,半年';
    };
  });

  listHost.querySelectorAll('.stock-link').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      gotoStock(a.dataset.code);
    };
  });
  listHost.querySelectorAll('.card[data-code]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('a,button,.chip,.sp-bt-link,.wl-toggle-btn')) return;
      gotoStock(el.dataset.code);
    });
  });
  // R1000-B1: 自选按钮
  listHost.querySelectorAll('.wl-toggle-btn').forEach(function (btn) {
    btn.onclick = function (e) {
      e.stopPropagation();
      e.preventDefault();
      window.wlToggle(btn.dataset.wlCode, btn.dataset.wlName).then(function () {
        window.wlRefreshBtn(btn);
      });
    };
    window.wlRefreshBtn(btn);
  });
}

window.loadStrategyPicker = loadStrategyPicker;
window.renderStrategyPicker = renderStrategyPicker;

// ═══════════════════════════════════════════════════════════
// 综合策略选股 · Comprehensive Strategy Card
// ═══════════════════════════════════════════════════════════
let _compLoaded = false;
let _compData = null;
let _compPollTimer = null;
let _compPollCount = 0;
const _COMP_POLL_INTERVAL = 2000;  // 2s 轮询进度
const _COMP_POLL_MAX = 180;        // 最多 6 分钟

function _compProgressLabel(phase) {
  const map = {
    building_cache: '构建涨停缓存...',
    phase1: 'Phase 1: 随机搜索',
    phase2: 'Phase 2: 交叉搜索',
    phase3: 'Phase 3: 微调搜索',
    weight_random: '权重微调 Phase 1: 随机',
    weight_refine: '权重微调 Phase 2: 交叉+微调',
    done: '已完成',
    idle: '空闲',
  };
  return map[phase] || phase;
}

async function loadComprehensive(refresh = false) {
  const list = document.getElementById('comp-list');
  const status = document.getElementById('comp-status');
  if (status) status.innerHTML = '<span class="dim">加载中...</span>';
  try {
    const qs = refresh ? '?refresh=1' : '';
    const data = await api('/api/comprehensive/scan' + qs, { timeout: 10_000 });
    if (!data) {
      if (status) status.innerHTML = '<span class="bad">加载失败</span>';
      return;
    }
    if (data._warming) {
      if (status) status.innerHTML = '<span class="dim">⏳ 后台扫描中, 请稍后刷新...</span>';
      return;
    }
    if (data._refreshing) {
      if (status) status.innerHTML = '<span class="dim">⏳ 正在刷新综合策略...</span>';
      setTimeout(() => loadComprehensive(false), 3000);
      return;
    }
    _compData = data;
    _compLoaded = true;
    renderComprehensive();
    loadCompCompare();
  } catch (e) {
    if (status) status.innerHTML = '<span class="bad">加载异常: ' + escapeHtml(e.message) + '</span>';
  }
}

// 2026-08-03: 昨日推荐 vs 今日推荐 + 实时涨幅
let _compCompareData = null;
let _compCompareTimer = null;
async function loadCompCompare() {
  try {
    const r = await api('/api/comprehensive/compare', { timeout: 8_000 });
    if (r && r.ok && r.data) {
      _compCompareData = r.data;
      renderCompCompare();
    }
  } catch (_) {}
}

function renderCompCompare() {
  const host = document.getElementById('comp-compare-host');
  if (!host || !_compCompareData) return;
  const d = _compCompareData;
  const summary = d.summary || {};
  const fpCls = (v) => v > 0 ? 'color:#ef4444;font-weight:600;' : v < 0 ? 'color:#22c55e;font-weight:600;' : 'color:var(--ink-4);';
  const tagCls = (v) => v > 0 ? 'background:rgba(239,68,68,.12);color:#ef4444;' : v < 0 ? 'background:rgba(34,197,94,.12);color:#22c55e;' : 'background:rgba(125,125,125,.12);color:var(--ink-3);';
  const arrow = (v) => v > 0 ? '▲' : v < 0 ? '▼' : '─';

  function _row(s, mode) {
    const code = s.code || '';
    const chg = s.change_pct || 0;
    const cp = s.current_price || 0;
    return `<tr>
      <td style="text-align:left;padding:4px 8px;font-size:12px;"><a href="#" class="zt-stock-link" data-stock-code="${code}" style="color:var(--accent-1);text-decoration:none;font-weight:600;">${escapeHtml(code)} ${escapeHtml(s.name||'')}</a></td>
      <td style="text-align:left;padding:4px 6px;font-size:11px;color:var(--ink-3);">${escapeHtml(s.sector||'')}</td>
      <td style="text-align:right;padding:4px 8px;font-size:11.5px;color:var(--ink-2);">${(s.score||0).toFixed(1)}</td>
      <td style="text-align:right;padding:4px 8px;font-size:12px;">${cp > 0 ? cp.toFixed(2) : '—'}</td>
      ${mode === 'today' ? `<td style="text-align:right;padding:4px 8px;font-size:11.5px;color:var(--ink-4);">${s.prev_close > 0 ? s.prev_close.toFixed(2) : '—'}</td>` : ''}
      <td style="text-align:right;padding:4px 8px;font-size:12.5px;${fpCls(chg)}">${arrow(chg)} ${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</td>
    </tr>`;
  }

  const todayRows = (d.today || []).map(s => _row(s, 'today')).join('') || '<tr><td colspan="6" style="text-align:center;padding:8px;color:var(--ink-4);font-size:11.5px;">今日扫描未就绪</td></tr>';
  const yRows = (d.yesterday || []).map(s => _row(s, 'yesterday')).join('') || '<tr><td colspan="5" style="text-align:center;padding:8px;color:var(--ink-4);font-size:11.5px;">昨日无快照 (首日)</td></tr>';

  host.innerHTML = `
    <div class="card mt-16">
      <div class="card-head">
        <span>📊 推票对比 · 今日 vs 昨日 <span class="dim" style="font-size:11px;font-weight:400;">综合策略 7 维加权 · 实时涨幅</span></span>
        <span style="display:flex;gap:6px;align-items:center;font-size:11px;color:var(--ink-3);">
          ${d.meta && d.meta.yesterday_ready ? `<span class="chip" style="${tagCls(summary.yesterday_avg_change||0)}">昨日平均 ${summary.yesterday_avg_change>=0?'+':''}${summary.yesterday_avg_change}% · ${summary.yesterday_winners}/${summary.yesterday_count} 涨</span>` : ''}
        </span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px 12px;">
        <div>
          <div style="font-size:11px;font-weight:600;color:var(--ink-3);margin-bottom:4px;">📌 今日推荐 (${d.today_date||''})</div>
          <table class="data-table" style="width:100%;font-size:12px;border-collapse:collapse;">
            <thead><tr>
              <th style="text-align:left;padding:4px 8px;font-size:10.5px;">代码 名称</th>
              <th style="text-align:left;padding:4px 6px;font-size:10.5px;">板块</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">分数</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">现价</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">日涨跌</th>
            </tr></thead>
            <tbody>${todayRows}</tbody>
          </table>
        </div>
        <div>
          <div style="font-size:11px;font-weight:600;color:var(--ink-3);margin-bottom:4px;">📈 昨日推荐 (${d.yesterday_date||''}) · 至今</div>
          <table class="data-table" style="width:100%;font-size:12px;border-collapse:collapse;">
            <thead><tr>
              <th style="text-align:left;padding:4px 8px;font-size:10.5px;">代码 名称</th>
              <th style="text-align:left;padding:4px 6px;font-size:10.5px;">板块</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">分数</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">现价</th>
              <th style="text-align:right;padding:4px 8px;font-size:10.5px;">累计%</th>
            </tr></thead>
            <tbody>${yRows}</tbody>
          </table>
        </div>
      </div>
    </div>
  `;

  // 绑定代码点击跳转
  host.querySelectorAll('.zt-stock-link').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const code = a.getAttribute('data-stock-code');
      if (code && typeof gotoStock === 'function') gotoStock(code);
    });
  });
}

function renderComprehensive() {
  const host = document.getElementById('comp-card-host');
  if (!host || !_compData) return;
  const data = _compData;
  const meta = data._meta || {};
  const signals = data.signals || [];
  const topN = signals.slice(0, 30);

  let html = '<div class="card comp-card">';
  // Header
  html += '<div class="card-head"><span class="card-ttl">综合策略</span>';
  html += '<span class="dim" style="font-size:0.8em;margin-left:8px;">7维加权融合 · 全市场扫描</span>';
  html += '<div style="margin-left:auto;display:flex;gap:6px;">';
  html += `<button class="btn btn-mini" id="comp-refresh-btn" title="刷新扫描">🔄 刷新</button>`;
  html += `<button class="btn btn-mini" id="comp-opt-btn" title="启动10K迭代优化">⚡ 优化</button>`;
  html += `<button class="btn btn-mini" id="comp-finetune-btn" title="权重微调找最高胜率">🎯 微调权重</button>`;
  html += '</div></div>';

  // Stats bar
  html += '<div class="comp-stats" style="padding:6px 12px;display:flex;gap:16px;font-size:0.85em;flex-wrap:wrap;">';
  html += `<span>扫描 <b>${data.total_scanned || '?'}</b> 只</span>`;
  html += `<span>命中 <b>${data.matched_count || 0}</b> 只</span>`;
  html += `<span>耗时 <b>${(data.took_ms / 1000).toFixed(1)}s</b></span>`;
  if (meta.ts) {
    html += `<span class="dim">更新: ${new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</span>`;
  }
  html += '</div>';

  // Progress area (hidden by default)
  html += '<div id="comp-progress" class="comp-progress" style="display:none;padding:8px 12px;">';
  html += '<div class="comp-progress-bar" style="height:6px;background:var(--ink-3);border-radius:3px;overflow:hidden;">';
  html += '<div id="comp-progress-fill" style="height:100%;width:0%;background:var(--up-strong);transition:width 0.3s;"></div>';
  html += '</div>';
  html += '<div id="comp-progress-text" class="dim" style="margin-top:4px;font-size:0.8em;"></div>';
  html += '</div>';

  // Results list
  if (signals.length === 0) {
    html += '<div class="dim" style="padding:24px;text-align:center;">暂无综合策略信号 · 点击「扫描」或「优化」</div>';
  } else {
    html += '<div id="comp-list" class="stocks-table-wrap" style="max-height:70vh;overflow-y:auto;">';
    html += '<table class="stocks-table"><thead><tr>';
    html += '<th>#</th><th>代码</th><th>名称</th><th class="r">综合分</th>';
    html += '<th class="r">ZT</th><th class="r">策略</th><th class="r">龙虎</th>';
    html += '<th class="r">AI</th><th class="r">基面</th><th>板块</th>';
    html += '<th>命中</th><th>详情</th>';
    html += '</tr></thead><tbody>';

    topN.forEach((s, i) => {
      const sc = s.scores || {};
      const w = s.weights || {};
      const c = s.composite || 0;
      const color = c >= 70 ? 'var(--down-strong)' : c >= 50 ? 'var(--wb-score-mid)' : c >= 35 ? 'var(--warn)' : 'var(--ink-4)';
      html += '<tr class="clickable" data-code="' + escapeHtml(s.code) + '" style="cursor:pointer;">';
      html += `<td>${i + 1}</td>`;
      html += `<td class="mono">${escapeHtml(s.code)}</td>`;
      html += `<td>${escapeHtml(s.name || '')}</td>`;
      html += `<td class="r"><b style="color:${color}">${c.toFixed(0)}</b></td>`;
      html += `<td class="r dim">${(sc.zt || 0).toFixed(0)}</td>`;
      html += `<td class="r dim">${(sc.strategy || 0).toFixed(0)}</td>`;
      html += `<td class="r dim">${(sc.dragons || 0).toFixed(0)}</td>`;
      html += `<td class="r dim">${(sc.ai || 0).toFixed(0)}</td>`;
      html += `<td class="r dim">${(sc.fundamental || 0).toFixed(0)}</td>`;
      html += `<td class="dim" style="font-size:0.8em;">${s.sector_hot ? '🔥' : ''} ${escapeHtml(s.sector || '')}</td>`;
      html += `<td>${s.strategy_hits || 0}/3</td>`;
      html += `<td class="dim" style="font-size:0.75em;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(s.details || '')}">${escapeHtml(s.details || '')}</td>`;
      html += '</tr>';
    });
    html += '</tbody></table></div>';
  }
  html += '</div>';  // close card
  host.innerHTML = html;

  // Bind events
  const refreshBtn = document.getElementById('comp-refresh-btn');
  if (refreshBtn) refreshBtn.onclick = () => loadComprehensive(true);

  const optBtn = document.getElementById('comp-opt-btn');
  if (optBtn) optBtn.onclick = async () => {
    optBtn.disabled = true;
    optBtn.textContent = '启动中...';
    try {
      const r = await api('/api/comprehensive/optimize?iterations=10000&sample=300', { method: 'POST', timeout: 5_000 });
      if (r && r.status === 'started') {
        _startCompPolling();
      } else if (r && r.status === 'already_running') {
        _startCompPolling();
        optBtn.textContent = '已运行中';
      }
    } catch (e) {
      optBtn.textContent = '⚡ 优化';
      optBtn.disabled = false;
      alert('启动优化失败: ' + e.message);
    }
  };

  const ftBtn = document.getElementById('comp-finetune-btn');
  if (ftBtn) ftBtn.onclick = async () => {
    ftBtn.disabled = true;
    ftBtn.textContent = '启动中...';
    try {
      const r = await api('/api/comprehensive/optimize?iterations=5000&weight_only=1&sample=200', { method: 'POST', timeout: 5_000 });
      if (r && r.status === 'started') {
        _startCompPolling();
      } else if (r && r.status === 'already_running') {
        _startCompPolling();
        ftBtn.textContent = '已运行中';
      }
    } catch (e) {
      ftBtn.textContent = '🎯 微调权重';
      ftBtn.disabled = false;
    }
  };

  // Row click → go to stock
  host.querySelectorAll('tr[data-code]').forEach(tr => {
    tr.onclick = () => gotoStock(tr.dataset.code);
  });
}

function _startCompPolling() {
  _compPollCount = 0;
  if (_compPollTimer) clearTimeout(_compPollTimer);
  const progDiv = document.getElementById('comp-progress');
  const fill = document.getElementById('comp-progress-fill');
  const text = document.getElementById('comp-progress-text');
  if (progDiv) progDiv.style.display = 'block';

  async function _poll() {
    if (_compPollCount >= _COMP_POLL_MAX) {
      if (text) text.textContent = '超时 — 优化可能仍在后台运行, 稍后点「刷新」查看结果';
      if (progDiv) progDiv.style.display = 'none';
      _compPollTimer = null;
      return;
    }
    _compPollCount++;
    try {
      const p = await api('/api/comprehensive/progress', { timeout: 3_000 });
      if (!p) { _compPollTimer = setTimeout(_poll, _COMP_POLL_INTERVAL); return; }
      const pct = p.total > 0 ? Math.min(100, (p.iter / p.total * 100)).toFixed(0) : 0;
      if (fill) fill.style.width = pct + '%';
      if (text) {
        let msg = `${_compProgressLabel(p.phase)} · ${p.iter || 0}/${p.total || '?'} · 最佳分: ${(p.best_score || 0).toFixed(0)}`;
        if (p.hold3_wr != null) msg += ` · 持有3天胜率: ${p.hold3_wr}%`;
        if (p.trades) msg += ` · ${p.trades}笔`;
        if (p.msg) msg += ` · ${p.msg}`;
        text.textContent = msg;
      }
      if (p.phase === 'done') {
        if (text) text.textContent = '优化完成! 正在刷新结果...';
        setTimeout(async () => {
          if (progDiv) progDiv.style.display = 'none';
          await loadComprehensive(true);
        }, 2000);
        _compPollTimer = null;
        return;
      }
    } catch (e) { /* ignore poll errors */ }
    _compPollTimer = setTimeout(_poll, _COMP_POLL_INTERVAL);
  }
  _poll();
}

window.loadComprehensive = loadComprehensive;
window.renderComprehensive = renderComprehensive;
window._spLoaded = () => _spLoaded;
