/*!
 * ai-client.js — 统一 AI 接口客户端 (R145 / 2026-08-15)
 * 把系统中所有 AI 端点封装成 window.AI 单例。
 * 委托 _fetchWithTimeout (app.js:546) 做 transport — 不重写超时/重试/trace id 逻辑。
 * 委托 _yerenCacheGet/Put/Key 做 in-process LRU (5min, 50 项)。
 * 关键差异: yeren chat 不重试 (LLM stateful 避免双倍扣费) + in-flight dedup
 *           stock analysis polling 自动管 background=1 + 3s×60s 轮询
 *           ai_metrics 跳过信封解包 (该端点返原始 dict)
 * 失败抛 AIError { code, status, traceId, retryAfter, cause } — 永不吞错。
 */
(function () {
  'use strict';
  if (window.AI) {
    console.warn('[AI] window.AI 已被定义,跳过重复加载');
    return;
  }

  // ─────────────────────────────────────────────
  // AIError — 标准化错误
  // ─────────────────────────────────────────────
  class AIError extends Error {
    constructor(msg, opts = {}) {
      super(msg);
      this.name = 'AIError';
      this.code = opts.code || 'UNKNOWN';
      this.status = opts.status != null ? opts.status : null;
      this.traceId = opts.traceId || null;
      this.retryAfter = opts.retryAfter || null;
      this.cause = opts.cause || null;
    }
  }

  // ─────────────────────────────────────────────
  // 工具: 简单 hash (dedup key)
  // ─────────────────────────────────────────────
  function _strHash(s) {
    let h = 0;
    const t = String(s == null ? '' : s);
    for (let i = 0; i < t.length; i++) {
      h = ((h << 5) - h + t.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(36);
  }
  // R159 2026-08-18: 进度键 (UUIDv4-like) — 客户端生成, 服务端用作 Redis key 后缀
  function _uuidv4() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    // 兜底 (老 Safari): 16 字节随机 hex
    const a = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(a);
    else for (let i = 0; i < 16; i++) a[i] = Math.floor(Math.random() * 256);
    a[6] = (a[6] & 0x0f) | 0x40; a[8] = (a[8] & 0x3f) | 0x80;
    const h = Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  }
  function _now() { return Date.now(); }

  // ─────────────────────────────────────────────
  // 工具: SW 缓存失效 — 命中 _API_CACHE 里匹配 regex 的请求
  // ─────────────────────────────────────────────
  async function _invalidateSW(re) {
    try {
      if (!('caches' in window) || typeof re !== 'string') return 0;
      const cacheName = (typeof window !== 'undefined' && window._API_CACHE_NAME) || 'tuixue-api-v1';
      const cache = await caches.open(cacheName);
      const keys = await cache.keys();
      let removed = 0;
      const matcher = new RegExp(re);
      await Promise.all(keys.map(async (req) => {
        try {
          if (matcher.test(req.url)) {
            await cache.delete(req);
            removed++;
          }
        } catch (_) { /* ignore */ }
      }));
      return removed;
    } catch (_) { return 0; }
  }

  // ─────────────────────────────────────────────
  // 工具: 把可能的非 JSON 解析错误 (Pydantic 422 等) 改成 AIError
  // ─────────────────────────────────────────────
  function _makeAIError(resp, j, fallback) {
    const errCode = (j && j.error && (j.error.code || j.error.name)) || fallback || 'SERVER';
    const errMsg = (j && j.error && j.error.message) || (j && j.error) || `HTTP ${resp.status}`;
    const traceId = resp.headers && resp.headers.get ? resp.headers.get('X-Trace-Id') : null;
    return new AIError(errMsg, { code: errCode, status: resp.status, traceId });
  }

  // ─────────────────────────────────────────────
  // AI 单例类
  // ─────────────────────────────────────────────
  class AI {
    constructor(opts = {}) {
      this.opts = Object.assign({
        defaultTimeoutMs: 30000,
        chatTimeoutMs: 100000,
        indexTimeoutMs: 4000,
        lookupTimeoutMs: 5000,
        pollIntervalMs: 3000,
        pollMaxMs: 60000,
        maxRetries: 0,           // 默认不重试, 由 _call 单独控制
        chatMaxRetries: 0,       // LLM 永远不重试
        postMaxRetries: 2,       // 写路径可以接受重试 (POST 幂等)
      }, opts);
      // in-flight dedup map
      this._inflight = new Map();
      // 全局 metrics
      this._metrics = { calls: 0, ok: 0, fail: 0, byEndpoint: {} };
    }

    // ─── 内部: _call(method, path, body, opts)
    // body: { json: object } | null ; opts: { timeoutMs, retry, parseRaw, signal, headers, skipUnwrap }
    async _call(method, path, body, opts = {}) {
      const retry = opts.retry != null ? opts.retry : (method === 'POST' ? this.opts.postMaxRetries > 0 : this.opts.maxRetries > 0);
      const maxRetries = method === 'POST' ? this.opts.postMaxRetries : this.opts.maxRetries;
      const timeoutMs = opts.timeoutMs || (path.includes('/chat') ? this.opts.chatTimeoutMs : this.opts.defaultTimeoutMs);
      const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
      const fetchOpts = {
        method,
        headers,
        ...(opts.signal ? { signal: opts.signal } : {}),
        ...(body && body.json !== undefined ? { body: JSON.stringify(body.json) } : {}),
        timeout: timeoutMs,
        maxRetries: retry ? Math.max(maxRetries, 1) : 0,
      };
      const t0 = _now();
      let resp;
      try {
        resp = await window._fetchWithTimeout(path, fetchOpts);
      } catch (e) {
        const isAbort = e && (e.name === 'AbortError' || /abort/i.test(String(e.message)));
        const isNet = !isAbort && /failed to fetch|networkerror|load failed/i.test(String(e.message || e));
        const code = isAbort ? 'TIMEOUT' : (isNet ? 'NETWORK' : 'UNKNOWN');
        this._metrics.calls++;
        this._metrics.fail++;
        throw new AIError(e.message || String(e), { code, cause: e });
      }
      const dt = _now() - t0;
      // 401/403/404 — 业务错, 不计入 metric
      if (resp.status === 429) {
        const ra = parseInt(resp.headers.get('Retry-After') || '0', 10);
        this._metrics.calls++;
        this._metrics.fail++;
        throw new AIError(`RATE_LIMIT ${resp.status}`, { code: 'RATE_LIMIT', status: resp.status, retryAfter: ra });
      }
      if (resp.status >= 400) {
        let j = null;
        try { j = await resp.json(); } catch (_) {}
        this._metrics.calls++;
        this._metrics.fail++;
        throw _makeAIError(resp, j, resp.status >= 500 ? 'SERVER' : 'CLIENT');
      }
      // 解析 + 解封
      let j;
      try { j = await resp.json(); } catch (e) {
        this._metrics.calls++;
        this._metrics.fail++;
        throw new AIError('响应非 JSON: ' + (e.message || ''), { code: 'PARSE', status: resp.status });
      }
      if (opts.parseRaw) {
        this._metrics.calls++;
        this._metrics.ok++;
        return j;
      }
      // 标准信封: { ok, data, error, ts, trace_id? }
      if (j && typeof j === 'object' && 'ok' in j) {
        if (!j.ok) {
          this._metrics.calls++;
          this._metrics.fail++;
          throw _makeAIError(resp, j, 'BUSINESS');
        }
        this._metrics.calls++;
        this._metrics.ok++;
        return j.data;
      }
      // 没有信封 (服务器没 wrap) — 直接返回对象
      this._metrics.calls++;
      this._metrics.ok++;
      return j;
    }

    // ─── 内部: dedup —— 同 key 同时刻 in-flight, 共用同一 promise
    _dedup(key, fn) {
      const existing = this._inflight.get(key);
      if (existing) return existing.promise;
      const promise = (async () => {
        try { return await fn(); } finally { this._inflight.delete(key); }
      })();
      this._inflight.set(key, { promise });
      return promise;
    }

    // ─── 缓存快捷
    cache = {
      get: (key) => (window._yerenCacheGet ? window._yerenCacheGet(key) : null),
      put: (key, payload) => (window._yerenCachePut ? window._yerenCachePut(key, payload) : null),
      bypass: () => { window._yerenBypassCache = true; },
      noBypass: () => { window._yerenBypassCache = false; },
      invalidate: (re) => _invalidateSW(re),
    };

    // ════════════════════════════════════════════════
    // Yeren AI 节目 (/api/yeren/ai/*)
    // ════════════════════════════════════════════════

    /**
     * chat — 战法 AI 对话 (核心入口)
     * opts: { timeoutMs, skipCache, signal }
     * @returns {Promise<{reply, suggestions, rules_hit, used_ctx_keys, ctx_summary, tool_calls, resolved_code, resolved_name, code, info?}>}
     */
    async chat({ code = null, message, history = [] } = {}, opts = {}) {
      if (!message) throw new AIError('message 必填', { code: 'VALIDATION' });
      const histForReq = (history || []).filter(h => h && h.role && h.content != null)
        .slice(-12).map(h => ({ role: h.role, content: h.content }));
      const cacheKey = window._yerenCacheKey
        ? window._yerenCacheKey(code, message, histForReq)
        : ((code || '') + '||' + String(message).trim());
      // 1) 缓存命中 + 不 skip → 直返
      if (!opts.skipCache && !window._yerenBypassCache) {
        const cached = this.cache.get(cacheKey);
        if (cached && cached.payload && cached.payload.data) return cached.payload.data;
      }
      // 2) in-flight dedup by cacheKey (避免双击 double-charge)
      const dedupKey = 'chat:' + _strHash(cacheKey);
      // R159 2026-08-18: 进度键 — 服务端 chat_yeren 期间持续 emit 到 Redis;
      // 调用方可监听 opts.onProgress(phase, msg) 拿到 "MiniMax 思考中… turn 2/3".
      const progressKey = opts.progressKey || _uuidv4();
      const fetchPromise = this._dedup(dedupKey, async () => {
        const path = '/api/yeren/ai/chat' + (window._yerenBypassCache ? '?_nocache=1' : '');
        const data = await this._call('POST', path,
          { json: { code, message, history: histForReq, progress_key: progressKey } },
          { timeoutMs: opts.timeoutMs || this.opts.chatTimeoutMs, retry: false, signal: opts.signal }
        );
        // 写缓存
        if (!opts.skipCache) {
          const envelope = { ok: true, data, error: null, ts: Date.now() / 1000 };
          this.cache.put(cacheKey, envelope);
        }
        return data;
      });
      fetchPromise.progressKey = progressKey;
      return fetchPromise;
    }

    /** lookup — 自动解析股票名/代码 */
    async lookup(q, opts = {}) {
      if (!q) return { q, hits: [], count: 0 };
      const limit = opts.limit || 10;
      const path = `/api/yeren/ai/lookup?q=${encodeURIComponent(q)}&limit=${limit}`;
      return this._call('GET', path, null,
        { timeoutMs: opts.timeoutMs || this.opts.lookupTimeoutMs, retry: true });
    }

    /** context — 加载某 code 的 RAG context 摘要 */
    async context(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      return this._call('GET', `/api/yeren/ai/context/${code}`, null,
        { timeoutMs: opts.timeoutMs || this.opts.defaultTimeoutMs, retry: true });
    }

    /** indexStatus — 索引健康度 (kpi 监控) */
    async indexStatus(opts = {}) {
      return this._call('GET', '/api/yeren/ai/index_status', null,
        { timeoutMs: opts.timeoutMs || this.opts.indexTimeoutMs, retry: false });
    }

    /** hotCodes — 热门代码 (首页/AI 节目欢迎页) */
    async hotCodes(opts = {}) {
      const limit = opts.limit || 8;
      return this._call('GET', `/api/yeren/ai/hot_codes?limit=${limit}`, null,
        { timeoutMs: opts.timeoutMs || this.opts.indexTimeoutMs, retry: false });
    }

    /** related — 同板块相关股票 (chat 气泡底部 chips) */
    async related(code, opts = {}) {
      if (!code) return { items: [], sector: '' };
      const limit = opts.limit || 3;
      return this._call('GET', `/api/yeren/ai/related/${encodeURIComponent(code)}?limit=${limit}`, null,
        { timeoutMs: opts.timeoutMs || this.opts.lookupTimeoutMs, retry: false });
    }

    // ════════════════════════════════════════════════
    // Stock AI (/api/stock/{code}/ai_*)
    // ════════════════════════════════════════════════

    /**
     * stockAnalysis — 单股 AI 复盘
     * {code, date?, background?} — background=1 时返回 queued/eta_sec
     */
    async stockAnalysis(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      const q = new URLSearchParams();
      if (opts.date) q.set('date', opts.date);
      q.set('background', opts.background ? '1' : '0');
      const path = `/api/stock/${code}/ai_analysis?${q.toString()}`;
      return this._call(opts.background ? 'POST' : 'GET', path, null,
        { timeoutMs: opts.timeoutMs || this.opts.defaultTimeoutMs, retry: !opts.background });
    }

    /**
     * stockAnalysisPolling — 后台运行 + 轮询完成态
     * 流程: POST background=1 → 每 3s GET → 出现 verdict/role 等关键字段视为完成
     * opts: { pollIntervalMs, pollMaxMs, onProgress({queued, eta_sec, attempt, elapsedMs}), date, signal }
     */
    async stockAnalysisPolling(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      const pollMs = opts.pollIntervalMs || this.opts.pollIntervalMs;
      const maxMs = opts.pollMaxMs || this.opts.pollMaxMs;
      // 1) POST background=1 拿排队回执
      const queued = await this.stockAnalysis(code, { background: true, date: opts.date });
      const etaSec = (queued && queued.eta_sec) || 25;
      // 2) 若后端直接同步出了 verdict, 立即返回
      if (queued && (queued.verdict || queued.sector || queued.run_id === undefined)) {
        return queued;
      }
      // 3) 否则轮询
      const start = _now();
      let attempt = 0;
      while (_now() - start < maxMs) {
        attempt++;
        await new Promise(r => setTimeout(r, pollMs));
        if (opts.signal && opts.signal.aborted) throw new AIError('用户取消', { code: 'ABORTED' });
        try {
          const result = await this.stockAnalysis(code, { background: false, date: opts.date });
          // 判定完成的标志: verdict / role / layer_pass / suggested_window 等任一存在
          const done = result && (result.verdict || result.role || result.sector
            || (result.layer_pass && Object.keys(result.layer_pass).length)
            || result.summary || result.suggested_window);
          if (typeof opts.onProgress === 'function') {
            try { opts.onProgress({ queued: !!queued, eta_sec: etaSec, attempt, elapsedMs: _now() - start }); } catch (_) {}
          }
          if (done) {
            // 写完后主动失效 SW 缓存 (避免 15s 窗口内读旧)
            _invalidateSW('/ai_analysis|crash_risk|history/');
            return result;
          }
        } catch (e) {
          if (e && e.code === 'ABORTED') throw e;
          if (e && e.code === 'TIMEOUT') continue;  // 轮询中单次超时无害,继续
          // 其他错 — 仅当已过 1/3 预算仍全失败才放弃
          if (_now() - start > maxMs / 3 && attempt > 3) throw e;
        }
      }
      throw new AIError(`轮询超时 ${Math.round(maxMs / 1000)}s 未完成`, { code: 'TIMEOUT' });
    }

    /** stockCrashRisk — 暴跌风险扫描 */
    async stockCrashRisk(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      const q = new URLSearchParams();
      if (opts.force) q.set('force', '1');
      const path = `/api/stock/${code}/ai_crash_risk${q.toString() ? '?' + q.toString() : ''}`;
      return this._call('GET', path, null,
        { timeoutMs: opts.timeoutMs || this.opts.defaultTimeoutMs, retry: true });
    }

    /** stockHistory — 历史判定 */
    async stockHistory(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      const days = opts.days || 14;
      return this._call('GET', `/api/stock/${code}/ai_history?days=${days}`, null,
        { timeoutMs: opts.timeoutMs || 8000, retry: true });
    }

    /** stockLayerDetail — 四层铁律明细 */
    async stockLayerDetail(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      return this._call('GET', `/api/stock/${code}/ai_layer_detail`, null,
        { timeoutMs: opts.timeoutMs || 6000, retry: true });
    }

    /** stockRefresh — 强刷 (清 Redis 缓存重新判定) */
    async stockRefresh(code, opts = {}) {
      if (!code || !/^\d{6}$/.test(code)) throw new AIError('code 必须 6 位数字', { code: 'VALIDATION' });
      const data = await this._call('POST', `/api/stock/${code}/ai_refresh`, null,
        { timeoutMs: opts.timeoutMs || 50000, retry: false });
      _invalidateSW('/ai_analysis|crash_risk|history/');
      return data;
    }

    // ════════════════════════════════════════════════
    // Watchlist AI (/api/watchlist/{code}/ai)
    // ════════════════════════════════════════════════

    /** watchlistGetAi — 读已有 AI 判定 */
    async watchlistGetAi(code, opts = {}) {
      if (!code) throw new AIError('code 必填', { code: 'VALIDATION' });
      return this._call('GET', `/api/watchlist/${encodeURIComponent(code)}/ai`, null,
        { timeoutMs: opts.timeoutMs || 8000, retry: true });
    }

    /** watchlistAnalyze — 触发 AI 复盘 (单股) — 替原 app.js:13090 + view-other.js:3883 两个重复实现 */
    async watchlistAnalyze(code, opts = {}) {
      if (!code) throw new AIError('code 必填', { code: 'VALIDATION' });
      const q = opts.force ? '?force=1' : '';
      const data = await this._call('POST', `/api/watchlist/${encodeURIComponent(code)}/ai${q}`, null,
        { timeoutMs: opts.timeoutMs || 50000, retry: false });
      _invalidateSW('/watchlist/');
      return data;
    }

    // ════════════════════════════════════════════════
    // Aggregate (/api/screen/ai_aggregate, /api/ai/metrics)
    // ════════════════════════════════════════════════

    /** screenAggregate — 板块/全 A 选股 AI 复盘 */
    async screenAggregate(scored, opts = {}) {
      if (!Array.isArray(scored) || !scored.length) throw new AIError('scored 必须是非空数组', { code: 'VALIDATION' });
      return this._call('POST', '/api/screen/ai_aggregate',
        { json: { scored } },
        { timeoutMs: opts.timeoutMs || 60000, retry: false });
    }

    /** aiMetrics — 上游 AI 调用指标 (返回原始 dict,跳过信封解包) */
    async aiMetrics(opts = {}) {
      return this._call('GET', '/api/ai/metrics', null,
        { timeoutMs: opts.timeoutMs || 4000, retry: false, parseRaw: true });
    }

    // ════════════════════════════════════════════════
    // 自检 + 度量
    // ════════════════════════════════════════════════

    /** 健康检查 (供 bootstrap / 路由用) */
    async ping() {
      try {
        await this.indexStatus({ timeoutMs: 2000 });
        return true;
      } catch (_) { return false; }
    }

    metrics() {
      return Object.assign({ ts: _now() }, this._metrics);
    }
  } // ─── end class AI

  // 暴露
  window.AI = new AI();
  window.TUIXUE_AI = AI;
  window.AIError = AIError;
  // 防止 _call 等待时出错被丢
  if (typeof console !== 'undefined') {
    console.info('[AI] client loaded · endpoints: 16 · cache TTL=5m · chatTimeout=' + window.AI.opts.chatTimeoutMs + 'ms');
  }
})();
