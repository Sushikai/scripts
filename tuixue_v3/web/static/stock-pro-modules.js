// =============================================================
// stock-pro-modules.js  (2026-08-08 · R-pro-stock v1)
// 个股页专业终端增量模块 — 不破坏现有 view-stock.js 5000+ 行单体
//
// 增量内容:
//   A. 相关个股 (related tab) 真正接通 /api/stock/{code}/related_stocks
//   B. 分时叠加昨收/昨日曲线 (overlay) — 不是替换
//   C. K 线指标 chip 切换 + 数据缺失提示
//   D. 资金流向 5/20/60 日汇总增强
//   E. 砸盘风险 + AI 铁律卡轻量专业版 (summary)
//   F. 移动端 viewport 自适应 hook (layout < 480px 切单列决策卡)
//   G. 通用 escapeHtml / num helpers (从 view-stock.js 复用,缺则自备)
//
// 设计原则:
//   - 全部挂在 window.__tx3StockPro,避免污染 view-stock.js 全局
//   - 不引入新依赖,不重新画整张图,只在现有 DOM 上做轻量增强
//   - 每个增强都有降级:数据缺失不报红,显示明确原因
//   - 与现有 _stockAuxCache 兼容:related_news/news/sectors 也共享
// =============================================================

(function () {
  'use strict';

  // —— 共享常量 (从 view-stock.js 同步) ————————————————
  const UP = '#d33b3b';      // 涨 红
  const DOWN = '#1a8754';    // 跌 绿
  const INK = '#1a1a1a';
  const INK2 = '#6a6a6a';
  const INK3 = '#9a9a9a';

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.from((r || document).querySelectorAll(s)); }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function fmtPct(p) {
    if (p == null || p === '' || (typeof p === 'number' && isNaN(p))) return '—';
    const n = +p;
    if (!isFinite(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(2) + '%';
  }
  function fmtPrice(p) {
    if (p == null || p === '' || (typeof p === 'number' && isNaN(p))) return '—';
    const n = +p;
    if (!isFinite(n)) return '—';
    return n.toFixed(2);
  }
  function fmtAmt(w) {
    // 万 → 自适应 万/亿
    if (w == null || !isFinite(+w)) return '—';
    const n = +w;
    if (Math.abs(n) >= 10000) return (n / 10000).toFixed(2) + ' 亿';
    return n.toFixed(0) + ' 万';
  }
  function colorForPct(p) {
    if (p == null || !isFinite(+p)) return INK2;
    const n = +p;
    if (n > 0) return UP;
    if (n < 0) return DOWN;
    return INK2;
  }
  // —— 通用 fetch helper (复用 view-stock.js 的 api,缺则自实现) ——
  async function fetchApi(path) {
    if (typeof window.api === 'function') {
      try {
        const r = await window.api(path);
        if (window.console && console.debug) console.debug('[stock-pro] window.api ok:', path.slice(0, 50), '→', typeof r, r && Object.keys(r || {}).join(','));
        // api() 可能返 unwrapped data,统一包 envelope
        if (r && typeof r.ok === 'undefined') return { ok: true, data: r };
        return r;
      } catch (e) {
        if (window.console && console.debug) console.debug('[stock-pro] window.api 失败,回退 fetch:', e.message);
      }
    }
    try {
      const r = await fetch(path, { headers: { Accept: 'application/json' } });
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };
      const j = await r.json();
      return j && typeof j.ok !== 'undefined' ? j : { ok: true, data: j };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  // ===========================================================
  // P1 数据原子化 (R4001-R4100): 模块注册表 + 优先级调度器
  // ===========================================================
  // 第一性原理: renderStockDetail 把 11 个数据模块全绑在一个函数里,
  // 相互耦合、无独立缓存、无失败降级、无加载优先级。
  // 原子化后: 每模块 = 独立 key + priority + loader + render + cache + degrade。
  // ===========================================================
  const _MODULES = {};
  const _MODULE_STATE = {};   // key → {status:'idle'|'loading'|'ready'|'failed', ts, err}

  // 模块声明 (按优先级: hero > kline > flow > seats > crash > ai …)
  // priority 越小越先调度 (数字小 = 高优先)
  function _defModule(key, opts) {
    _MODULES[key] = Object.assign({
      priority: 100,
      deps: [],          // 依赖的其他模块 key,先加载完
      visibleOnly: false, // true 时只在对应 tab 可见才加载 (tab-gated)
      ttl: 60_000,        // 数据新鲜窗口
      silentFail: true,   // 失败静默 (模块级降级,不 toast)
      // R4301-R4400 (P4 决策原子化): 模块决策接口
      // decide(code, data) → { state:'act'|'wait'|'avoid', reason:str, evidence:any }
      //   - 'act'    : 绿灯, 可买
      //   - 'wait'   : 黄灯, 观望
      //   - 'avoid'  : 红灯, 回避
      //   - reason   : 一句话给用户看的理由
      //   - evidence : 1 个支撑数据点 (e.g. {riskScore: 0.32, verdict: 'ok'})
      decide: null,       // 缺省 = 不参与决策
      weight: 0,          // 决策权重 (sum 权重后取 max)
      // R4501-R4600 (P6 智能原子化): 模块自然语言摘要 (供 ChatBot / 工具拼装)
      // insight(code, data) → string  一个人类可读短句 (≤ 30 字)
      insight: null,
      // R4501-R4600: 智能触发钩子 — 哪些 SSE 事件能让该模块 reload
      // list: e.g. ['crash_patch', 'flow_patch'] 表示 patch 到来时本模块
      //     自动 mark stale + 1s 后 reload (避免风暴)
      smartTriggers: [],
      // R4501-R4600: 依赖的其他模块 decided-state 变化时自动重算
      // list: ['crash', 'seats_breakdown'] 他们的 decide state 变了, 本模块
      //     触发 `_renderDecisionHero` (聚合器随手刷)
      smartChain: [],
    }, opts);
    _MODULE_STATE[key] = { status: 'idle', ts: 0, err: null };
  }

  // 从 /full 预取缓存取数据 (零请求);缺失才 fallback 单端点
  // _stockAuxCache 由 view-stock.js 的 renderStockDetail 填充 (/full 数据)
  function _aux(key) {
    return (_stockAuxCache && _stockAuxCache.code) ? (_stockAuxCache[key] || null) : null;
  }

  // ===========================================================
  // P2 渲染原子化 (R4101-R4200): 模块骨架 + 加载状态 + 失败降级
  // ===========================================================
  // 第一性原理: 600 行巨型 renderStockDetail 把 11 个模块渲染混在一起,
  // 用户看不到"哪个模块加载到哪一步"。原子化后: 每个模块有独立
  // (skeleton) → (loading) → (ready) → (failed) 的视觉状态机,
  // 失败时给出明确原因,不污染整页。
  // ===========================================================

  // 模块 → 容器选择器 (来自 index.html 中各 article / section 容器)
  const _MODULE_CONTAINER = {
    profile:        '#stock-profile-body',
    crash:          '#crash-panel',
    crash_extras:   '#crash-extra',
    seats_breakdown:'#seat-breakdown',
    seats_related:  '#view-stock .seats-related-host',
    seal_ratio:     '#q-seats[data-module-seal]',
    streak_panel:   '#q-streak-host',
    ai_analysis:    '#ai-panel',
    ai_history:     '#ai-history-bar',
    deep_analysis:  '#stock-deep-analy-card',
    my_trades:      '#stock-mytrades-list',
    intraday:       '#intra-day-note',
  };

  // R4101: 骨架占位 — 模块加载时显示 shimmer, 准备好后清掉
  function _skeleton(key, lines) {
    const sel = _MODULE_CONTAINER[key];
    if (!sel) return;
    const el = $(sel);
    if (!el) return;
    const n = lines || 3;
    const html = Array.from({ length: n }).map(() =>
      '<div class="mod-skel-line" style="height:14px;background:linear-gradient(90deg,var(--bg-1) 25%,var(--line) 50%,var(--bg-1) 75%);background-size:200% 100%;animation:mod-skel 1.4s infinite;border-radius:3px;margin:6px 0"></div>'
    ).join('');
    _ensureSkeletonStyle();
    el.dataset.modState = 'loading';
    el.dataset.modKey = key;
    el.insertAdjacentHTML('afterbegin', `<div class="mod-skel mod-skel-${key}">${html}</div>`);
  }

  function _clearSkeleton(key) {
    const sel = _MODULE_CONTAINER[key];
    if (!sel) return;
    const el = $(sel);
    if (!el) return;
    const sk = el.querySelector('.mod-skel-' + key);
    if (sk) sk.remove();
    el.dataset.modState = 'ready';
  }

  // R4102: 失败降级 — 模块失败时显示可读的小 banner, 不影响其他模块
  function _renderFailed(key, err) {
    const sel = _MODULE_CONTAINER[key];
    if (!sel) return;
    const el = $(sel);
    if (!el) return;
    _clearSkeleton(key);
    el.dataset.modState = 'failed';
    const msg = (err || '加载失败').toString().slice(0, 60);
    el.insertAdjacentHTML('afterbegin', `<div class="mod-failed" data-mod-failed="${key}" style="font-size:11px;color:var(--ink-3);padding:6px 10px;border:1px dashed var(--line);border-radius:4px;margin:4px 0;background:var(--bg-1)">⚠ ${key} — ${escapeHtml(msg)}</div>`);
  }

  // R4103: 容器存在性预检 — 切股时清掉所有旧 mod-failed
  function _resetModuleContainers() {
    $$('.mod-failed').forEach(el => el.remove());
    $$('[data-mod-state]').forEach(el => { el.dataset.modState = ''; });
  }

  // R4104: 注入骨架闪烁 CSS (一次性)
  let _skelInjected = false;
  function _ensureSkeletonStyle() {
    if (_skelInjected) return;
    _skelInjected = true;
    const css = `@keyframes mod-skel{0%{background-position:200% 0}100%{background-position:-200% 0}}`;
    let s = document.getElementById('mod-skel-style');
    if (!s) {
      s = document.createElement('style');
      s.id = 'mod-skel-style';
      s.textContent = css;
      document.head.appendChild(s);
    }
  }

  // R4105: 调度器接入 — 加载前显示 skeleton, 成功清掉, 失败显示降级 banner
  function _decorateModuleEvent(detail) {
    const key = detail.key;
    const st = _MODULE_STATE[key];
    if (!st) return;
    if (st.status === 'ready') { _clearSkeleton(key); }
    else if (st.status === 'failed') { _renderFailed(key, st.err); }
  }

  _defModule('profile', {
    priority: 0, ttl: 3600_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => {
      const cached = _aux('profile');
      if (cached) return Promise.resolve({ ok: true, data: cached, from: 'full' });
      return fetchApi(`/api/stock/${code}/profile`).then(r =>
        r && r.ok ? { ok: true, data: r.data, from: 'api' } : { ok: false, err: 'profile 不可达' });
    },
    render: (code, d) => { if (typeof renderProfile === 'function') renderProfile(d.data); },
    decide: (code, data) => {
      // profile: 行业 + 主营 + 行业景气度
      if (!data) return { state: 'wait', reason: '画像缺失', evidence: null };
      const tag = data.ai_tag || data.tags || {};
      const isMain = tag.is_main_field === true;
      const sector = data.sector || data.industry || '';
      if (isMain) return { state: 'act', reason: '主战场' + (sector ? ' · ' + sector : ''), evidence: { is_main_field: true, sector } };
      return { state: 'wait', reason: '非主战场' + (sector ? ' · ' + sector : ' · 杂毛'), evidence: { is_main_field: false, sector } };
    },
    weight: 30,
  });

  _defModule('crash', {
    priority: 10, ttl: 120_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => {
      const cached = _aux('ai_status');
      if (cached) return Promise.resolve({ ok: true, data: cached, from: 'full' });
      return (window.AI
        ? window.AI.stockCrashRisk(code).then(d => ({ ok: true, data: d }))
        : fetchApi(`/api/stock/${code}/ai_crash_risk`).then(r =>
            r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'crash 不可达' }));
    },
    render: (code, d) => { if (typeof renderCrashData === 'function') renderCrashData(d.data); },
    decide: (code, data) => {
      // crash: 风险评分 0-1, <0.3 绿灯, 0.3-0.6 观望, >0.6 回避
      if (!data) return { state: 'wait', reason: '风控数据缺失', evidence: null };
      const score = data.risk_score ?? data.risk ?? 0.5;
      const rounded = Math.round(score * 100) / 100;
      if (score < 0.3) return { state: 'act', reason: '风控低分 · ' + rounded, evidence: { risk_score: score } };
      if (score < 0.6) return { state: 'wait', reason: '风控中等 · ' + rounded, evidence: { risk_score: score } };
      return { state: 'avoid', reason: '风控高分 · ' + rounded, evidence: { risk_score: score } };
    },
    weight: 40,
    insight: (code, data) => {
      if (!data) return '风控数据未到';
      const s = data.risk_score ?? data.risk ?? 0.5;
      return '风控评分 ' + Math.round(s * 100) + '/100';
    },
    smartTriggers: ['crash_patch', 'flow_patch'],
  });

  _defModule('crash_extras', {
    priority: 11, ttl: 180_000, visibleOnly: false,
    deps: ['crash'], silentFail: true,
    loader: (code) => fetchApi(`/api/stock/${code}/crash_extras`).then(r =>
      r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'crash_extras 不可达' }),
    render: (code, d) => { if (typeof renderCrashExtra === 'function') renderCrashExtra(d.data); },
  });

  _defModule('seats_breakdown', {
    priority: 15, ttl: 180_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => {
      const cached = _aux('seat_breakdown');
      if (cached) return Promise.resolve({ ok: true, data: cached, from: 'full' });
      return fetchApi(`/api/stock/${code}/seat_breakdown`).then(r =>
        r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'seat_breakdown 不可达' });
    },
    render: (code, d) => { if (typeof renderSeatBreakdown === 'function') renderSeatBreakdown(d.data); },
    decide: (code, data) => {
      // seats_breakdown: 拉萨天团比例 / 主力净买入
      if (!data) return { state: 'wait', reason: '席位缺失', evidence: null };
      const lasa = data.lasa_ratio ?? data.lasa_share ?? 0;
      const mainNet = data.main_net_inflow ?? data.main_net ?? 0;
      if (lasa > 0.5) return { state: 'avoid', reason: '拉萨天团主导 ' + Math.round(lasa * 100) + '%', evidence: { lasa_ratio: lasa, main_net: mainNet } };
      if (mainNet > 0) return { state: 'act', reason: '主力净买入 ' + (mainNet / 1e8).toFixed(2) + '亿', evidence: { main_net: mainNet, lasa_ratio: lasa } };
      return { state: 'wait', reason: '主力观望', evidence: { main_net: mainNet, lasa_ratio: lasa } };
    },
    weight: 30,
    insight: (code, data) => {
      if (!data) return '席位未到';
      const lasa = data.lasa_ratio ?? data.lasa_share ?? 0;
      const mainNet = data.main_net_inflow ?? data.main_net ?? 0;
      const parts = [];
      if (lasa > 0) parts.push('拉萨' + Math.round(lasa * 100) + '%');
      if (mainNet) parts.push('主力净买' + (mainNet / 1e8).toFixed(2) + '亿');
      return parts.join(' · ') || '席位均值';
    },
    smartTriggers: ['seats_patch', 'flow_patch'],
  });

  _defModule('seats_related', {
    priority: 16, ttl: 600_000, visibleOnly: true,
    deps: [], silentFail: true,
    loader: (code) => fetchApi(`/api/stock/${code}/seat_related`).then(r =>
      r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'seat_related 不可达' }),
    render: (code, d) => { if (typeof renderSeatsRelated === 'function') renderSeatsRelated(d.data); },
  });

  _defModule('seal_ratio', {
    priority: 20, ttl: 120_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => fetchApi(`/api/stock/${code}/intraday_5d`).then(r =>
      r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'intraday_5d 不可达' }),
    render: (code, d) => { if (typeof _renderSealRatio === 'function') _renderSealRatio(d.data); },
  });

  _defModule('streak_panel', {
    priority: 25, ttl: 120_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => Promise.resolve({ ok: true, data: null, from: 'inline' }),
    render: (code, d) => { if (typeof _loadStockStreakPanel === 'function') _loadStockStreakPanel(code, d.data); },
  });

  _defModule('ai_analysis', {
    priority: 30, ttl: 300_000, visibleOnly: true,
    deps: [], silentFail: true,
    loader: (code) => (window.AI
      ? window.AI.stockAnalysis(code).then(d => ({ ok: true, data: d }))
      : fetchApi(`/api/stock/${code}/ai_analysis`).then(r =>
          r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'ai_analysis 不可达' })),
    render: (code, d) => { if (typeof loadAIAnalysis === 'function') loadAIAnalysis(code).catch(() => {}); },
    decide: (code, data) => {
      // ai_analysis: M3 verdict '买/观望/回避' + conviction 0-100
      if (!data) return { state: 'wait', reason: 'AI 判定未到', evidence: null };
      const verdict = data.verdict || data.summary_verdict || '';
      const conviction = data.conviction ?? data.score ?? 0;
      if (verdict.includes('买')) return { state: 'act', reason: 'AI 看好 · ' + conviction, evidence: { verdict, conviction } };
      if (verdict.includes('回避')) return { state: 'avoid', reason: 'AI 回避 · ' + conviction, evidence: { verdict, conviction } };
      return { state: 'wait', reason: 'AI 观望 · ' + conviction, evidence: { verdict, conviction } };
    },
    weight: 50,
    insight: (code, data) => {
      if (!data) return 'AI 判定未到';
      const v = data.verdict || data.summary_verdict || '观望';
      const c = data.conviction ?? data.score ?? 0;
      return 'AI ' + v + '·' + c;
    },
    smartTriggers: ['ai_ready', 'crash_patch'],
  });

  _defModule('ai_history', {
    priority: 31, ttl: 300_000, visibleOnly: true,
    deps: ['ai_analysis'], silentFail: true,
    loader: (code) => (window.AI
      ? window.AI.stockHistory(code, { days: 14 }).then(d => ({ ok: true, data: d }))
      : fetchApi(`/api/stock/${code}/ai_history?days=14`).then(r =>
          r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'ai_history 不可达' })),
    render: (code, d) => { if (typeof loadAIHistory === 'function') loadAIHistory(code).catch(() => {}); },
  });

  _defModule('deep_analysis', {
    priority: 40, ttl: 600_000, visibleOnly: false,
    deps: [], silentFail: true,
    // deep_analysis 是后台任务端点 (?background=1 → 1s 返 queued), 前台由内部轮询完成
    loader: (code) => fetchApi(`/api/stock/${code}/deep_analysis?background=1`).then(r =>
      r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'deep_analysis 不可达' }),
    render: (code, d) => { if (typeof loadStockDeepAnalysis === 'function') loadStockDeepAnalysis(code).catch(() => {}); },
  });

  _defModule('my_trades', {
    priority: 45, ttl: 600_000, visibleOnly: false,
    deps: [], silentFail: true,
    loader: (code) => fetchApi(`/api/review/trades?code=${code}&since_days=720`).then(r =>
      r && r.ok ? { ok: true, data: r.data } : { ok: false, err: 'mytrades 不可达' }),
    render: (code, d) => { if (typeof loadStockMyTrades === 'function') loadStockMyTrades(code).catch(() => {}); },
  });

  _defModule('intraday', {
    priority: 50, ttl: 120_000, visibleOnly: true,
    deps: [], silentFail: true,
    loader: (code) => {
      const cached = _aux('intraday');
      if (cached) return Promise.resolve({ ok: true, data: cached, from: 'full', date: todayStr() });
      const today = todayStr();
      return fetchApi(`/api/stock/${code}/intraday?date=${today}`).then(r =>
        r && r.ok ? { ok: true, data: r.data, date: today } : { ok: false, err: 'intraday 不可达' });
    },
    render: (code, d) => { if (typeof loadIntraDay === 'function') loadIntraDay(code, d.date); },
  });

  // 调度器: 并发上限 2 (防压垮上游), 按 priority 排序, 依赖先装, 可见性 gating
  // R4002: max 从 3 收紧到 2 — 上游数据源单 worker 只吃得下少量并发, 超了就 503/假死
  const _SCHED = { running: 0, queue: [], max: 2, inflight: new Set(), done: {} };

  function _loadModule(key, code, force) {
    const t0 = performance.now();
    const m = _MODULES[key];
    if (!m) return Promise.resolve(false);
    const st = _MODULE_STATE[key];
    // 新鲜缓存命中 (且非 force)
    if (!force && st.status === 'ready' && Date.now() - st.ts < m.ttl) return Promise.resolve(true);
    // 已 inflight → 复用
    if (_SCHED.inflight.has(key)) return Promise.resolve(false);
    return new Promise((resolve) => {
      _SCHED.queue.push({ key, code, resolve, force, t0 });
      _drainQueue();
    });
  }

  function _drainQueue() {
    if (_SCHED.running >= _SCHED.max) return;
    // 取最高优先级 (priority 数字最小) 且依赖已就绪的项
    const ready = _SCHED.queue
      .filter(q => _depsReady(q.key))
      .sort((a, b) => (_MODULES[a.key].priority - _MODULES[b.key].priority) || 0);
    if (!ready.length) return;
    const next = ready[0];
    _SCHED.queue.splice(_SCHED.queue.indexOf(next), 1);
    _SCHED.running++;
    _SCHED.inflight.add(next.key);
    // R4601-R4700: 切股时 abort, 即便已 inflight 也不继续
    if (_acRef && _acRef.signal.aborted) {
      _SCHED.running--; _SCHED.inflight.delete(next.key);
      next.resolve(false);
      setTimeout(_drainQueue, 0);
      return;
    }
    _MODULE_STATE[next.key].status = 'loading';
    // R4101: 加载前显示骨架
    _skeleton(next.key, 2);
    const m = _MODULES[next.key];
    Promise.resolve()
      .then(() => m.loader(next.code))
      .then((res) => {
        _SCHED.running--; _SCHED.inflight.delete(next.key);
        if (res && res.ok) {
          _MODULE_STATE[next.key] = { status: 'ready', ts: Date.now(), err: null };
          const rT0 = performance.now();
          try { m.render(next.code, res); } catch (e) { console.debug('[mod:' + next.key + '] render fail:', e.message); }
          // R4601-R4700 (P7 极限原子化): perf 埋点
          _perfRecord('loads', next.key, performance.now() - next.t0);
          _perfRecord('renders', next.key, performance.now() - rT0);
          // R4201-R4300: 加载完成时应用 pending patch (避免 SSE 抢在 ready 前到的 patch 丢失)
          _flushPendingPatch(next.key);
          // R4401-R4500: 模块 ready 后刷新 hero pill (聚合决策可能变了)
          if (typeof _renderDecisionHero === 'function') _renderDecisionHero();
        } else {
          _MODULE_STATE[next.key] = { status: 'failed', ts: Date.now(), err: (res && res.err) || 'unknown' };
        }
        _decorateModuleEvent({ key: next.key });
        next.resolve(true);
        document.dispatchEvent(new CustomEvent('stock-module-loaded', { detail: { key: next.key, status: _MODULE_STATE[next.key].status } }));
        _drainQueue();
      })
      .catch((e) => {
        _SCHED.running--; _SCHED.inflight.delete(next.key);
        _MODULE_STATE[next.key] = { status: 'failed', ts: Date.now(), err: e.message };
        // R4601-R4700: failed 路径也记录耗时
        _perfRecord('loads', next.key, performance.now() - next.t0);
        _decorateModuleEvent({ key: next.key });
        next.resolve(false);
        document.dispatchEvent(new CustomEvent('stock-module-loaded', { detail: { key: next.key, status: 'failed' } }));
        _drainQueue();
      });
  }

  function _depsReady(key) {
    const m = _MODULES[key];
    return (m.deps || []).every(d => _MODULE_STATE[d] && _MODULE_STATE[d].status === 'ready');
  }

  // 模块可见性 gating: 只在对应 tab 可见时才加载 (节省带宽 + 后端 worker)
  function _ensureVisibleModule(code, key) {
    const m = _MODULES[key];
    if (!m || !m.visibleOnly) return _loadModule(key, code);
    const activeTab = $('.view-stock .chart-tab.active')?.dataset?.tab || '';
    const tabMap = { seats_related: 'related', ai_analysis: 'ai', ai_history: 'ai', intraday: 'intraday' };
    const needTab = tabMap[key] || '';
    if (!needTab || activeTab === needTab) return _loadModule(key, code);
    // 未可见 → 延迟到 tab 激活时加载
    return Promise.resolve(false);
  }

  // R4001 关键: 只加载"真缺"的模块 — full 已聚合的 (profile/crash/seats_breakdown/intraday)
  // 走缓存零请求; crash_extras/seal_ratio/ai/deep/my_trades 才真正打网络。
  function _ensureAllStockModules(code) {
    return Promise.all([
      _loadModule('profile', code),       // full 预取,零请求
      _loadModule('crash', code),         // full ai_status,零请求
      _loadModule('seats_breakdown', code), // full 预取,零请求
      _loadModule('crash_extras', code),  // 独立端点
      _loadModule('seal_ratio', code),    // intraday_5d 端点
      _loadModule('streak_panel', code),  // inline
      _loadModule('deep_analysis', code), // 后台触发
      _loadModule('my_trades', code),     // 独立端点
    ]);
  }

  function _getModuleState(key) {
    return _MODULE_STATE[key] || null;
  }

  // R4201-R4300 (P3 刷新原子化): SSE 模块级 patch 监听
  // 模块已 ready 后收 patch → 在原位 hot-update, 无需重 load 也不走网络
  // 模块 idle/loading/ready 都安全: ready 直接 patch; idle/loading 时 patch 入 _pendingPatch[key]
  // 等下次 _drainQueue 拉到该模块时再 patch (避免覆盖新数据)
  const _pendingPatch = Object.create(null);
  function onModulePatch(modKey, data, ts) {
    if (!modKey || !data) return;
    // R4601-R4700 (P7 极限原子化): 限频, 1s 内最多 6 个 patch
    if (!_perfShouldAcceptPatch()) return;
    _perfMon.patches++;
    const code = window._currentStockCode || window.currentStockCode;
    if (!code) return;
    // modKey → 模块 key 映射 (crash_patch 同时影响 crash + crash_extras)
    const keyMap = {
      crash: ['crash', 'crash_extras'],
      flow: ['seal_ratio'],
      intraday: ['intraday'],
      seats: ['seats_breakdown'],
    };
    const targetKeys = keyMap[modKey] || [];
    for (const k of targetKeys) {
      const s = _MODULE_STATE[k];
      if (!s) continue;
      // ready → 立即 patch (仅更新该模块的子集)
      if (s.status === 'ready' && typeof _renderModulePatch === 'function') {
        _renderModulePatch(k, data, ts);
      } else {
        // idle/loading → 缓存 patch, 防止 load 完成瞬间被旧 patch 覆盖
        _pendingPatch[k] = { data, ts };
      }
    }
    // R4501-R4600 (P6 智能原子化): SSE patch → 智能触发器
    // 哪些模块配了 smartTriggers 含此 modKey+'_patch', 1s 后自动 reload
    smartReload(modKey + '_patch');
  }

  // 收到 ready 后应用 pending patch (在 _drainQueue 完成后调用)
  function _flushPendingPatch(key) {
    const p = _pendingPatch[key];
    if (!p) return;
    delete _pendingPatch[key];
    if (typeof _renderModulePatch === 'function') {
      _renderModulePatch(key, p.data, p.ts);
    }
  }

  // 通用 patch renderer: 仅更新模块自带 DOM 节点的子集,无破坏性
  // 早期模块可能没注册 _renderModulePatch, 提供空实现兜底
  let _renderModulePatch = function (key, data, ts) {
    const _ev = new CustomEvent('tx3:module-patch', { detail: { key, data, ts } });
    window.dispatchEvent(_ev);
    // R4401-R4500 (P5 视觉原子化): 收到 patch 时给容器加 1 次 mod-pulse
    const cont = _MODULE_CONTAINER[key];
    if (cont) {
      cont.classList.remove('mod-pulse');
      // 强制 reflow 重启动画
      void cont.offsetWidth;
      cont.classList.add('mod-pulse');
    }
    // 数据变了 → 决策可能也变,刷新 hero pill
    if (typeof _renderDecisionHero === 'function') {
      _renderDecisionHero();
    }
  };

  // R4401-R4500 (P5 视觉原子化): 决策 hero pill 渲染
  // 容器: .decision-pill[data-deci-state] (HTML 静态存在)
  // 每个状态 1 次写, 减少 DOM 抖动
  function _renderDecisionHero() {
    const code = window._currentStockCode || window.currentStockCode;
    if (!code) return;
    const root = document.querySelector('#decision-hero, .decision-hero');
    if (!root) return;
    const d = decideStock(code);
    if (!d) return;
    root.classList.remove('is-act', 'is-wait', 'is-avoid');
    root.classList.add('is-' + d.state);
    let dot = root.querySelector('.pill-dot');
    if (!dot) { dot = document.createElement('span'); dot.className = 'pill-dot'; root.appendChild(dot); }
    let text = root.querySelector('.decision-label');
    if (!text) { text = document.createElement('span'); text.className = 'decision-label'; root.appendChild(text); }
    let reason = root.querySelector('.decision-reason');
    if (!reason) { reason = document.createElement('span'); reason.className = 'decision-reason'; root.appendChild(reason); }
    const labelMap = { act: '🟢 可买', wait: '🟡 观望', avoid: '🔴 回避' };
    text.textContent = labelMap[d.state] || d.state;
    reason.textContent = d.reason || '';
    // 派 CustomEvent 给上层仪表
    document.dispatchEvent(new CustomEvent('tx3:decision-update', { detail: d }));
  }

  function decideStock(code) {
    const votes = [];
    const score = { act: 0, wait: 0, avoid: 0 };
    for (const key of Object.keys(_MODULES)) {
      const m = _MODULES[key];
      const st = _MODULE_STATE[key];
      if (!m.decide || !st || st.status !== 'ready') continue;
      // _stockAuxCache 里 module 缓存的 data (loader 已经写进去)
      const cache = _stockAuxCache || {};
      const dataHit = (() => {
        // 通用: 尝试 4 个常见缓存键
        const map = {
          profile: 'profile', crash: 'ai_status', crash_extras: 'crash_extras',
          seats_breakdown: 'seat_breakdown', seal_ratio: 'fund_flow',
          ai_analysis: 'ai_status', intraday: 'intraday',
        };
        const ck = map[key] || key;
        return cache[ck] || null;
      })();
      let v;
      try { v = m.decide(code, dataHit); }
      catch (e) { v = { state: 'wait', reason: 'decide 异常', evidence: null }; }
      if (!v || !v.state) continue;
      const w = m.weight || 1;
      // avoid 强权: 3x
      const adj = (v.state === 'avoid') ? w * 3 : w;
      score[v.state] = (score[v.state] || 0) + adj;
      votes.push({ key, state: v.state, reason: v.reason, weight: w, evidence: v.evidence });
    }
    // 最高分胜出
    let state = 'wait';
    let top = score.wait;
    if (score.act > top) { state = 'act'; top = score.act; }
    if (score.avoid > top) { state = 'avoid'; top = score.avoid; }
    // 平分: act > wait > avoid
    if (score.act === top && score.act > 0) state = 'act';
    // 头部理由: 取 weight 最大的 vote
    const topVote = votes.slice().sort((a, b) => (b.weight * (b.state === 'avoid' ? 3 : 1)) - (a.weight * (a.state === 'avoid' ? 3 : 1)))[0];
    return {
      state,
      reason: topVote ? topVote.reason : '数据未到',
      score,
      votes,
      moduleCount: votes.length,
    };
  }

  // R4501-R4600 (P6 智能原子化): 拼装自然语言上下文
  // 用于 ChatBot / 推荐面板 / 卡片 tooltip — 各模块提供 1 句短摘要
  function insightStock(code) {
    const lines = [];
    const cache = _stockAuxCache || {};
    for (const key of Object.keys(_MODULES)) {
      const m = _MODULES[key];
      const st = _MODULE_STATE[key];
      if (!m.insight || !st || st.status !== 'ready') continue;
      const map = {
        profile: 'profile', crash: 'ai_status', crash_extras: 'crash_extras',
        seats_breakdown: 'seat_breakdown', seal_ratio: 'fund_flow',
        ai_analysis: 'ai_status', intraday: 'intraday',
      };
      const ck = map[key] || key;
      const data = cache[ck] || null;
      try {
        const s = m.insight(code, data);
        if (s) lines.push({ key, text: s });
      } catch (e) { /* skip */ }
    }
    return {
      code,
      ts: Date.now(),
      lines,
      decision: decideStock(code),
    };
  }

  // R4501-R4600: 智能触发 — SSE patch 来了之后, 自动 mark stale + 1s 后 reload
  // 保护: 一个 tick 内最多触发 3 个 smartReload, 避免风暴
  const _smartReloadQueue = new Set();
  let _smartReloadFlushTimer = null;
  function smartReload(reason) {
    const code = window._currentStockCode || window.currentStockCode;
    if (!code) return;
    _smartReloadQueue.add(code);
    if (_smartReloadFlushTimer) return;
    _smartReloadFlushTimer = setTimeout(() => {
      const cod = window._currentStockCode || window.currentStockCode;
      _smartReloadFlushTimer = null;
      if (!cod || !_smartReloadQueue.has(cod)) return;
      _smartReloadQueue.delete(cod);
      // 触发受影响的智能模块 reload
      let cnt = 0;
      for (const key of Object.keys(_MODULES)) {
        const m = _MODULES[key];
        if (!m.smartTriggers || !m.smartTriggers.length) continue;
        if (m.smartTriggers.indexOf(reason) === -1) continue;
        if (cnt >= 3) break;  // 节流
        cnt++;
        try { _loadModule(key, cod); } catch (e) { /* silent */ }
      }
    }, 1000);
  }

  // R4601-R4700 (P7 极限原子化): 性能埋点 + 内存淘汰 + 切股 cancel
  // 1) SSE patch 频率上限, 1s 内最多 6 个 patch, 多了 drop
  // 2) 切股时, 所有 inflight loader 走 _acRef.current.abort()
  // 3) 模块 TTL 过期自动 cooldown 内存 (用 lastAccess 戳)
  // 4) 每个模块加载/渲染耗时埋点, _perfMon 可读
  const _perfMon = {
    loads: {},     // key → {n, sumMs, maxMs}
    renders: {},   // key → {n, sumMs, maxMs}
    patches: 0,    // 全局 patch 计数
    patches_dropped: 0,
  };
  const _lastPatchBucket = { ts: 0, n: 0 };
  function _perfRecord(kind, key, ms) {
    const g = _perfMon[kind] || (_perfMon[kind] = {});
    let s = g[key];
    if (!s) { s = g[key] = { n: 0, sumMs: 0, maxMs: 0 }; g[key] = s; }
    s.n++; s.sumMs += ms;
    if (ms > s.maxMs) s.maxMs = ms;
  }
  function _perfShouldAcceptPatch() {
    const now = Date.now();
    if (now - _lastPatchBucket.ts > 1000) { _lastPatchBucket.ts = now; _lastPatchBucket.n = 0; }
    _lastPatchBucket.n++;
    if (_lastPatchBucket.n > 6) { _perfMon.patches_dropped++; return false; }
    return true;
  }

  // 2) AbortController 切股时 cancel
  let _acRef = null;
  function _getAC() {
    if (!_acRef || _acRef.signal.aborted) {
      _acRef = (typeof AbortController !== 'undefined') ? new AbortController() : { signal: { aborted: false } };
    }
    return _acRef;
  }
  function _abortAllLoaders() {
    if (_acRef) {
      try { _acRef.abort(); } catch {}
      _acRef = null;
    }
    // 清空调度队列
    _SCHED.queue.length = 0;
    _SCHED.inflight.clear();
  }

  // 3) 模块 TTL 过期自动 cooldown: stale 状态 → 不再 render
  function _gcStaleModules() {
    const now = Date.now();
    let n = 0;
    for (const key of Object.keys(_MODULE_STATE)) {
      const st = _MODULE_STATE[key];
      const m = _MODULES[key];
      if (!st || !m || st.status !== 'ready') continue;
      const ttl = m.ttl || 60_000;
      if (now - st.ts > ttl * 2) {  // 2× ttl 才 gc (避免误伤)
        _MODULE_STATE[key] = { status: 'idle', ts: 0, err: null };
        n++;
      }
    }
    return n;
  }
  // 30s 跑一次 gc
  setInterval(_gcStaleModules, 30_000);

  // 4) 模块 metrics 汇总 (供 /api/_perf)
  function getMetrics() {
    const ready = Object.keys(_MODULE_STATE).filter(k => _MODULE_STATE[k].status === 'ready').length;
    const failed = Object.keys(_MODULE_STATE).filter(k => _MODULE_STATE[k].status === 'failed').length;
    const loading = Object.keys(_MODULE_STATE).filter(k => _MODULE_STATE[k].status === 'loading').length;
    return {
      ready, failed, loading,
      patches: _perfMon.patches,
      patches_dropped: _perfMon.patches_dropped,
      loads: Object.keys(_perfMon.loads).length,
      pendingPatches: Object.keys(_pendingPatch).length,
    };
  }

  // ===========================================================
  // B. 分时叠加昨收/昨日曲线 (overlay layer)
  // 设计:在现有 intraDayChart 之上叠加一条昨日收盘基准线 + 昨日分时(若有)
  // 触发:分时 tab 激活且 intra-day-chart 已画时,在图上方插入 1-2 个 chip
  // ===========================================================
  const _intraOverlay = { date: null, yclose: null, yesterday: null, yesterdayDate: null };

  async function tryLoadIntraOverlay(code, currentDate) {
    if (!code || !currentDate) return;
    // 不阻塞主图,后台拉一次,失败则放弃 overlay
    try {
      // 1) 拉当前日的 quote → 拿昨收基准
      const env = await fetchApi(`/api/stock/${code}/core`);
      if (!env || !env.ok) return;
      const quote = (env.data && env.data.quote) || env.quote || {};
      _intraOverlay.yclose = +quote.pre_close || +quote.昨收 || null;
      _intraOverlay.date = currentDate;
      // 2) 拉昨日分时 (由 intra-day-pick 上一天的日期推断)
      const yest = _shiftDate(currentDate, -1);
      const env2 = await fetchApi(`/api/stock/${code}/intraday?date=${yest}`);
      if (env2 && env2.ok) {
        const d2 = env2.data || env2;
        _intraOverlay.yesterday = (d2 && d2.points) || (d2 && d2.intraday) || null;
        _intraOverlay.yesterdayDate = yest;
      }
      renderIntraOverlayChips();
    } catch (e) { /* 静默失败,overlay 是增强 */ }
  }

  function _shiftDate(iso, deltaDays) {
    try {
      const d = new Date(iso + 'T00:00:00');
      d.setDate(d.getDate() + deltaDays);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      return `${y}-${m}-${dd}`;
    } catch (e) { return iso; }
  }

  function renderIntraOverlayChips() {
    const note = $('#intra-day-note');
    if (!note) return;
    const bits = [];
    if (_intraOverlay.yclose != null) bits.push(`昨收基准 <b style="color:var(--ink-1)">${_intraOverlay.yclose.toFixed(2)}</b>`);
    if (_intraOverlay.yesterdayDate) bits.push(`昨日 ${_intraOverlay.yesterdayDate} ${_intraOverlay.yesterday ? '已加载' : '暂无数据'}`);
    if (bits.length) {
      note.innerHTML = '<span class="intra-overlay-meta">' + bits.join(' · ') + '</span>';
    }
  }

  // ===========================================================
  // C. K 线指标 chip — 缺数据时降级提示
  // 已有逻辑在 view-stock.js:3161,这里只补强:数据缺失时显示提示
  // ===========================================================
  function probeKlineReadiness() {
    const state = window.klineState;
    if (!state) return { ready: false, reason: 'klineState 尚未初始化' };
    const k = (window._klineData || (state.kline || []));
    const len = Array.isArray(k) ? k.length : 0;
    if (len < 30) return { ready: false, reason: `数据不足 (${len} 根)`, length: len };
    return { ready: true, length: len };
  }

  // ===========================================================
  // D. 资金流向 5/20/60 日汇总
  // 监听 flow tab,绘制完成后插入 chip
  // ===========================================================
  async function renderFundFlowSummary(code, fundData) {
    const host = $('#flow-kpi');
    if (!host || !fundData) return;
    // 不重复插入
    if ($('#flow-summary-extra')) return;
    const summary = fundData.summary || {};
    const keys = ['main_5d', 'main_20d', 'main_60d'];
    const items = keys.map(k => {
      const v = summary[k];
      if (v == null) return null;
      const color = colorForPct(v);
      return `<div class="metric"><span class="m-num" style="color:${color}">${fmtPct(v)}</span><span class="m-unit">${k.replace('main_', '').replace('d', ' 日')}</span></div>`;
    }).filter(Boolean);
    if (!items.length) return;
    const wrap = document.createElement('div');
    wrap.id = 'flow-summary-extra';
    wrap.style.cssText = 'margin-top:.5rem;padding:.4rem .6rem;border:1px dashed var(--line);border-radius:6px;background:var(--bg-1);display:flex;gap:.5rem;flex-wrap:wrap;align-items:center';
    wrap.innerHTML = '<span style="font-size:.7rem;color:var(--ink-2)">主力累计</span>' + items.join('');
    host.appendChild(wrap);
  }

  // ===========================================================
  // E. 移动端 viewport 自适应 (≤ 480px → 单列决策卡 + 大触控 chip)
  // 设计:不替换桌面 DOM,只在移动尺寸下加 class 隐藏次要列
  // ===========================================================
  function applyMobileAdapt() {
    const isMobile = window.matchMedia('(max-width: 480px)').matches;
    document.body.classList.toggle('tx-mobile-stock', isMobile);
    // stock 页内 表格 → 强制横向滚动
    if (isMobile) {
      $$('.related-table').forEach(t => {
        const wrap = t.closest('.table-wrap');
        if (wrap) wrap.style.overflowX = 'auto';
      });
    }
  }
  window.addEventListener('resize', applyMobileAdapt);

  // ===========================================================
  // F. Tab handler 接入 — 复用现有 chart-tab click 流,不重写
  // 当 tab 为 intraday 时调用 tryLoadIntraOverlay
  // 注:新闻/板块/相关个股 三个 tab 已移除(R-pro-stock v1.1 用户要求),本模块仅保留分时叠加
  // ===========================================================
  function hookTabHandlers() {
    // 找 stock view 内所有 chart-tab,挂额外 handler
    $$('.view-stock .chart-tab[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (tab === 'intraday') {
          const code = window.currentStockCode || window._currentStockCode;
          const pick = $('#intra-day-pick');
          const date = pick && pick.value;
          if (code && date) tryLoadIntraOverlay(code, date);
        }
      });
    });
  }

  // ===========================================================
  // G. 初始化入口 — 页面 ready 后挂 hook + 自适应
  // ===========================================================
  function init() {
    hookTabHandlers();
    applyMobileAdapt();
    // 暴露 API
    window.__tx3StockPro = {
      tryLoadIntraOverlay,
      probeKlineReadiness,
      renderFundFlowSummary,
      applyMobileAdapt,
      // R4001: 模块化数据加载
      ensureAllStockModules: _ensureAllStockModules,
      loadModule: _loadModule,
      ensureVisibleModule: _ensureVisibleModule,
      getModuleState: _getModuleState,
      // R4201: SSE per-module patch 入口
      onModulePatch,
      _pendingPatch,
      // R4301: 决策原子化 (12 模块聚合 act/wait/avoid)
      decideStock,
      // R4501: 智能原子化 (自然语言摘要 + 智能触发)
      insightStock,
      smartReload,
      // R4401: 视觉原子化 (decision hero + mod-pulse)
      _renderDecisionHero,
      // R4601: 极限原子化 (perf 埋点 + 切股 cancel + 内存淘汰)
      getMetrics,
      _abortAllLoaders,
      _perfMon,
      _MODULES,
    };
    // R4001: tab 切换时懒加载可见模块 (seats_related / ai_analysis / ai_history / intraday)
    $$('.view-stock .chart-tab[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        const code = window.currentStockCode || window._currentStockCode;
        if (!code) return;
        const map = { seats_related: 'related', ai_analysis: 'ai', ai_history: 'ai', intraday: 'intraday' };
        Object.keys(map).forEach(key => {
          if (map[key] === tab) _loadModule(key, code).catch(() => {});
        });
        if (tab === 'intraday') {
          const pick = $('#intra-day-pick');
          const date = pick && pick.value;
          if (code && date) tryLoadIntraOverlay(code, date);
        }
      });
    });
    // 调试钩子
    if (window.console && console.debug) {
      console.debug('[stock-pro] 模块挂载完成 · P1 数据原子化 R4001 已就绪');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();