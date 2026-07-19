// 周线擒牛 · 全市场扫描 + pattern 过滤
// 2026-07-19: 跟龙头战法页并列的"买点分析方法论",5 大信号全市场扫描
let _wbLoaded = false;
let _wbLoading = false;
let _wbData = null;
let _wbFilter = 'all';     // 'all' | <pattern_key>
let _wbSortKey = 'count';  // count | code | change_pct
let _wbSortDir = 'desc';

const _WB_PATTERN_LABELS = {
  all:                  '全部',
  sanxing_taodi:        '三星探底',
  zhanwen_5w:           '站稳5周线',
  tupo_pingtai:         '突破震荡平台',
  junxian_fangxiang:    '均线方向',
  zhouxian_duiliang:    '周线堆量',
};

async function loadWeeklyBull(refresh = false) {
  if (_wbLoading && !refresh) return;
  _wbLoading = true;
  const status = $('#weekly-bull-status');
  if (status) status.innerHTML = '<span class="dim">扫描中…</span>';
  try {
    const url = '/api/weekly_bull' + (refresh ? '?refresh=1' : '');
    const env = await api(url, { timeout: 30000 });
    if (env && env.ok) {
      _wbData = env.data;
      _wbLoaded = true;
      renderWeeklyBull();
    } else {
      if (status) status.innerHTML = `<span class="bad">加载失败: ${escapeHtml(env?.error || '未知错误')}</span>`;
    }
  } catch (e) {
    if (status) status.innerHTML = `<span class="bad">加载异常: ${escapeHtml(e.message)}</span>`;
  } finally {
    _wbLoading = false;
  }
}

function renderWeeklyBull() {
  if (!_wbData) return;
  const chipsHost = $('#weekly-bull-chips');
  const statusHost = $('#weekly-bull-status');
  const listHost = $('#weekly-bull-list');

  // ── 顶部 6 个 chip 过滤 ──
  const byPattern = _wbData.by_pattern || {};
  const chipDefs = [
    ['all', 'all', _wbData.signals?.length || 0],
    ['sanxing_taodi',     'sanxing_taodi',     byPattern.sanxing_taodi?.length || 0],
    ['zhanwen_5w',        'zhanwen_5w',        byPattern.zhanwen_5w?.length || 0],
    ['tupo_pingtai',      'tupo_pingtai',      byPattern.tupo_pingtai?.length || 0],
    ['junxian_fangxiang', 'junxian_fangxiang', byPattern.junxian_fangxiang?.length || 0],
    ['zhouxian_duiliang', 'zhouxian_duiliang', byPattern.zhouxian_duiliang?.length || 0],
  ];
  if (chipsHost) {
    chipsHost.innerHTML = chipDefs.map(([k, key, cnt]) => {
      const pressed = _wbFilter === key ? 'true' : 'false';
      const cls = pressed === 'true' ? 'tag-good' : '';
      return `<span class="chip wb-filter-chip ${cls}" data-wb-filter="${escapeHtml(key)}" aria-pressed="${pressed}">${escapeHtml(_WB_PATTERN_LABELS[k] || k)} <span class="chip-count">${cnt}</span></span>`;
    }).join('');
    chipsHost.querySelectorAll('.wb-filter-chip').forEach(c => {
      c.onclick = () => {
        _wbFilter = c.dataset.wbFilter;
        // URL 同步 (深链 + 后退支持)
        const qs = _wbFilter === 'all' ? '' : `?pattern=${encodeURIComponent(_wbFilter)}`;
        const newHash = '#weekly_bull' + qs;
        if (location.hash !== newHash) {
          history.replaceState(null, '', newHash);
        }
        renderWeeklyBull();
      };
    });
  }

  // ── 状态统计 ──
  if (statusHost) {
    const total = _wbData.total_scanned || 0;
    const matched = _wbData.matched_count || 0;
    const took = _wbData.took_ms || 0;
    const ts = _wbData.ts || '';
    statusHost.innerHTML = `
      <span class="caption dim">扫描 <b>${total}</b> 只 · 命中 <b class="good">${matched}</b> 只</span>
      <span class="caption dim"> · ${took}ms</span>
      <span class="caption dim"> · ${escapeHtml(ts)}</span>
      <button class="btn btn-mini" id="wb-refresh" style="margin-left:.5rem">↻ 刷新</button>
    `;
    const refreshBtn = $('#wb-refresh');
    if (refreshBtn) refreshBtn.onclick = () => loadWeeklyBull(true);
  }

  // ── 列表 ──
  if (!listHost) return;
  let signals = _wbData.signals || [];
  if (_wbFilter !== 'all') {
    signals = signals.filter(s => (s.matched || []).includes(_wbFilter));
  }
  // 排序
  signals = signals.slice().sort((a, b) => {
    let va, vb;
    if (_wbSortKey === 'count') { va = a.count; vb = b.count; }
    else if (_wbSortKey === 'code') { va = a.code; vb = b.code; }
    else if (_wbSortKey === 'change_pct') {
      va = a.weekly_last?.change_pct ?? 0;
      vb = b.weekly_last?.change_pct ?? 0;
    } else { va = 0; vb = 0; }
    if (va < vb) return _wbSortDir === 'asc' ? -1 : 1;
    if (va > vb) return _wbSortDir === 'asc' ? 1 : -1;
    return 0;
  });

  if (signals.length === 0) {
    listHost.innerHTML = `<div class="empty-state"><span class="dim">当前过滤无命中股</span></div>`;
    return;
  }

  const sortArrow = (k) => _wbSortKey === k ? (_wbSortDir === 'asc' ? ' ▲' : ' ▼') : '';
  listHost.innerHTML = `
    <table class="wb-table">
      <thead>
        <tr>
          <th class="wb-sort" data-wb-sort="code">代码${sortArrow('code')}</th>
          <th>周收盘</th>
          <th class="wb-sort" data-wb-sort="change_pct">周涨跌${sortArrow('change_pct')}</th>
          <th class="wb-sort" data-wb-sort="count">命中${sortArrow('count')}</th>
          <th>命中信号</th>
          <th>理由</th>
        </tr>
      </thead>
      <tbody>
        ${signals.map(s => {
          const wk = s.weekly_last || {};
          const chips = (s.matched || []).map(k => {
            return `<span class="chip tag-good wb-mini" data-wb-filter="${escapeHtml(k)}" title="${escapeHtml((s.reasons || {})[k] || '')}">${escapeHtml(_WB_PATTERN_LABELS[k] || k)}</span>`;
          }).join('');
          const reasons = (s.matched || []).map(k => {
            const reason = (s.reasons || {})[k] || '';
            return `<div class="wb-reason">· <b>${escapeHtml(_WB_PATTERN_LABELS[k] || k)}</b>: ${escapeHtml(reason.slice(0, 60))}</div>`;
          }).join('');
          return `<tr class="clickable" data-code="${escapeHtml(s.code)}">
            <td><a href="#" class="stock-link" data-code="${escapeHtml(s.code)}">${escapeHtml(s.code)}</a></td>
            <td>${wk.close != null ? wk.close.toFixed(2) : '—'}</td>
            <td>${wk.change_pct != null ? (wk.change_pct >= 0 ? '<span class="good">+' : '<span class="bad">') + wk.change_pct.toFixed(2) + '%</span>' : '—'}</td>
            <td><b>${s.count}</b>/5</td>
            <td><div class="wb-chips">${chips}</div></td>
            <td><div class="wb-reasons">${reasons}</div></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;

  // 行点击 → 个股页 (全局委托)
  listHost.querySelectorAll('a.stock-link').forEach(a => {
    a.onclick = (e) => {
      e.preventDefault();
      location.hash = `#stock=${encodeURIComponent(a.dataset.code)}`;
    };
  });
  // 行点击 (非链接区) 同样跳转
  listHost.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('a,button,.chip')) return;
      location.hash = `#stock=${encodeURIComponent(tr.dataset.code)}`;
    });
  });
  // 表头排序
  listHost.querySelectorAll('.wb-sort').forEach(th => {
    th.style.cursor = 'pointer';
    th.onclick = () => {
      const k = th.dataset.wbSort;
      if (_wbSortKey === k) _wbSortDir = _wbSortDir === 'asc' ? 'desc' : 'asc';
      else { _wbSortKey = k; _wbSortDir = 'desc'; }
      renderWeeklyBull();
    };
  });
  // chip 二次点击 (在表内) 切换过滤
  listHost.querySelectorAll('.chip.wb-mini').forEach(c => {
    c.style.cursor = 'pointer';
    c.onclick = (e) => {
      e.stopPropagation();
      _wbFilter = c.dataset.wbFilter;
      const qs = `?pattern=${encodeURIComponent(_wbFilter)}`;
      const newHash = '#weekly_bull' + qs;
      if (location.hash !== newHash) {
        history.replaceState(null, '', newHash);
      }
      renderWeeklyBull();
    };
  });
}

// 暴露到全局 (app.js 会调用)
window.loadWeeklyBull = loadWeeklyBull;
window.renderWeeklyBull = renderWeeklyBull;
window._wbLoaded = () => _wbLoaded;

// 重写 loadWeeklyBull 入口,支持 ?pattern= override
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