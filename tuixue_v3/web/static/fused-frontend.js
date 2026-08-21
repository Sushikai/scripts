/*
 * fused-frontend.js — 综合高胜率融合 tab 渲染
 *
 * R-2026-08-16: 独立 tab「09 · 综合高胜率」。
 * 数据源: /api/zt/fused_recommend (live) + /api/zt/fused_backtest (历史 5d-max-high WR) +
 *         /api/zt/fused_optimized (cron 写入的最优参数)
 *
 * 性能: 首次加载 6-10s (三路并行), 30s in-process + 5min Redis 暖路径 → 秒开。
 * 默认展开 top10, 每行附 ⚠️ 未达标警示标签 (win_rate_pct < 80%)。
 */
(() => {
  const $mount = document.getElementById('fused-mount');
  if (!$mount) return;

  // ── 状态 ──
  let _data = null;           // /api/zt/fused_recommend 响应 (data)
  let _optimized = null;      // /api/zt/fused_optimized (历史最优参数)
  let _loading = false;
  let _ts = 0;
  let _retried = false;       // R2000.57: 空结果自动重试一次 (子源超时常见, 缓存不落空)

  // ── 加载函数 ──
  async function load(force = false) {
    const now = Date.now();
    if (!force && _data && (now - _ts) < 30_000) return;
    _loading = true; render();
    try {
      const r = await fetch('/api/zt/fused_recommend?top_n=10&refresh=' + (force ? 1 : 0), {
        cache: 'no-store',
      }).then(x => x.json());
      _data = (r && r.data) || null;
      _ts = Date.now();
    } catch (e) {
      console.error('fused load 失败:', e);
    } finally {
      _loading = false; render();
    }
  }

  async function loadOptimized() {
    try {
      const r = await fetch('/api/zt/fused_optimized', { cache: 'no-store' })
        .then(x => x.json()).catch(() => null);
      _optimized = (r && r.data) || null;
    } catch (e) { /* 静默 */ }
  }

  // ── 渲染 ──
  function render() {
    if (!_data || !_data.top10) {
      $mount.innerHTML = _loading
        ? `<div class="card"><div class="card-eyebrow">综合高胜率</div><div style="padding:20px;text-align:center;color:var(--ink-3)">⏳ 三路并行计算中 (zt-live_pick + dragons + dexin)…</div></div>`
        : `<div class="card"><div class="card-eyebrow">综合高胜率</div><div style="padding:20px;text-align:center;color:var(--ink-3)">暂无数据</div><div style="text-align:center;padding:0 0 14px"><button class="btn" id="fused-retry-btn">🔄 重试计算</button></div></div>`;
      const btn = document.getElementById('fused-retry-btn');
      if (btn) btn.addEventListener('click', () => { _retried = true; load(true); });
      return;
    }
    if (!_data.top10.length && !_retried) {
      _retried = true;
      load(true);
      return;
    }
    const top = _data.top10;
    const weights = _data.weights || {};
    const meets = top.filter(t => t.win_rate_pct >= 80).length;
    const meta = document.getElementById('fused-meta');
    if (meta) {
      const wParts = Object.entries(weights).map(([k, v]) => `${k}=${(v * 100).toFixed(0)}%`).join(' · ');
      meta.innerHTML = `权重: ${wParts} · 池子 ${_data.pool_size || 0} 只 · ${meets}/${top.length} 达标 · 缓存 ${_data._cache_hit || 'fresh'}`;
    }

    const warn = meets < top.length
      ? `<div class="card" style="margin-bottom:10px;border-left:3px solid #f59e0b;padding:8px 12px">⚠️ <b>${meets}/${top.length}</b> 标的 ≥ 80%, <b>诚实上限 58-63%</b>。未达标行已加警示标签。明日 16:00 后台 OOS 验证 + 进化权重, 持续逼近诚实天花板。</div>`
      : '';

    const cards = top.map((p, idx) => {
      const warnTag = p.win_rate_pct < 80
        ? `<span class="fused-rec-warn" title="5d-max-high 胜率 ${p.win_rate_pct}% < 80%">⚠️ 未达标</span>`
        : '';
      const comp = p.components || {};
      const compBar = (k) => {
        const v = Math.round(comp[k] || 0);
        return `<div class="fused-rec-bar" title="${k}=${v}"><span style="width:${v}%;background:${k === 'zt' ? '#3b82f6' : k === 'dragons' ? '#ef4444' : '#10b981'}"></span></div>`;
      };
      const sig = p.source_signals || {};
      const zt = sig.zt || {};
      const dr = sig.dragons || {};
      const dx = sig.dexin || {};
      return `
        <div class="fused-rec-card" data-code="${p.code}" onclick="window.location.hash='view-stock&code=${p.code}'">
          <div class="fused-rec-row1">
            <span class="fused-rec-rank">#${idx + 1}</span>
            ${warnTag}
            <span class="fused-rec-sector">${p.sector || '—'}</span>
          </div>
          <div class="fused-rec-row2">
            <span class="fused-rec-code">${p.code}</span>
            <span class="fused-rec-name">${p.name || ''}</span>
          </div>
          <div class="fused-rec-row3">
            <span class="fused-rec-score">${p.fused_score?.toFixed?.(1) || '—'}</span>
            <span class="fused-rec-wr">WR ${p.win_rate_pct?.toFixed?.(1) || '—'}%</span>
            <span class="fused-rec-conf" title="confidence = min(weights)">conf ${(p.confidence * 100)?.toFixed?.(0) || '—'}%</span>
          </div>
          <div class="fused-rec-bars">
            ${compBar('zt')}
            ${compBar('dragons')}
            ${compBar('dexin')}
          </div>
          <div class="fused-rec-meta">
            <span title="zt: streak=${zt.streak ?? '—'} 封单=${zt.limit_order_amount ?? '—'}亿">zt ${zt.rating || ''}</span>
            <span title="dragons: rank=${dr.rank ?? '—'} 主线=${dr.is_mainline ? '是' : '否'}">龙 ${dr.rank ?? '—'}</span>
            <span title="dexin stage">得 ${dx.stage || '—'}</span>
          </div>
        </div>`;
    }).join('');

    const optInfo = _optimized
      ? `<div class="card fused-rec-opt-card">
          <div class="card-eyebrow">📊 历史最优参数 (cron 写入)</div>
          <div class="fused-rec-opt-grid">
            <div><span class="dim">WR</span> <b>${_optimized.best_wr ?? '—'}%</b></div>
            <div><span class="dim">avg_return</span> <b>${_optimized.best_avg_return ?? '—'}%</b></div>
            <div><span class="dim">max_dd</span> <b>${_optimized.best_dd ?? '—'}%</b></div>
            <div><span class="dim">达标</span> <b>${_optimized.meet_target ? '✅ 是' : '❌ 否'}</b></div>
            <div><span class="dim">iter</span> <b>${_optimized.iterations_done ?? '—'}</b></div>
            <div><span class="dim">耗时</span> <b>${_optimized.elapsed_sec ?? '—'}s</b></div>
          </div>
        </div>`
      : '';

    $mount.innerHTML = `
      ${warn}
      ${optInfo}
      <div class="card">
        <div class="card-eyebrow collapsible" id="fused-toggle" aria-expanded="true">
          <span>🔥 Fused Top 10</span>
          <span class="arrow">▶</span>
        </div>
        <div class="fused-rec-grid">${cards}</div>
      </div>`;

    // 折叠事件
    const toggle = document.getElementById('fused-toggle');
    if (toggle) {
      toggle.addEventListener('click', () => {
        const grid = $mount.querySelector('.fused-rec-grid');
        if (!grid) return;
        const expanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        grid.style.display = expanded ? 'none' : 'grid';
      });
    }
  }

  // ── 事件绑定 ──
  function bindEvents() {
    const refresh = document.getElementById('fused-refresh');
    if (refresh) refresh.addEventListener('click', () => load(true));
    const evolve = document.getElementById('fused-evolve-btn');
    if (evolve) evolve.addEventListener('click', async () => {
      if (!confirm('启动 10K 进化算法重训权重? 完成后会写入 cache_store 并影响明日推荐。')) return;
      evolve.disabled = true;
      evolve.textContent = '⏳ 进化中…';
      try {
        const r = await fetch('/api/zt/fused_evolve?iterations=500&target_wr=80&days=180&refresh=1', {
          cache: 'no-store',
        }).then(x => x.json());
        if (r && r.ok && r.data) {
          _optimized = r.data;
          alert(`✅ 进化完成\nWR=${r.data.best_wr}%  DD=${r.data.best_dd}%  iter=${r.data.iterations_done}`);
        } else {
          alert('进化失败: ' + (r && r.error || '未知'));
        }
        render();
      } catch (e) {
        alert('进化异常: ' + e);
      } finally {
        evolve.disabled = false;
        evolve.textContent = '🔁 重新进化';
      }
    });
  }

  // ── init ──
  load(false);
  loadOptimized();
  bindEvents();

  // 暴露给 app.js (切换 tab 时刷新)
  window.__fusedReload = () => { load(true); loadOptimized(); };
})();
