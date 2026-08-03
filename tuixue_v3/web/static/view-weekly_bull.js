// 周线擒牛 · 全市场扫描 + pattern 过滤 + 评分 (R150: token 化)
// 不再使用任何硬编码 #xxx 颜色 — 全部走 var(--xxx) token
let _wbLoaded = false;
let _wbLoading = false;
let _wbData = null;
let _wbFilter = 'all';
let _wbSortKey = 'score';
let _wbSortDir = 'desc';

const _WB_PATTERN_LABELS = {
  all:                       '全部',
  sanxing_taodi:             '三星探底',
  zhanwen_5w:                '站稳5周线',
  tupo_pingtai:              '突破震荡平台',
  tupo_pingtai_aggressive:   '突破3周(激进)',
  junxian_fangxiang:         '均线方向',
  zhouxian_duiliang:         '周线堆量',
};

// pattern 配色 — 全部 token 化,在双主题下都可见
const _WB_PATTERN_TOKENS = {
  all:                       'var(--wb-pattern-default)',
  sanxing_taodi:             'var(--wb-pattern-sanxing)',
  zhanwen_5w:                'var(--wb-pattern-zhanwen)',
  tupo_pingtai:              'var(--wb-pattern-tupo)',
  tupo_pingtai_aggressive:   'var(--wb-pattern-aggressive)',
  junxian_fangxiang:         'var(--wb-pattern-junxian)',
  zhouxian_duiliang:         'var(--wb-pattern-duiliang)',
};

// 评分色 — 走 token
function _wbScoreToken(sc) {
  if (sc >= 70) return 'var(--down-strong)';
  if (sc >= 45) return 'var(--wb-score-mid)';
  if (sc >= 25) return 'var(--warn)';
  return 'var(--up)';
}

async function loadWeeklyBull(refresh = false) {
  if (_wbLoading && !refresh) return;
  _wbLoading = true;
  const status = $('#weekly-bull-status');
  if (status) status.innerHTML = '<span class="dim">扫描中…</span>';
  try {
    const url = '/api/weekly_bull' + (refresh ? '?refresh=1' : '');
    const data = await api(url, { timeout: 30000 });
    if (data && data.signals) {
      _wbData = data;
      _wbLoaded = true;
      renderWeeklyBull();
    } else {
      if (status) status.innerHTML = '<span class="bad">加载失败: 数据格式异常</span>';
    }
  } catch (e) {
    if (status) status.innerHTML = '<span class="bad">加载异常: ' + escapeHtml(e.message) + '</span>';
  } finally {
    _wbLoading = false;
  }
}

function _wbPct(v) {
  if (v == null) return '—';
  const s = (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
  const cls = v >= 3 ? 'wb-pct-strong-up' : v >= 0 ? 'wb-pct-up'
            : v >= -3 ? 'wb-pct-down' : 'wb-pct-strong-down';
  return '<span class="' + cls + '">' + s + '</span>';
}

function renderWeeklyBull() {
  if (!_wbData) return;
  const chipsHost = $('#weekly-bull-chips');
  const statusHost = $('#weekly-bull-status');
  const listHost = $('#weekly-bull-list');

  const byPattern = _wbData.by_pattern || {};
  const chipDefs = [
    ['all', 'all', _wbData.signals?.length || 0],
    ['sanxing_taodi',             'sanxing_taodi',             (byPattern.sanxing_taodi || []).length],
    ['zhanwen_5w',                'zhanwen_5w',                (byPattern.zhanwen_5w || []).length],
    ['tupo_pingtai',              'tupo_pingtai',              (byPattern.tupo_pingtai || []).length],
    ['tupo_pingtai_aggressive',   'tupo_pingtai_aggressive',   (byPattern.tupo_pingtai_aggressive || []).length],
    ['junxian_fangxiang',         'junxian_fangxiang',         (byPattern.junxian_fangxiang || []).length],
    ['zhouxian_duiliang',         'zhouxian_duiliang',         (byPattern.zhouxian_duiliang || []).length],
  ];
  if (chipsHost) {
    chipsHost.innerHTML = chipDefs.map(([k, key, cnt]) => {
      const pressed = _wbFilter === key;
      const colorTok = _WB_PATTERN_TOKENS[key] || _WB_PATTERN_TOKENS.all;
      const klass = pressed ? 'wb-chip wb-chip--pressed' : 'wb-chip';
      return '<span class="chip wb-filter-chip ' + klass + '" data-wb-filter="' + escapeHtml(key) + '" style="--wb-accent:' + colorTok + '" aria-pressed="' + pressed + '">' +
        escapeHtml(_WB_PATTERN_LABELS[k] || k) +
        ' <span class="wb-chip-count">' + cnt + '</span></span>';
    }).join('');
    chipsHost.querySelectorAll('.wb-filter-chip').forEach(c => {
      c.onclick = () => {
        _wbFilter = c.dataset.wbFilter;
        const qs = _wbFilter === 'all' ? '' : '?pattern=' + encodeURIComponent(_wbFilter);
        const newHash = '#weekly_bull' + qs;
        if (location.hash !== newHash) history.replaceState(null, '', newHash);
        renderWeeklyBull();
      };
    });
  }

  if (statusHost) {
    const total = _wbData.total_scanned || 0;
    const matched = _wbData.matched_count || 0;
    const took = _wbData.took_ms || 0;
    const ts = _wbData.ts || '';
    statusHost.innerHTML =
      '<span class="caption dim">扫描 <b>' + total + '</b> 只 · 命中 <b class="good">' + matched + '</b> 只</span>' +
      '<span class="caption dim"> · ' + took + 'ms</span>' +
      '<span class="caption dim"> · ' + escapeHtml(ts) + '</span>' +
      '<button class="btn btn-mini wb-refresh" id="wb-refresh">刷新</button>';
    const refreshBtn = $('#wb-refresh');
    if (refreshBtn) refreshBtn.onclick = () => loadWeeklyBull(true);
  }

  if (!listHost) return;
  let signals = _wbData.signals || [];
  if (_wbFilter !== 'all') {
    signals = signals.filter(s => (s.matched || []).includes(_wbFilter));
  }
  signals = signals.slice().sort((a, b) => {
    let va, vb;
    if (_wbSortKey === 'score') { va = a.score || 0; vb = b.score || 0; }
    else if (_wbSortKey === 'count') { va = a.count; vb = b.count; }
    else if (_wbSortKey === 'code') { va = a.code; vb = b.code; }
    else if (_wbSortKey === 'change_pct') { va = a.weekly_last?.change_pct ?? 0; vb = b.weekly_last?.change_pct ?? 0; }
    else { va = 0; vb = 0; }
    if (va < vb) return _wbSortDir === 'asc' ? -1 : 1;
    if (va > vb) return _wbSortDir === 'asc' ? 1 : -1;
    return 0;
  });

  if (!signals.length) {
    listHost.innerHTML = '<div class="empty-state"><span class="dim">当前过滤无命中股</span></div>';
    return;
  }

  const sortArrow = (k) => _wbSortKey === k ? (_wbSortDir === 'asc' ? ' &#9650;' : ' &#9660;') : '';
  const sortBar = '<div class="wb-sort-bar">' +
    '<span class="wb-sort" data-wb-sort="score">评分' + sortArrow('score') + '</span>' +
    '<span class="wb-sort" data-wb-sort="code">代码' + sortArrow('code') + '</span>' +
    '<span class="wb-sort-col">周收盘</span>' +
    '<span class="wb-sort" data-wb-sort="change_pct">周涨跌' + sortArrow('change_pct') + '</span>' +
    '<span class="wb-sort" data-wb-sort="count">命中' + sortArrow('count') + '</span>' +
    '<span class="wb-sort-col wb-sort-detail">信号详情</span></div>';

  const cardHtml = (s) => {
    const code = escapeHtml(s.code || '');
    const name = escapeHtml(s.name || '');
    const wk = s.weekly_last || {};
    const chg = wk.change_pct;
    const sc = s.score || 0;
    const colorTok = _wbScoreToken(sc);
    const chips = (s.matched || []).map(k => {
      const colorTokK = _WB_PATTERN_TOKENS[k] || _WB_PATTERN_TOKENS.all;
      const reason = (s.reasons || {})[k] || '';
      return '<span class="chip wb-mini" data-wb-filter="' + escapeHtml(k) + '" style="--wb-accent:' + colorTokK + '" title="' + escapeHtml(reason) + '">' +
        escapeHtml(_WB_PATTERN_LABELS[k] || k) + '</span>';
    }).join('');
    const reasonsHtml = (s.matched || []).map(k => {
      const reason = (s.reasons || {})[k] || '';
      return '<div class="wb-reason-row"><span class="wb-reason-label">' + escapeHtml(_WB_PATTERN_LABELS[k] || k) + ':</span> ' + escapeHtml(reason.slice(0, 70)) + '</div>';
    }).join('');

    return '<div class="card wb-card" data-code="' + code + '" style="--wb-accent:' + colorTok + '">' +
      '<div class="wb-card-row">' +
        '<div class="wb-card-main">' +
          '<div class="card-head">' +
            '<a href="#" class="stock-link wb-code-link" data-code="' + code + '" data-patterns="' + escapeHtml((s.matched || []).join(',')) + '">' + code + '</a>' +
            '<span class="wb-stock-name">' + name + '</span>' +
            (wk.close != null ? '<span class="wb-close">¥' + Number(wk.close).toFixed(2) + '</span>' : '') +
            (chg != null ? '<span>' + _wbPct(chg) + '</span>' : '') +
            '<button class="wl-toggle-btn" data-wl-code="' + code + '" data-wl-name="' + name + '" title="加入自选">⭐</button>' +
          '</div>' +
          '<div class="wb-card-chips">' + chips + '</div>' +
          '<div class="wb-card-reasons">' + reasonsHtml + '</div>' +
          '<div class="wb-score-bar"><div class="wb-score-fill" style="width:' + Math.min(sc, 100) + '%"></div></div>' +
        '</div>' +
        '<div class="wb-card-score">' +
          '<span class="wb-score-num">' + sc + '</span>' +
          '<span class="wb-score-max">/100</span>' +
        '</div>' +
      '</div>' +
    '</div>';
  };

  // 2026-08-03: 命中过多(>50)时分批渲染,避免 12K+ px / 105 cards 撑死 DOM
  const WB_RENDER_LIMIT = 50;
  const totalCount = signals.length;
  let rendered = Math.min(WB_RENDER_LIMIT, totalCount);
  const moreBarHtml = () => rendered < totalCount
    ? '<div class="wb-more-bar" id="wb-more-bar">' +
        '<button class="btn btn-mini" id="wb-show-more">再显示 ' + Math.min(WB_RENDER_LIMIT, totalCount - rendered) + ' 条 (已显示 ' + rendered + '/' + totalCount + ')</button>' +
      '</div>'
    : '';
  listHost.innerHTML = sortBar + signals.slice(0, rendered).map(cardHtml).join('') + moreBarHtml();
  _wbBind(listHost, signals.slice(0, rendered));
  const bindMore = () => {
    const btn = listHost.querySelector('#wb-show-more');
    if (!btn) return;
    btn.onclick = () => {
      const next = Math.min(rendered + WB_RENDER_LIMIT, totalCount);
      const batch = signals.slice(rendered, next);
      const bar = listHost.querySelector('#wb-more-bar');
      bar.insertAdjacentHTML('beforebegin', batch.map(cardHtml).join(''));
      rendered = next;
      bar.outerHTML = moreBarHtml();
      _wbBind(listHost, batch);
      bindMore();
    };
  };
  bindMore();
}

function _wbBind(listHost, _signals) {
  listHost.querySelectorAll('.wb-sort').forEach(th => {
    th.onclick = () => {
      const k = th.dataset.wbSort;
      if (_wbSortKey === k) _wbSortDir = _wbSortDir === 'asc' ? 'desc' : 'asc';
      else { _wbSortKey = k; _wbSortDir = 'desc'; }
      renderWeeklyBull();
    };
  });

  listHost.querySelectorAll('a.stock-link').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      gotoStock(a.dataset.code);
    };
  });
  listHost.querySelectorAll('.card[data-code]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('a,button,.chip,.wb-sort')) return;
      gotoStock(el.dataset.code);
    });
  });
  listHost.querySelectorAll('.chip.wb-mini').forEach(c => {
    c.onclick = (e) => {
      e.stopPropagation();
      _wbFilter = c.dataset.wbFilter;
      const qs = '?pattern=' + encodeURIComponent(_wbFilter);
      const newHash = '#weekly_bull' + qs;
      if (location.hash !== newHash) history.replaceState(null, '', newHash);
      renderWeeklyBull();
    };
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

window.loadWeeklyBull = loadWeeklyBull;
window.renderWeeklyBull = renderWeeklyBull;
window._wbLoaded = () => _wbLoaded;

const _origLoadWeeklyBull = loadWeeklyBull;
async function _loadWeeklyBullWithOverride(refresh = false) {
  await _origLoadWeeklyBull(refresh);
  if (window._wbFilterOverride) {
    _wbFilter = window._wbFilterOverride;
    window._wbFilterOverride = null;
    renderWeeklyBull();
  }
}
window.loadWeeklyBull = _loadWeeklyBullWithOverride;
