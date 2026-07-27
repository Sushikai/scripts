/* web/static/dexin-frontend.js
   得鑫量变术 · 时序链条量化选股 · 信息密集卡片前端
   挂在 #dexin-mount,自初始化 IIFE; 4 个 stage tab (藏诈/虚杀/等待突破/得鑫 + 危险剔除)
   风格: 沿用项目主设计 token, 卡片密集展示 4 模块数据 + 原话溯源
   移动端 ≤768px: .dexin-creed-panel 默认收起; 心法"展开"按钮 (复用 .dexin-creed-toggle)
*/
(function(){
  'use strict';
  var $mount = document.getElementById('dexin-mount');
  if (!$mount) return;

  // ── 状态 ──
  var _data = null;                     // {stages:..., laws:..., regime, ...}
  var _activeTab = 'cang_zha';
  var _loading = false;
  var _reqId = 0;                       // 防止竞态

  // ── Tab 元数据(显示名 + 颜色) ──
  var TABS = {
    de_xin:           { label: '得鑫主升',       accent: '--accent',   chipClass: 'chip-de-xin'   },
    clearing:         { label: '等待突破',       accent: '--accent-2', chipClass: 'chip-clearing' },
    xu_sha:           { label: '虚杀洗盘',       accent: '--down',     chipClass: 'chip-xu-ben'   },
    cang_zha:         { label: '藏诈诱多',       accent: '--up',       chipClass: 'chip-cang'     },
    xu_sha_dangerous: { label: '虚杀·危险剔除', accent: '--warn',     chipClass: 'chip-xu-danger'},
  };

  // ── 移动端判定 ──
  function isMobile(){
    return window.innerWidth <= 768;
  }

  // ── helpers ──
  function $(s, root){ return (root||$mount).querySelector(s); }
  function $$(s, root){ return Array.from((root||$mount.ownerDocument).querySelectorAll(s)); }

  function esc(s){
    if (s == null) return '';
    return String(s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function fmtPct(v){
    if (v == null || isNaN(v)) return '—';
    var n = Number(v);
    var s = (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
    return '<span class="' + (n >= 0 ? 'pct-up' : 'pct-down') + '">' + s + '</span>';
  }

  function fmtN(v, d){
    if (v == null || isNaN(v)) return '—';
    var n = Number(v);
    if (Math.abs(n) >= 1e8) return (n/1e8).toFixed(d||2) + '亿';
    if (Math.abs(n) >= 1e4) return (n/1e4).toFixed(d||2) + '万';
    return n.toFixed(d||2);
  }

  function chip(text, kind){
    return '<span class="dx-chip ' + (kind||'') + '">' + esc(text||'—') + '</span>';
  }

  function stageBadge(stage, variant){
    var t = TABS[stage] || TABS.cang_zha;
    var label = t.label;
    if (stage === 'xu_sha' && variant === 'dangerous') label = '虚杀·危险回补';
    else if (stage === 'xu_sha' && variant === 'benign') label = '虚杀·良性回踩';
    else if (stage === 'clearing') label = '等待突破';
    return '<span class="dx-stage-badge ' + t.chipClass + '">' + esc(label) + '</span>';
  }

  // 阶段节点渲染: 藏诈日 / 虚杀日 / 洗盘区间 / 得鑫日 / cycle_days
  function phaseDatesChips(dates){
    if (!dates || typeof dates !== 'object') return '';
    var parts = [];
    var ORDER = [
      ['藏诈日',    'chip-info',   '藏诈'],
      ['虚杀日',    'chip-warn',   '虚杀'],
      ['洗盘区间',  'chip-mute',   '区间'],
      ['得鑫日',    'chip-good',   '得鑫'],
    ];
    for (var i = 0; i < ORDER.length; i++){
      var key = ORDER[i][0], cls = ORDER[i][1], label = ORDER[i][2];
      var v = dates[key];
      if (v == null || v === '' || v === '—') continue;
      parts.push('<span class="dx-phase-chip ' + cls + '" title="' + esc(key) + '">' + esc(label) + ' ' + esc(String(v)) + '</span>');
    }
    if (dates.cycle_days && dates['得鑫日']) {
      parts.push('<span class="dx-phase-chip chip-de-xin" title="完整洗盘周期">' + dates.cycle_days + '天周期</span>');
    }
    return parts.length ? '<div class="dx-phase-row">' + parts.join('') + '</div>' : '';
  }

  function gapBadge(g){
    if (!g) return '';
    if (g.has_unfilled_up && g.filled_count === 0) {
      return chip('有未回补向上跳空', 'chip-good');
    }
    if (g.has_unfilled_up && g.filled_count > 0) {
      return chip('缺口回补' + g.filled_count + '次', 'chip-warn');
    }
    if (g.filled_count > 0) {
      return chip('缺口回补' + g.filled_count + '次', 'chip-warn');
    }
    return chip('无明显缺口', 'chip-mute');
  }

  function boxBadge(b){
    if (!b) return '';
    var pos = b.pos != null ? b.pos : 0.5;
    var posTxt = (pos*100).toFixed(0) + '%';
    var cls = pos > 0.75 ? 'chip-good' : (pos < 0.25 ? 'chip-warn' : 'chip-mute');
    return chip('箱体 ' + posTxt + ' [' + b.low + '~' + b.high + '] [' + b.width_pct + '%]', cls);
  }

  function volBadge(v){
    if (!v) return '';
    var parts = [];
    if (v.amount_yi != null) parts.push(chip('成交 ' + v.amount_yi.toFixed(2) + '亿', 'chip-info'));
    if (v.vol_ratio != null) parts.push(chip('量比 ' + v.vol_ratio.toFixed(2), v.vol_ratio > 2 ? 'chip-good' : 'chip-mute'));
    if (v.turnover_pct != null) parts.push(chip('换手 ' + v.turnover_pct.toFixed(2) + '%', v.turnover_pct > 5 ? 'chip-good' : 'chip-mute'));
    if (v.amplitude != null) parts.push(chip('振幅 ' + v.amplitude.toFixed(2) + '%', v.amplitude > 5 ? 'chip-info' : 'chip-mute'));
    if (v.change_pct != null) { var cp = Number(v.change_pct); parts.push(chip('今收 ' + (cp >= 0 ? '+' : '') + cp.toFixed(2) + '%', cp >= 0 ? 'chip-cang' : 'chip-good')); }
    return parts.join('');
  }

  function sectorChips(s){
    if (!s) return '';
    var parts = [];
    if (s.l1 && s.l1 !== '—') parts.push(chip('L1 ' + s.l1, 'chip-info'));
    if (s.l2 && s.l2 !== '—') parts.push(chip('L2 ' + s.l2, 'chip-info'));
    if (s.l3 && s.l3 !== '—') parts.push(chip('L3 ' + s.l3, 'chip-info'));
    if (s.l4 && s.l4 !== '—') parts.push(chip(s.l4, 'chip-mute'));
    if (s.role && s.role !== '—') parts.push(chip(s.role, 'chip-good'));
    return parts.join('');
  }

  function dragonBadge(d){
    if (!d) return '';
    var net = d.net_yi || 0;
    var cls = net > 0 ? 'chip-good' : (net < 0 ? 'chip-warn' : 'chip-mute');
    var netChip = chip('主力净流入 ' + (net >= 0 ? '+' : '') + net.toFixed(2) + '亿', cls);
    var seats = d.seat_summary ? chip(d.seat_summary, 'chip-info') : '';
    var risk = d.risk_flag ? chip('风险:' + d.risk_flag, 'chip-warn') : '';
    return [netChip, seats, risk].filter(Boolean).join('');
  }

  // R20: 把当前 dexin 候选的 code→phase 映射广播给其他 view
  // (全 A 行的 .dxin-row-badge 显示真实阶段颜色, 而不是统一"验")
  function _broadcastDexinMap(){
    if (!_data || !_data.stages) return;
    var map = {};
    var PHASE_TAB = {
      cang_zha: 'cang_zha',
      xu_sha:   'xu_sha',
      clearing: 'clearing',
      de_xin:   'de_xin',
      xu_sha_dangerous: 'xu_sha',
    };
    Object.keys(_data.stages).forEach(function(stage){
      var arr = _data.stages[stage] || [];
      arr.forEach(function(stk){
        if (!stk || !stk.code) return;
        var cur = map[stk.code];
        var curPri = cur ? _phasePriority(cur.phase) : -1;
        var newPri = _phasePriority(PHASE_TAB[stage] || stage);
        if (newPri > curPri) {
          map[stk.code] = {
            phase: PHASE_TAB[stage] || stage,
            variant: stk.variant || 'benign',
            name: stk.name || '',
            cycle: (stk.phase_dates && stk.phase_dates.cycle_days) || 0,
          };
        }
      });
    });
    window.__dexinPhaseMap = map;
    document.dispatchEvent(new CustomEvent('dexin-loaded', { detail: { map: map } }));
  }
  function _phasePriority(p){
    return ({ de_xin: 4, clearing: 3, xu_sha: 2, cang_zha: 1 })[p] || 0;
  }

  // ── 卡片渲染 ──
  function renderCard(stk, idx){
    var quote = stk.quote || '';
    var advice = stk.advice || '';
    var variantCls = stk.variant === 'dangerous' ? ' dx-card-danger' : (stk.variant === 'benign' ? ' dx-card-benign' : '');
    var quoteHtml =
      '<div class="dx-quote">' +
        '<div class="dx-quote-label">原话溯源</div>' +
        '<div class="dx-quote-text">' + esc(quote) + '</div>' +
      '</div>';
    return [
      '<article class="dx-card' + variantCls + '">',
        '<header class="card-head dx-card-head">',
          '<div class="dx-card-rank">#' + (idx+1) + '</div>',
          '<div class="dx-card-title">',
            '<a class="stock-link dx-code" data-code="' + esc(stk.code) + '">' + esc(stk.code) + '</a>',
            '<span class="dx-name">' + esc(stk.name||stk.code) + '</span>',
          '</div>',
          stageBadge(stk.stage, stk.variant),
          '<button class="wl-toggle-btn dx-wl-btn" data-wl-code="' + esc(stk.code) + '" data-wl-name="' + esc(stk.name||stk.code) + '" title="加入自选">⭐</button>',
        '</header>',
        phaseDatesChips(stk.phase_dates || {}),
        quoteHtml,
        '<div class="dx-chips dx-chips-sector">' + sectorChips(stk.sector) + '</div>',
        '<div class="dx-chips dx-chips-vol">' + volBadge(stk.volume) + boxBadge(stk.box) + gapBadge(stk.gap) + '</div>',
        '<div class="dx-chips dx-chips-dragon">' + dragonBadge(stk.dragon) + '</div>',
        '<div class="dx-advice">',
          '<div class="dx-advice-label">操作建议</div>',
          '<div class="dx-advice-text">' + esc(advice) + '</div>',
        '</div>',
      '</article>',
    ].join('');
  }

  // ── 页面渲染 ──
  function renderActiveTab(){
    if (!_data) {
      $mount.innerHTML = '<div class="dx-loading">加载中…</div>';
      return;
    }
    // 同步 tabs.active 标记 (HTML 默认 cang_zha active, JS 默认 _activeTab=de_xin,
    // 不主动同步会导致点击之外的路径(深链/首屏)tab 高亮错位)
    var tabsRoot = document.getElementById('dexin-tabs');
    if (tabsRoot) {
      $$('.dexin-tab', tabsRoot).forEach(function(el){
        el.classList.toggle('active', el.getAttribute('data-tab') === _activeTab);
      });
    }

    var stages = _data.stages || {};
    var list = stages[_activeTab] || [];
    var t = TABS[_activeTab] || TABS.cang_zha;
    var title = t.label;
    var total = _data.classified_total || 0;
    var regime = _data.regime || '—';
    var regimeQuote = _data.regime_quote || '';

    var meta = document.getElementById('dexin-meta');
    if (meta) {
      meta.innerHTML = '候选 <b>' + (_data.candidate_total||0) + '</b> · 分类 <b>' + total + '</b> · 行情 <b>' + esc(regime) + '</b>';
    }

    // 折叠面板内容: 纲领四句 + disclaimer (无论桌面/移动端都填, 由 CSS 控显隐)
    var creedList = document.getElementById('dexin-creed-list');
    if (creedList) {
      var laws = (_data.laws && _data.laws.creed) || [];
      creedList.innerHTML = laws.length
        ? laws.map(function(s){ return '<div class="dx-creed-item">' + esc(s) + '</div>'; }).join('')
        : '';
    }
    var dis = document.getElementById('dexin-disclaimer');
    if (dis) {
      dis.textContent = _data.disclaimer || '';
    }

    var listHtml = list.length
      ? list.map(function(s, i){ return renderCard(s, i); }).join('')
      : '<div class="dx-empty">该阶段无符合原话条件的标的（' + esc(regimeQuote) + '）</div>';

    $mount.innerHTML = [
      '<div class="dx-section-head">',
        '<div class="card-head dx-section-title">',
          '<span class="dx-section-dot" style="background:var(' + t.accent + ')"></span>',
          esc(title),
          '<span class="dx-section-count">(' + list.length + ' / 10)</span>',
        '</div>',
        '<div class="dx-section-regime">',
          chip('当前行情', 'chip-info'),
          '<span class="dx-section-quote">' + esc(regimeQuote) + '</span>',
        '</div>',
      '</div>',
      '<div class="dx-card-grid">' + listHtml + '</div>',
    ].join('');
    // R1000-B1: 刷新自选按钮状态
    $$('.wl-toggle-btn', $mount).forEach(function (btn) { window.wlRefreshBtn(btn); });
  }

  // ── 拉数据 ──
  async function loadScreen(refresh){
    if (_loading) return;
    _loading = true;
    var myReq = ++_reqId;
    var btn = document.getElementById('dexin-refresh');
    if (btn) btn.disabled = true;
    var metaEl = document.getElementById('dexin-meta');
    if (metaEl && (metaEl.textContent || '').trim() === '—') metaEl.textContent = '加载得鑫四阶段数据…';
    $mount.innerHTML = '<div class="dx-loading">加载得鑫四阶段数据…</div>';
    var _t0 = Date.now();
    var _tickId = setInterval(function(){
      var el = document.querySelector('.dx-loading');
      if (!el) return;
      var s = ((Date.now() - _t0) / 1000).toFixed(1);
      el.textContent = '加载得鑫四阶段数据… ' + s + 's';
    }, 200);
    try {
      var url = '/api/dexin/screen' + (refresh ? '?refresh=true' : '');
      var d = await api(url, { timeout: 60000 });
      if (myReq !== _reqId) return;                 // 过期
      _data = d || {};
      window.__dexinDirtyFromWatchlist = false;
      var hint = document.getElementById('dexin-wl-dirty-hint');
      if (hint) hint.hidden = true;
      renderActiveTab();
      // R20: 派发 dexin-loaded 事件, 全 A / dash / 自选页面可订阅后给行内 badge 上色
      _broadcastDexinMap();
    } catch (e) {
      if (myReq !== _reqId) return;
      $mount.innerHTML = '<div class="dx-error">加载失败: ' + esc(e.message||String(e)) + '</div>';
    } finally {
      clearInterval(_tickId);
      _loading = false;
      if (btn) btn.disabled = false;
    }
  }

  // ── 事件绑定 ──
  function bindEvents(){
    var tabsRoot = document.getElementById('dexin-tabs');
    if (tabsRoot) {
      tabsRoot.addEventListener('click', function(e){
        var t = e.target.closest('.dexin-tab');
        if (!t) return;
        var tab = t.getAttribute('data-tab');
        if (!tab || tab === _activeTab) return;
        $$('.dexin-tab', tabsRoot).forEach(function(el){ el.classList.toggle('active', el === t); });
        _activeTab = tab;
        renderActiveTab();
      });
    }
    var btn = document.getElementById('dexin-refresh');
    if (btn) btn.addEventListener('click', function(){ loadScreen(true); });
    // R1000-B1: 自选按钮 — 事件委托 (卡片 innerHTML 渲染)
    $mount.addEventListener('click', function (e) {
      var wlBtn = e.target.closest('.wl-toggle-btn');
      if (!wlBtn) return;
      e.stopPropagation();
      e.preventDefault();
      var code = wlBtn.dataset.wlCode;
      var name = wlBtn.dataset.wlName;
      window.wlToggle(code, name).then(function () {
        window.wlRefreshBtn(wlBtn);
      });
    });

    // 心法常驻显示 (桌面 + 移动端都直接展开, 无折叠按钮)

    // 监听 view-enter — 第一次进入才拉数据
    document.addEventListener('view-enter', function(ev){
      if (ev && ev.detail && ev.detail.name === 'dexin' && !_data) {
        loadScreen(false);
      }
    });
  }

  // ── 初始化 ──
  bindEvents();
  // R20: 自选变化 → 如果用户当前在 dexin 视图, 标记需要刷新 (懒刷新 — 不强制立即触发)
  document.addEventListener('watchlist-changed', function(ev){
    var view = document.querySelector('.view-dexin');
    if (view && !view.hidden) {
      // 当前正在 dexin 视图: 标个 dirty, 等用户主动点 refresh 再 reload (避免静默 refetch 打断浏览)
      window.__dexinDirtyFromWatchlist = true;
      var hint = document.getElementById('dexin-wl-dirty-hint');
      if (hint) hint.hidden = false;
    }
  });
  // 如果页面初始就是 dexin view (deep link), 立即拉
  var view = document.querySelector('.view-dexin');
  if (view && !view.hidden && !_data) {
    loadScreen(false);
  }
})();
