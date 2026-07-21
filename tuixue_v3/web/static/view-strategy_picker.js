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
  min_matched: 1,
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
    const data = await api('/api/strategies/scan?' + qs.toString(), { timeout: 60_000 });
    if (data && (data.signals || data._skip)) {
      _spData = data;
      _spLoaded = true;
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

  const cards = signals.map(s => {
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
          <div class="sp-card-head">
            <a href="#" class="stock-link sp-stock-link" data-code="${code}" data-strategies="${(s.matched_keys || []).join(',')}">${code}</a>
            <span class="sp-stock-name">${name}</span>
            <span class="sp-stock-close">${s.rl && s.rl.current_close != null ? Number(s.rl.current_close).toFixed(2) : '—'} ¥</span>
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
  }).join('');
  listHost.innerHTML = cards;

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
      const code = a.dataset.code;
      const strategies = a.dataset.strategies || '';
      location.hash = '#stock=' + encodeURIComponent(code) + (strategies ? '&from=sp&s=' + encodeURIComponent(strategies) : '');
    };
  });
  listHost.querySelectorAll('.card[data-code]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('a,button,.chip,.sp-bt-link')) return;
      const code = el.dataset.code;
      location.hash = '#stock=' + encodeURIComponent(code);
    });
  });
}

window.loadStrategyPicker = loadStrategyPicker;
window.renderStrategyPicker = renderStrategyPicker;
window._spLoaded = () => _spLoaded;
