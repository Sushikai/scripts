(function() {
  if (window._allStocksInit) return;
  window._allStocksInit = true;

  // === 0. scope helpers (用 $1 区分 all_stocks 内,$ = app.js 全局) ========
  function $(s, root) { return (root || document).querySelector(s); }
  function $$(s, root) { return Array.from((root || document).querySelectorAll(s)); }
  function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }
  function el(tag, opts, ...children) {
    const e = document.createElement(tag);
    if (opts) Object.entries(opts).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'style' && typeof v === 'object') Object.entries(v).forEach(([sk, sv]) => e.style[sk] = sv);
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) e.setAttribute(k, v);
    });
    children.flat().forEach(c => { if (c == null) return; if (typeof c === 'string') e.appendChild(document.createTextNode(c)); else e.appendChild(c); });
    return e;
  }

  // === 1. state ==========================================================
  const state = {
    pageSize: 30,
    offset: 0,
    loadedCount: 0,
    totalAvailable: 0,
    hasMore: true,
    loading: false,
    l1: '', l2: '', l3: '', l4: '', domain: '',
    sort: 'amount', order: 'desc',
    _filterData: null,
    _watchedCodes: new Set(),
    _sentinelObserver: null,
    _qsTimer: null,
    _hiddenCols: new Set(),
    _colLabelsByDataCol: {},
    _initialised: false,
  };

  // === 2. toast ==========================================================
  function toast(msg, type, ms) {
    const t = $('#as-toast');
    if (!t) return;
    t.textContent = msg;
    t.className = `as-toast show ${type || ''}`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.className = 'as-toast'; }, ms || 2400);
  }

  // === 3. fetchJSON =======================================================
  async function fetchJSON(path, params) {
    params = params || {};
    const url = new URL(path, location.origin);
    Object.entries(params).forEach(([k, v]) => {
      if (v == null || v === '') return;
      if (Array.isArray(v)) v.forEach(x => url.searchParams.append(k, x));
      else url.searchParams.set(k, v);
    });
    const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const env = await r.json();
    if (!env.ok) throw new Error(env.error || 'API err');
    return env.data;
  }

  // === 4. computePageSize =================================================
  function computePageSize() {
    const scroll = $('#as-table-scroll');
    if (!scroll) return state.pageSize || 30;
    const containerH = scroll.clientHeight || (window.innerHeight - 380);
    let rowH = 36;
    const sample = $('#as-stocks-tbody tr.stock-row');
    if (sample) rowH = sample.offsetHeight || 36;
    if (!rowH || rowH < 20) rowH = 36;
    const visible = Math.max(8, Math.floor(containerH / rowH));
    return Math.max(15, Math.ceil(visible * 1.5));
  }

  function readMultiSelect(id) {
    const sel = $('#' + id);
    if (!sel) return [];
    return Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  }
  function setMultiSelect(id, vals) {
    const sel = $('#' + id);
    if (!sel) return;
    Array.from(sel.options).forEach(o => o.selected = vals.includes(o.value));
  }

  // === 5. loadFilters =====================================================
  async function loadFilters() {
    try {
      const data = await fetchJSON('/api/all_stocks/filters');
      state._filterData = data;
      // L2 申万
      setSelectOptions('as-l2', data.industries || []);
      // L3 产业链
      setSelectOptions('as-l3', (data.chains || []).map(c => c.name));
      // L4 细分
      setSelectOptions('as-l4', data.l4 || []);
      // 领域 — 优先用后端返回 (Step 2e),fallback 静态兜底
      const domains = data.domains || data.DOMAINS || [
        '机器人本体','机器人零部件','机器视觉','AI 算力','AI 芯片','AI 软件',
        '智能驾驶','半导体','新能源车','传统行业','未分类',
      ];
      setSelectOptions('as-domain', domains);

      // L1 集群 chip
      if (data.clusters && data.clusters.length) {
        renderClusterChips(data.clusters);
      }
    } catch (e) {
      console.warn('loadFilters failed:', e);
    }
  }
  function setSelectOptions(id, items) {
    const sel = $('#' + id);
    if (!sel) return;
    sel.innerHTML = items.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  }

  function renderClusterChips(clusters) {
    const row = $('#as-cluster-row');
    if (!row) return;
    row.innerHTML = '';
    const all = el('span', {
      class: 'cluster-chip' + (state.l1 ? '' : ' active'),
      'data-l1': '',
    }, el('span', { class: 'dot', style: 'background: var(--accent-grad-rainbow);' }), '全部');
    row.appendChild(all);
    clusters.forEach(c => {
      const color = c.color || '#888';
      const chip = el('span', {
        class: 'cluster-chip' + (state.l1 === c.name ? ' active' : ''),
        'data-l1': c.name,
        title: `${c.name} · ${c.desc || ''}\n申万: ${(c.sw_set || []).join(' / ')}`,
        style: { '--cc': color },
      }, el('span', { class: 'dot', style: `background:${color};` }),
        (c.icon ? c.icon + ' ' : '') + c.name);
      row.appendChild(chip);
    });
    $$('.cluster-chip', row).forEach(c => {
      c.addEventListener('click', () => {
        state.l1 = c.dataset.l1 || '';
        // 切 L1: 清 L2/L3/L4/domain (粗筛为主)
        state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
        syncUI();
        syncUrl();
        loadBoard();
        toast(state.l1 ? `已切到 ${state.l1}` : '已重置集群');
      });
    });
  }

  // === 6. 统一级联 (Bug 5) =================================================
  // layer ∈ {'l1','l2','l3','l4','domain'} — 切细层时不再 wipe 粗层
  function applyAllStocksCascade(layer) {
    if (layer === 'l1') {
      // 切 L1 粗筛,清细层
      state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
    } else if (layer === 'l2') {
      // L2 申万 切,保留 L1 联合
      // 语义: L1 ∩ L2 (但当前后端是 l2 → l1, 仍兼容)
    } else if (layer === 'l3') {
      // L3 是细分产业链,选具体后清 L4 / domain
      state.l4 = ''; state.domain = '';
    } else if (layer === 'l4') {
      // L4 是最细分,清 domain
      state.domain = '';
    }
  }

  // === 7. loadBoard (首屏 / 筛选重置) =====================================
  async function loadBoard() {
    if (state.loading) return;
    state.loading = true;
    state.offset = 0;
    state.loadedCount = 0;
    state.hasMore = true;
    state.pageSize = computePageSize();

    const tbody = $('#as-stocks-tbody');
    tbody.innerHTML = renderSkeleton(state.pageSize);
    setSentinel('loading', '首屏加载中…');

    try {
      const data = await fetchJSON('/api/all_stocks/board', {
        page_size: state.pageSize,
        offset: 0,
        l1: state.l1 || '',
        l2: state.l2 || '',
        l3: state.l3 || '',
        l4: state.l4 || '',
        domain: state.domain || '',
        sort: state.sort,
        order: state.order,
        with_fund: true,
      });
      renderRows(data, false);
      state.offset = data.next_offset || (data.items && data.items.length) || 0;
      state.loadedCount = state.offset;
      state.totalAvailable = data.total_available || state.loadedCount;
      state.hasMore = !!data.has_more;
      renderMeta(data);
      updateSentinel();
      // 首屏渲染后,如果样本行替换,重算一次更准的 pageSize
      const newPS = computePageSize();
      if (Math.abs(newPS - state.pageSize) > 4 && state.hasMore) state.pageSize = newPS;
    } catch (e) {
      console.error('loadBoard failed:', e);
      tbody.innerHTML = `<tr><td colspan="20" class="empty">
        <div class="empty-icon">!</div>
        <div class="empty-title">加载失败</div>
        <div>${escapeHtml(e.message)}</div>
        <div class="empty-suggestion">
          <div style="font-size:11px;margin-bottom:6px;">网络受限/限频时,可能需要重试</div>
          <button onclick="window.__initAllStocksLoadBoard && window.__initAllStocksLoadBoard()">重新加载</button>
        </div>
      </td></tr>`;
      setSentinel('idle', '加载失败 — 滚动重试');
    } finally {
      state.loading = false;
    }
  }
  window.__initAllStocksLoadBoard = loadBoard;  // 给 empty-state 按钮调用

  // === 8. loadMore (滚动追加) ==============================================
  async function loadMore() {
    if (state.loading || !state.hasMore) return;
    state.loading = true;
    setSentinel('loading', '加载中…');
    try {
      const data = await fetchJSON('/api/all_stocks/board', {
        page_size: state.pageSize,
        offset: state.offset,
        l1: state.l1 || '',
        l2: state.l2 || '',
        l3: state.l3 || '',
        l4: state.l4 || '',
        domain: state.domain || '',
        sort: state.sort,
        order: state.order,
        with_fund: true,
      });
      renderRows(data, true);
      state.offset = data.next_offset || (state.offset + ((data.items && data.items.length) || 0));
      state.loadedCount = state.offset;
      state.totalAvailable = data.total_available || state.loadedCount;
      state.hasMore = !!data.has_more;
      renderMeta(data);
      updateSentinel();
    } catch (e) {
      console.warn('loadMore failed:', e);
      setSentinel('error', `加载失败 — 滚动重试 (${escapeHtml(e.message)})`);
    } finally {
      state.loading = false;
    }
  }

  // === 9. 滚动哨兵 ========================================================
  function setupSentinelObserver() {
    if (state._sentinelObserver) state._sentinelObserver.disconnect();
    const sentinel = $('#as-scroll-sentinel');
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;
    state._sentinelObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting && !state.loading && state.hasMore) loadMore();
      }
    }, {
      root: $('#as-table-scroll') || null,
      rootMargin: '300px 0px',
      threshold: 0,
    });
    state._sentinelObserver.observe(sentinel);
  }
  function setSentinel(s, text) {
    const el_ = $('#as-scroll-sentinel');
    if (!el_) return;
    el_.dataset.state = s;
    el_.textContent = '';
    const t = el('span', { class: 'ss-text' }, text);
    el_.appendChild(t);
  }
  function updateSentinel() {
    if (!state.hasMore) { setSentinel('done', `已加载全部 ${state.totalAvailable} 只`); return; }
    if (state.loading)  { setSentinel('loading', '加载中…'); return; }
    setSentinel('idle', `滚动加载更多 · 还有 ${state.totalAvailable - state.loadedCount} 只`);
  }

  // === 10. renderSkeleton =================================================
  function renderSkeleton(n) {
    const rows = Math.min(n, 12);
    let html = '';
    for (let i = 0; i < rows; i++) {
      html += `<tr class="skeleton-row">
        <td class="sticky-left"><span class="sk sk-sm"></span></td>
        <td class="sticky-left-2"><span class="sk sk-lg"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-num"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
        <td><span class="sk sk-sm"></span></td>
      </tr>`;
    }
    return html;
  }

  // === 11. renderRows =====================================================
  function renderRows(data, append) {
    const items = (data && data.items) || [];
    const tbody = $('#as-stocks-tbody');
    if (!items.length) {
      if (append) return;
      tbody.innerHTML = `<tr><td colspan="20" class="empty">
        <div class="empty-icon">?</div>
        <div class="empty-title">没有符合筛选的股票</div>
        <div>试试放宽筛选条件</div>
        <div class="empty-suggestion">
          <button onclick="document.getElementById('as-btn-reset').click()">清除全部筛选</button>
        </div>
      </td></tr>`;
      return;
    }
    const html = items.map(r => {
      const pct = r.change_pct || 0;
      const pctCls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
      const pctFl  = pct > 0 ? 'flash-up' : (pct < 0 ? 'flash-down' : '');
      const amt = r.change_amt || 0;
      const amtCls = amt > 0 ? 'up' : amt < 0 ? 'down' : '';
      const fund = r.main_fund_inflow_wan || 0;
      const fundCls = fund > 0 ? 'up' : fund < 0 ? 'down' : '';
      const tax = r.taxonomy || {};
      const domains = r.domain || [];
      const domainChip = domains.length
        ? domains.map(d => `<span class="chip chip-domain chip-click" data-goto-domain="${escapeHtml(d)}" title="查看所有「${escapeHtml(d)}」标的">${escapeHtml(d)}</span>`).join('')
        : '<span class="dim">—</span>';
      const l1 = tax.l1
        ? `<span class="chip chip-l1" style="--chip-bg:${tax.l1_color || '#888'}22;--chip-fg:${tax.l1_color || '#888'};border-color:${tax.l1_color || '#888'};" title="L1 集群">${escapeHtml(tax.l1)}</span>`
        : '<span class="dim">—</span>';
      const l2 = tax.l2
        ? `<span class="chip chip-click" data-goto-l2="${escapeHtml(tax.l2)}" title="查看所有 ${escapeHtml(tax.l2)} 标的">${escapeHtml(tax.l2)}</span>`
        : '<span class="dim">—</span>';
      const l3 = tax.l3
        ? `<span class="chip chip-click" data-goto-l3="${escapeHtml(tax.l3)}" title="查看所有 ${escapeHtml(tax.l3)} 标的">${escapeHtml(tax.l3)}${tax.l3_source && tax.l3_source !== 'cache' ? ` <span class="dim" style="font-size:9px;">(${escapeHtml(tax.l3_source)})</span>` : ''}</span>`
        : '<span class="dim">—</span>';
      const l4List = tax.l4 || [];
      const l4 = l4List.length
        ? l4List.map(x => `<span class="chip chip-click" data-goto-l4="${escapeHtml(x)}" title="查看所有 ${escapeHtml(x)} 标的">${escapeHtml(x)}</span>`).join('')
        : '<span class="dim">—</span>';
      const role = tax.role ? `<span class="chip-role role-${escapeHtml(tax.role)}">${escapeHtml(tax.role)}</span>` : '';
      const ztTag = r.zt_today ? `<span class="zt-tag"><span class="zt-icon"></span>涨停</span>`
                  : r.zt_recent ? `<span class="zt-recent" title="近 3 日累计涨停 ${r.zt_recent} 次">${r.zt_recent}日</span>`
                  : '<span class="dim">—</span>';
      const taxSrc = ((tax.l3_source || '').slice(0, 4));
      const srcTag = taxSrc
        ? `<span class="dim" style="font-size:10px;" title="taxonomy 来源: ${escapeHtml(taxSrc)}">${escapeHtml(taxSrc)}</span>`
        : '<span class="dim">—</span>';
      return `<tr class="stock-row" data-code="${escapeHtml(r.code)}" data-name="${escapeHtml(r.name || '')}">
        <td class="cat" data-col="自选"><span class="star-btn" data-star-code="${escapeHtml(r.code)}" data-star-name="${escapeHtml(r.name || '')}" title="加自选">☆</span></td>
        <td class="cat sticky-left" data-col="代码"><span class="code-link" data-code="${escapeHtml(r.code)}">${escapeHtml(r.code)}</span></td>
        <td class="cat sticky-left-2" data-col="名称"><span class="name">${escapeHtml(r.name || '')}</span></td>
        <td class="cat" data-col="领域">${domainChip}</td>
        <td class="num ${pctCls} ${pctFl}" data-col="涨幅">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
        <td class="num ${amtCls}" data-col="涨跌额">${amt >= 0 ? '+' : ''}${amt.toFixed(2)}</td>
        <td class="num" data-col="换手">${(r.turnover || 0).toFixed(2)}</td>
        <td class="num" data-col="量比">${(r.volume_ratio || 0).toFixed(2)}</td>
        <td class="num" data-col="振幅">${(r.amplitude || 0).toFixed(2)}</td>
        <td class="num cat-mid" data-col="成交额">${(r.amount_yi || 0).toFixed(2)}</td>
        <td class="num cat-mid" data-col="市值">${(r.mcap_yi || 0).toFixed(0)}</td>
        <td class="num cat-mid" data-col="PE">${r.pe_ttm ? r.pe_ttm.toFixed(1) : '—'}</td>
        <td class="num cat-mid ${fundCls}" data-col="主力净流入">${fund >= 0 ? '+' : ''}${fund.toFixed(0)}</td>
        <td class="cat-mid" data-col="L1">${l1}</td>
        <td class="cat-mid" data-col="L2">${l2}</td>
        <td class="cat-mid" data-col="L3">${l3}${role}</td>
        <td class="cat-mid" data-col="L4">${l4}</td>
        <td class="cat-mid" data-col="来源">${srcTag}</td>
        <td class="cat-mid" data-col="涨停">${ztTag}</td>
        <td class="cat-mid" data-col="同链涨停" data-code="${escapeHtml(r.code)}" data-ztchips="1"><span class="zt-chips-placeholder dim" style="font-size:11px">…</span></td>
      </tr>`;
    }).join('');
    if (append) tbody.insertAdjacentHTML('beforeend', html);
    else tbody.innerHTML = html;
    // 追加/替换后立即套列显隐 + 自选染色 + 行内 handler
    if (typeof applyColVisibility === 'function') applyColVisibility();
    if (typeof refreshStarMarks === 'function') refreshStarMarks();
    bindRowHandlers();
    // 2026-07-14: 同链涨停 chips — 触发批量拉取(防重复入队)
    hydrateZtChainChips();
  }

  // ── 2026-07-14: 把所有没注入 chips 的<td data-ztchips> 填上 (防抖 + 空跑优化)
  let _ztHydrateTimer = null;
  function hydrateZtChainChips() {
    if (_ztHydrateTimer) return;
    _ztHydrateTimer = setTimeout(async () => {
      _ztHydrateTimer = null;
      const cells = Array.from(document.querySelectorAll('#as-stocks-tbody td[data-ztchips="1"]'));
      if (!cells.length) return;
      const codes = cells.map(c => c.dataset.code).filter(Boolean);
      const rows = await _ztChainFetch(codes);
      for (const cell of cells) {
        const code = cell.dataset.code;
        const html = _renderZtChainChips(code, { max: 3 });
        // chips 渲染前清掉占位
        cell.innerHTML = html || '<span class="dim" style="font-size:11px">—</span>';
      }
    }, 50);
  }

  // === 12. bindRowHandlers ================================================
  function bindRowHandlers() {
    // ⭐ 加自选 — 统一走 POST /api/watchlist (Bug 6)
    $$('.star-btn', $('#as-stocks-table tbody')).forEach(el_ => {
      el_.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (el_.classList.contains('active')) { toast('已在自选池', 'ok', 1400); return; }
        const code = el_.dataset.starCode;
        const name = el_.dataset.starName || '';
        try {
          const resp = await fetch('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, name, tag: '全A风向' }),
          });
          const env = await resp.json();
          if (env.ok) {
            el_.classList.add('active');
            state._watchedCodes.add(code);
            toast(`已加自选 ${code} ${name}`, 'ok');
          } else {
            toast('加自选失败: ' + (env.error || '未知'), 'err');
          }
        } catch (err) {
          toast('加自选失败: ' + err.message, 'err');
        }
      });
    });
    // 代码 → 个股详情 (新窗口,带 from=all_stocks)
    $$('.code-link', $('#as-stocks-table tbody')).forEach(el_ => {
      el_.addEventListener('click', () => {
        const code = el_.dataset.code;
        window.open(`/?code=${code}&from=all_stocks`, '_blank', 'noopener');
      });
    });
    // L2 / L3 / L4 / 领域 chip 联动 — 切细层不再 wipe L1 (R4)
    function bindGotoLayer(attr, layer) {
      $$(`[data-goto-${attr}]`, $('#as-stocks-table tbody')).forEach(el_ => {
        el_.addEventListener('click', () => {
          state[layer] = el_.dataset[('goto' + attr.charAt(0).toUpperCase() + attr.slice(1)).replace('L', 'L')];
          // compute from dataset
          const val = el_.dataset['goto' + attr.toUpperCase()] || el_.dataset[attr.replace(/^./, c => c)] || '';
          // dataset for "goto-l2" → "gotoL2"
          const key = attr.replace(/-/g, '');
          state[layer] = el_.dataset['goto' + (attr.charAt(0).toUpperCase() + attr.slice(1))];
          applyAllStocksCascade(layer);
          syncUI();
          syncUrl();
          loadBoard();
          toast(`已联动 ${layer.toUpperCase()} = ${state[layer]}`);
        });
      });
    }
    // 简化版(明确层映射):
    function makeGotoBinder(attr, layer, dsKey) {
      $$(`[data-goto-${attr}]`, $('#as-stocks-table tbody')).forEach(el_ => {
        el_.addEventListener('click', () => {
          state[layer] = el_.dataset[dsKey];
          applyAllStocksCascade(layer);
          syncUI(); syncUrl(); loadBoard();
          toast(`已联动 ${layer} = ${state[layer]}`);
        });
      });
    }
    makeGotoBinder('l2', 'l2', 'gotoL2');
    makeGotoBinder('l3', 'l3', 'gotoL3');
    makeGotoBinder('l4', 'l4', 'gotoL4');
    makeGotoBinder('domain', 'domain', 'gotoDomain');
  }

  // === 13. renderMeta + active filters ====================================
  function renderMeta(data) {
    const tookMs = (data && data.took_ms) || 0;
    const count = (data && data.count) || 0;
    const totalUni = (data && data.total_universe) || 0;
    const totalCand = (data && data.total_candidates) || 0;
    const totalAvail = state.totalAvailable || count;
    const loaded = state.loadedCount || count;
    const cacheTag = data && data.cache_hit ? ' · <span style="color:var(--accent)">cache</span>' : '';
    const node = $('#as-meta-count');
    if (node) node.innerHTML =
      `<b>${loaded}</b> / ${totalAvail} 只 · 候选 ${totalCand} / 总池 ${totalUni} · ${(data && data.sort) || state.sort} ${(data && data.order) || state.order} · ${tookMs}ms${cacheTag}`;
    renderActiveFilters();
  }

  function renderActiveFilters() {
    const tags = [];
    if (state.l1) tags.push({ key: 'l1', label: `L1 · ${state.l1}` });
    [[state.l2,'l2','L2'],[state.l3,'l3','L3'],[state.l4,'l4','L4'],[state.domain,'domain','领域']].forEach(([v, key, prefix]) => {
      (v || '').split(',').filter(Boolean).forEach(x => {
        tags.push({ key: `${key}-multi`, label: `${prefix} · ${x}`, val: x });
      });
    });
    const root = $('#as-active-filters');
    if (!root) return;
    root.innerHTML = tags.map(t =>
      `<span class="chip-active" data-key="${t.key}" data-val="${escapeHtml(t.val || '')}">${escapeHtml(t.label)} <span class="x" title="移除">✕</span></span>`
    ).join('');
    $$('#as-active-filters .chip-active .x').forEach(x => {
      x.addEventListener('click', () => {
        const parent = x.parentElement;
        const key = parent.dataset.key;
        const val = parent.dataset.val;
        if (key === 'l1') state.l1 = '';
        else if (key === 'l2-multi') state.l2 = state.l2.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'l3-multi') state.l3 = state.l3.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'l4-multi') state.l4 = state.l4.split(',').filter(v => v && v !== val).join(',');
        else if (key === 'domain-multi') state.domain = state.domain.split(',').filter(v => v && v !== val).join(',');
        syncUI(); syncUrl(); loadBoard();
      });
    });
  }

  // === 14. syncUI =========================================================
  function syncUI() {
    $$('.cluster-chip').forEach(c => c.classList.toggle('active', (c.dataset.l1 || '') === state.l1));
    // 排序
    const sortSel = $('#as-sort');
    if (sortSel) {
      Array.from(sortSel.options).forEach(o => {
        if (o.value === state.sort && o.dataset.order === state.order) sortSel.value = o.value;
      });
    }
    // 多选
    setMultiSelect('as-l2',     state.l2.split(',').filter(Boolean));
    setMultiSelect('as-l3',     state.l3.split(',').filter(Boolean));
    setMultiSelect('as-l4',     state.l4.split(',').filter(Boolean));
    setMultiSelect('as-domain', state.domain.split(',').filter(Boolean));
    // 角标
    [['as-l2',state.l2],['as-l3',state.l3],['as-l4',state.l4],['as-domain',state.domain]].forEach(([id, val]) => {
      const sel = $('#' + id);
      if (!sel) return;
      const group = sel.closest('.filter-group');
      if (!group) return;
      const n = val.split(',').filter(Boolean).length;
      group.classList.toggle('has-active', n > 0);
      const oldBadge = group.querySelector('.count-badge');
      if (oldBadge) oldBadge.remove();
      if (n > 0) {
        const label = group.querySelector('label');
        if (label && !label.querySelector('.count-badge')) {
          label.appendChild(el('span', { class: 'count-badge' }, String(n)));
        }
      }
    });
  }

  // === 15. URL 同步 / 深链 ================================================
  function syncUrl() {
    const params = new URLSearchParams();
    if (state.l1)     params.set('l1',     state.l1);
    if (state.l2)     params.set('l2',     state.l2);
    if (state.l3)     params.set('l3',     state.l3);
    if (state.l4)     params.set('l4',     state.l4);
    if (state.domain) params.set('domain', state.domain);
    if (state.sort !== 'amount' || state.order !== 'desc') {
      params.set('sort',  state.sort);
      params.set('order', state.order);
    }
    // Bug 4: pageSize 只在非默认 30 时写;offset 只在 >0 时写,reset 后默认不写
    if (state.pageSize && state.pageSize !== 30) params.set('ps', state.pageSize);
    if (state.offset > 0)                          params.set('off', state.offset);
    const q = params.toString();
    const newUrl = '#all_stocks' + (q ? '?' + q : '');
    if (location.hash !== newUrl) {
      try { history.replaceState(null, '', '/' + newUrl); } catch (e) {}
    }
  }

  function applyAllStocksDeepLink(qs) {
    const q = new URLSearchParams(qs || '');
    const get = k => q.get(k);
    if (get('l1'))     state.l1     = get('l1');
    if (get('l2'))     state.l2     = get('l2');
    if (get('l3'))     state.l3     = get('l3');
    if (get('l4'))     state.l4     = get('l4');
    if (get('domain')) state.domain = get('domain');
    if (get('sort'))   state.sort   = get('sort');
    if (get('order'))  state.order  = get('order');
    if (get('ps'))     state.pageSize = Math.max(15, parseInt(get('ps')) || 30);
    if (get('off'))    state.offset  = Math.max(0, parseInt(get('off')) || 0);
  }

  // === 16. 排序表头 + 排序 sync ============================================
  function bindSortHeader() {
    $$('#as-stocks-table thead th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const sort = th.dataset.sort;
        let order = th.dataset.order || 'desc';
        if (state.sort === sort) order = state.order === 'desc' ? 'asc' : 'desc';
        state.sort = sort;
        state.order = order;
        th.dataset.order = order;
        const sortSel = $('#as-sort');
        if (sortSel) {
          let matched = false;
          Array.from(sortSel.options).forEach(o => {
            const isMatch = o.value === sort && o.dataset.order === order;
            o.selected = isMatch;
            if (isMatch) matched = true;
          });
          // 后端没该组合的 option 时,加一个临时 option
          if (!matched) {
            const o = document.createElement('option');
            o.value = sort; o.dataset.order = order; o.selected = true;
            o.textContent = `${sort} ${order}`;
            sortSel.appendChild(o);
          }
        }
        syncUrl();
        loadBoard();
        updateSortArrows();
      });
    });
    updateSortArrows();
  }

  function updateSortArrows() {
    $$('#as-stocks-table thead th.sortable').forEach(th => {
      const arrow = th.querySelector('.arrow');
      if (!arrow) return;
      if (th.dataset.sort === state.sort) {
        arrow.textContent = state.order === 'desc' ? '↓' : '↑';
      } else {
        arrow.textContent = '';
      }
    });
  }

  // === 17. bindControls (按钮 + select) ===================================
  function bindControls() {
    // 排序 select
    const sortSel = $('#as-sort');
    if (sortSel) sortSel.addEventListener('change', (e) => {
      const opt = e.target.selectedOptions[0];
      if (!opt) return;
      state.sort = opt.value;
      state.order = opt.dataset.order || 'desc';
      syncUrl(); loadBoard();
      updateSortArrows();
    });

    // 多选 select 改动
    [['as-l2','l2'],['as-l3','l3'],['as-l4','l4'],['as-domain','domain']].forEach(([id, key]) => {
      const sel = $('#' + id);
      if (!sel) return;
      sel.addEventListener('change', () => {
        const vals = readMultiSelect(id);
        state[key] = vals.join(',');
        applyAllStocksCascade(key);
        syncUI(); syncUrl(); loadBoard();
      });
    });

    // 重置
    const resetBtn = $('#as-btn-reset');
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.l1 = ''; state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
      state.sort = 'amount'; state.order = 'desc';
      state.pageSize = 30;
      state.offset = 0;
      // 同时清掉快速搜索框 + 触发行过滤
      if (qsInput) {
        qsInput.value = '';
        qsBox.classList.remove('has-value');
        if (qsClear) qsClear.hidden = true;
        applyQuickSearch('');
      }
      syncUI(); syncUrl(); loadBoard();
      toast('已重置所有筛选', 'ok');
    });
    // 刷新
    const refreshBtn = $('#as-btn-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => {
      loadBoard();
      toast('已刷新', 'ok');
    });

    // 快速搜索
    const qsBox = $('#as-quick-search');
    const qsInput = $('#as-qs-input');
    const qsClear = $('#as-qs-clear');
    if (qsInput) {
      qsInput.addEventListener('input', () => {
        const v = qsInput.value.trim();
        qsBox.classList.toggle('has-value', !!v);
        if (qsClear) qsClear.hidden = !v;
        clearTimeout(state._qsTimer);
        state._qsTimer = setTimeout(() => applyQuickSearch(v), 180);
      });
      qsInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); applyQuickSearch(qsInput.value.trim()); }
        if (e.key === 'Escape') {
          qsInput.value = ''; qsBox.classList.remove('has-value');
          if (qsClear) qsClear.hidden = true;
          applyQuickSearch('');
        }
      });
    }
    if (qsClear) qsClear.addEventListener('click', () => {
      qsInput.value = ''; qsBox.classList.remove('has-value'); qsClear.hidden = true;
      applyQuickSearch('');
      qsInput.focus();
    });

    // 列显隐
    setupColToggle();

    // 滚回顶部 FAB (rAF 节流 + passive,避免每帧触发 layout)
    const fab = $('#as-scroll-top-fab');
    const scrollEl = $('#as-table-scroll');
    if (fab && scrollEl) {
      let _fabRafPending = false;
      scrollEl.addEventListener('scroll', () => {
        if (_fabRafPending) return;
        _fabRafPending = true;
        requestAnimationFrame(() => {
          fab.classList.toggle('show', scrollEl.scrollTop > scrollEl.clientHeight * 1.5);
          _fabRafPending = false;
        });
      }, { passive: true });
      fab.addEventListener('click', () => {
        scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }

    // pull-to-refresh (移动端)
    bindPullToRefresh();

    // 移动底部操作栏
    bindMobileActionBar();
  }

  // === 18. applyQuickSearch ===============================================
  function applyQuickSearch(q) {
    q = (q || '').toLowerCase();
    const rows = $$('#as-stocks-tbody tr.stock-row');
    let shown = 0;
    rows.forEach(tr => {
      const code = tr.dataset.code || '';
      const name = (tr.dataset.name || '').toLowerCase();
      const show = !q || code.includes(q) || name.includes(q);
      tr.style.display = show ? '' : 'none';
      if (show) shown++;
    });
    const s = $('#as-scroll-sentinel');
    if (s) {
      if (q && shown === 0 && !state.loading) {
        setSentinel('error', `无匹配 "${q}" 的股票 — 试试更短的关键词`);
      } else if (!q) {
        updateSentinel();
      }
    }
  }

  // === 19. 列显隐 (Bug 7: 用 data-col 而非 textContent) ====================
  function setupColToggle() {
    const wrap = $('#as-col-toggle');
    const btn  = $('#as-col-toggle-btn');
    const menu = $('#as-col-toggle-menu');
    if (!wrap || !btn || !menu) return;

    // Bug 7: 用 thead th data-col 直接收集,不再 textContent 匹配
    const cols = $$('#as-stocks-table thead th[data-col]').map(th => ({
      col: th.dataset.col, label: th.dataset.col,
    }));
    // 显示顺序按 thead 顺序
    state._colLabelsByDataCol = {};
    cols.forEach(c => { state._colLabelsByDataCol[c.col] = c; });

    // 用户偏好
    try {
      const saved = JSON.parse(localStorage.getItem('all_stocks_hidden_cols') || '[]');
      state._hiddenCols = new Set(saved);
    } catch (_) { state._hiddenCols = new Set(); }

    menu.innerHTML = cols.map(c => `
      <div class="col-toggle-row">
        <input type="checkbox" id="ct-${c.col}" data-col="${escapeHtml(c.col)}" ${state._hiddenCols.has(c.col) ? '' : 'checked'} />
        <label for="ct-${c.col}">${escapeHtml(c.label)}</label>
      </div>
    `).join('');

    applyColVisibility();

    menu.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => {
        const col = cb.dataset.col;
        if (cb.checked) state._hiddenCols.delete(col); else state._hiddenCols.add(col);
        try { localStorage.setItem('all_stocks_hidden_cols', JSON.stringify([...state._hiddenCols])); } catch (_) {}
        applyColVisibility();
      });
    });

    btn.addEventListener('click', (e) => { e.stopPropagation(); wrap.classList.toggle('open'); });
    document.addEventListener('click', (e) => {
      if (!wrap.contains(e.target)) wrap.classList.remove('open');
    });
  }

  function applyColVisibility() {
    const showMap = {};
    Object.values(state._colLabelsByDataCol).forEach(c => { showMap[c.col] = !state._hiddenCols.has(c.col); });
    $$('#as-stocks-table thead th').forEach(th => {
      const col = th.dataset.col;
      if (col in showMap) th.style.display = showMap[col] ? '' : 'none';
    });
    $$('#as-stocks-table tbody td[data-col]').forEach(td => {
      const col = td.dataset.col;
      if (col in showMap) td.style.display = showMap[col] ? '' : 'none';
    });
  }

  // === 20. refreshStarMarks ===============================================
  async function loadWatchlistForStars() {
    try {
      const data = await fetchJSON('/api/watchlist');
      const items = (data && data.items) || [];
      state._watchedCodes = new Set(items.map(x => x.code || x));
    } catch (e) {
      console.warn('loadWatchlistForStars failed:', e);
      state._watchedCodes = new Set();
    }
    refreshStarMarks();
  }
  function refreshStarMarks() {
    $$('#as-stocks-tbody .star-btn').forEach(el_ => {
      if (state._watchedCodes.has(el_.dataset.starCode)) el_.classList.add('active');
      else el_.classList.remove('active');
    });
  }

  // === 21. pull-to-refresh ================================================
  function bindPullToRefresh() {
    const isTouch = 'ontouchstart' in window;
    const scroll = $('#as-table-scroll');
    if (!scroll) return;
    let startY = 0, active = false;
    const start = (e) => {
      if (scroll.scrollTop > 5) return;
      startY = (e.touches ? e.touches[0].clientY : e.clientY);
      active = true;
    };
    const move = (e) => {
      if (!active) return;
      const y = (e.touches ? e.touches[0].clientY : e.clientY);
      const dy = y - startY;
      if (dy > 60) {
        active = false;
        showPtrIndicator();
        setTimeout(() => { loadBoard(); hidePtrIndicator(); }, 400);
      }
    };
    const end = () => { active = false; };
    if (isTouch) {
      scroll.addEventListener('touchstart', start, { passive: true });
      scroll.addEventListener('touchmove', move, { passive: true });
      scroll.addEventListener('touchend', end);
    }
  }
  function showPtrIndicator() {
    let ind = $('#ptr-indicator');
    if (!ind) {
      ind = el('div', { id: 'ptr-indicator', class: 'ptr-indicator' },
        el('span', { class: 'ptr-spinner' }), '刷新中…');
      document.body.appendChild(ind);
    }
    ind.classList.add('show');
  }
  function hidePtrIndicator() {
    const ind = $('#ptr-indicator');
    if (ind) ind.classList.remove('show');
  }

  // === 22. table-scroll fade indicator ====================================
  function bindTableScrollIndicator() {
    const card = $('.as-table-card');
    const scroll = $('#as-table-scroll');
    if (!card || !scroll) return;
    function update() {
      const sl = scroll.scrollLeft;
      const max = scroll.scrollWidth - scroll.clientWidth;
      card.classList.toggle('has-scroll-left',  sl > 4);
      card.classList.toggle('has-scroll-right', sl < max - 4);
    }
    let _fadeRafPending = false;
    scroll.addEventListener('scroll', () => {
      if (_fadeRafPending) return;
      _fadeRafPending = true;
      requestAnimationFrame(() => { update(); _fadeRafPending = false; });
    }, { passive: true });
    window.addEventListener('resize', update);
    setTimeout(update, 100);
    const obs = new MutationObserver(() => setTimeout(update, 50));
    obs.observe(scroll, { childList: true, subtree: true });
  }

  // === 23. 移动底部 sheet (复用原 all_stocks.html 逻辑,精简) ================
  function createBottomSheet() {
    const backdrop = el('div', { class: 'bottom-sheet-backdrop' });
    const sheet = el('div', { class: 'bottom-sheet' });
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.setAttribute('aria-label', '筛选面板');
    const head = el('div', { class: 'bottom-sheet-head' },
      el('h3', { id: 'as-sheet-h3' }, '筛选'),
      el('button', { class: 'bottom-sheet-close', 'aria-label': '关闭筛选面板' }, '✕'));
    const body = el('div', { class: 'bottom-sheet-body' });
    sheet.appendChild(head); sheet.appendChild(body);
    document.body.appendChild(backdrop);
    document.body.appendChild(sheet);
    function close() {
      backdrop.classList.remove('show');
      sheet.classList.remove('show');
      // 还原焦点到触发元素
      try { if (sheet._lastFocus && sheet._lastFocus.focus) sheet._lastFocus.focus(); } catch {}
      document.removeEventListener('keydown', _onKey);
      document.dispatchEvent(new CustomEvent('mab:closed'));
    }
    function show(triggerEl) {
      sheet._lastFocus = triggerEl || document.activeElement;
      backdrop.classList.add('show');
      sheet.classList.add('show');
      // 移焦点到 sheet 第一个可聚焦元素
      setTimeout(() => {
        const first = sheet.querySelector('button, [tabindex]:not([tabindex="-1"]), input, select, textarea');
        if (first) first.focus();
      }, 60);
      document.addEventListener('keydown', _onKey);
    }
    function _onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key === 'Tab') {
        // focus trap: 在 sheet 内循环
        const focusables = sheet.querySelectorAll('button:not([disabled]), [tabindex]:not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]');
        if (!focusables.length) return;
        const first = focusables[0], last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    backdrop.addEventListener('click', close);
    head.querySelector('.bottom-sheet-close').addEventListener('click', close);
    return { backdrop, sheet, body, head, close, show };
  }

  function buildFilterSheetHTML() {
    return `
      <div class="sheet-section">
        <div class="sheet-label">集群 (L1)</div>
        <div class="sheet-chips" id="as-sheet-cluster-row"></div>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">申万 (L2)</div>
        <select id="as-sheet-l2" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">产业链 (L3)</div>
        <select id="as-sheet-l3" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">细分 (L4)</div>
        <select id="as-sheet-l4" class="sheet-select" multiple></select>
      </div>
      <div class="sheet-section">
        <div class="sheet-label">主战场 (领域)</div>
        <select id="as-sheet-domain" class="sheet-select" multiple></select>
      </div>
      <button class="btn sheet-apply">应用筛选</button>
    `;
  }

  function buildSortSheetHTML() {
    const sortDefs = [
      {sort:'amount',order:'desc',label:'成交额 ↓'},
      {sort:'change_pct',order:'desc',label:'涨幅 ↓'},
      {sort:'change_pct',order:'asc',label:'涨幅 ↑'},
      {sort:'change_amt',order:'desc',label:'涨跌额 ↓'},
      {sort:'turnover',order:'desc',label:'换手 ↓'},
      {sort:'volume_ratio',order:'desc',label:'量比 ↓'},
      {sort:'main_fund_inflow',order:'desc',label:'主力净流入 ↓'},
      {sort:'mcap',order:'desc',label:'市值 ↓'},
      {sort:'amplitude',order:'desc',label:'振幅 ↓'},
    ];
    return sortDefs.map(s => {
      const active = state.sort === s.sort && state.order === s.order;
      return `<button class="sheet-sort-row ${active?'active':''}" data-sort="${s.sort}" data-order="${s.order}">
        <span class="sheet-sort-label">${s.label}</span>
        ${active?'<span class="sheet-sort-tick">✓</span>':''}
      </button>`;
    }).join('');
  }

  function bindSheetHandlers(scope) {
    const clusterRow = scope.querySelector('#as-sheet-cluster-row');
    if (clusterRow) {
      const cs = (state._filterData && state._filterData.clusters) || [];
      const allBtn = el('button', {
        class: 'sheet-cluster-chip' + (!state.l1 ? ' active' : ''),
        'data-l1': '',
      }, '全部');
      clusterRow.appendChild(allBtn);
      cs.forEach(c => {
        const b = el('button', {
          class: 'sheet-cluster-chip' + (state.l1 === c.name ? ' active' : ''),
          'data-l1': c.name,
          style: '--cc:' + (c.color || '#888'),
        }, el('span', { class: 'dot', style: `background:${c.color || '#888'}` }), (c.icon || '') + c.name);
        clusterRow.appendChild(b);
      });
      clusterRow.addEventListener('click', (e) => {
        const chip = e.target.closest('.sheet-cluster-chip');
        if (!chip) return;
        clusterRow.querySelectorAll('.sheet-cluster-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        state.l1 = chip.dataset.l1;
        applyAllStocksCascade('l1');
      });
    }
    const fdata = state._filterData;
    if (fdata) {
      fillSheetSelect('as-sheet-l2', fdata.industries || []);
      fillSheetSelect('as-sheet-l3', (fdata.chains || []).map(c => c.name));
      fillSheetSelect('as-sheet-l4', fdata.l4 || []);
      const DOMAINS = (fdata && fdata.domains) || [
        '机器人本体','机器人零部件','机器视觉','AI 算力','AI 芯片','AI 软件',
        '智能驾驶','半导体','新能源车','传统行业','未分类',
      ];
      fillSheetSelect('as-sheet-domain', DOMAINS);
    }
    [['as-sheet-l2','l2'],['as-sheet-l3','l3'],['as-sheet-l4','l4'],['as-sheet-domain','domain']].forEach(([id, key]) => {
      const sel = scope.querySelector('#' + id);
      if (!sel) return;
      Array.from(sel.options).forEach(o => o.selected = (state[key] || '').split(',').filter(Boolean).includes(o.value));
    });
    const apply = scope.querySelector('.sheet-apply');
    if (apply) apply.addEventListener('click', () => {
      state.l2 = readMultiSelectRaw(scope.querySelector('#as-sheet-l2')).join(',');
      state.l3 = readMultiSelectRaw(scope.querySelector('#as-sheet-l3')).join(',');
      state.l4 = readMultiSelectRaw(scope.querySelector('#as-sheet-l4')).join(',');
      state.domain = readMultiSelectRaw(scope.querySelector('#as-sheet-domain')).join(',');
      applyAllStocksCascade('l2');
      applyAllStocksCascade('l3');
      applyAllStocksCascade('l4');
      applyAllStocksCascade('domain');
      syncUI(); syncUrl(); loadBoard();
      scope.querySelector('.bottom-sheet-backdrop').classList.remove('show');
      scope.querySelector('.bottom-sheet').classList.remove('show');
      updateMabBadge();
      toast('筛选已应用', 'ok');
    });
    scope.querySelectorAll('.sheet-sort-row').forEach(r => {
      r.addEventListener('click', () => {
        state.sort = r.dataset.sort;
        state.order = r.dataset.order;
        const sortSel = $('#as-sort');
        if (sortSel) {
          Array.from(sortSel.options).forEach(o => { o.selected = (o.value === state.sort && o.dataset.order === state.order); });
        }
        syncUrl(); loadBoard();
        updateSortArrows();
        const sheetEl = r.closest('.bottom-sheet');
        sheetEl.classList.remove('show');
        document.querySelector('.bottom-sheet-backdrop').classList.remove('show');
        updateMabBadge();
        toast(`已切到 ${r.textContent.replace('✓','').trim()}`, 'ok');
      });
    });
  }
  function fillSheetSelect(id, items) {
    const sel = $('#' + id);
    if (!sel) return;
    sel.innerHTML = items.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join('');
  }
  function readMultiSelectRaw(sel) {
    if (!sel) return [];
    return Array.from(sel.selectedOptions).map(o => o.value).filter(Boolean);
  }

  function bindMobileActionBar() {
    const bar = $('#as-mobile-action-bar');
    if (!bar) return;
    let sheet = null;
    function getSheet() { if (!sheet) sheet = createBottomSheet(); return sheet; }
    const filterBtn = $('#as-mab-filters');
    const sortBtn = $('#as-mab-sort');
    const refreshBtn = $('#as-mab-refresh');
    const resetBtn = $('#as-mab-reset');
    if (filterBtn) filterBtn.addEventListener('click', () => {
      const s = getSheet();
      s.head.querySelector('h3').textContent = '筛选';
      s.body.innerHTML = buildFilterSheetHTML();
      // 需要重新解析
      const tmpScope = el('div');
      tmpScope.appendChild(s.body);
      // 把 s.body 的 children 临时挪到 scope
      const tmp = el('div');
      while (s.body.firstChild) tmp.appendChild(s.body.firstChild);
      bindSheetHandlers(tmp);
      while (tmp.firstChild) s.body.appendChild(tmp.firstChild);
      s.show();
    });
    if (sortBtn) sortBtn.addEventListener('click', () => {
      const s = getSheet();
      s.head.querySelector('h3').textContent = '排序';
      s.body.innerHTML = buildSortSheetHTML();
      const tmp = el('div');
      while (s.body.firstChild) tmp.appendChild(s.body.firstChild);
      bindSheetHandlers(tmp);
      while (tmp.firstChild) s.body.appendChild(tmp.firstChild);
      s.show();
    });
    if (refreshBtn) refreshBtn.addEventListener('click', () => { loadBoard(); toast('已刷新', 'ok'); });
    if (resetBtn) resetBtn.addEventListener('click', () => {
      state.l1 = ''; state.l2 = ''; state.l3 = ''; state.l4 = ''; state.domain = '';
      state.sort = 'amount'; state.order = 'desc';
      state.pageSize = 30; state.offset = 0;
      syncUI(); syncUrl(); loadBoard();
      toast('已重置所有筛选', 'ok');
    });
    document.addEventListener('mab:closed', updateMabBadge);
    updateMabBadge();
  }

  function updateMabBadge() {
    const n = [state.l1,
      ...state.l2.split(',').filter(Boolean),
      ...state.l3.split(',').filter(Boolean),
      ...state.l4.split(',').filter(Boolean),
      ...state.domain.split(',').filter(Boolean)].filter(Boolean).length;
    const badge = $('#as-mab-filter-count');
    if (badge) badge.textContent = n > 0 ? String(n) : '';
    const sortLabel = $('#as-mab-sort-label');
    if (sortLabel) {
      const sortNames = {
        amount: '成交额', change_pct: '涨幅', change_amt: '涨跌额',
        turnover: '换手', volume_ratio: '量比', main_fund_inflow: '主力',
        mcap: '市值', amplitude: '振幅',
      };
      sortLabel.textContent = sortNames[state.sort] || '排序';
    }
  }

  // === 24. 智能返回按钮 (R17 兼容 — 走主 app shell smart-back) ===========
  function setupAllStocksBackNav() {
    // 用主 app shell 的 smart-back 风格 — 写入 _prev_page 给详情页读
    try {
      const curr = { url: location.pathname + (location.hash || ''), label: '全 A 风向' };
      sessionStorage.setItem('_curr_page', JSON.stringify(curr));
      window.addEventListener('pagehide', () => {
        try { sessionStorage.setItem('_prev_page', JSON.stringify(curr)); } catch (_) {}
      });
    } catch (_) {}
  }

  // === 25. init ===========================================================
  function init() {
    // 解析深链 hash
    const h = (location.hash || '').replace(/^#/, '');
    if (h.startsWith('all_stocks')) {
      const qs = h.includes('?') ? h.split('?').slice(1).join('?') : '';
      if (qs) applyAllStocksDeepLink(qs);
    }
    setupSentinelObserver();
    setupAllStocksBackNav();
    bindSortHeader();
    bindControls();
    bindTableScrollIndicator();
    syncUI();
    loadWatchlistForStars().then(loadFilters).then(() => {
      syncUI();
      return loadBoard();
    }).then(() => {
      updateSortArrows();
      if (state.l1 || state.l2 || state.l3 || state.l4 || state.domain) {
        toast(`深链已应用: ${[state.l1, state.l2, state.l3, state.l4, state.domain].filter(Boolean).join(' / ')}`);
      }
    });
  }

  // === 26. view-enter 钩子 ================================================
  document.addEventListener('view-enter', (e) => {
    if (!e.detail || e.detail.name !== 'all_stocks') return;
    if (state._initialised) {
      // 重新进入 view — 重新解析 URL 深链 (支持 sidebar 跨页切换)
      const h = (location.hash || '').replace(/^#/, '');
      if (h.startsWith('all_stocks')) {
        const qs = h.includes('?') ? h.split('?').slice(1).join('?') : '';
        applyAllStocksDeepLink(qs);
        syncUI(); loadBoard();
      } else {
        loadBoard();
      }
      return;
    }
    state._initialised = true;
    init();
  });

  // === 27. view-leave cleanup =============================================
  _registerViewLeave('all_stocks', () => {
    if (state._sentinelObserver) {
      state._sentinelObserver.disconnect();
      state._sentinelObserver = null;
    }
    // 不重置 _initialised: 下次再进 view-enter 直接复用 (避免重 bind)
    // 但状态会被 syncUrl 覆盖 — 由 init 解析 hash 重置
  });

  // 暴露到 window 便于 debug / onclick 引用
  window.initAllStocks = init;
})();
