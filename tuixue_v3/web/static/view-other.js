// ────────────────────────────────────────────
// OPTIMIZE
// ────────────────────────────────────────────
async function loadReports() {
  try {
    const data = await api('/api/reports');
    const tbody = $('#reports-table tbody');
    const list = data.reports || [];
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无报告</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(p => `<tr>
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.type)}</td>
      <td class="num">${escapeHtml(String(p.size_kb))} KB</td>
      <td>${escapeHtml(p.mtime)}</td>
    </tr>`).join('');
  } catch (e) {
    $('#reports-table tbody').innerHTML = `<tr><td colspan="4" class="empty">加载失败</td></tr>`;
  }
}

$('#run-optimize')?.addEventListener('click', async () => {
  const btn = $('#run-optimize');
  btn.disabled = true;
  btn.querySelector('span').textContent = '调优中…';
  $('#optimize-status').textContent = '启动 SSE 进度流 …';
  toast('开始参数调优，进度会实时显示', 'info', 3000);
  _showLoading('参数调优 网格扫描');
  const es = new EventSource('/api/stream/optimize');
  es.addEventListener('progress', (ev) => {
    try {
      const p = JSON.parse(ev.data);
      if (p.phase === 'iter_done') {
        $('#optimize-status').textContent =
          `iter ${p.iter}/${p.total} 完成 · trials=${p.trials} · best=${p.best_score?.toFixed(2) || '?'} · ${p.elapsed_sec}s`;
      } else if (p.phase === 'new_best') {
        $('#optimize-status').textContent =
          `⭐ iter ${p.iter} 新最佳 ${p.key}=${p.value} score=${p.score?.toFixed(2)}`;
      } else if (p.phase === 'iter_start') {
        $('#optimize-status').textContent = `iter ${p.iter}/${p.total} 进行中 ...`;
      } else if (p.phase === 'done') {
        $('#optimize-status').textContent =
          `完成 · trials=${p.total_trials} · best=${p.best_score?.toFixed(2)}`;
      }
    } catch {}
  });
  es.addEventListener('done', (ev) => {
    try {
      const r = JSON.parse(ev.data);
      $('#optimize-status').textContent = `完成 · 用时 ${r.elapsed_sec || '?'}s · trials=${r.total_trials || '?'}`;
      toast('调优完成，已写入报告目录', 'success');
      loadReports();
    } catch {
      $('#optimize-status').textContent = '完成';
      loadReports();
    }
    es.close();
    _hideLoading();
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  });
  es.onerror = () => {
    // EventSource 不会自动重连 (server 不重试); 只显示错误
    $('#optimize-status').textContent = 'SSE 连接中断（可重试）';
    es.close();
    _hideLoading();
    btn.disabled = false;
    btn.querySelector('span').textContent = '开始调优';
  };
});

// ────────────────────────────────────────────
// LAWS view — 读 /api/laws（与 AI 复用同一源）
// ────────────────────────────────────────────
var lawsRendered = false;
var lawsData = null;
async function renderLawsOnce() {
  const host = $('#laws-categories');
  const kj = $('#laws-koujue');
  const auditHost = $('#laws-compliance');
  if (!host) return;

  // 第一次进：拉后端
  if (!lawsData) {
    host.innerHTML = '<div class="dim" style="padding:1rem">加载铁律 …</div>';
    try {
      lawsData = await api('/api/laws');
    } catch (e) {
      host.innerHTML = `<div class="dim" style="padding:1rem;color:${DOWN}">加载失败: ${e.message}</div>`;
      return;
    }
  }
  if (lawsRendered) return;
  lawsRendered = true;

  const cats = lawsData.categories || [];
  host.innerHTML = cats.map(c => `
    <article class="law-card">
      <div class="law-head">
        <span class="law-num">${escapeHtml(c.num)}</span>
        <h3 class="law-title">${escapeHtml(c.name)}</h3>
      </div>
      <span class="law-sub">${escapeHtml(c.sub)}</span>
      <ol class="law-list">
        ${(c.items || []).map(t => `<li>${escapeHtml(t)}</li>`).join('')}
      </ol>
    </article>
  `).join('');

  if (kj) kj.textContent = lawsData.koujue || '';

  const audit = lawsData.audit || [];
  auditHost.innerHTML = audit.map(g => {
    const ratio = g.passed / Math.max(1, g.total);
    const ratioCls = ratio >= 0.5 ? 'good' : ratio >= 0.25 ? '' : 'warn';
    return `
      <button class="compliance-cat" aria-expanded="false" data-target="${g.name}">
        <span class="cc-name">${g.name}</span>
        <span class="cc-ratio ${ratioCls}">${g.passed} / ${g.total} 已实现 · ${Math.round(ratio*100)}%</span>
      </button>
      <div class="compliance-rows" data-rows="${g.name}" hidden>
        ${g.rows.map(([k, txt]) => `<div class="compliance-row">
          <span class="cr-mark ${k}">${k === 'ok' ? '✓' : k === 'warn' ? '!' : '✗'}</span>
          <span class="cr-text">${escapeHtml(txt)}</span>
        </div>`).join('')}
      </div>`;
  }).join('');

  $$('.compliance-cat').forEach(btn => {
    btn.addEventListener('click', () => {
      const tgt = btn.dataset.target;
      const rows = $(`[data-rows="${tgt}"]`);
      const open = !rows.hidden;
      rows.hidden = open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      btn.classList.toggle('open', !open);
    });
  });
}

// ────────────────────────────────────────────
// DRAGONS · 龙头战法
// ────────────────────────────────────────────
var _dragonsLoaded = false;
var _dragonsLoading = false;
var _dragonsData = null;                          // 缓存最近一次 /api/dragons 返回
var _dragonsSortState = { key: 'rank', dir: 'asc' };  // 全涨停表排序状态

// 排序键映射(对应 dragons.py 输出的字段)
var _DRAGONS_SORT_KEYS = {
  rank:        s => s.rank ?? 999,
  code:        s => s.code || '',
  name:        s => s.name || '',
  sector:      s => s.sector || '',
  concept:     s => (s.taxonomy?.l3 || s.taxonomy?.l2 || ''),
  streak:      s => s.streak ?? 0,
  market_cap:  s => s.market_cap_yi ?? 0,
  turnover:    s => s.turnover_pct ?? 0,
  seal:        s => s.seal_ratio_pct ?? -1,         // 缺失值排最后
  score:       s => s.score_total ?? 0,
};
function _sortDragonsAll(list, key, dir) {
  const fn = _DRAGONS_SORT_KEYS[key];
  if (!fn || !list) return list || [];
  const sorted = [...list].sort((a, b) => {
    const av = fn(a), bv = fn(b);
    if (typeof av === 'string') {
      return dir === 'asc' ? av.localeCompare(bv, 'zh-Hans') : bv.localeCompare(av, 'zh-Hans');
    }
    return dir === 'asc' ? av - bv : bv - av;
  });
  return sorted;
}

// 龙虎榜 STEP 4 行内 AI 评分明细(8 维卡片) — 2026-07-19 加 周线擒牛 + 回升位
function _renderAIAnalysisCards(bd, s) {
  if (!bd) bd = {};
  const labels = ['连板强度', '资金认可', '封成比', '市值匹配', '技术形态', '题材纯度', '周线擒牛', '回升位'];
  const cards = labels.map(k => {
    const v = bd[k] || { pts: 0, max: 0, note: '' };
    const max = v.max || 0;
    const pct = max > 0 ? Math.round((v.pts || 0) / max * 100) : 0;
    const cls = pct >= 70 ? 'high' : pct >= 40 ? 'mid' : 'low';
    const ptsStr = `<span class="adc-pts">${v.pts || 0}<span class="max">/${max}</span></span>`;
    return `<div class="ai-detail-card">
      <div class="adc-label">${k}</div>
      <div class="adc-bar"><div class="adc-bar-fill ${cls}" style="width:${pct}%"></div></div>
      ${ptsStr}
      <div class="adc-note">${escapeHtml(v.note || '—')}</div>
    </div>`;
  }).join('');
  const aliases = (s.seat_aliases || []).slice(0, 4);
  const aliasLine = aliases.length
    ? ` · 江湖: ${aliases.map(a => '「' + escapeHtml(a) + '」').join(' · ')}`
    : '';
  const warnLine = (s.warnings || []).length
    ? ` ·  ${s.warnings.length} 项警告`
    : '';
  // 周线擒牛命中 chip (前端跳转 → 周线擒牛过滤页)
  const wb = s.wb_hits || {};
  const wbChips = (wb.matched || []).length
    ? wb.matched.map(k => `<span class="chip tag-good" data-action="show-view:weekly_bull?pattern=${escapeHtml(k)}" title="${escapeHtml(wb.reasons?.[k] || '')}">${escapeHtml(_WB_LABELS[k] || k)}</span>`).join('')
    : '';
  // 回升位 chip
  const rl = s.rl_hit || {};
  const rlChip = rl.has_signal
    ? `<span class="chip ${rl.near_support ? 'tag-good' : ''}" data-action="show-view:stock" data-code="${escapeHtml(s.code)}" title="${escapeHtml((rl.explanation || '').slice(0, 80))}">1/3位=${escapeHtml(String(rl.level_1_3 ?? '—'))} · 距${escapeHtml(String(rl.distance_to_level_1_3_pct ?? '—'))}%</span>`
    : '';
  return `<div class="ai-detail-grid">${cards}</div>
    <div class="ai-detail-footer">
      <span class="meta">${escapeHtml(s.code)} · ${escapeHtml(s.name)} · ${escapeHtml(s.sector || '—')} · ${escapeHtml(String(s.streak ?? 0))}板 · 评分 <b>${escapeHtml(String(s.score_total ?? 0))}</b>${aliasLine}${warnLine}</span>
      ${wbChips ? `<div class="ai-detail-wb">周线: ${wbChips}</div>` : ''}
      ${rlChip ? `<div class="ai-detail-rl">回升位: ${rlChip}</div>` : ''}
      <button class="btn btn-mini" data-goto="${escapeHtml(s.code)}">→ 查看完整个股分析</button>
    </div>`;
}

// 周线擒牛 pattern label 表 (前端展示用)
const _WB_LABELS = {
  sanxing_taodi:     '三星探底',
  zhanwen_5w:        '站稳5周线',
  tupo_pingtai:      '突破震荡平台',
  junxian_fangxiang: '均线方向',
  zhouxian_duiliang: '周线堆量',
};

function renderDragons(data) {
  // api() 已 unwrap envelope, data 本身就是 {date, sentiment, ...}
  if (!data || typeof data !== 'object') return;
  const d = data;

  // 头部信息
  $('#dragons-date').textContent = d.date || '—';
  $('#dragons-elapsed').textContent = d.stats ? `耗时 ${d.stats.elapsed_sec}s · 评分 ${d.stats.total_zt}只 · 龙虎榜 ${d.stats.lhb_loaded}/${d.stats.total_zt} · 技术面 ${d.stats.tech_loaded}/${d.stats.total_zt}` : '';
  if (d.stats?.seal_degraded) {
    $('#dragons-elapsed').textContent += ` · 封单降级 ${d.stats.seal_degraded}只`;
  }

  // STEP 1: 情绪
  const s = d.sentiment || {};
  const sentimentColor = s.action === '积极' ? 'good' : s.action === '空仓' ? 'bad' : 'neutral';
  $('#dragons-sentiment-label').innerHTML =
    `<span class="sentiment-pill sentiment-${sentimentColor}">${s.label || '—'}</span>` +
    `<span class="caption dim" style="margin-left: .5rem">操作: <b>${s.action || '—'}</b></span>`;
  $('#dragons-zt-count').textContent = s.zt_count ?? '—';
  $('#dragons-max-streak').textContent = (s.max_streak || 0) + '板';
  const sd = s.streak_dist || {};
  const sdStr = Object.keys(sd).sort((a,b)=>Number(b)-Number(a))
    .map(k => `${k}板×${sd[k]}`).join(' · ') || '—';
  $('#dragons-streak-dist').textContent = sdStr;

  // STEP 2: 主线 Top 5
  const main = d.mainline || [];
  if (main.length === 0) {
    $('#dragons-mainline').innerHTML = emptyState({ icon: '🐉', title: '无主线数据', hint: '当前未识别出强势主线板块,可放宽筛选条件或等待下一交易日数据' });
  } else {
    $('#dragons-mainline').innerHTML = main.slice(0, 5).map(m => {
      const pct = (m.change_pct ?? 0).toFixed(2);
      const inflow = (m.net_inflow_yi ?? 0).toFixed(2);
      const flowBadge = m.rank_flow ? `<span class="badge">流#${m.rank_flow}</span>` : '';
      const pctBadge = m.rank_pct ? `<span class="badge">幅#${m.rank_pct}</span>` : '';
      const secName = m.name || '';
      const tx = m.taxonomy || {};
      const l1Chip = tx.l1 && tx.l1_color
        ? `<span class="chip-l1-mini" style="display:inline-block;padding:0 6px;font-size:9px;line-height:16px;border-radius:3px;background:${escapeHtml(tx.l1_color)}22;color:${escapeHtml(tx.l1_color)};border:1px solid ${escapeHtml(tx.l1_color)};margin-left:6px;vertical-align:middle">${escapeHtml(tx.l1)}</span>`
        : '';
      return `
        <div class="mainline-card">
          <a href="#" class="mainline-name sector-link" data-sector="${escapeHtml(secName)}">${escapeHtml(secName) || '—'}</a>
          <div class="mainline-meta">
            <span class="${pct >= 0 ? 'good' : 'bad'}">${pct >= 0 ? '+' : ''}${pct}%</span>
            <span class="dim">净流入 ${inflow}亿</span>
          </div>
          <div class="mainline-badges">${flowBadge}${pctBadge}${l1Chip}</div>
        </div>`;
    }).join('');
    // 板块名点击 → 切到 sector 视图
    $$('#dragons-mainline .sector-link').forEach(a => {
      a.onclick = e => {
        e.preventDefault();
        const sec = a.dataset.sector || '';
        const qs = sec ? `?l2=${encodeURIComponent(sec)}` : '';
        location.hash = `#all_stocks${qs}`;
      };
    });
  }

  // STEP 3: Top 10 龙头卡片
  const top10 = d.top10 || [];
  if (top10.length === 0) {
    $('#dragons-top10').innerHTML = emptyState({ icon: '🐲', title: '无龙头候选', hint: '暂无连板高度 ≥ 3 的标的,可放宽席位筛选或查看历史龙头' });
  } else {
    $('#dragons-top10').innerHTML = top10.map(s => {
      const bd = s.score_breakdown || {};
      const breakdown = ['连板强度','资金认可','封成比','市值匹配','技术形态','题材纯度','周线擒牛','回升位'].map(k => {
        const v = bd[k] || {pts: 0, max: 0, note: ''};
        const pct = v.max > 0 ? Math.round(v.pts / v.max * 100) : 0;
        const barClass = pct >= 70 ? 'high' : pct >= 40 ? 'mid' : 'low';
        return `<div class="bd-row">
          <span class="bd-label">${k}</span>
          <span class="bd-bar"><span class="bd-fill bd-${barClass}" style="width:${pct}%"></span></span>
          <span class="bd-pts">${v.pts}/${v.max}</span>
        </div>`;
      }).join('');
      const warn = (s.warnings || []).length
        ? `<div class="dragon-warn"> ${s.warnings.join(' · ')}</div>`
        : '';
      const mainlineBadge = s.is_mainline ? '<span class="badge badge-main">主线</span>' : '';
      const sealTxt = s.seal_ratio_pct != null ? `${s.seal_ratio_pct.toFixed(1)}%` : '—';
      const aliasChips = (s.seat_aliases || []).length
        ? `<div class="dragon-aliases">${s.seat_aliases.slice(0, 4).map(a => `<span class="alias-chip">「${escapeHtml(a)}」</span>`).join('')}</div>`
        : '';
      // 周线擒牛 chip 集合
      const wb = s.wb_hits || {};
      const wbChips = (wb.matched || []).length
        ? wb.matched.slice(0, 3).map(k => `<span class="chip tag-good wb-mini" data-action="show-view:weekly_bull?pattern=${escapeHtml(k)}" title="${escapeHtml(wb.reasons?.[k] || '')}">${escapeHtml(_WB_LABELS[k] || k)}</span>`).join('')
        : '';
      const wbBadge = (wb.count || 0) >= 1
        ? `<span class="dragon-wb-badge">周线 ${wb.count}/5</span>`
        : '';
      // 回升位 chip
      const rl = s.rl_hit || {};
      const rlBadge = rl.has_signal
        ? `<span class="dragon-rl-badge ${rl.near_support ? 'rl-near' : ''}" title="${escapeHtml((rl.explanation || '').slice(0, 80))}">1/3位=${escapeHtml(String(rl.level_1_3 ?? '—'))}</span>`
        : '';
      return `
        <div class="dragon-card${s.rank && s.rank <= 3 ? ' rank-top3' : ''}">
          <div class="dragon-head">
            <span class="dragon-rank">#${escapeHtml(String(s.rank))}</span>
            <span class="dragon-code">${escapeHtml(s.code)}</span>
            <span class="dragon-name">${escapeHtml(s.name)}</span>
            <span class="dragon-score">${escapeHtml(String(s.score_total))}</span>
            ${wbBadge}${rlBadge}
          </div>
          <div class="dragon-meta">
            ${(() => {
              const tx = s.taxonomy || {};
              const color = tx.l1_color || '#888';
              return `<span class="dragon-sector-chip" style="display:inline-block;padding:0 7px;font-size:10px;line-height:18px;border-radius:4px;background:${color}22;color:${color};border:1px solid ${color}44;">${escapeHtml(s.sector || '—')}</span>`;
            })()} ${mainlineBadge}
            <span class="dim"> · ${escapeHtml(String(s.streak))}板 · 市值${escapeHtml(String(s.market_cap_yi))}亿 · 换手${escapeHtml(String(s.turnover_pct))}% · 封成${escapeHtml(sealTxt)}</span>
          </div>
          ${wbChips ? `<div class="dragon-wb-chips">${wbChips}</div>` : ''}
          <div class="dragon-bd">${breakdown}</div>
          ${aliasChips}
          ${warn}
        </div>`;
    }).join('');
  }

  // STEP 4: 全部涨停 (默认折叠)
  $('#dragons-all-count').textContent = (d.all || []).length;
  const allBody = $('#dragons-all-table tbody');
  const allList = d.all || [];
  // 排序(应用当前状态)
  const sortedAll = _sortDragonsAll(allList, _dragonsSortState.key, _dragonsSortState.dir);
  // 更新列头视觉
  $$('#dragons-all-table th.sortable').forEach(th => {
    th.classList.remove('active-sort');
    const arrow = th.querySelector('.sort-arrow');
    if (arrow) arrow.textContent = '';
    if (th.dataset.sort === _dragonsSortState.key) {
      th.classList.add('active-sort');
      if (arrow) arrow.textContent = _dragonsSortState.dir === 'asc' ? '▲' : '▼';
    }
  });
  if (allList.length === 0) {
    allBody.innerHTML = '<tr><td colspan="11" class="empty">无数据</td></tr>';
  } else {
    allBody.innerHTML = sortedAll.map(s => {
      const sealTxt = s.seal_ratio_pct != null ? `${s.seal_ratio_pct.toFixed(1)}%` : '—';
      const warnTxt = (s.warnings || []).length ? escapeHtml(s.warnings.join('; ')) : '—';
      const bd = s.score_breakdown || {};
      const bdHtml = _renderAIAnalysisCards(bd, s);
      return `<tr data-code="${escapeHtml(s.code)}" class="clickable ai-toggle">
        <td>${escapeHtml(String(s.rank))}</td>
        <td><a href="#" class="stock-link" data-code="${escapeHtml(s.code)}">${escapeHtml(s.code)}</a></td>
        <td>${escapeHtml(s.name)}</td>
        <td>${escapeHtml(s.sector || '—')}</td>
        <td>${(() => {
          const tx = s.taxonomy || {};
          const parts = [];
          if (tx.l3) parts.push(escapeHtml(tx.l3));
          if (tx.l2 && tx.l2 !== tx.l3) parts.push(`<span class="dim">${escapeHtml(tx.l2)}</span>`);
          return parts.length ? parts.join(' · ') : '<span class="dim">—</span>';
        })()}</td>
        <td>${escapeHtml(String(s.streak))}板</td>
        <td>${escapeHtml(String(s.market_cap_yi))}亿</td>
        <td>${escapeHtml(String(s.turnover_pct))}%</td>
        <td>${escapeHtml(sealTxt)}</td>
        <td><b>${escapeHtml(String(s.score_total))}</b></td>
        <td class="dim">${warnTxt}</td>
      </tr>
      <tr class="ai-detail-row" data-bd-code="${s.code}" hidden>
        <td colspan="11">${bdHtml}</td>
      </tr>`;
    }).join('');
  }

  // 行点击 → 展开/收起 AI 评分明细(代码 a 自己 stopPropagation,不会双触发)
  $('#dragons-all-table tbody').querySelectorAll('tr.ai-toggle[data-code]').forEach(tr => {
    tr.addEventListener('click', (e) => {
      if (e.target.closest('a') || e.target.closest('button')) return;
      const code = tr.dataset.code;
      const detail = document.querySelector(`#dragons-all-table tr.ai-detail-row[data-bd-code="${code}"]`);
      if (!detail) return;
      const willShow = detail.hidden;
      detail.hidden = !willShow;
      tr.classList.toggle('expanded', willShow);
    });
  });
  // 行内"→ 查看完整"按钮 → 跳个股页
  $('#dragons-all-table tbody').querySelectorAll('button[data-goto]').forEach(b => {
    b.addEventListener('click', e => {
      e.stopPropagation();
      gotoStock(b.dataset.goto);
    });
  });
  // 代码 a → 跳个股页
  $('#dragons-all-table tbody').querySelectorAll('.stock-link').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      gotoStock(a.dataset.code);
    });
  });

  // 表头点击 → 切换排序(只重绘表格,不发后端)
  $$('#dragons-all-table th.sortable').forEach(th => {
    th.onclick = () => {
      const key = th.dataset.sort;
      if (_dragonsSortState.key === key) {
        _dragonsSortState.dir = _dragonsSortState.dir === 'asc' ? 'desc' : 'asc';
      } else {
        _dragonsSortState.key = key;
        _dragonsSortState.dir = (key === 'rank' || key === 'code' || key === 'name' || key === 'sector' || key === 'concept') ? 'asc' : 'desc';
      }
      if (_dragonsData) renderDragons(_dragonsData);
    };
  });

  // STEP 4 折叠交互
  $('#dragons-all-toggle').onclick = () => {
    const wrap = $('#dragons-all-wrap');
    wrap.classList.toggle('hidden');
    $('#dragons-all-toggle .arrow').textContent =
      wrap.classList.contains('hidden') ? '▶' : '▼';
  };

  // STEP 4 决策建议
  const dec = d.decisions || {};
  const overall = dec.overall || '—';
  const plays = dec.plays || [];
  const dips = dec.dips || [];
  const avoids = dec.avoids || [];
  if (!plays.length && !dips.length && !avoids.length) {
    $('#dragons-decision').innerHTML = `<p class="empty">${escapeHtml(overall)} (Top10 中无可执行标的)</p>`;
  } else {
    const playHtml = plays.length
      ? `<div class="decision-col">
          <div class="decision-title">🎯 尾盘打板 (${plays.length})</div>
          ${plays.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    const dipHtml = dips.length
      ? `<div class="decision-col">
          <div class="decision-title"> 次日低吸 (${dips.length})</div>
          ${dips.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · ${escapeHtml(String(p.streak))}板 · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    const avoidHtml = avoids.length
      ? `<div class="decision-col">
          <div class="decision-title"> 回避 (${avoids.length})</div>
          ${avoids.map(p => `<div class="decision-item">
            <a href="#" class="stock-link" data-code="${escapeHtml(p.code)}"><b>${escapeHtml(p.name)}</b> ${escapeHtml(p.code)}</a>
            <span class="dim"> · ${escapeHtml(p.sector || '')} · 评分${escapeHtml(String(p.score))}</span>
            <div class="decision-reason decision-warn">${escapeHtml(p.reason || '')}</div>
          </div>`).join('')}
        </div>`
      : '';
    $('#dragons-decision').innerHTML = `
      <p class="decision-overall">💡 <b>${escapeHtml(overall)}</b></p>
      <div class="decision-grid">${playHtml}${dipHtml}${avoidHtml}</div>
    `;
    // 重新绑定 stock-link (新插入的 DOM)
    $('#dragons-decision').querySelectorAll('.stock-link').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        const code = a.dataset.code;
        $('#stock-code').value = code;
        showView('stock');
        loadStockDetail(code);
      });
    });
  }
}

async function loadDragons(refresh = false) {
  if (_dragonsLoading) return;
  _dragonsLoading = true;
  $('#dragons-status').textContent = refresh ? '刷新中 …' : '加载中 …';
  try {
    const url = '/api/dragons' + (refresh ? '?refresh=true' : '');
    const data = await api(url, { timeout: 60000 });
    _dragonsData = data;
    renderDragons(data);
    $('#dragons-status').textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN');
    _dragonsLoaded = true;
  } catch (e) {
    $('#dragons-status').textContent = '加载失败: ' + (e.message || e);
    toast('龙头加载失败');
  } finally {
    _dragonsLoading = false;
  }
}

$('#dragons-refresh')?.addEventListener('click', () => loadDragons(true));

// 全局绑定 — showView 包装统一在 app.js:4223-4230,这里只 bind jump 按钮
$$('[data-jump]').forEach(el => {
  el.addEventListener('click', () => {
    showView(el.dataset.jump);
  });
});

// R5: 跨页 stock-link 点击统一拦截 — 当前页打开个股详情
document.addEventListener('click', e => {
  const a = e.target.closest('a.stock-link[data-code]');
  if (!a) return;
  e.preventDefault();
  const code = a.dataset.code;
  if (!code) return;
  $('#stock-code').value = code;
  showView('stock');
  loadStockDetail(code);
});

$('#refresh-ticker')?.addEventListener('click', () => {
  refreshTicker();
  toast('已刷新');
});

// R12-A: 一键清空所有交易 (清库重测用)
$('#review-clear-all')?.addEventListener('click', async () => {
  if (!confirm(' 确定清空所有交易记录?\n\n此操作不可逆!\n• 删除所有 trades 行\n• 删除所有 trade_reviews 行\n• 清空 Redis AI 缓存')) return;
  if (!confirm(' 最后确认: 真的要清空吗?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades_all?confirm=YES', { method: 'DELETE', timeout: 10000 });
    const j = await r.json();
    if (!j.ok) { showToast('✗ 清空失败: ' + (j.error || ''), 'error'); return; }
    showToast(`✓ 已清空 (trades=${j.data.deleted_trades} reviews=${j.data.deleted_reviews})`, 'success');
    if (typeof _reviewLoadList === 'function') await _reviewLoadList();
    if (typeof _reviewLoadPortfolio === 'function') await _reviewLoadPortfolio();
    if (typeof _reviewLoadStats === 'function') await _reviewLoadStats();
    if (typeof _reviewRefreshIntegrity === 'function') await _reviewRefreshIntegrity();
  } catch (e) {
    showToast('✗ 请求失败: ' + e.message, 'error');
  }
});

// R14: 一键 AI 复盘所有交易 — 后台并发跑,不阻塞页面
$('#review-bulk-ai')?.addEventListener('click', async () => {
  const trades = (_reviewState && _reviewState.trades) || [];
  if (!trades.length) { showToast('✗ 当前没有交易可复盘', 'error', 2500); return; }
  const needRun = trades.filter(t => !t.last_review).length;
  const cached  = trades.length - needRun;
  const lines = [
    `将对 ${trades.length} 笔交易启动 AI 复盘`,
    needRun ? `其中 ${needRun} 笔需要现跑(≈60s/笔),${cached} 笔走缓存秒回` : `${cached} 笔全部命中缓存,瞬时完成`,
    '',
    '后台并发 2 路,可在原地继续浏览/操作其它页',
    '完成每笔后只局部更新该行,账单/持仓不会闪',
  ];
  if (!confirm(lines.join('\n'))) return;
  const btn = document.getElementById('review-bulk-ai');
  const original = btn.textContent;
  btn.disabled = true;
  let done = 0, okCnt = 0, failCnt = 0;
  const CONC = 2;
  const queue = trades.slice();
  const patchProgress = () => { btn.textContent = `⏳ ${done}/${queue.length}`; };
  patchProgress();
  async function worker() {
    while (queue.length) {
      const t = queue.shift();
      const wasCached = !!t.last_review;
      try {
        // force=false:已复盘的笔秒回,未复盘的笔调 LLM (≈60s)
        const r = await _fetchWithTimeout(`/api/review/trades/${t.id}/review?force=false`, { method: 'POST' });
        const j = await r.json();
        if (j.ok && j.data) {
          okCnt++;
          // R15-fix: 局部更新行 — 不重渲整张表,不影响账单 / 持仓 / 浮盈
          _reviewPatchRow(t.id, j.data);
          const local = (_reviewState.trades || []).find(x => x.id === t.id);
          if (local) local.last_review = j.data;
          const v = j.data.verdict || '';
          const s = (j.data.score != null) ? `${j.data.score}分` : '';
          showToast(`✓ #${t.id} ${v} ${s}${wasCached ? ' ⌛缓存' : ''}`.trim(), 'success', 1800);
        } else {
          failCnt++;
          showToast(`✗ #${t.id} ${j.error || '失败'}`, 'error', 2500);
        }
      } catch (e) {
        failCnt++;
        showToast(`✗ #${t.id} ${e.message}`, 'error', 2500);
      } finally {
        done++;
        patchProgress();
        // R15-fix: 不要每笔都重渲 — 只在全部完成时再统一刷一次
      }
    }
  }
  const ws = Array.from({ length: CONC }, () => worker());
  await Promise.all(ws);
  btn.disabled = false;
  btn.textContent = original;
  showToast(`✅ 全部完成 · 成功 ${okCnt} / 失败 ${failCnt}`, 'success', 4000);
  // R15-fix: 一次性刷,账单不闪
  try { await _reviewLoadList(); } catch {}
  try { await _reviewRefreshIntegrity(); } catch {}
  try { await _reviewLoadPortfolio(); } catch {}
});

// R13: 「修复脏数据」按钮 — dirty 时显示, 点击等同 clear-all + 引导重录
$('#review-fix-dirty')?.addEventListener('click', async () => {
  if (!confirm(' 检测到 DB 残留历史脏数据 (老解析器切碎 shares / 无法反查的 code)。\n\n清空所有交易后请重新粘贴录入。\n\n继续?')) return;
  if (!confirm(' 最终确认?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades_all?confirm=YES', { method: 'DELETE', timeout: 10000 });
    const j = await r.json();
    if (!j.ok) { showToast('✗ 清空失败: ' + (j.error || ''), 'error'); return; }
    showToast(`✓ 脏数据已清 (trades=${j.data.deleted_trades}) — 请重新粘贴导入`, 'success');
    if (typeof _reviewLoadList === 'function') await _reviewLoadList();
    if (typeof _reviewLoadPortfolio === 'function') await _reviewLoadPortfolio();
    if (typeof _reviewRefreshIntegrity === 'function') await _reviewRefreshIntegrity();
  } catch (e) {
    showToast('✗ 请求失败: ' + e.message, 'error');
  }
});

// R13: 一致性校验 — 前端分组聚合 vs 后端 FIFO 单源真值
async function _reviewRefreshIntegrity() {
  const badge = document.getElementById('integrity-badge');
  const fixBtn = document.getElementById('review-fix-dirty');
  if (!badge) return;
  badge.dataset.state = 'loading';
  badge.querySelector('.ib-text').textContent = '对账中…';
  try {
    const r = await _fetchWithTimeout('/api/review/integrity', { timeout: 5000 });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'API err');
    const d = j.data;
    if (d.dirty_codes && d.dirty_codes.length) {
      badge.dataset.state = 'dirty';
      badge.querySelector('.ib-text').textContent = `脏数据 ${d.dirty_codes.length} 项`;
      badge.title = d.recommendation + `\n脏: ${d.dirty_codes.map(x => x.name).join(', ')}`;
      if (fixBtn) fixBtn.hidden = false;
    } else if (!d.ok || Math.abs(d.discrepancy) > (d.threshold || 0.01)) {
      badge.dataset.state = 'mismatch';
      badge.querySelector('.ib-text').textContent =
        `差 ${d.discrepancy >= 0 ? '+' : ''}${d.discrepancy.toFixed(2)} 元`;
      badge.title = `前端分组: ${d.group_sum}\n后端 portfolio: ${d.portfolio_total}\n差异: ${d.discrepancy}`;
      if (fixBtn) fixBtn.hidden = true;
    } else {
      badge.dataset.state = 'ok';
      const total = d.portfolio_total;
      const sign = total > 0 ? '+' : '';
      badge.querySelector('.ib-text').textContent =
        d.n_groups ? `✓ ${sign}${total.toFixed(2)}` : '✓ 空仓';
      badge.title = `前端分组: ${d.group_sum}\n后端 portfolio: ${d.portfolio_total}\n已实现: ${d.portfolio_realized} · 浮: ${d.portfolio_unrealized}\n分组数: ${d.n_groups}`;
      if (fixBtn) fixBtn.hidden = true;
    }
  } catch (e) {
    badge.dataset.state = 'mismatch';
    badge.querySelector('.ib-text').textContent = '对账失败';
    badge.title = '拉取 /api/review/integrity 失败: ' + e.message;
  }
}

// 注: 主题切换的唯一 handler 在 app.js:4489 (2026-07-12 升级为增量 setOption 而非 dispose+recreate)。
//     旧的 handler 已移除 — 否则 addEventListener 叠加导致两次切换互相抵消,看上去点了没反应。
//     系统主题跟随也保留在 app.js。

// debounce: 窗口 resize 时最多 150ms 刷新一次所有 ECharts,避免高频触发卡顿
window.addEventListener('resize', (() => {
  let _t;
  return () => {
    clearTimeout(_t);
    _t = setTimeout(() => Object.values(echartsCharts).forEach(c => c && c.resize()), 150);
  };
})());

// 启动 — 隐藏标签页时不轮询(省带宽 + 服务端负载)
refreshTicker();
setInterval(() => { if (!document.hidden) refreshTicker(); }, 30 * 1000);

// ────────────────────────────────────────────
// TUNNEL · 外网访问状态 + 启动
// ────────────────────────────────────────────
async function refreshTunnel() {
  const dot = $('#tunnel-dot');
  const text = $('#tunnel-text');
  const urlRow = $('#tunnel-url-row');
  const urlEl = $('#tunnel-url');
  const btnLabel = $('#tunnel-btn-label');
  const status = $('#tunnel-status');
  if (!dot) return;
  try {
    const r = await api('/api/tunnel/status');
    if (!r) return;
    const state = r.state || (r.running ? 'online' : 'offline');

    // 2026-07-12: 显示 sentinel-based 后端的指示 (TG-bot / MQTT)
    const sentinels = r.sentinels || [];
    const sentinelHint = $('#tunnel-sentinel-hint');
    if (sentinelHint) {
      if (sentinels.length) {
        const lines = sentinels.map(s => {
          if (s.name.includes('Telegram')) {
            return '🤖 Telegram bot 也在跑 — 打开 @&lt;bot&gt; 发 `GET /api/health` 试试';
          }
          if (s.name.includes('MQTT')) {
            return '📡 MQTT 代理也在跑 — 用任意 MQTT iOS app 连接到 broker.hivemq.com:8883';
          }
          return `🛰 ${s.name}: ${s.info ? '就绪' : '初始化中'}`;
        });
        sentinelHint.innerHTML = lines.join('<br>');
        sentinelHint.removeAttribute('hidden');
      } else {
        sentinelHint.setAttribute('hidden', '');
      }
    }

    if (state === 'online' && r.url) {
      status.classList.add('online');
      status.classList.remove('offline', 'starting');
      // 2026-07-12: 显示用的是哪条机制 (用 emoji 让机制一目了然)
      const methodEmoji = methodEmojiFor(r.method);
      text.textContent = `公网已通 · ${methodEmoji} ${r.method || ''}`.trim();
      urlRow.hidden = false;
      urlEl.href = r.url;
      urlEl.textContent = r.url.replace(/^https?:\/\//, '').slice(0, 48);
      btnLabel.textContent = '重启';
      $('#tunnel-diag')?.setAttribute('hidden', '');
    } else if (state === 'starting') {
      status.classList.remove('online', 'offline');
      status.classList.add('starting');
      text.textContent = '启动中 (18 路 fallback)…';
      urlRow.hidden = true;
      btnLabel.textContent = '重启';
    } else {
      // offline → LAN fallback
      status.classList.remove('online', 'starting');
      status.classList.add('offline');
      text.textContent = ` 局域网 ${r.lan_ip}:${r.port}`;
      urlRow.hidden = true;
      btnLabel.textContent = '启动隧道';
    }
  } catch (e) {
    text.textContent = '状态读取失败';
  }
}

function methodEmojiFor(method) {
  if (!method) return '🌐';
  const m = method.toLowerCase();
  if (m.includes('tailscale'))    return '🔒';
  if (m.includes('zerotier'))     return '🔗';
  if (m.includes('telegram'))     return '🤖';
  if (m.includes('ntfy'))         return '🔔';
  if (m.includes('mqtt'))         return '📡';
  if (m.includes('cf-worker') || m.includes('cf'))     return '☁️';
  if (m.includes('paas'))         return '🐳';
  if (m.includes('trystero'))     return '🌊';
  if (m.includes('cloudflare'))   return '☁️';
  if (m.includes('ngrok'))        return '🪜';
  if (m.includes('localhost') || m.includes('lhr')) return '🌍';
  if (m.includes('serveo'))       return '🐡';
  return '🌐';
}
$('#tunnel-btn')?.addEventListener('click', async () => {
  const btn = $('#tunnel-btn');
  const btnLabel = $('#tunnel-btn-label');
  btn.disabled = true;

  // R-tunnel-2026-07-15: 如果当前是 online 状态→先停旧隧道再重开,确保全新链接
  const statusEl = $('#tunnel-status');
  const isRestart = statusEl && statusEl.classList.contains('online');
  if (isRestart) {
    btnLabel.textContent = '停止旧隧道…';
    try {
      await api('/api/tunnel/stop', { method: 'POST', timeout: 10_000 });
    } catch (e) {
      // stop 失败仍继续尝试 start (旧进程可能已被杀)
    }
  }

  btnLabel.textContent = '启动中…';
  // 即时显示诊断面板
  const diag = $('#tunnel-diag');
  const diagBody = $('#tunnel-diag-body');
  if (diag) diag.removeAttribute('hidden');
  if (diagBody) diagBody.innerHTML = '⏳ 后台启动 18 路 fallback (tailscale · tg-bot · ntfy · mqtt · cf-worker · paas · trystero · cloudflared quic/http2/ipv4 · ngrok · ...)...';
  try {
    const r = await api('/api/tunnel/start', { method: 'POST', timeout: 75_000 });
    const d = r.data || r;
    if (d && d.url) {
      if (diag) diag.setAttribute('hidden', '');
      await refreshTunnel();
      const tgMsg = d.tg_sent
        ? '✅ 已自动推到 Telegram'
        : ` TG 推送失败 (${d.tg_err || 'DNS 阻断'}), URL 仍可访问`;
      toast(`✓ 公网入口 ${d.url.slice(8, 36)}… · ${tgMsg}`, d.tg_sent ? 'success' : 'warn', 4500);
    } else {
      // 启动失败 — 给清晰的诊断 + LAN 兜底
      const err = (d && d.error) || r.error || '60s 内未拿到 URL';
      if (diagBody) {
        diagBody.innerHTML = `
          <p style="margin:.25rem 0">${escapeHtml(err)}</p>
          <p style="margin:.25rem 0">常见原因:</p>
          <ul style="margin:.25rem 0 .5rem 1.25rem">
            <li>当前网络 DNS 被劫持到 198.18.x <code>(~/.hermes/.env 配 VPN/自定义 DNS 可解)</code></li>
            <li>运营商/路由器拦截 trycloudflare.com / ngrok.com / serveo.net / lhr.life</li>
            <li>cloudflared 没装: <code>brew install cloudflared</code></li>
          </ul>
          <p style="margin:.25rem 0;color:var(--ink-2)">💡 此时仍可用上方 <b>局域网入口</b> (同一 WiFi 手机直接访问)</p>`;
      }
      toast(`启动失败: ${err}`, 'error', 6000);
    }
  } catch (e) {
    if (diagBody) diagBody.innerHTML = `⏱ 后端调用超时/失败: ${escapeHtml(e.message)}`;
    toast('启动失败: ' + e.message, 'error', 4500);
  } finally {
    btn.disabled = false;
    btnLabel.textContent = '重启';
  }
});

$('#tunnel-qr-btn')?.addEventListener('click', () => {
  const url = $('#tunnel-url').href;
  const qrWrap = $('#tunnel-qr-wrap');
  const qrImg = $('#tunnel-qr');
  if (!url || url === '#') return;
  if (qrWrap.hidden) {
    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&margin=2&data=${encodeURIComponent(url)}`;
    qrWrap.hidden = false;
  } else {
    qrWrap.hidden = true;
  }
});
$('#tunnel-tg-btn')?.addEventListener('click', async () => {
  const btn = $('#tunnel-tg-btn');
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '推送中…';
  try {
    const data = await api('/api/tunnel/push', { method: 'POST' });
    const target = data.target || data.url || data.lan;
    const label = data.url ? '公网' : 'LAN';

    if (data.tg_ok) {
      toast(`✅ 已推到 Telegram · ${label} ${target.slice(8, 32)}…`, 'success', 3200);
      return;
    }
    // TG 不可用 → fallback：剪贴板 + 原生分享面板（移动端）
    const shareText = data.text || `${label} 入口：${target}`;
    let copied = false;
    try {
      await navigator.clipboard.writeText(target);
      copied = true;
    } catch {}
    // 移动端优先走 navigator.share，会弹出系统分享面板（含 Telegram 选项）
    if (navigator.share) {
      try {
        await navigator.share({ title: '退学 v3 · 控制台', text: shareText, url: target });
        toast(` 已唤起系统分享（含 Telegram）`, 'success', 2800);
        return;
      } catch (e) { /* 用户取消 */ }
    }
    if (copied) {
      toast(` TG 推送失败（${data.tg_err || '网络'}），已复制到剪贴板 — 长按聊天框粘贴`, 'info', 4500);
    } else {
      // 兜底：弹个 prompt 让用户手动复制
      prompt('TG 推送失败，手动复制 URL：', target);
    }
  } catch (e) {
    toast('推送失败:' + e.message, 'error', 4000);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
});
refreshTunnel();
setInterval(() => { if (!document.hidden) refreshTunnel(); }, 10 * 1000);

// ────────────────────────────────────────────
// 复盘 · 统计 / 交易列表
// ────────────────────────────────────────────
function _reviewFmtNum(n, d = 2) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(d);
}

function _reviewPct(n) {
  if (n == null || isNaN(n)) return { text: '—', cls: 'cell-flat' };
  if (n > 0.5)  return { text: '+' + n.toFixed(1) + '%', cls: 'cell-up' };
  if (n < -0.5) return { text: n.toFixed(1) + '%',  cls: 'cell-down' };
  return { text: n.toFixed(1) + '%', cls: 'cell-flat' };
}

function _reviewConflictBadge(n) {
  if (n == null) return '<span class="conflict-badge low">—</span>';
  if (n === 0) return `<span class="conflict-badge low">0</span>`;
  if (n <= 2)  return `<span class="conflict-badge mid">${n}</span>`;
  return `<span class="conflict-badge high">${n}</span>`;
}

function _reviewRulePills(rules, kind) {
  if (!rules || rules.length === 0) {
    return `<span class="caption dim">—</span>`;
  }
  return rules.slice(0, 4).map(r => {
    const id = (r && r.id) ? r.id : '?';
    const text = (r && r.text) ? r.text : (typeof r === 'string' ? r : '');
    return `<span class="rule-pill ${kind}" title="${escapeHtml(text)}"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(text.slice(0, 18))}</span>`;
  }).join('');
}

function _reviewDirection(d) {
  if (d === 'buy')  return '<span class="cell-up">▲ 买</span>';
  if (d === 'sell') return '<span class="cell-down">▼ 卖</span>';
  return d;
}

function _reviewVerdict(v) {
  if (!v || v === '—') return '<span class="caption dim">—</span>';
  return `<span class="verdict-pill ${escapeHtml(v)}">${escapeHtml(v)}</span>`;
}

// 盈亏金额上色 (红涨绿跌 · A 股习惯)
function _reviewMoney(n) {
  if (n == null || isNaN(n)) return { text: '—', cls: 'cell-flat' };
  const v = Number(n);
  const s = (v > 0 ? '+' : '') + v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  if (v > 0.5)  return { text: s, cls: 'cell-up' };
  if (v < -0.5) return { text: s, cls: 'cell-down' };
  return { text: '0', cls: 'cell-flat' };
}

async function _reviewLoadList() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="11" class="dim center">加载中…</td></tr>';
  try {
    const r = await _fetchWithTimeout('/api/review/trades?limit=80&since_days=180');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    _reviewState.trades = j.data || [];
    _reviewRender();
    _reviewLoadStats();
    const ts = $('#review-ts');
    if (ts) ts.textContent = '已更新 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11" class="dim center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function _reviewStatusPill(live) {
  const s = (live && live.status) || '-';
  const map = {
    holding: { t: '持仓', c: 'st-hold' },
    open:    { t: '持仓', c: 'st-hold' },
    sold:    { t: '已卖', c: 'st-sold' },
    cleared: { t: '清仓', c: 'st-clear' },
  };
  const m = map[s];
  return m ? `<span class="pos-pill ${m.c}">${m.t}</span>` : '';
}

function _reviewRender() {
  const tbody = $('#review-tbody');
  if (!tbody) return;
  if (!_reviewState.trades.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="dim center">暂无交易 · 上面录入第一笔</td></tr>';
    return;
  }
  // ── 按 code 分组: 一只股票多笔 → 主行(持仓/汇总) + 可折叠明细 ──
  const groups = new Map();
  for (const t of _reviewState.trades) {
    const k = t.code;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(t);
  }
  const html = [];
  for (const [code, list] of groups) {
    list.sort((a, b) => (b.id || 0) - (a.id || 0)); // 新的在前
    const first  = list[0];
    const name   = list.find(x => x.name)?.name || first.name || '—';
    // 持仓状态:有任一 holding/open → 持仓;全清 → 清仓
    // 主行的「持仓 / 已清仓」状态 — 用 totalHeld 驱动,而不是 per-row live.status
    // (用户反馈: 后端 status 字段不可靠,totalHeld > 0 才是真正的持仓)
    const totalHeld = list.reduce((s, t) => s + ((t.live && t.live.held_shares) || 0), 0);
    const groupStatusPill = totalHeld > 0
      ? `<span class="pos-pill st-hold">持仓 <b>${totalHeld}</b> 股</span>`
      : `<span class="pos-pill st-clear">清仓</span>`;
    // GROUP 累计盈亏 — 跨所有行求和,不依赖某一行 live (用户要求与手算账单完全一致)
    //   卖单 row.live.cum_pnl = 该笔已实现盈亏
    //   买单 row.live.cum_pnl = 仍未卖出部分的浮动盈亏 (持仓归 0 时清 0)
    //   → 所有行累加 = 总盈亏 = 已实现 + 未实现
    const groupTodayPnl = list.reduce((s, t) => s + ((t.live && t.live.today_pnl) || 0), 0);
    const groupCumPnl   = list.reduce((s, t) => s + ((t.live && t.live.cum_pnl) || 0), 0);
    // 累计盈亏比 = 总盈亏 / 该股"净投入成本"(买入总额 - 卖出收入, 即仍在仓的真实成本)
    const groupCost = list.reduce((s, t) => {
      const v = (t.price || 0) * (t.shares || 0);
      return s + (t.direction === 'buy' ? v : -v);
    }, 0);
    const groupCumPct = groupCost > 0 ? (groupCumPnl / groupCost * 100) : 0;
    const today = _reviewMoney(groupTodayPnl);
    const cum = _reviewMoney(groupCumPnl);
    const cumPct = _reviewPct(groupCumPct);
    // 子行最新一笔的 live — 用于 PnL 子表
    const holding = list.find(t => {
      const s = (t.live && t.live.status) || '';
      return s === 'holding' || s === 'open';
    });
    const dateStr = (first.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
    const timeStr = (first.occurred_at || '').replace('T', ' ').slice(11, 16) || '—';
    const mistake = (holding || first).last_review?.main_mistake
                 || (holding || first).last_review?.mistake_pattern
                 || '';
    const mistakeHtml = mistake
      ? `<span class="main-mistake-pill" title="${escapeHtml(mistake)}">${escapeHtml(mistake)}</span>`
      : '<span class="caption dim">未复盘</span>';
    const reviewed = !!(holding || first).last_review;
    // 持仓/汇总统计 (显示给用户看"这是这只股票当前的总账")
    const totalHoldTxt = totalHeld > 0 ? `${totalHeld} 股` : '已清仓';
    // 主行「方向」列改为更实用的汇总: 持仓中显示持仓占比; 清仓显示买/卖笔数分布
    const buyCount = list.filter(t => t.direction === 'buy').length;
    const sellCount = list.filter(t => t.direction === 'sell').length;
    const groupSummary = totalHeld > 0
      ? `<span class="group-summary-hold"><b>${buyCount}</b><span class="dim">买</span> / <b>${sellCount}</b><span class="dim">卖</span></span>`
      : `<span class="group-summary-clear"><b>${buyCount}</b><span class="dim">买</span> / <b>${sellCount}</b><span class="dim">卖</span> · 已清</span>`;

    const expandable = list.length > 1;
    const gid = `grp-${code}-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
    // 主行: 价格/股数/PnL 取最新一笔 (避免误导)
    html.push(`
      <tr class="rv-group-hd ${expandable ? 'rv-expandable' : ''}" data-code="${escapeHtml(code)}" data-group="${gid}">
        <td class="rv-nm">
          ${expandable
            ? `<button type="button" class="rv-expand-btn" data-toggle="${gid}" aria-label="展开明细">▶</button>`
            : `<span class="rv-expand-spacer"></span>`}
          <a class="np-code" href="#" data-jump-code="${escapeHtml(code)}" title="点击进入个股详情">${escapeHtml(code)}</a>
          <span class="np-name" data-edit-name="1" data-trade-id="${first.id}" data-code="${escapeHtml(code)}" title="点击修改股票名">${escapeHtml(name)}</span>
          ${groupStatusPill}
          ${expandable ? `<span class="caption dim rv-n">${list.length} 笔明细</span>` : ''}
        </td>
        <td class="group-summary-cell">${groupSummary}</td>
        <td class="caption">${escapeHtml(dateStr || '—')}</td>
        <td class="cell-num">${_reviewFmtNum(first.price, 2)}</td>
        <td class="caption">${escapeHtml(timeStr)}</td>
        <td class="cell-num"><span title="持仓股数 (跨多笔汇总)">${totalHoldTxt}</span></td>
        <td class="cell-num ${today.cls}">${today.text}</td>
        <td class="cell-num ${cum.cls}">${cum.text}</td>
        <td class="cell-num ${cumPct.cls}">${cumPct.text}</td>
        <td>${mistakeHtml}</td>
        <td class="rv-act">
          <button class="btn-mini ${reviewed ? '' : 'primary'}" data-action="ai-review:${first.id}">${reviewed ? 'AI 复盘' : 'AI 复盘 ●'}</button>
          <button class="btn-mini danger" data-action="review-delete:${first.id}">删</button>
        </td>
      </tr>
    `);
    if (expandable) {
      // 折叠明细:子表显示每一笔独立行
      const childRows = list.map(t => {
        const live = t.live || {};
        const rev = t.last_review || {};
        const tToday = _reviewMoney(live.today_pnl);
        const tCum   = _reviewMoney(live.cum_pnl);
        const tCumPct = _reviewPct(live.cum_pnl_pct);
        const dStr = (t.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3');
        const tmStr = (t.occurred_at || '').replace('T', ' ').slice(11, 16) || '—';
        const tk = rev.main_mistake || rev.mistake_pattern || '';
        const tkHtml = tk
          ? `<span class="main-mistake-pill" title="${escapeHtml(tk)}">${escapeHtml(tk)}</span>`
          : '<span class="caption dim">未复盘</span>';
        const tRev = !!t.last_review;
        return `
          <tr class="rv-child" data-trade-id="${t.id}" data-code="${escapeHtml(code)}" data-trade-date="${escapeHtml(t.trade_date || '')}" data-group="${gid}">
            <td class="rv-nm rv-child-nm">
              <span class="rv-child-line"></span>
              <a class="np-code" href="#" data-jump-code="${escapeHtml(code)}" data-jump-date="${escapeHtml(t.trade_date || '')}" data-jump-time="${escapeHtml(t.occurred_at || '').slice(0,16)}" title="进入 ${escapeHtml(code)} 个股详情 · 跳到 ${escapeHtml(dStr || '此笔对应日')} 行情">${escapeHtml(code)}</a>
              <span class="caption dim" style="margin-left:.3rem">${escapeHtml(t.occurred_at || '').slice(0,16)}</span>
            </td>
            <td>${_reviewDirection(t.direction)}</td>
            <td class="caption">${escapeHtml(dStr || '—')}</td>
            <td class="cell-num">${_reviewFmtNum(t.price, 2)}</td>
            <td class="caption">${escapeHtml(tmStr)}</td>
            <td class="cell-num">${t.shares}</td>
            <td class="cell-num ${tToday.cls}">${tToday.text}</td>
            <td class="cell-num ${tCum.cls}">${tCum.text}</td>
            <td class="cell-num ${tCumPct.cls}">${tCumPct.text}</td>
            <td>${tkHtml}</td>
            <td class="rv-act">
              <button class="btn-mini ${tRev ? '' : 'primary'}" data-action="ai-review:${t.id}">${tRev ? 'AI' : 'AI●'}</button>
              <button class="btn-mini danger" data-action="review-delete:${t.id}">×</button>
            </td>
          </tr>
        `;
      }).join('');
      html.push(`
        <tr class="rv-child-wrap" data-group="${gid}" hidden>
          <td colspan="11" class="rv-child-cell">
            <table class="data-table review-table review-table-child">
              <tbody>${childRows}</tbody>
            </table>
          </td>
        </tr>
      `);
    }
  }
  tbody.innerHTML = html.join('');

  // ── 底部汇总 (所有可见交易 · 含子行 · 不含 000000 占位) ──
  // - 今日盈亏 = Σ today_pnl
  // - 累计盈亏 = 已实现 + 浮动 (cleared 不再重复计)
  // - 含手续费累计 = 累计 − 笔数 × 5 (用户口径:每笔买卖固定 5 元手续费)
  const tfoot = $('#review-tfoot');
  if (tfoot) {
    const PLACEHOLDER = new Set(['', '000000', '—']);
    const allTrades = (_reviewState.trades || []).filter(t => !PLACEHOLDER.has(String(t.code || '').trim()));
    if (allTrades.length) {
      let sToday = 0, sRealized = 0, sFloat = 0;
      let nHolding = 0, nSold = 0, nCleared = 0;
      for (const t of allTrades) {
        const live = t.live || {};
        const st = live.status || '-';
        const today = +(live.today_pnl || 0);
        const cum = +(live.cum_pnl || 0);
        if (st === 'holding' || st === 'open') {
          sToday += today;
          sFloat += cum;
          nHolding++;
        } else if (st === 'sold') {
          sToday += today;
          sRealized += cum;
          nSold++;
        } else if (st === 'cleared') {
          nCleared++;
        }
      }
      const sCum = sRealized + sFloat;
      const totalTrades = nHolding + nSold + nCleared;
      const feeTotal = totalTrades * 5;
      const sReal = sCum - feeTotal;
      const fmtMoney = (v) => {
        return (v > 0 ? '+' : (v < 0 ? '−' : '')) + '¥' + Math.abs(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      };
      const clsToday = sToday > 0.5 ? 'cell-up' : (sToday < -0.5 ? 'cell-down' : 'cell-flat');
      const clsCum   = sCum > 0.5 ? 'cell-up' : (sCum < -0.5 ? 'cell-down' : 'cell-flat');
      const clsReal  = sReal > 0.5 ? 'cell-up' : (sReal < -0.5 ? 'cell-down' : 'cell-flat');
      const todayEl  = $('#rv-sum-today');
      const cumEl    = $('#rv-sum-cum');
      const cumSubEl = $('#rv-sum-cum-sub');
      const realEl   = $('#rv-sum-real');
      const realSubEl = $('#rv-sum-real-sub');
      const metaEl   = $('#rv-sum-meta');
      if (todayEl)   { todayEl.textContent   = fmtMoney(sToday); todayEl.className   = `cell-num bold ${clsToday}`; }
      if (cumEl)     { cumEl.textContent     = fmtMoney(sCum);   cumEl.className     = `cell-num bold ${clsCum}`; }
      if (cumSubEl)  { cumSubEl.textContent  = `实 ${fmtMoney(sRealized)} · 浮 ${fmtMoney(sFloat)}`; }
      if (realEl)    { realEl.textContent    = fmtMoney(sReal);  realEl.className    = `cell-num bold ${clsReal}`; }
      if (realSubEl) { realSubEl.textContent = `含手续费 −¥${feeTotal.toLocaleString('zh-CN')} (${totalTrades} × ¥5)`; }
      if (metaEl)    { metaEl.textContent    = `共 ${totalTrades} 笔 · 持仓 ${nHolding} · 已卖 ${nSold} · 清仓 ${nCleared}`; }
      tfoot.hidden = false;
    } else {
      tfoot.hidden = true;
    }
  }

  // ── 代码点击 → 跳个股详情 (带 trade 日期上下文) ──
  // 主行:不传日期 → 取最新; 子行: 传 trade_date → 历史快照到那一天
  tbody.querySelectorAll('[data-jump-code]').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const code = a.dataset.jumpCode;
      if (!code) return;
      // YYYYMMDD → YYYY-MM-DD
      const rawDate = a.dataset.jumpDate || '';
      const date = rawDate && /^\d{8}$/.test(rawDate)
        ? `${rawDate.slice(0,4)}-${rawDate.slice(4,6)}-${rawDate.slice(6,8)}`
        : '';
      // 跳到 stock 视图并加载个股
      if (typeof showView === 'function') showView('stock');
      else window.location.hash = '#/stock';
      loadStockDetail(code, date);
    });
  });

  // ── 股票名点击 → 转 <input> 内联编辑 ──
  tbody.querySelectorAll('[data-edit-name]').forEach(span => {
    span.addEventListener('click', (e) => {
      e.stopPropagation();
      _inlineEditName(span);
    });
  });

  // 主行: 空白处点击 = 默认跳个股详情 (避开按钮 + 编辑中的 input)
  tbody.querySelectorAll('.rv-group-hd > td.rv-nm').forEach(td => {
    td.style.cursor = 'pointer';
    // R-a11y-013: 键盘可达
    td.setAttribute('tabindex', '0');
    td.setAttribute('role', 'button');
    td.setAttribute('aria-label', '跳到个股详情');
    const handler = (e) => {
      if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('button')) return;
      if (e.target.closest('input')) return;
      if (e.target.closest('[data-jump-code],[data-edit-name]')) return;
      e.preventDefault();
      const tr = td.closest('tr.rv-group-hd');
      const c = tr?.dataset.code;
      if (c) loadStockDetail(c);
    };
    td.addEventListener('click', handler);
    td.addEventListener('keydown', handler);
  });

  // 折叠按钮
  tbody.querySelectorAll('.rv-expand-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const gid = btn.dataset.toggle;
      const wrap = tbody.querySelector(`tr.rv-child-wrap[data-group="${gid}"]`);
      const expanded = wrap && !wrap.hidden;
      if (wrap) wrap.hidden = expanded;
      btn.textContent = expanded ? '▶' : '▼';
      btn.classList.toggle('open', !expanded);
      btn.setAttribute('aria-expanded', String(!expanded));
    });
  });
  // 2026-07-14: 用户反馈每只股票交易明细"不见了" — 实际是默认折叠,要点击 ▶ 才看
  // 解决:首次进入页面默认全部展开,信息密度优先(沿用 feedback_more_info_visible 规则)
  // localStorage 记忆用户后续手动折叠的组,刷新不丢
  const collapsedKey = 'review_collapsed_groups';
  let collapsed = new Set();
  try { collapsed = new Set(JSON.parse(localStorage.getItem(collapsedKey) || '[]')); } catch {}
  tbody.querySelectorAll('tr.rv-child-wrap[data-group]').forEach(wrap => {
    const gid = wrap.dataset.group;
    const btn = tbody.querySelector(`.rv-expand-btn[data-toggle="${gid}"]`);
    if (!btn) return;
    if (!collapsed.has(gid)) {
      wrap.hidden = false;
      btn.textContent = '▼';
      btn.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
    }
    // 单击 ▶ 也会同步写 collapsed 集合,刷新保留
    btn.addEventListener('click', () => {
      const isOpen = !wrap.hidden;
      if (isOpen) collapsed.add(gid);
      else collapsed.delete(gid);
      try { localStorage.setItem(collapsedKey, JSON.stringify(Array.from(collapsed))); } catch {}
    }, true);  // capture 阶段优先于主 click,避免时序冲突
  });
  // 子行空白处点击 = 跳个股 (避开按钮 + 编辑中 input) — 带该笔 trade_date
  tbody.querySelectorAll('tr.rv-child > td.rv-child-nm').forEach(td => {
    td.style.cursor = 'pointer';
    td.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      if (e.target.closest('input')) return;
      if (e.target.closest('[data-jump-code]')) return;
      const tr = td.closest('tr.rv-child');
      const c = tr?.dataset.code;
      const td2 = tr?.dataset.tradeDate || '';
      const date = td2 && /^\d{8}$/.test(td2)
        ? `${td2.slice(0,4)}-${td2.slice(4,6)}-${td2.slice(6,8)}`
        : '';
      if (c) loadStockDetail(c, date);
    });
  });
}

// ── 内联编辑股票名 — 点击 → 输入框 → 失焦 / Enter 保存 ──
async function _inlineEditName(span) {
  if (span.querySelector('input')) return;            // 已经在编辑
  const tradeId = span.dataset.tradeId;
  const code = span.dataset.code;
  const orig = span.textContent.trim();
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.value = orig;
  inp.className = 'np-name-input';
  inp.maxLength = 32;
  inp.style.cssText = 'width:11em;font:inherit;padding:2px 6px;border:1px solid var(--accent);border-radius:4px;background:#fff;color:var(--ink-1)';
  span.textContent = '';
  span.appendChild(inp);
  inp.focus();
  inp.select();
  let committed = false;
  const finish = async (save) => {
    if (committed) return;
    committed = true;
    const v = (inp.value || '').trim();
    if (!save || v === orig || !v) {
      // 取消 / 无变化
      span.textContent = orig;
      return;
    }
    span.textContent = '…';           // saving 状态
    try {
      const r = await fetch(`/api/review/trades/${encodeURIComponent(tradeId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ name: v, code }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || 'HTTP ' + r.status);
      span.textContent = v;
      // 更新本地 state — 同时刷新 _reviewState.trades
      const tr = _reviewState.trades.find(x => x.id === tradeId);
      if (tr) tr.name = v;
      // 顶部资金栏 / 持仓标签可能也要刷
      if (typeof _reviewLoadPortfolio === 'function') {
        try { await _reviewLoadPortfolio(); } catch {}
      }
    } catch (e) {
      console.warn('[inline name edit] failed', e);
      span.textContent = orig;
      span.title = '保存失败: ' + e.message;
    }
  };
  inp.addEventListener('blur', () => finish(true));
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); inp.value = orig; inp.blur(); }
  });
}

// ── 顶部资金栏 ──
async function _reviewLoadPortfolio() {
  const bar = $('#review-capbar');
  if (!bar) return;
  try {
    const r = await _fetchWithTimeout('/api/review/portfolio');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    _renderCapbar(j.data || {});
    const ts = $('#pf-ts');
    if (ts) ts.textContent = '实时 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }) +
      ` · 报价 ${j.data?.quotes_ok ?? 0}/${j.data?.codes ?? 0}`;
  } catch (e) {
    bar.innerHTML = `<span class="dim">资金栏加载失败: ${escapeHtml(e.message)}</span>`;
  }
}

function _capTile(lbl, valObj, sub) {
  const cls = valObj.cls || '';
  return `<div class="cap-tile">
    <div class="cap-lbl">${lbl}</div>
    <div class="cap-val ${cls}">${valObj.text}</div>
    ${sub ? `<div class="cap-sub ${sub.cls || ''}">${sub.text}</div>` : ''}
  </div>`;
}

function _renderCapbar(d) {
  const bar = $('#review-capbar');
  if (!bar) return;
  const yuan = n => (n == null ? '—' : '¥' + Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 }));
  const total = { text: d.total_capital ? yuan(d.total_capital) : '未设置', cls: '' };
  const posText = d.position_value != null ? yuan(d.position_value) : '—';
  const posRatio = d.position_ratio != null
    ? { text: d.position_ratio.toFixed(1) + '% 仓 · ' + d.position_count + ' 只', cls: 'dim' }
    : { text: (d.position_count || 0) + ' 只 · 设总资金看仓位%', cls: 'dim' };
  const today = _reviewMoney(d.today_pnl);
  const todaySub = d.today_pnl_pct != null ? _reviewPct(d.today_pnl_pct) : null;
  const total_pnl = _reviewMoney(d.total_pnl);
  const totalSub = {
    text: `浮 ${_reviewMoney(d.unrealized_pnl).text} · 实 ${_reviewMoney(d.realized_pnl).text}` +
      (d.codes ? ` · ${d.trade_count || d.codes} 笔` : ''),
    cls: 'dim',
  };
  // 含手续费总盈亏 = 总盈亏 − 笔数 × 5 (用户口径)
  const tCount = d.trade_count || 0;
  const feeAdj = (d.total_pnl != null && tCount > 0) ? round2(d.total_pnl - tCount * 5) : null;
  const feeAdjObj = feeAdj != null
    ? _reviewMoney(feeAdj)
    : { text: '—', cls: '' };
  const feeSub = tCount > 0
    ? { text: `−¥${(tCount * 5).toLocaleString('zh-CN')} (${tCount} × ¥5)`, cls: 'dim' }
    : { text: '无交易 · 0', cls: 'dim' };
  const ratio = d.total_pnl_pct != null
    ? _reviewPct(d.total_pnl_pct)
    : { text: '设总资金', cls: 'cell-flat' };
  bar.innerHTML =
    _capTile('总资金 (满仓)', total, { text: d.cash != null ? '可用 ' + yuan(d.cash) : '', cls: 'dim' }) +
    _capTile('仓位', { text: posText, cls: '' }, posRatio) +
    _capTile('今日盈亏', today, todaySub) +
    _capTile('总盈亏', total_pnl, totalSub) +
    _capTile('含手续费', feeAdjObj, feeSub) +
    _capTile('盈亏比', ratio, { text: '总盈亏 / 总资金', cls: 'dim' });
  _renderPositions(d.positions || []);
}

function round2(v) { return Math.round((+v || 0) * 100) / 100; }

function _renderPositions(positions) {
  const box = $('#review-positions');
  if (!box) return;
  if (!positions.length) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="pos-title">当前持仓 · 实时</div>' +
    '<div class="pos-grid">' + positions.map(p => {
      const up = _reviewPct(p.unrealized_pct);
      const today = _reviewPct(p.prev_close ? (p.price - p.prev_close) / p.prev_close * 100 : null);
      const code = escapeHtml(p.code);
      return `<div class="pos-card" data-action="open-stock:${code}" style="cursor:pointer">
        <div class="pos-hd"><code>${code}</code> <span>${escapeHtml(p.name || '')}</span>
          <button class="pos-del" title="删除该股全部交易(不可逆)" data-action="review-delete-position:${encodeURIComponent(code)}|${encodeURIComponent(p.name || '')}|${p.shares}">×</button>
        </div>
        <div class="pos-row"><span class="dim">现价</span><b class="${today.cls}">${_reviewFmtNum(p.price, 2)}</b> <span class="${today.cls}">${today.text}</span></div>
        <div class="pos-row"><span class="dim">${p.shares}股 @ ${_reviewFmtNum(p.avg_cost, 2)}</span></div>
        <div class="pos-row"><span class="dim">浮盈</span><b class="${up.cls}">${_reviewMoney(p.unrealized).text}</b> <span class="${up.cls}">${up.text}</span></div>
      </div>`;
    }).join('') + '</div>';
}

async function _reviewDeletePosition(code, name, shares) {
  if (!confirm(`确定删除 ${code} ${name || ''} 的全部交易记录吗?\n(共 ${shares || '?'} 股持仓)\n此操作不可逆。`)) return;
  try {
    const r = await _fetchWithTimeout('/api/review/positions/' + encodeURIComponent(code), { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
      showToast(`✓ 已清空 ${code} · 删除 ${j.data.deleted} 笔`, 'success');
      _reviewLoadPortfolio();
      _reviewLoadList();
    } else {
      showToast('删除失败: ' + (j.error || ''), 'error');
    }
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error');
  }
}

// ── 总资金设置 ──
async function _reviewLoadSettings() {
  try {
    const r = await _fetchWithTimeout('/api/review/settings');
    const j = await r.json();
    const inp = $('#cap-total');
    if (inp && j.data?.total_capital) inp.value = j.data.total_capital;
  } catch (e) { /* ignore */ }
}

function _reviewBindCapital() {
  const btn = $('#cap-save');
  if (!btn || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', async () => {
    const v = parseFloat($('#cap-total').value);
    if (!v || v <= 0) { showToast('请填一个正数总资金', 'error'); return; }
    btn.disabled = true;
    try {
      const r = await _fetchWithTimeout('/api/review/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ total_capital: v }),
      });
      const j = await r.json();
      if (j.ok) { showToast('✓ 总资金已保存', 'success'); _reviewLoadPortfolio(); }
      else showToast('保存失败: ' + (j.error || ''), 'error');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
    finally { btn.disabled = false; }
  });
}

// ── 录入表单折叠展开 (R50-FIX: 用户先看表, 再记一笔) ──
function _reviewBindToggle() {
  const btn = $('#rf-toggle-btn');
  const wrap = $('#review-form-wrap');
  if (!btn || !wrap || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', () => {
    const open = !wrap.hidden;
    wrap.hidden = open;
    btn.textContent = open ? '+ 记一笔' : '× 收起';
    if (!open) {
      // 展开时滚到表单,便于操作
      setTimeout(() => wrap.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
      setTimeout(() => $('#rf-code')?.focus(), 280);
    }
  });
}

// ── 买入时刻点推算 ──
function _reviewBindInfer() {
  const btn = $('#rf-infer');
  if (!btn || btn._bound) return;
  btn._bound = true;
  btn.addEventListener('click', async () => {
    const code = ($('#rf-code').value || '').trim();
    const price = parseFloat($('#rf-price').value);
    const dateRaw = ($('#rf-date').value || '').replace(/-/g, '');
    const hint = $('#rf-infer-hint');
    if (!code) { showToast('先填股票代码', 'error'); return; }
    btn.disabled = true; if (hint) hint.textContent = '分时反推中…';
    try {
      let url = '/api/review/time_points?code=' + encodeURIComponent(code);
      if (dateRaw) url += '&date=' + dateRaw;
      if (price) url += '&price=' + price;
      const r = await _fetchWithTimeout(url);
      const j = await r.json();
      const d = j.data || {};
      const sel = $('#rf-time');
      sel.innerHTML = '<option value="">自动/手填</option>';
      if (d.available && d.points && d.points.length) {
        d.points.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.time;
          const tag = p.match === 'exact' ? ' ✓命中' : (p.match === 'near' ? ' ~接近' : '');
          opt.textContent = `${p.time} @ ${p.close}${tag}`;
          sel.appendChild(opt);
        });
        const firstMatch = d.points.find(p => p.match === 'exact') || d.points[0];
        if (firstMatch) sel.value = firstMatch.time;
        if (hint) hint.textContent = d.reason || `${d.points.length} 个候选时刻`;
      } else {
        if (hint) hint.textContent = d.reason || '无可用分时,请手动填时间';
      }
    } catch (e) {
      if (hint) hint.textContent = '推算失败: ' + e.message;
    } finally { btn.disabled = false; }
  });
}

async function _reviewRefreshFlows() {
  // 兼容旧调用:直接走 portfolio 实时价刷新
  await _reviewLoadPortfolio();
}

// B-20: 已合并到 _reviewOnViewEnter 内的 capTimer (15s),
// 此函数保留兼容,但不再起新定时器避免双倍请求。
function _reviewStartFlowsPolling() {
  // no-op: capTimer 在 _reviewOnViewEnter 内启动, 周期 15s,
  // 同时覆写 flowsTimer 字段以便 _reviewOnViewLeave 也能 clearInterval
  if (_reviewState.flowsTimer) clearInterval(_reviewState.flowsTimer);
  _reviewState.flowsTimer = null;
}

// ── AI 复盘子页面 · 进入入口 ──
var _aiReviewState = {
  tradeId: null,
  trade: null,
  review: null,
  running: false,
};

function openAiReview(tradeId) {
  // 在主表里找这笔交易
  const t = (_reviewState.trades || []).find(t => t.id === tradeId) || null;
  const hasReview = !!(t && t.last_review);
  _aiReviewState.tradeId = tradeId;
  _aiReviewState.trade = t;
  _aiReviewState.review = t?.last_review || null;
  if (hasReview) {
    // 已有复盘 → 跳面板看详细结果(原行为)
    showView('ai-review');
    return;
  }
  // 未复盘 → 后台跑,不要跳转页面
  _reviewRunInBackground(tradeId, t);
}

// R-bug-2 + R-fix-2026-07-14: 后台跑 AI 复盘 — POST 立刻返 202 不阻塞前端;UI 立刻解锁,后台跑完只 patch 单行 + toast,失败/超时也不影响主表。
async function _reviewRunInBackground(tradeId, t) {
  if (!tradeId) return;
  // 视觉反馈:把当前所有指向这 tradeId 的 AI 复盘按钮打上"⏳"状态
  const btns = document.querySelectorAll(`button[data-action="ai-review:${tradeId}"], button[data-action="review-run:${tradeId}"]`);
  btns.forEach(b => { b.dataset._oldText = b.textContent; b.disabled = true; b.textContent = '⏳'; });
  showToast(`🌀 AI 复盘 #${tradeId} 已排队 · 约 30-60s 后完成`, 'info', 2500);
  const t0 = Date.now();
  try {
    const r = await _fetchWithTimeout(`/api/review/trades/${tradeId}/review?force=true`, { method: 'POST', timeout: 8000 });
    const j = await r.json();
    if (!j.ok) {
      btns.forEach(b => { b.disabled = false; b.textContent = b.dataset._oldText || 'AI 复盘'; });
      showToast(`✗ #${tradeId} 排队失败: ${j.error || '未知错误'}`, 'error', 4000);
      return;
    }
    if (j.data && !j.data.queued && j.data.verdict) {
      _aiReviewState.review = j.data;
      _aiReviewState.trade = t;
      _aiReviewState.tradeId = tradeId;
      btns.forEach(b => { b.disabled = false; b.textContent = '✓ ' + (j.data.verdict || '已复盘'); });
      await _reviewLoadList();
      return;
    }
    btns.forEach(b => { b.disabled = false; b.textContent = '⏳ 后台'; });
    _reviewPollOne(tradeId, btns, t0);
  } catch (e) {
    btns.forEach(b => { b.disabled = false; b.textContent = '⏳ 后台'; });
    _reviewPollOne(tradeId, btns, t0);
    if (!String(e.message || '').includes('abort')) {
      console.warn('AI review POST error (will poll anyway):', e);
    }
  }
}

// 轮询单笔复盘状态:每 4s 一次,最多 90s;完成只 patch 单行 + toast,不动主表
function _reviewPollOne(tradeId, btns, t0) {
  const startedAt = t0 || Date.now();
  const deadline = startedAt + 90000;
  const tick = async () => {
    if (Date.now() > deadline) {
      btns.forEach(b => { b.disabled = false; b.textContent = b.dataset._oldText || 'AI 复盘'; });
      showToast(`⏰ #${tradeId} 复盘超时未完成(>90s)`, 'warn', 4000);
      return;
    }
    try {
      const r = await _fetchWithTimeout(`/api/review/trades/${tradeId}/status`, { timeout: 5000 });
      const j = await r.json();
      if (j.ok && j.data && j.data.has_review && (j.data.ts_created * 1000) >= startedAt - 1000) {
        btns.forEach(b => { b.disabled = false; b.textContent = '✓ ' + (j.data.verdict || '已复盘'); });
        showToast(`✓ #${tradeId} 复盘完成 · ${j.data.verdict || ''} ${j.data.score || ''}分`, 'success', 3500);
        _reviewPatchRow(tradeId, j.data);
        return;
      }
    } catch (e) {}
    setTimeout(tick, 4000);
  };
  setTimeout(tick, 3000);
}

function _reviewPatchRow(tradeId, statusData) {
  if (!_reviewState || !Array.isArray(_reviewState.trades)) return;
  for (const t of _reviewState.trades) {
    if (t.id === tradeId) {
      t.last_review = t.last_review || {};
      t.last_review.verdict = statusData.verdict || t.last_review.verdict || '';
      t.last_review.score = statusData.score || t.last_review.score || 0;
      break;
    }
  }
  const row = document.querySelector(`tr[data-trade-id="${tradeId}"]`);
  if (row) {
    const btn = row.querySelector(`button[data-action="ai-review:${tradeId}"], button[data-action="review-run:${tradeId}"]`);
    if (btn) { btn.disabled = false; btn.textContent = '✓ ' + (statusData.verdict || '已复盘'); }
  }
}

async function _airvOnViewEnter() {
  const view = document.querySelector('.view-ai-review');
  if (!view || view.hidden) return;
  const tid = _aiReviewState.tradeId;
  if (!tid) { _renderAiReviewEmpty(); return; }
  // 标题
  const t = _aiReviewState.trade;
  if (t) {
    $('#airv-title').textContent = `${t.direction === 'buy' ? '买' : '卖'} ${t.name || t.code} @ ${_reviewFmtNum(t.price, 2)}`;
    const sub = `${t.code} · ${(t.trade_date || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')} ${(t.occurred_at || '').slice(11, 16) || ''} · ${t.shares} 股`;
    $('#airv-sub').textContent = sub;
  }
  // 后退按钮绑定 (幂等)
  const back = $('#airv-back');
  if (back && !back._bound) {
    back._bound = true;
    back.addEventListener('click', () => showView('review'));
  }
  // 先拉一次历史 review 列表(取最新一条直接显示,免 LLM)
  try {
    const r = await _fetchWithTimeout('/api/review/trades/' + tid + '/reviews');
    const j = await r.json();
    const reviews = (j.data && j.data.reviews) || [];
    if (reviews.length) {
      _aiReviewState.review = reviews[0];  // 最新一条
      _renderAiReview(reviews[0]);
      return;
    }
  } catch (e) { /* ignore */ }
  _renderAiReviewPending();
  await _airvRunLLM(false);
}

async function _airvRunLLM(force = true) {
  const tid = _aiReviewState.tradeId;
  if (!tid || _aiReviewState.running) return;
  // 非强制重算 → 走 SSE 流,实时显示阶段进度(拉盘面→AI→铁律→完成)
  if (!force && typeof EventSource !== 'undefined') {
    return _airvRunViaSSE(tid);
  }
  _aiReviewState.running = true;
  const hint = $('#airv-status');
  if (hint) hint.textContent = force ? '🌀 AI 强制重算中…约需 1 分钟' : '🌀 AI 复盘中…约需 1 分钟';
  try {
    const r = await _fetchWithTimeout(`/api/review/trades/${tid}/review?force=${force}`, { method: 'POST' });
    const j = await r.json();
    if (j.ok && j.data) {
      _aiReviewState.review = j.data;
      _renderAiReview(j.data);
      if (hint) hint.textContent = '✓ 已完成 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
      _reviewLoadList();  // 同步主表 reviewed 标记
    } else {
      if (hint) hint.textContent = '✗ 复盘失败: ' + (j.error || '未知错误');
    }
  } catch (e) {
    if (hint) hint.textContent = '✗ 复盘超时/失败: ' + e.message;
  } finally {
    _aiReviewState.running = false;
  }
}

// R-ui-021: SSE 流式复盘 — 实时推送阶段/铁律,完成后渲染 + 同步主表
function _airvRunViaSSE(tid) {
  return new Promise((resolve) => {
    _aiReviewState.running = true;
    const hint = $('#airv-status');
    const es = new EventSource(`/api/stream/review/${tid}`);
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      try { es.close(); } catch {}
      _aiReviewState.running = false;
      resolve();
    };
    es.addEventListener('progress', (ev) => {
      try { const d = JSON.parse(ev.data); if (hint) hint.textContent = `🌀 ${d.msg || d.stage || 'AI 复盘中…'}`; } catch {}
    });
    es.addEventListener('rule_failed', (ev) => {
      if (hint) { const cur = hint.textContent || ''; hint.textContent = cur.includes('铁律') ? cur : '🔍 铁律分析中…'; }
    });
    es.addEventListener('done', (ev) => {
      try {
        const data = JSON.parse(ev.data);
        _aiReviewState.review = data;
        _renderAiReview(data);
        if (hint) hint.textContent = '✓ 已完成 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
        _reviewLoadList();
      } catch (e) {
        if (hint) hint.textContent = '✗ 解析失败: ' + e.message;
      }
      finish();
    });
    es.addEventListener('error', (ev) => {
      // SSE 断连或后端 error 事件 → 回退到 POST(仅一次)
      if (settled) return;
      try { es.close(); } catch {}
      _aiReviewState.running = false;
      settled = true;
      _airvRunLLM(true).then(resolve);
    });
  });
}

function _renderAiReviewEmpty() {
  $('#airv-title').textContent = 'AI 复盘';
  $('#airv-sub').textContent = '先回到复盘主页,选一笔交易点 AI 复盘';
  $('#airv-body').innerHTML = '<article class="card mt-16"><div class="dim center" style="padding:2rem">还没有选中交易</div></article>';
}

function _renderAiReviewPending() {
  $('#airv-body').innerHTML = '<article class="card mt-16"><div class="dim center" style="padding:2rem">⏳ AI 复盘数据收集中…<div class="caption dim mt-8">限价/分时/K线/席位/新闻,全部拉完后才出结论</div></div></article>';
}

function _airvClass(verdict) {
  if (verdict === '优秀') return 'v-good';
  if (verdict === '及格') return 'v-pass';
  if (verdict === '失误') return 'v-bad';
  if (verdict === '严重失误') return 'v-worse';
  return '';
}

function _renderAiReview(rev) {
  const body = $('#airv-body');
  if (!body) return;
  const summary = rev.summary || '';
  const advice = rev.ai_advice || '';
  const recap = rev.limit_up_recap || '';
  const mainM = rev.main_mistake || rev.mistake_pattern || '';
  const verdict = rev.verdict || '—';
  const score = rev.score || 0;
  const risks = rev.key_risks || [];
  const rulesP = rev.rules_passed || [];
  const rulesF = rev.rules_failed || [];
  const improv = rev.improvement || '';
  const ts = rev.ts_created ? new Date(rev.ts_created * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
  const cls = _airvClass(verdict);
  body.innerHTML = `
    <article class="card mt-16 airv-card">
      <div class="card-eyebrow flex-between">
        <span>VERDICT · AI 评分</span>
        <span class="caption dim">${escapeHtml(ts)}</span>
      </div>
      <div class="airv-head">
        <div class="airv-verdict ${cls}">${escapeHtml(verdict)}</div>
        <div class="airv-score">
          <div class="num">${score}</div><div class="cap">/ 100</div>
        </div>
        ${mainM ? `<div class="main-mistake-pill big" title="${escapeHtml(mainM)}">${escapeHtml(mainM)}</div>` : ''}
      </div>
    </article>

    ${recap ? `
    <article class="card mt-12">
      <div class="card-eyebrow">PART 1 · 当日涨停全景回溯</div>
      <div class="airv-md">${escapeHtml(recap)}</div>
    </article>` : ''}

    <article class="card mt-12">
      <div class="card-eyebrow">PART 2 · 本次操作 AI 复盘</div>
      <div class="airv-md">${escapeHtml(summary)}</div>
      ${advice ? `<div class="airv-advice"><span class="cap">AI 建议</span><div>${escapeHtml(advice)}</div></div>` : ''}
    </article>

    <article class="card mt-12">
      <div class="card-eyebrow">铁律对照</div>
      <div class="airv-rules">
        <div>
          <div class="cap dim">通过 (${rulesP.length})</div>
          ${rulesP.length ? rulesP.map(r => {
            const id = (r && r.id) || '?';
            const tx = (r && r.text) || (typeof r === 'string' ? r : '');
            return `<span class="rule-pill pass"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(tx).slice(0, 60)}</span>`;
          }).join('') : '<span class="caption dim">无</span>'}
        </div>
        <div>
          <div class="cap dim">违反 (${rulesF.length})</div>
          ${rulesF.length ? rulesF.map(r => {
            const id = (r && r.id) || '?';
            const tx = (r && r.text) || (typeof r === 'string' ? r : '');
            return `<span class="rule-pill fail"><span class="rid">${escapeHtml(id)}</span>${escapeHtml(tx).slice(0, 60)}</span>`;
          }).join('') : '<span class="caption dim">无</span>'}
        </div>
      </div>
    </article>

    ${(risks.length || improv) ? `
    <article class="card mt-12">
      ${risks.length ? `<div class="airv-risks"><div class="cap dim">关键风险</div>${risks.map(k => `<div class="risk-line"> ${escapeHtml(k)}</div>`).join('')}</div>` : ''}
      ${improv ? `<div class="airv-improv"><div class="cap dim">下一步改进</div><div class="airv-md">${escapeHtml(improv)}</div></div>` : ''}
    </article>` : ''}

    <article class="card mt-12">
      <div class="card-eyebrow flex-between">
        <span>操作</span>
        <span id="airv-status" class="caption dim">${escapeHtml(ts)} · 模型 ${escapeHtml(rev.model || 'MiniMax-M3')}</span>
      </div>
      <div class="flex-row gap-8 mt-8">
        <button class="btn btn-mini primary" data-action="airv-rerun">↻ 强制重跑</button>
        <button class="btn btn-mini" data-action="show-view:review">‹ 返回复盘</button>
      </div>
    </article>
  `;
}

// R-ui-011: 单一 toast 路径 — showToast 直通 toast() 队列, 不再 remove+create 闪屏
// 之前: 复盘每笔完成 → remove + createElement(z-index 9999) 一次, 14 笔就是 14 次闪
// 现在: 复用 drainToast 队列 + 同 kind 相邻去重, 自动节流
function showToast(msg, type) {
  const kind = type === 'success' ? 'success' : type === 'error' ? 'error' : 'info';
  if (typeof toast === 'function') {
    return toast(msg, kind, type === 'error' ? 4000 : 2400);
  }
  // 兜底 (toast 未定义时): 保留老 inline 行为
  if (window.__toastBox) window.__toastBox.remove();
  const colors = { info: '#d4a056', success: '#4fb074', error: '#d97a6c' };
  const box = document.createElement('div');
  box.textContent = msg;
  box.style.cssText = `
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 12px 24px; background: rgba(20,18,14,0.95); color: ${colors[kind] || colors.info};
    border: 1px solid ${colors[kind] || colors.info}; border-radius: 8px;
    font-size: 14px; z-index: 9999; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    max-width: 80vw;
  `;
  document.body.appendChild(box);
  window.__toastBox = box;
  setTimeout(() => { if (box.parentNode) box.remove(); }, 4000);
}

async function _reviewDelete(tradeId) {
  if (!confirm('确认删除这笔交易及其复盘?')) return;
  try {
    const r = await _fetchWithTimeout('/api/review/trades/' + tradeId, { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) _reviewLoadList();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

// R-relax-2026-07-14: 复盘页 next_picks 放宽档状态 — 默认 0 严格,用户点按钮才改
var _RELAX_LABELS = {
  0: { label: '严格', caps: '5 只', desc: '默认 7 条规则全开' },
  1: { label: '放宽', caps: '15 只', desc: '多送候选,screen 拉空时全 A 兜底' },
  2: { label: '极宽松', caps: '50 只', desc: '接近全 A 流动性筛选,适合 market 安静 / 数据源挂时' },
};
var _reviewRelaxLevel = 0;

function _reviewUpdateRelaxInfo() {
  const info = document.getElementById('review-next-relax-info');
  if (!info) return;
  const cfg = _RELAX_LABELS[_reviewRelaxLevel] || _RELAX_LABELS[0];
  info.innerHTML = `当前筛选档:<b>${escapeHtml(cfg.label)}</b> (${escapeHtml(cfg.caps)}) — ${escapeHtml(cfg.desc)}`;
}

var _reviewNextPickToken = 0;

function _reviewRenderPicks(d, listEl, metaEl) {
  if (!d.picks || !d.picks.length) return false;
  if (metaEl) {
    if (d.user_patterns && d.user_patterns.length) {
      metaEl.innerHTML = ` <span style="color:var(--accent)">你的常见错模式:</span> ${d.user_patterns.slice(0, 4).map(p => `<span class="rule-pill fail">${escapeHtml(p)}</span>`).join(' ')}`;
    } else {
      metaEl.textContent = '✅ 暂无历史错模式(继续积累交易后会有更精准预警)';
    }
  }
  listEl.innerHTML = d.picks.map((p, i) => {
    const v = p.ai_verdict || '观望';
    const score = p.ai_score != null ? p.ai_score : '?';
    const risk = (p.risk_warnings || []).map(r => `<span class="rule-pill warn">${escapeHtml(r)}</span>`).join(' ');
    return `<li>
      <span class="np-idx">${i+1}</span>
      <code class="np-code" data-action="open-stock:${p.code}" style="cursor:pointer">${escapeHtml(p.code)}</code>
      <span class="np-name">${escapeHtml(p.name || '—')}</span>
      <span class="np-sector caption dim">${escapeHtml(p.sector || '')}</span>
      <span class="verdict-pill ${escapeHtml(v)}">${escapeHtml(v)} ${score}/100</span>
      <span class="np-risk">${risk}</span>
    </li>`;
  }).join('');
  return true;
}

async function _reviewLoadNextPicks(target = 'review') {
  const listEl = $(`#${target}-next-pick-list`);
  const metaEl = $(`#${target}-next-meta`);
  if (!listEl) return;
  const myToken = ++_reviewNextPickToken;   // 防并发:切档/重复点只认最后一次
  const relax = _reviewRelaxLevel;
  listEl.innerHTML = '<li class="caption dim">后端筛选中 (screen + 错模式预警)…</li>';
  if (metaEl) metaEl.textContent = '—';
  _reviewUpdateRelaxInfo();
  const emptyTip = () => {
    if (myToken !== _reviewNextPickToken) return;
    listEl.innerHTML = `<li class="caption dim">${relax === 0 ? '无候选 · 试试点 [放宽] 拉到 15 只' : '无候选 · 数据源未通 / market 太安静'}</li>`;
    if (metaEl) metaEl.textContent = '';
  };
  try {
    // 1) force=1 触发后台重算,秒回 (computing 或 陈旧缓存)
    let r = await _fetchWithTimeout(`/api/review/next_picks?relax=${relax}&force=1`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    let j = await r.json();
    if (myToken !== _reviewNextPickToken) return;   // 期间用户又切了档
    if (_reviewRenderPicks(j.data || {}, listEl, metaEl)) return;
    // 2) 后台在算 → 轮询 force=0 读缓存,最多 ~24s
    const deadline = Date.now() + 24000;
    while (Date.now() < deadline) {
      await new Promise(res => setTimeout(res, 2500));
      if (myToken !== _reviewNextPickToken) return;
      r = await _fetchWithTimeout(`/api/review/next_picks?relax=${relax}`);
      if (!r.ok) continue;
      j = await r.json();
      if (myToken !== _reviewNextPickToken) return;
      const meta = j.meta || {};
      if (_reviewRenderPicks(j.data || {}, listEl, metaEl)) return;
      if (!meta.computing && !meta.in_flight && !meta.refreshing) { emptyTip(); return; }   // 算完了仍空
    }
    emptyTip();   // 超时兜底
  } catch (e) {
    if (myToken !== _reviewNextPickToken) return;
    listEl.innerHTML = `<li class="caption dim">加载失败: ${escapeHtml(e.message)}</li>`;
  }
}

// R-relax-2026-07-14: 切换 relax 档 → 立即刷新
function _wireReviewRelaxButtons() {
  document.querySelectorAll('.np-relax-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const lv = parseInt(btn.dataset.relax || '0', 10);
      _reviewRelaxLevel = lv;
      document.querySelectorAll('.np-relax-btn').forEach(b => b.classList.toggle('on', b === btn));
      _reviewLoadNextPicks();
    });
  });
}

async function _reviewLoadStats() {
  try {
    const r = await _fetchWithTimeout('/api/review/stats?since_days=90');
    if (!r.ok) return;
    const j = await r.json();
    const d = j.data || {};
    // B-22: 数字字段全部 Number 化 + null 保护,避免服务端返 null 时崩
const safeNum = (x) => x != null && Number.isFinite(Number(x)) ? Number(x) : null;
const tiles = [
      { lbl: '已平仓', val: d.closed ?? 0 },
      { lbl: '胜率',   val: safeNum(d.win_rate) != null ? safeNum(d.win_rate).toFixed(1) + '%' : '—', cls: safeNum(d.win_rate) >= 50 ? 'cell-up' : 'cell-down' },
      { lbl: '平均盈亏', val: safeNum(d.avg_pnl) != null ? (safeNum(d.avg_pnl) > 0 ? '+' : '') + safeNum(d.avg_pnl).toFixed(2) + '%' : '—', cls: safeNum(d.avg_pnl) > 0 ? 'cell-up' : 'cell-down' },
      { lbl: '最佳', val: d.best && safeNum(d.best.pnl_pct) != null ? (safeNum(d.best.pnl_pct) > 0 ? '+' : '') + safeNum(d.best.pnl_pct).toFixed(2) + '%' : '—', code: d.best?.code, cls: 'cell-up' },
      { lbl: '最差', val: d.worst && safeNum(d.worst.pnl_pct) != null ? safeNum(d.worst.pnl_pct).toFixed(2) + '%' : '—', code: d.worst?.code, cls: 'cell-down' },
    ];
    const tradeClickable = t => t.code ? `data-action="open-stock:${escapeHtml(t.code)}" style="cursor:pointer"` : '';
    $('#review-stats').innerHTML = tiles.map(t => `
      <div class="stat-tile" ${tradeClickable(t)}>
        <div class="lbl">${t.lbl}${t.code ? ` · <code style="color:var(--accent);font-size:.7rem">${escapeHtml(t.code)}</code>` : ''}</div>
        <div class="val ${t.cls || ''}">${t.val}</div>
      </div>
    `).join('') + (d.by_pattern && d.by_pattern.length ? `
      <div class="stat-tile" style="grid-column: span 2">
        <div class="lbl">常见错误模式</div>
        <div style="font-size:.85rem; margin-top:.3rem">
          ${d.by_pattern.slice(0, 5).map(p => `<span class="rule-pill fail">${escapeHtml(p.pattern)} ×${p.count}</span>`).join(' ')}
        </div>
      </div>
    ` : '');
  } catch (e) { console.warn('stats load failed', e); }
}

// ─── 截图 / 批量文本 AI 自动录入 (2026-07-11 增强:支持批量) ─────────
var _snapState = {
  running: 0,
  trades: [],   // [{ direction, code, name, price, shares, trade_date, occurred_at, memo, source }]
  thumbSlots: [], // 缩略图卡片列表
};

function _snapNormTime(timeStr) {
  if (!timeStr) return '';
  const m = String(timeStr).match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return '';
  return `${String(m[1]).padStart(2, '0')}:${m[2]}`;
}

function _snapNormDate(dateStr) {
  const s = String(dateStr || '').trim();
  let m;
  if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2})/))) return `${m[1]}-${m[2]}-${m[3]}`;
  if ((m = s.match(/^(\d{4})(\d{2})(\d{2})$/))) return `${m[1]}-${m[2]}-${m[3]}`;
  // 默认今天
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function _snapYMD(dateStr) {
  const n = _snapNormDate(dateStr);
  return n.replace(/-/g, '');
}

function _snapPreviewRender() {
  const box = $('#snap-preview-box');
  const tbody = $('#snap-tbody');
  const cntEl = $('#snap-count');
  const metaEl = $('#snap-meta');
  if (!box || !tbody) return;
  if (!_snapState.trades.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (cntEl) cntEl.textContent = _snapState.trades.length;
  if (metaEl) {
    const ai = _snapState.trades.filter(t => t.source === 'ai').length;
    const ocr = _snapState.trades.filter(t => t.source === 'ocr').length;
    const txt = _snapState.trades.filter(t => t.source === 'text').length;
    const parts = [];
    if (ai)  parts.push(`AI ${ai} 笔`);
    if (ocr) parts.push(`OCR ${ocr} 笔`);
    if (txt) parts.push(`文本 ${txt} 笔`);
    metaEl.textContent = `来源:${parts.join(' / ') || '-'} · 编辑后可点 "全部录入"`;
  }
  tbody.innerHTML = _snapState.trades.map((t, i) => `
    <tr data-idx="${i}">
      <td>${i + 1}</td>
      <td>
        <select class="snap-edit" data-field="direction">
          <option value="buy" ${t.direction === 'buy' ? 'selected' : ''}>买</option>
          <option value="sell" ${t.direction === 'sell' ? 'selected' : ''}>卖</option>
        </select>
      </td>
      <td><input class="snap-edit" data-field="code" value="${String(t.code || '').replace(/"/g,'&quot;')}" maxlength="6"></td>
      <td><input class="snap-edit" data-field="name" value="${String(t.name || '').replace(/"/g,'&quot;')}" maxlength="20"></td>
      <td><input class="snap-edit" data-field="price" type="number" step="0.01" value="${t.price || 0}"></td>
      <td><input class="snap-edit" data-field="shares" type="number" step="100" value="${t.shares || 0}"></td>
      <td><input class="snap-edit" data-field="date" type="date" value="${_snapNormDate(t.trade_date)}"></td>
      <td><input class="snap-edit" data-field="time" placeholder="HH:MM" value="${_snapNormTime(t.occurred_at)}"></td>
      <td><input class="snap-edit" data-field="memo" value="${String(t.memo || '').replace(/"/g,'&quot;')}" maxlength="120"></td>
      <td><span class="src-tag ${t.source === 'ocr' ? 'ocr' : ''}">${t.source === 'ai' ? 'AI' : t.source === 'ocr' ? 'OCR' : '文本'}</span></td>
      <td><button type="button" class="row-del" data-action="del">×</button></td>
    </tr>
  `).join('');

  // 单元格编辑同步到 state
  tbody.querySelectorAll('.snap-edit').forEach(el => {
    el.addEventListener('change', (e) => {
      const tr = e.target.closest('tr');
      const i = parseInt(tr.dataset.idx, 10);
      const f = e.target.dataset.field;
      const t = _snapState.trades[i];
      if (!t) return;
      if (f === 'direction') t.direction = e.target.value;
      else if (f === 'code') t.code = String(e.target.value).replace(/\D/g, '').slice(0, 6);
      else if (f === 'name') t.name = e.target.value.trim();
      else if (f === 'price') t.price = parseFloat(e.target.value) || 0;
      else if (f === 'shares') t.shares = parseInt(e.target.value, 10) || 0;
      else if (f === 'date') {
        t.trade_date = _snapYMD(e.target.value);
        t.occurred_at = (t.occurred_at && _snapNormTime(t.occurred_at))
          ? `${e.target.value}T${_snapNormTime(t.occurred_at)}:00`
          : '';
      }
      else if (f === 'time') {
        t.occurred_at = e.target.value
          ? `${_snapNormDate(t.trade_date)}T${e.target.value}:00`
          : '';
      }
      else if (f === 'memo') t.memo = e.target.value;
    });
  });
  tbody.querySelectorAll('button[data-action="del"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      const i = parseInt(tr.dataset.idx, 10);
      _snapState.trades.splice(i, 1);
      _snapPreviewRender();
    });
  });
}

function _snapAppend(trades, source) {
  if (!Array.isArray(trades) || !trades.length) return 0;
  let n = 0;
  for (const t of trades) {
    if (!t.code && !t.price && !t.shares) continue;
    _snapState.trades.push({
      direction: t.direction || 'buy',
      code: String(t.code || '').slice(0, 6),
      name: t.name || '',
      price: parseFloat(t.price) || 0,
      shares: parseInt(t.shares, 10) || 0,
      trade_date: t.trade_date || _snapYMD(new Date()),
      occurred_at: t.occurred_at || '',
      memo: t.memo || '',
      source,
    });
    n++;
  }
  if (n) _snapPreviewRender();
  return n;
}

function _snapFillFormFromPreview() {
  // 单笔 quick action: 拿第一笔填入"录入新交易"表单
  const t = _snapState.trades[0];
  if (!t) return;
  if (t.direction) $('#rf-direction').value = t.direction;
  if (t.code) $('#rf-code').value = t.code;
  if (t.name) { $('#rf-name').value = t.name; delete $('#rf-name').dataset.autoFilled; }
  if (t.price) $('#rf-price').value = t.price;
  if (t.shares) $('#rf-shares').value = t.shares;
  if (t.trade_date) {
    $('#rf-date').value = _snapNormDate(t.trade_date);
  }
  if (t.occurred_at) {
    const hhmm = _snapNormTime(t.occurred_at);
    if (hhmm) {
      const sel = $('#rf-time');
      if (sel) {
        let found = false;
        for (const o of sel.options) {
          if (o.value === hhmm) { found = true; o.selected = true; break; }
        }
        if (!found) {
          const opt = document.createElement('option');
          opt.value = hhmm; opt.textContent = hhmm + ' (从截图)';
          opt.selected = true;
          sel.appendChild(opt);
        }
      }
    }
  }
  if (t.memo && !$('#rf-memo').value) $('#rf-memo').value = t.memo;
  document.querySelectorAll('#review-form input, #review-form select').forEach(el => {
    if (el.value && el.offsetParent) {
      el.style.transition = 'background .3s';
      el.style.background = 'rgba(74,222,128,0.15)';
      el.classList.add('flash-green');
      setTimeout(() => { el.style.background = ''; el.classList.remove('flash-green'); }, 700);
    }
  });
}

function _reviewBindScreenshot() {
  const drop = $('#snap-drop');
  const inp = $('#snap-file');
  const thumbs = $('#snap-thumbs');
  const status = $('#snap-status');
  const tag = $('#snap-source-tag');
  const tabImg = $('#snap-tab-img');
  const tabText = $('#snap-tab-text');
  const paneImg = $('#snap-pane-img');
  const paneText = $('#snap-pane-text');
  const textArea = $('#snap-text');
  const textParseBtn = $('#snap-text-parse');
  const textExampleBtn = $('#snap-text-example');
  const clearBtn = $('#snap-clear');
  const saveBtn = $('#snap-batch-save');
  if (!drop || drop._bound) return;
  drop._bound = true;
  // 即使已初始化过,也要重新 render 一次空表 (view re-enter)
  _snapPreviewRender();

  const setStatus = (text, cls = '') => {
    if (!status) return;
    status.className = 'snap-status' + (cls ? ' ' + cls : '');
    status.innerHTML = text;
  };

  const parseOneFile = async (file) => {
    _snapState.running++;
    setStatus(`<span class="snap-spinner"></span>AI 解析中: ${escapeHtml(file.name)}…`, '');
    if (tag) { tag.hidden = false; tag.textContent = '解析中'; tag.style.color = ''; }
    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'shot.png');
      const r = await _fetchWithTimeout('/api/review/parse_trade_image', {
        method: 'POST',
        body: fd,
        timeout: 60_000,
      });
      const j = await r.json();
      if (!j.ok || !j.data || j.data.missing) {
        setStatus('✗ ' + (j.error || '未识别出有效字段'), 'err');
        return { ok: false, err: j.error || 'missing' };
      }
      const trades = j.data.trades || [];
      const source = j.data.source || 'ai';
      const conf = j.data.confidence || 0;
      const added = _snapAppend(trades, source);
      return { ok: !!added, added, source, conf };
    } catch (e) {
      return { ok: false, err: e.message };
    } finally {
      _snapState.running--;
    }
  };

  const parseBatchFiles = async (fileList) => {
    if (!fileList || !fileList.length) return;
    const files = Array.from(fileList);
    for (const f of files) {
      if (f.size > 6 * 1024 * 1024) {
        setStatus(`✗ ${f.name} 超过 6MB,跳过`, 'err');
        continue;
      }
      if (!/^image\/(png|jpe?g|webp)$/i.test(f.type) && !/\.(png|jpe?g|webp)$/i.test(f.name)) {
        setStatus(`✗ ${f.name} 格式不支持`, 'err');
        continue;
      }
      await parseOneFile(f);
    }
    finalizeBatch();
  };

  const finalizeBatch = () => {
    const total = _snapState.trades.length;
    if (!total) {
      setStatus('✗ 没识别出任何有效交易,请手填或换 OCR', 'err');
      if (tag) { tag.textContent = '失败'; tag.style.color = '#f87171'; }
      return;
    }
    const ai  = _snapState.trades.filter(t => t.source === 'ai').length;
    const ocr = _snapState.trades.filter(t => t.source === 'ocr').length;
    setStatus(
      `✓ 共识别出 <b>${total}</b> 笔交易 (${ai} AI + ${ocr} OCR) · 可在表中编辑,再点 "全部录入"`,
      'ok'
    );
    if (tag) {
      tag.textContent = ai ? `🤖 AI ${ai}` : (ocr ? '🔤 OCR' : '已就绪');
      tag.style.color = ai ? '#4ade80' : '#d4a056';
    }
    // 缩略图保留(显示计数);若有单笔且表单为空,可自动填
    if (total === 1 && !$('#rf-code').value) {
      _snapFillFormFromPreview();
    }
  };

  // tab 切换
  const switchTab = (which) => {
    const useImg = which === 'img';
    if (tabImg) tabImg.classList.toggle('active', useImg);
    if (tabText) tabText.classList.toggle('active', !useImg);
    if (paneImg) paneImg.hidden = !useImg;
    if (paneText) paneText.hidden = useImg;
  };
  if (tabImg) tabImg.addEventListener('click', () => switchTab('img'));
  if (tabText) tabText.addEventListener('click', () => switchTab('text'));

  // 一键粘贴示例 — 让用户立即看到格式,降低试用门槛
  if (textExampleBtn && textArea) {
    textExampleBtn.addEventListener('click', () => {
      textArea.value =
        '600519  贵州茅台  buy   1820.50  100  2026-07-11  09:35\n' +
        '002747  埃斯顿   buy    42.00  100  2026-07-11  10:00\n' +
        '300750  宁德时代 sell  320.00  100  2026-07-11  14:30\n' +
        '\n' +
        '# 也可以从券商 App 直接复制粘贴历史成交 (Tab/空格/逗号都行)';
      textArea.focus();
      showToast('已填入示例 — 点「解析 → 预览」试试', 'info', 2200);
    });
  }

  // 文件选择 + 拖放 + 粘贴 → 多文件
  drop.addEventListener('click', () => inp.click());
  inp.addEventListener('change', async (e) => {
    await parseBatchFiles(e.target.files);
    e.target.value = '';
  });
  drop.addEventListener('dragover', (e) => {
    e.preventDefault();
    drop.classList.add('dragover');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', async (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    await parseBatchFiles(e.dataTransfer.files);
  });
  drop.addEventListener('paste', async (e) => {
    const items = e.clipboardData?.items || [];
    const files = [];
    for (const it of items) {
      if (it.kind === 'file' && /^image\//.test(it.type)) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      await parseBatchFiles(files);
    }
  });

  // R12-A: 顶级智能文本解析 — 字段提取而非 split+scan
  // 支持任意分隔符 (空格/Tab/ASCII|/全角｜/中英逗号/顿号)
  // 支持多种时间格式 (HH:MM / HH:MM:SS / HH-MM)
  // 支持多种日期格式 (YYYY-MM-DD / YYYYMMDD / YYYY/M/D / YYYY.M.D / M月D日)
  // 支持中文或英文 direction (任意位置)
  // 智能识别标题行并跳过
  function _smartParseTradeText(raw) {
    const today = _snapYMD(new Date());
    const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#') && !l.startsWith('//'));
    const parsed = [];
    const stats = { header: 0, dedup: 0, noStock: 0, valid: 0 };

    const headerKwRe = /(操作|方向|证券|成交价|成交金额|成交量|股票名|股票代码|代码|名称|价格|时间|金额|数量)/;
    const HEADER_NAMES = /^(|证券|成交价|成交金额|成交量|股票名|股票代码|代码|名称|价格|时间|金额|数量|方向|操作|名称)$/;
    const DIRECTION_RE = /(买入|卖出|买\b|卖\b|\bbuy\b|\bsell\b)/i;
    const TIME_RE = /(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?:[:：]([0-5]\d))?(?!\d)/;
    const DATE_RE = /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2})[:：](\d{1,2})(?:[:：](\d{1,2}))?)?/;
    const DATE_YMD = /(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)/;
    const CODE_RE = /(?<![-\d])([036]\d{5})(?![-\d])/;
    const CN_RUN_RE = /[一-龥]{2,8}/g;
    // R13 关键修复: 优先级问题 — 原正则首支 `\d{1,3}(?:,\d{3})*` 把 `18004.00` 错切成 `180` + `04.00`,
    // 直接吞掉 shares (2800 → 100 fallback)。改为先匹配带小数点的整体,再回退到整数。
    const NUM_RE = /(\d+\.\d+|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+)/g;

    for (const ln of lines) {
      // ── 标题行判定 ──
      const has6digitCode = CODE_RE.test(ln);
      const hasDateTime = DATE_RE.test(ln) || DATE_YMD.test(ln) || TIME_RE.test(ln);
      const hasDirection = DIRECTION_RE.test(ln);
      const chineseRunRatio = (ln.match(/[一-龥]/g) || []).length / Math.max(ln.length, 1);
      // 有方向词 + 6 位代码 → 一定是交易行,跳过表头判定
      if (!has6digitCode && !hasDirection) {
        if (headerKwRe.test(ln)) { stats.header++; continue; }
        if (!hasDateTime && chineseRunRatio > 0.5 && ln.length <= 30) { stats.header++; continue; }
      }

      // ── 方向 (任意位置) ──
      const dirMatch = ln.match(DIRECTION_RE);
      if (!dirMatch) { stats.noStock++; continue; }
      const direction = /卖|sell/i.test(dirMatch[0]) ? 'sell' : 'buy';

      // ── 6 位代码 ──
      let code = '';
      const codeMatch = ln.match(CODE_RE);
      if (codeMatch) code = codeMatch[1];

      // ── 时间戳 ──
      let dateStr = '', timeStr = '';
      const dt = ln.match(DATE_RE);
      if (dt) {
        dateStr = `${dt[1]}-${String(dt[2]).padStart(2,'0')}-${String(dt[3]).padStart(2,'0')}`;
        if (dt[4]) timeStr = `${String(dt[4]).padStart(2,'0')}:${String(dt[5]).padStart(2,'0')}`;
      }
      if (!dateStr) {
        const d2 = ln.match(DATE_YMD);
        if (d2) dateStr = `${d2[1]}-${d2[2]}-${d2[3]}`;
      }
      const tm = ln.match(TIME_RE);
      if (tm && !timeStr) timeStr = `${String(tm[1]).padStart(2,'0')}:${tm[2]}`;
      if (!dateStr) {
        const d3 = ln.match(/(\d{1,2})月(\d{1,2})日?/);
        if (d3) {
          const yr = new Date().getFullYear();
          dateStr = `${yr}-${String(d3[1]).padStart(2,'0')}-${String(d3[2]).padStart(2,'0')}`;
        }
      }
      if (!dateStr) dateStr = today;

      // ── 数字分类: 价格 / 总额 / 股数 ──
      const allNums = [];
      let nm;
      while ((nm = NUM_RE.exec(ln)) !== null) {
        const raw = nm[1].replace(/,/g, '');
        const val = parseFloat(raw);
        if (isNaN(val)) continue;
        const before = ln.slice(Math.max(0, nm.index - 1), nm.index);
        const after = ln.slice(nm.index + nm[1].length, nm.index + nm[1].length + 1);
        if (/[-/:：.]/.test(before) || /[-/:：.]/.test(after)) continue;
        if (code && raw === code) continue;
        allNums.push({ val, idx: nm.index, hasDecimal: /\./.test(raw) });
      }

      let price = 0, total = 0, shares = 0;
      const decimalNums = allNums.filter(n => n.hasDecimal);
      const intNums = allNums.filter(n => !n.hasDecimal);

      // 价格: 第一个小数。如果第 2 个小数 > price×50 → total
      if (decimalNums.length) {
        price = decimalNums[0].val;
        if (decimalNums.length >= 2 && decimalNums[1].val > price * 50) {
          total = decimalNums[1].val;
        }
      }
      // 整数分类
      for (const n of intNums) {
        const v = n.val;
        if (!shares && v >= 100 && v <= 100000 && v % 100 === 0) {
          shares = v;
        } else if (!total && v >= 100) {
          total = v;
        }
      }

      // ── 名字: 方向词后的整段字段 (支持 ASCII 前缀如 "TCL科技" / "ST星云") ──
      let name = '';
      const dirIdx = ln.search(DIRECTION_RE);
      if (dirIdx >= 0) {
        // 取方向词所在位置,以及方向词的结束位置
        const dirEnd = dirIdx + ln.slice(dirIdx).match(DIRECTION_RE)[0].length;
        // 从 dirEnd 后跳过空白 / 分隔符
        let cursor = dirEnd;
        while (cursor < ln.length && /[\s,，|/／、:：]/.test(ln[cursor])) cursor++;
        // 截到第一个数字 / 6 位代码 / 日期为止
        const rest = ln.slice(cursor);
        const stopRe = /(?<![A-Za-z])(?=\d)|(?=\d{4}[-/.\s])/;
        const stopMatch = rest.search(stopRe);
        name = stopMatch > 0 ? rest.slice(0, stopMatch).trim() : rest.trim();
        // 去掉尾部标点
        name = name.replace(/[，,。.\s]+$/, '');
      }
      // 兜底: 旧法 — 最长中文段 (排除方向词)
      if (!name || /^(买入|卖出|买|卖|操作|方向)$/.test(name)) {
        const cnRuns = [];
        let cm2;
        CN_RUN_RE.lastIndex = 0;
        while ((cm2 = CN_RUN_RE.exec(ln)) !== null) {
          if (HEADER_NAMES.test(cm2[0])) continue;
          if (/^(买入|卖出|买|卖|操作|方向|证券)$/.test(cm2[0])) continue;
          cnRuns.push({ text: cm2[0], len: cm2[0].length });
        }
        if (cnRuns.length) {
          cnRuns.sort((a, b) => b.len - a.len);
          name = cnRuns[0].text;
        }
      }

      // ── 兜底 ──
      if (!price && total > 0 && shares > 0) {
        price = Math.round((total / shares) * 100) / 100;
      }
      if (!shares) shares = 100;
      if (!price || !name) { stats.noStock++; continue; }

      // ── 批内去重 ──
      const dedupKey = `${direction}|${code}|${name}|${price}|${shares}|${dateStr}|${timeStr}`;
      if (_parseDedupSet && _parseDedupSet.has(dedupKey)) { stats.dedup++; continue; }
      if (!_parseDedupSet) _parseDedupSet = new Set();
      _parseDedupSet.add(dedupKey);

      parsed.push({
        direction, code, name,
        price, shares, total_amount: total,
        trade_date: _snapYMD(dateStr),
        occurred_at: timeStr ? `${dateStr}T${timeStr}:00` : '',
        memo: '',
      });
      stats.valid++;
    }
    return { trades: parsed, stats };
  }

  // 文本批量解析
  if (textParseBtn) {
    textParseBtn.addEventListener('click', () => {
      const raw = (textArea?.value || '').trim();
      if (!raw) {
        setStatus('✗ 请先粘贴或输入交易行', 'err');
        return;
      }
      _parseDedupSet = new Set();
      const { trades: parsed, stats } = _smartParseTradeText(raw);
      if (!parsed.length) {
        let msg = '✗ 没解析出有效字段';
        if (stats.header) msg += ` · 跳过 ${stats.header} 行标题`;
        if (stats.noStock) msg += ` · ${stats.noStock} 行无法识别`;
        setStatus(msg, 'err');
        return;
      }
      const added = _snapAppend(parsed, 'text');
      const n = _snapState.trades.length;
      if (!added) {
        setStatus('✗ 解析后无有效字段', 'err');
        return;
      }
      const extras = [];
      if (stats.header) extras.push(`跳过 ${stats.header} 行标题`);
      if (stats.dedup) extras.push(`批内去重 ${stats.dedup}`);
      if (stats.noStock) extras.push(`无法识别 ${stats.noStock}`);
      setStatus(`✓ 解析出 <b>${added}</b> 笔 (累计 <b>${n}</b>)${extras.length ? ' · ' + extras.join(' · ') : ''} · 核对后录入`, 'ok');
      if (n === 1) _snapFillFormFromPreview();
    });
  }
  // 清空预览
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      _snapState.trades = [];
      _snapPreviewRender();
      setStatus('已清空 · 可重新上传/粘贴/输入', '');
      if (tag) tag.hidden = true;
    });
  }

  // 批量录入
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      if (!_snapState.trades.length) {
        setStatus('✗ 没有可录入的交易', 'err');
        return;
      }
      saveBtn.disabled = true;
      saveBtn.textContent = '📥 录入中…';
      try {
        // code/name 任一 + price/shares 必须有;后端 _normalize 会用 name 反查 code
        const clean = _snapState.trades
          .map(t => ({
            direction: t.direction || 'buy',
            code: String(t.code || '').replace(/\D/g, '').slice(0, 6).padStart(6, '0'),
            name: t.name || '',
            price: parseFloat(t.price) || 0,
            shares: parseInt(t.shares, 10) || 0,
            total_amount: parseFloat(t.total_amount) || 0,
            occurred_at: t.occurred_at || '',
            trade_date: t.trade_date || _snapYMD(new Date()),
            memo: t.memo || '',
          }))
          .filter(t => (t.code || t.name) && t.price > 0 && t.shares >= 100);
        if (!clean.length) {
          setStatus('✗ 没有可录入的完整记录 (需 code 或 name + 价格 + 股数)', 'err');
          return;
        }
        const r = await _fetchWithTimeout('/api/review/trades', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trades: clean }),
          timeout: 30_000,
        });
        const j = await r.json();
        if (!j.ok) {
          setStatus('✗ 录入失败: ' + (j.error || 'unknown'), 'err');
          return;
        }
        const ok = j.data?.ok || 0;
        const fail = j.data?.fail || 0;
        const total = j.data?.total || clean.length;
        setStatus(`✓ 已录入 <b>${ok}</b>/${total} 笔${fail ? ` · 失败 ${fail} 笔` : ''}`, 'ok');
        showToast(`✓ 批量录入完成 (${ok}/${total})`, 'success');
        // 成功 → 清空成功的,失败保留
        const failedInputs = (j.data?.errors || []).map(e => e.input);
        const failedSet = new Set(failedInputs.map(x => JSON.stringify(x)));
        _snapState.trades = _snapState.trades.filter(t => failedSet.has(JSON.stringify({
          direction: t.direction || 'buy',
          code: String(t.code || '').replace(/\D/g, '').slice(0, 6).padStart(6, '0'),
          name: t.name || '',
          price: parseFloat(t.price) || 0,
          shares: parseInt(t.shares, 10) || 0,
          occurred_at: t.occurred_at || '',
          trade_date: t.trade_date || _snapYMD(new Date()),
          memo: t.memo || '',
        })));
        _snapPreviewRender();
        // 刷新交易明细
        if (typeof _reviewRefreshTrades === 'function') await _reviewRefreshTrades();
        if (typeof _reviewRefreshPortfolio === 'function') await _reviewRefreshPortfolio();
        // 后台批量触发 AI 复盘 (每笔错开 1.2s, 避免瞬时打爆 AI 限频)
        const inserted = j.data?.inserted || [];
        if (inserted.length && typeof _reviewRun === 'function') {
          let delay = 400;
          for (const it of inserted) {
            const tid = it.trade_id;
            if (!tid) continue;
            setTimeout(() => {
              _reviewRun(tid).catch(err => console.warn('batch AI review trade', tid, err));
            }, delay);
            delay += 1200;
          }
          showToast(`🤖 已排队 AI 复盘 ${inserted.length} 笔`, 'info');
        }
      } catch (e) {
        setStatus('✗ 录入请求失败: ' + e.message, 'err');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '📥 全部录入';
      }
    });
  }
}

// 录入表单
function _reviewBindForm() {
  const form = $('#review-form');
  if (!form || form._bound) return;
  form._bound = true;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = $('#rf-code').value.trim();
    const nameInput = $('#rf-name');
    const name = (nameInput.value || '').trim() || null;
    const direction = $('#rf-direction').value;
    const price = parseFloat($('#rf-price').value);
    const shares = parseInt($('#rf-shares').value);
    const memo = ($('#rf-memo').value || '').trim();
    if (!code || !price || !shares) {
      showToast('请填代码、价格、股数', 'error');
      return;
    }
    showToast(`保存中…${code} ${direction} @ ${price}`, 'info');
    try {
      const r = await _fetchWithTimeout('/api/review/trades', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name, direction, price, shares, memo }),
      });
      const j = await r.json();
      if (j.ok) {
        $('#rf-code').value = '';
        nameInput.value = ''; delete nameInput.dataset.autoFilled;
        $('#rf-price').value = ''; $('#rf-shares').value = ''; $('#rf-memo').value = '';
        showToast(`✓ 已记录 trade #${j.data.trade_id} · AI 复盘中…`, 'success');
        _reviewLoadList();
        // 自动跑复盘(后台,不阻塞)
        if (j.data?.trade_id) {
          setTimeout(() => _reviewRun(j.data.trade_id), 300);
        }
      } else {
        showToast(`保存失败: ${j.error || '未知错误'}`, 'error');
      }
    } catch (err) {
      showToast(`保存失败: ${err.message}`, 'error');
      console.error('save trade failed', err);
    }
  });
  // 股票代码联想 — 复用 /api/stock/search
  const codeInput = $('#rf-code');
  const nameInput = $('#rf-name');
  if (codeInput && nameInput) {
    let _searchBox = null;
    let _searchTimer = null;

    function _hideSuggest() {
      if (_searchBox) { _searchBox.remove(); _searchBox = null; }
    }

    function _showSuggest(items) {
      _hideSuggest();
      if (!items || items.length === 0) return;
      _searchBox = document.createElement('div');
      _searchBox.className = 'review-suggest';
      _searchBox.style.cssText = `
        position: absolute; background: rgba(20,18,14,0.98);
        border: 1px solid rgba(212,160,86,0.3); border-radius: 6px;
        max-height: 280px; overflow-y: auto; z-index: 100;
        min-width: 240px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      `;
      items.slice(0, 10).forEach(item => {
        const row = document.createElement('div');
        row.style.cssText = 'padding: 8px 12px; cursor: pointer; font-size: 13px; border-bottom: 1px solid rgba(232,227,216,0.05);';
        row.innerHTML = `<code style="color:#d4a056">${escapeHtml(item.code)}</code> <span style="color:#e8e3d8">${escapeHtml(item.name || '')}</span>`;
        row.addEventListener('mouseenter', () => row.style.background = 'rgba(212,160,86,0.15)');
        row.addEventListener('mouseleave', () => row.style.background = '');
        row.addEventListener('click', () => {
          codeInput.value = item.code;
          nameInput.value = item.name || '';
          _hideSuggest();
          codeInput.focus();
        });
        _searchBox.appendChild(row);
      });
      // 定位到 codeInput 下方
      const rect = codeInput.getBoundingClientRect();
      _searchBox.style.left = rect.left + 'px';
      _searchBox.style.top = (rect.bottom + 4) + 'px';
      _searchBox.style.position = 'fixed';
      document.body.appendChild(_searchBox);
    }

    codeInput.addEventListener('input', () => {
      clearTimeout(_searchTimer);
      const q = codeInput.value.trim();
      if (!q) { _hideSuggest(); nameInput.value = ''; return; }
      // 用户已填 name 时不打扰
      if (nameInput.value && nameInput.dataset.autoFilled) {
        // 如果继续改 code,清掉 autoFilled
        delete nameInput.dataset.autoFilled;
      }
      _searchTimer = setTimeout(async () => {
        try {
          const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
          if (!r.ok) return;
          const j = await r.json();
          const items = (j.data && j.data.results) || [];
          if (items.length === 1 && items[0].code === q) {
            // 精确匹配 — 直接填 name
            nameInput.value = items[0].name;
            nameInput.dataset.autoFilled = '1';
            _hideSuggest();
          } else {
            _showSuggest(items);
          }
        } catch (e) { /* ignore */ }
      }, 250);
    });

    codeInput.addEventListener('blur', () => {
      // 延迟关闭,让 click 触发
      setTimeout(_hideSuggest, 200);
    });

    // 也支持 name 输入反向查 code(可选)
    nameInput.addEventListener('input', () => {
      if (nameInput.dataset.autoFilled) delete nameInput.dataset.autoFilled;
    });
  }
}

// 切到 review view 时加载
function _reviewOnViewEnter() {
  if (document.querySelector('.view-review:not([hidden])')) {
    _reviewBindForm();
    _reviewBindScreenshot();
    _reviewBindCapital();
    _reviewBindInfer();
    _reviewBindToggle();
    _reviewLoadSettings();
    _reviewLoadPortfolio();
    _reviewLoadList();
    _reviewRefreshIntegrity();                  // R13: 对账 badge
    _reviewLoadNextPicks();
    // 顶部资金栏 + 持仓 15s 刷新 — 离开页面自动停
    if (_reviewState.capTimer) clearInterval(_reviewState.capTimer);
    _reviewState.capTimer = setInterval(() => {
      if (document.querySelector('.view-review:not([hidden])')) {
        _reviewLoadPortfolio();
      }
    }, 15000);
    const btn = $('#review-next-pick-refresh');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', () => _reviewLoadNextPicks());
    }
    // R-relax-2026-07-14: 放宽档按钮绑定 (严格 / 放宽 / 极宽松)
    if (!_wireReviewRelaxButtons._bound) {
      _wireReviewRelaxButtons._bound = true;
      _wireReviewRelaxButtons();
    }
    // R15: 进入页面 — 如果有未复盘的笔,后台并发补齐,逐笔刷新主表,不阻塞浏览
    //  - force=false:已复盘的笔走缓存秒回,未复盘的笔调 LLM (≈60s)
    //  - 用户可点 banner 上的"停"中断
    //  - 离开 view 不停(后台继续跑),再次进入会显示当前进度
    setTimeout(() => _reviewAutoReviewTick(), 600);
  }
}

// R-ui-012: 离开 review view 时清理所有定时器 + abort 进中的 in-flight fetch
// 之前这个 cleanup 不存在,反复切页会 capTimer 等 +1s 一次拉取
function _reviewOnViewLeave() {
  // 1) 顶部资金栏刷新定时器
  if (_reviewState.capTimer) {
    clearInterval(_reviewState.capTimer);
    _reviewState.capTimer = null;
  }
  // 2) 其它 setInterval 一次清掉
  for (const k of Object.keys(_reviewState)) {
    if (/Timer$/i.test(k) && _reviewState[k]) {
      try { clearInterval(_reviewState[k]); clearTimeout(_reviewState[k]); } catch {}
      _reviewState[k] = null;
    }
  }
  // 3) 任何 AbortController 池
  if (_reviewState._inflightAborter) {
    try { _reviewState._inflightAborter.abort(); } catch {}
    _reviewState._inflightAborter = null;
  }
}
_registerViewLeave('review', _reviewOnViewLeave);

// 离开个股页停掉实时轮询 + abort in-flight
function _stockOnViewLeave() {
  try { _stopStockPoll(); } catch {}
  if (window._stockInflightAborter) {
    try { window._stockInflightAborter.abort(); } catch {}
    window._stockInflightAborter = null;
  }
}
_registerViewLeave('stock', _stockOnViewLeave);

// R15: 自动复盘调度 — 状态机
var _reviewAuto = { running: false, queue: [], done: 0, total: 0, startedTs: 0, stop: false };
function _reviewAutoReviewTick() {
  // 不在 review view → 不主动启动,但已运行的允许继续
  if (!document.querySelector('.view-review:not([hidden])')) return;
  const trades = (_reviewState && _reviewState.trades) || [];
  // 只复盘当前 DB 里有 last_review 缺失的笔 (过滤 000000 占位)
  const pending = trades.filter(t => {
    const code = (t.code || '').toString().padStart(6, '0');
    const isPlaceholder = code === '000000' && !(t.name && /[一-龥]/.test(t.name || ''));
    return !isPlaceholder && !t.last_review;
  });
  // 2026-07-14: 用户反馈进入页面 banner 一直显示,即使已全部复盘
  // 先看 pending: 空 → 直接收尾 + 隐藏 banner(忽略 running 状态,允许在跑但无 pending 时收尾)
  if (!pending.length) {
    if (_reviewAuto.running) {
      // 之前有任务在跑但现在没 pending 了,直接收尾
      _reviewAuto.running = false;
      _reviewAuto.queue = [];
      _reviewAutoHideBanner();
    } else {
      _reviewAutoHideBanner();
    }
    return;
  }
  // 已有跑的任务还在 → 不要重启,让现有 worker 继续
  if (_reviewAuto.running) return;
  _reviewAuto = {
    running: true,
    queue: pending.slice(),
    done: 0,
    total: pending.length,
    startedTs: Date.now(),
    stop: false,
  };
  _reviewAutoShowBanner();
  // 启动一次性 integrity check 让 badge 反映开始前的真值,后续不再每笔重打
  _reviewRefreshIntegrity().catch(() => {});
  // 2 路并发 worker
  _reviewAutoRunWorker(0);
  _reviewAutoRunWorker(1);
}
function _reviewAutoRunWorker(workerId) {
  const next = async () => {
    // 用户中途点了"停" → 这条 worker 退出(已 in-flight 的请求让它跑完)
    if (_reviewAuto.stop) return;
    if (!_reviewAuto.queue.length) return;
    const t = _reviewAuto.queue.shift();
    if (!t) return;
    try {
      const r = await _fetchWithTimeout(`/api/review/trades/${t.id}/review?force=false`, { method: 'POST' });
      const j = await r.json();
      if (j.ok && j.data) {
        // R15-fix: 局部更新行 — 不重渲整张表 → 不影响账单 / 持仓 / 浮盈
        _reviewPatchRow(t.id, j.data);
        // 把 review 也写回 _reviewState.trades 内存 (后续汇总/筛选还要用)
        const local = (_reviewState.trades || []).find(x => x.id === t.id);
        if (local) local.last_review = j.data;
        showToast(`✓ #${t.id} 已复盘 · ${j.data.verdict || ''} ${j.data.score || ''}分`.trim(), 'success', 1500);
      } else {
        showToast(`✗ #${t.id} 失败: ${j.error || '?'}`, 'error', 2000);
      }
    } catch (e) {
      showToast(`✗ #${t.id} ${e.message}`, 'error', 2000);
    } finally {
      _reviewAuto.done++;
      _reviewAutoUpdateBanner();
      // R15-fix: 不要每笔都 _reviewLoadList / _reviewRefreshIntegrity — 会闪账单
      // 只在最后一次性刷新
      if (_reviewAuto.done >= _reviewAuto.total) {
        _reviewAutoFinish();
        return;
      }
      next();
    }
  };
  next();
}

// R15-fix: 局部更新单笔 review 信息 (不改行顺序 / 不闪持仓 / 不重算 PnL)
function _reviewPatchRow(tradeId, review) {
  if (!tradeId || !review) return;
  const mm = review.main_mistake || review.mistake_pattern || '';
  // 主行 + 子行 — 用属性 [data-trade-id]
  const rows = document.querySelectorAll(`tr[data-trade-id="${tradeId}"]`);
  rows.forEach(tr => {
    // 行结构: [name, direction, date, price, time, shares, today, cum, cum%, mistake, action]
    //                              0   1   2    3    4    5     6     7    8       9         10
    const tdList = tr.querySelectorAll(':scope > td');
    if (tdList.length >= 11) {
      const mistakeTd = tdList[9];  // mistake pill 列
      if (mistakeTd && mm) {
        const pill = mistakeTd.querySelector('.main-mistake-pill');
        if (pill) {
          pill.textContent = mm;
          pill.title = mm;
        } else {
          mistakeTd.innerHTML = `<span class="main-mistake-pill" title="${escapeHtml(mm)}">${escapeHtml(mm)}</span>`;
        }
      }
    }
    // 2) AI 复盘按钮 — 去掉 primary, 文案从 ● 变普通
    const btn = tr.querySelector(`button[data-action="ai-review:${tradeId}"]`);
    if (btn) {
      btn.classList.remove('primary');
      btn.textContent = 'AI 复盘';
    }
  });
}

function _reviewAutoShowBanner() {
  const b = document.getElementById('review-auto-banner');
  if (!b) return;
  b.hidden = false;
  const stopBtn = b.querySelector('.arb-stop');
  if (stopBtn && !stopBtn._bound) {
    stopBtn._bound = true;
    stopBtn.addEventListener('click', () => {
      _reviewAuto.stop = true;
      showToast('已请求停止,正在收尾…', 'info', 2000);
    });
  }
  _reviewAutoUpdateBanner();
}
function _reviewAutoUpdateBanner() {
  const b = document.getElementById('review-auto-banner');
  if (!b || b.hidden) return;
  const dt = Math.round((Date.now() - _reviewAuto.startedTs) / 1000);
  const m = Math.floor(dt / 60), s = dt % 60;
  b.querySelector('.arb-text').textContent =
    `正在后台复盘 ${_reviewAuto.done}/${_reviewAuto.total} 笔 · 已用 ${m}m${s}s · 可继续浏览`;
  b.querySelector('.arb-prog').textContent = '';
}
function _reviewAutoHideBanner() {
  const b = document.getElementById('review-auto-banner');
  if (b) b.hidden = true;
}
function _reviewAutoFinish() {
  if (!_reviewAuto.running) return;  // 防重入
  _reviewAuto.running = false;
  _reviewAuto.queue = [];
  const total = _reviewAuto.total;
  showToast(total > 0 ? `✅ 自动复盘完成 · 共 ${total} 笔` : '✅ 自动复盘完成', 'success', 4000);
  setTimeout(_reviewAutoHideBanner, 6000);
  // R15-fix: 全部完成后再统一刷新一次 (此时不会再闪了,因为只刷一次)
  try { _reviewLoadList(); } catch {}
  try { _reviewRefreshIntegrity(); } catch {}
  try { _reviewLoadPortfolio(); } catch {}
}

// 暴露:被 review bulk 按钮 / 别的流程复用
window.__reviewAutoAPI = { stop: () => { _reviewAuto.stop = true; }, get running() { return _reviewAuto.running; } };

// 切到 review view 时加载 (已通过 showView 钩子触发,这里不重复)
// const _origJump = window.jumpTo; // 项目用 showView,不用 jumpTo — 之前的覆盖无效

// ────────────────────────────────────────────
// WATCHLIST · 自选股池 (2026-07-11)
// ────────────────────────────────────────────
var _watchlistLoaded = false;
var _watchlistItems = [];
var _watchlistBatchRunning = false;

function _watchlistOnViewEnter() {
  if (!document.querySelector('.view-watchlist:not([hidden])')) return;
  _watchlistBindAdd();
  if (!_watchlistLoaded) {
    _watchlistLoaded = true;
    _watchlistLoad();
  } else {
    // 重新进入也要刷新一次 (用户从个股页回来时 watchlist_ai 已写入)
    _watchlistLoad();
  }
  // 集成次日选股 + 错模式预警 (复用 review 的 next_picks endpoint)
  _watchlistBindNextPick();
  _reviewLoadNextPicks('wl');
}

// "次日选股" 卡片按钮 + 防重入
var _wlNextPickLoaded = false;
function _watchlistBindNextPick() {
  const btn = $('#wl-next-pick-refresh');
  if (btn && !btn._bound) {
    btn._bound = true;
    btn.addEventListener('click', () => _reviewLoadNextPicks('wl'));
  }
}

function _watchlistBindAdd() {
  const btn = $('#wl-add-go');
  const input = $('#wl-add-code');
  const hint = $('#wl-add-hint');
  if (!btn || btn._bound) return;
  btn._bound = true;
  const doAdd = async (code, name) => {
    if (!code) return;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const r = await _fetchWithTimeout('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name }),
      });
      const j = await r.json();
      if (j.ok) {
        showToast(`✓ 已添加 ${j.data.item.name} (${j.data.item.code})`, 'success');
        input.value = '';
        $('#wl-add-results').innerHTML = '';
        _watchlistLoaded = false;
        _watchlistLoad();
        // 自动触发 AI (1.5s 后, 让用户能连续加多只)
        setTimeout(() => _watchlistAnalyzeOne(code, /*silent=*/true), 1500);
      } else {
        showToast(`添加失败: ${j.error || '未知错误'}`, 'error');
      }
    } catch (e) {
      showToast(`添加失败: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '+ 添加 ↗';
    }
  };
  btn.addEventListener('click', async () => {
    const q = (input.value || '').trim();
    if (!q) { showToast('请输入代码或名称', 'error'); return; }
    // 先尝试解析 — 如果是 6 位数字直接加,否则走搜索联想
    if (/^\d{6}$/.test(q)) { doAdd(q); return; }
    try {
      const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
      const j = await r.json();
      const items = (j.data && j.data.results) || [];
      if (items.length === 0) {
        showToast(`没找到 "${q}"`, 'error');
      } else if (items.length === 1) {
        doAdd(items[0].code, items[0].name);
      } else {
        // 多个候选 → 显示列表让用户点
        _wlShowSearchResults(items, (item) => doAdd(item.code, item.name));
      }
    } catch (e) {
      showToast(`搜索失败: ${e.message}`, 'error');
    }
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); btn.click(); }
  });
  // 联想 (250ms debounce)
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q || /^\d{6}$/.test(q)) { $('#wl-add-results').innerHTML = ''; return; }
    timer = setTimeout(async () => {
      try {
        const r = await _fetchWithTimeout('/api/stock/search?q=' + encodeURIComponent(q));
        const j = await r.json();
        const items = (j.data && j.data.results) || [];
        _wlShowSearchResults(items, (item) => doAdd(item.code, item.name));
      } catch (e) { /* ignore */ }
    }, 250);
  });
}

function _wlShowSearchResults(items, onPick) {
  const host = $('#wl-add-results');
  if (!host) return;
  if (!items || !items.length) { host.innerHTML = ''; return; }
  host.innerHTML = items.slice(0, 8).map(it => `
    <div class="wl-suggest-row" data-code="${escapeHtml(it.code)}">
      <code style="color:var(--accent)">${escapeHtml(it.code)}</code>
      <span>${escapeHtml(it.name || '')}</span>
      <span class="caption dim">${escapeHtml(it.market || '')}</span>
    </div>
  `).join('');
  host.querySelectorAll('.wl-suggest-row').forEach(row => {
    row.addEventListener('click', () => {
      const item = items.find(x => x.code === row.dataset.code);
      if (item && onPick) onPick(item);
    });
  });
}

async function _watchlistLoad() {
  const tbody = $('#wl-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="12" class="dim center">加载中 …</td></tr>';
  try {
    const r = await _fetchWithTimeout('/api/watchlist');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '加载失败');
    _watchlistItems = (j.data && j.data.items) || [];
    $('#wl-count').textContent = String(_watchlistItems.length);
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    $('#wl-ts').textContent = `更新 ${ts}`;
    _watchlistRender();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="12" class="dim center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function _watchlistRender() {
  const tbody = $('#wl-tbody');
  if (!tbody) return;
  if (!_watchlistItems.length) {
    tbody.innerHTML = `<tr><td colspan="12" style="padding:0;border:none;">
        ${emptyState({ icon: '⭐', title: '自选股池为空', hint: '在左上方输入框加第一只股票,或浏览全 A 风向把感兴趣的股票 ⭐ 进来', cta: { label: '浏览全 A 风向 →', jump: 'all_stocks' } })}
      </td></tr>`;
    return;
  }
  tbody.innerHTML = _watchlistItems.map(it => _watchlistRowHtml(it)).join('');
  // 绑定 row 内操作
  tbody.querySelectorAll('[data-wl-remove]').forEach(b => {
    b.addEventListener('click', () => _watchlistRemove(b.dataset.wlRemove));
  });
  tbody.querySelectorAll('[data-wl-ai]').forEach(b => {
    b.addEventListener('click', () => _watchlistAnalyzeOne(b.dataset.wlAi));
  });
  tbody.querySelectorAll('[data-wl-detail]').forEach(b => {
    b.addEventListener('click', () => {
      showView('stock');
      loadStockDetail(b.dataset.wlDetail);
    });
  });
}

function _watchlistRowHtml(it) {
  const code = it.code;
  const name = it.name || code;
  const snap = it.snapshot || {};
  const ai = it.ai;
  const q = it.quote || {};

  // 价格 / 涨幅 cell
  const price = snap.price != null ? fmtN(snap.price, 2) : '—';
  const chgPct = snap.chg_pct;
  const chgCls = chgPct > 0 ? 'up' : chgPct < 0 ? 'down' : 'flat';
  const chgHtml = chgPct != null && Number.isFinite(chgPct)
    ? `<span class="cell-${chgCls}">${(chgPct >= 0 ? '+' : '') + chgPct.toFixed(2)}%</span>`
    : '—';
  const turnover = snap.turnover != null ? `${snap.turnover.toFixed(2)}%` : '—';
  const mainPct = snap.main_pct;
  const retailPct = snap.retail_pct;
  const mainPctHtml = mainPct != null
    ? `<span class="${mainPct >= 30 ? 'cell-up' : mainPct < 20 ? 'cell-down' : 'cell-flat'}">${mainPct.toFixed(1)}%</span>`
    : '—';
  const pct5 = snap.pct_5d;
  const pct10 = snap.pct_10d;
  const pct5Html = pct5 != null
    ? `<span class="${pct5 >= 0 ? 'cell-up' : 'cell-down'}">${(pct5 >= 0 ? '+' : '') + pct5.toFixed(1)}%</span>`
    : '<span class="dim">—</span>';
  const pct10Html = pct10 != null
    ? `<span class="${pct10 >= 0 ? 'cell-up' : 'cell-down'}">${(pct10 >= 0 ? '+' : '') + pct10.toFixed(1)}%</span>`
    : '<span class="dim">—</span>';
  const secZt = snap.sector_zt;
  const secLink = secZt != null
    ? `⚡${secZt}只 <span class="dim">/ ${snap.streak || 0}连板</span>`
    : '<span class="dim">—</span>';

  // AI cell
  let aiCellHtml;
  if (!ai) {
    aiCellHtml = `<button class="btn btn-mini wl-btn-add" data-wl-ai="${escapeHtml(code)}">+ 添加分析</button>`;
  } else {
    const v = ai.verdict || '-';
    const vCls = ({ '买': 'buy', '观望': 'wait', '回避': 'avoid' })[v] || 'na';
    const conv = ai.conviction ?? 0;
    const stale = ai.is_stale ? '<span class="wl-stale-tag" title="跨日判定">昨日</span>' : '';
    aiCellHtml = `
      <div class="wl-ai-cell">
        <span class="verdict-pill v-${vCls}">${escapeHtml(v)} <b>${conv}</b></span>
        ${stale}
        ${ai.summary ? `<p class="wl-ai-summary" title="${escapeHtml(ai.summary)}">${escapeHtml(ai.summary.slice(0, 40))}${ai.summary.length > 40 ? '…' : ''}</p>` : ''}
        <button class="btn btn-tiny wl-btn-reai" data-wl-ai="${escapeHtml(code)}" title="重新 AI">↻</button>
      </div>
    `;
  }

  // 时间窗口 cell
  let windowHtml;
  if (ai && ai.suggested_window) {
    const winCls = ai.suggested_window === '暂观望' ? 'wl-win-wait' :
                   ai.suggested_window === '今早竞价' ? 'wl-win-fast' :
                   ai.suggested_window === '14:00 后' ? 'wl-win-late' : '';
    windowHtml = `<span class="wl-window ${winCls}">${escapeHtml(ai.suggested_window)}</span>`;
    if (ai.entry_price_range) {
      windowHtml += `<div class="wl-entry">入 ${escapeHtml(ai.entry_price_range)}</div>`;
    }
    if (ai.stop_loss) {
      windowHtml += `<div class="wl-stop">止 ${escapeHtml(ai.stop_loss)}</div>`;
    }
  } else {
    windowHtml = '<span class="dim">—</span>';
  }

  return `
    <tr data-code="${escapeHtml(code)}">
      <td><code class="wl-code" data-wl-detail="${escapeHtml(code)}">${escapeHtml(code)}</code></td>
      <td><span data-wl-detail="${escapeHtml(code)}" style="cursor:pointer">${escapeHtml(name)}</span></td>
      <td>${price}</td>
      <td>${chgHtml}</td>
      <td>${turnover}</td>
      <td>${mainPctHtml}${retailPct != null ? `<div class="dim" style="font-size:.7rem">散户 ${retailPct.toFixed(0)}%</div>` : ''}</td>
      <td>${pct5Html}</td>
      <td>${pct10Html}</td>
      <td>${secLink}</td>
      <td>${aiCellHtml}</td>
      <td>${windowHtml}</td>
      <td class="wl-ops">
        <button class="btn btn-tiny" data-wl-ai="${escapeHtml(code)}" title="AI 判定">✨</button>
        <button class="btn btn-tiny" data-wl-detail="${escapeHtml(code)}" title="查看个股">→</button>
        <button class="btn btn-tiny wl-btn-del" data-wl-remove="${escapeHtml(code)}" title="删除">✕</button>
      </td>
    </tr>
  `;
}

async function _watchlistRemove(code) {
  // 2026-07-15: 不再弹 confirm,直接删 + 提示成功
  try {
    const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(code), { method: 'DELETE' });
    const j = await r.json();
    if (j.ok) {
      showToast(`✓ 已删除 ${code}`, 'success');
      _watchlistLoaded = false;
      _watchlistLoad();
    } else {
      showToast(`删除失败: ${j.error || ''}`, 'error');
    }
  } catch (e) {
    showToast(`删除失败: ${e.message}`, 'error');
  }
}

async function _watchlistAnalyzeOne(code, silent = false) {
  if (!silent) showToast(`AI 判定 ${code} 中 … (建议 20-30 秒)`, 'info');
  const btn = $(`#wl-tbody [data-wl-ai="${code}"]`);
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(code) + '/ai', {
      method: 'POST',
    });
    const j = await r.json();
    if (j.ok && j.data && j.data.ai) {
      showToast(`✓ ${code} AI 完成: ${j.data.ai.verdict || '-'} (${j.data.ai.conviction || 0}/100)`, 'success');
      _watchlistLoad();
    } else {
      if (!silent) showToast(`AI 失败: ${j.error || '未知错误'}`, 'error');
      if (btn) { btn.disabled = false; btn.textContent = '↻'; }
    }
  } catch (e) {
    if (!silent) showToast(`AI 失败: ${e.message}`, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '↻'; }
  }
}

async function _watchlistBatchAI() {
  if (_watchlistBatchRunning) { showToast('已有批量任务进行中', 'info'); return; }
  const items = _watchlistItems.filter(it => !it.ai);
  if (!items.length) {
    showToast('所有股票都已有 AI 建议 · 单击 ↻ 重新判定', 'info');
    return;
  }
  // 2026-07-15: 不再弹 confirm,直接跑
  _watchlistBatchRunning = true;
  const card = $('#wl-batch-card');
  const fill = $('#wl-batch-fill');
  const status = $('#wl-batch-status');
  const log = $('#wl-batch-log');
  card.hidden = false;
  fill.style.width = '0%';
  log.innerHTML = '';
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    status.textContent = `(${i + 1}/${items.length}) 判定 ${it.code} ${it.name}`;
    try {
      const r = await _fetchWithTimeout('/api/watchlist/' + encodeURIComponent(it.code) + '/ai', { method: 'POST' });
      const j = await r.json();
      if (j.ok && j.data && j.data.ai) {
        const ai = j.data.ai;
        const item = document.createElement('li');
        item.className = 'wl-batch-ok';
        item.innerHTML = `<b>${escapeHtml(it.code)}</b> ${escapeHtml(it.name)} — <span class="verdict-pill v-${({ '买': 'buy', '观望': 'wait', '回避': 'avoid' })[ai.verdict] || 'na'}">${escapeHtml(ai.verdict)} ${ai.conviction || 0}/100</span>`;
        log.appendChild(item);
      } else {
        const item = document.createElement('li');
        item.className = 'wl-batch-fail';
        item.textContent = `${it.code} 失败: ${j.error || '未知'}`;
        log.appendChild(item);
      }
    } catch (e) {
      const item = document.createElement('li');
      item.className = 'wl-batch-fail';
      item.textContent = `${it.code} 失败: ${e.message}`;
      log.appendChild(item);
    }
    fill.style.width = `${((i + 1) / items.length) * 100}%`;
  }
  status.textContent = `✅ 完成 · 共 ${items.length} 只`;
  _watchlistBatchRunning = false;
  _watchlistLoad();
}

// R-mob-040: 检测 table-wrap 横向溢出 — 容器超宽时加 .has-overflow-x,触发右边缘渐隐
function _initTableOverflowHints() {
  const wraps = document.querySelectorAll('.table-wrap');
  const update = (wrap) => {
    const has = wrap.scrollWidth > wrap.clientWidth + 1;
    wrap.classList.toggle('has-overflow-x', has);
  };
  wraps.forEach(update);
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => wraps.forEach(update), 100);
  });
  if (typeof MutationObserver !== 'undefined') {
    const mo = new MutationObserver(() => wraps.forEach(update));
    wraps.forEach(w => mo.observe(w, { childList: true, subtree: true }));
  }
}

// 初始绑定 (DOMContentLoaded 时执行一次)
document.addEventListener('DOMContentLoaded', () => {
  // 批量 AI 按钮
  const batchBtn = $('#wl-batch-ai');
  if (batchBtn && !batchBtn._bound) {
    batchBtn._bound = true;
    batchBtn.addEventListener('click', _watchlistBatchAI);
  }
  // 刷新行情按钮
  const refBtn = $('#wl-refresh-quote');
  if (refBtn && !refBtn._bound) {
    refBtn._bound = true;
    refBtn.addEventListener('click', () => {
      _watchlistLoaded = false;
      _watchlistLoad();
      showToast('已刷新行情', 'info');
    });
  }
});

// 初始绑定(用户直接打开 review 时)
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(_reviewOnViewEnter, 200);
  // 资金占比轮询由 _reviewOnViewEnter 内的 capTimer 全权负责,这里不再重复 setInterval
  // (之前重复导致每 5s 一次拉取)

  // K线 · 周期切换
  $$('#kline-period .kt-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const days = +btn.dataset.days;
      if (days === klineState.period) return;
      klineState.period = days;
      syncKlineToolbar();
      if (currentStockCode) loadKline(currentStockCode, days);
    });
  });
  // K线 · 指标 toggle (MACD/KDJ 互斥)
  $$('#kline-indicators .kt-chip').forEach(btn => {
    btn.addEventListener('click', () => toggleKlineIndicator(btn.dataset.ind));
  });
});

/* ════════════════════════════════════════════════════════════════════
 * 全 A 风向 · initAllStocks 函数族 (2026-07-14 从 all_stocks.html 迁入)
 * 入口: 监听 view-enter 当 name === 'all_stocks' 时初始化
 * 容器: .view-all_stocks · ID 前缀: as- · 复用全 app shell (sidebar/topbar/ticker)
 * 修复:
 *   1. 真正的 #as-scroll-sentinel 放入 DOM (R16 无限滚动触发)
 *   2. 涨跌额排序 (change_amt) 后端已支持 + dropdown 已加选项
 *   3. 深链 ps/off 状态由 syncUrl (silent) 与 applyAllStocksDeepLink 处理
 *   4. Reset 清 state.pageSize/offset = 30/0,syncUrl 不再写出 ps/off
 *   5. 统一级联: applyAllStocksCascade(layer)
 *   6. 自选写走 POST /api/watchlist (跟读同源)
 *   7. 列显隐用 data-col 属性匹配,不再 textContent
 *   8. 领域由 /api/all_stocks/filters.domains 填充
 *   9. 单 filter UI: 桌面 popup + 移动 placeholder 复用 app shell
 * ════════════════════════════════════════════════════════════════════ */
