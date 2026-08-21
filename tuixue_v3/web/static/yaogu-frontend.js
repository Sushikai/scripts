// yaogu-frontend.js — 妖股页面 (YAOGU 500 调研 → 1000 迭代 · 2026-08-09)
// 数据源: /api/yaogu/live (实时榜单) + /api/yaogu/backtest (同 ZT 口径回测)
(function () {
  const mount = document.getElementById('yaogu-mount');
  if (!mount) return;
  let _live = null;
  // R156 2026-08-19: /api/yaogu/live 冷启 30-60s,先渲骨架避免空白闪烁
  if (!mount.dataset.initialized) {
    mount.dataset.initialized = '1';
    mount.innerHTML = `<div style="padding:24px;text-align:center;color:var(--ink-3);font-size:13px;">
      <div style="margin-bottom:8px;">妖股榜单加载中…</div>
      <div style="font-size:11px;opacity:.7;">首次冷算需 30-60s (实时打分 6 维 · 多源聚合), 之后秒级</div>
    </div>`;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  function pct(n, d = 1) { return n == null ? '—' : (n > 0 ? '+' : '') + Number(n).toFixed(d) + '%'; }
  function scoreColor(s) {
    if (s >= 70) return 'var(--color-danger)';
    if (s >= 50) return 'var(--accent-2)';
    return 'var(--ink-2)';
  }
  function stageChip(stage) {
    const color = stage.includes('加速') ? 'var(--color-danger)' : stage === '主升期' ? 'var(--accent-2)' : 'var(--accent)';
    return `<span style="display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;color:#fff;background:${color};">${stage}</span>`;
  }
  function gateChip(open) {
    return open
      ? `<span style="display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;font-weight:600;color:#fff;background:var(--color-danger);">闸门开 · 可抓</span>`
      : `<span style="display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;font-weight:600;color:var(--ink-2);background:var(--bg-1);border:1px solid var(--line-soft);">闸门关 · 观望</span>`;
  }
  function gotoStock(code) {
    if (typeof window.gotoStock === 'function') window.gotoStock(code);
    else if (typeof window.showView === 'function') window.showView('stock');
  }

  // ── 环境条 ──
  function renderEnv(env) {
    const state = (env && env.state) || '冰点';
    const color = (env && env.color) || 'var(--ink-3)';
    const desc = (env && env.desc) || '—';
    const gate = env && env.gate;
    return `
      <div class="card" style="margin-bottom:12px;">
        <div class="card-head"><span>情绪环境 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(R102: 4 态状态机 · 抓妖开关)</span></span></div>
        <div style="display:flex;flex-wrap:wrap;gap:10px 18px;padding:10px 12px;font-size:13px;align-items:center;">
          <span>涨停家数 <b style="font-family:monospace;font-size:14px;">${env ? env.zt_count : '—'}</b>
            <span style="color:var(--ink-3);font-size:11px;">(闸门 ≥30)</span></span>
          <span>晋级率 <b style="font-family:monospace;font-size:14px;">${env && env.promo_pct != null ? env.promo_pct + '%' : '—'}</b>
            <span style="color:var(--ink-3);font-size:11px;">(闸门 ≥25%)</span></span>
          <span style="display:inline-block;padding:2px 12px;border-radius:10px;font-size:12px;font-weight:700;color:#fff;background:${color};">${state}</span>
          <span style="font-size:11.5px;color:var(--ink-3);">${desc}</span>
          ${gate ? '<span style="font-size:11px;color:var(--color-danger);font-weight:600;">· 信号开放</span>' : '<span style="font-size:11px;color:var(--ink-3);">· 信号停发</span>'}
        </div>
      </div>`;
  }

  // ── 抓取信号 ──
  function renderSignals(signals) {
    if (!signals || !signals.length) {
      return `
        <div class="card" style="margin-bottom:12px;">
          <div class="card-head"><span>抓取信号 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(今日无 · 闸门关或无一字外的 2/3 板)</span></span></div>
          <div style="padding:14px;color:var(--ink-2);font-size:12.5px;">闸门未开或无候选 — 空仓观望也是纪律。</div>
        </div>`;
    }
    console.log('[yaogu] renderSignals', signals.length, 'rows:', signals.slice(0, 3).map(s => s.code));
    const rows = signals.map(s => `
      <tr style="cursor:pointer;" onclick="gotoStock('${s.code}')">
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-family:monospace;font-size:13px;color:var(--accent);">${s.code}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-size:13px;font-weight:600;">${esc(s.name)}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;">
          <span style="display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;font-weight:600;color:#fff;background:${s.streak === 2 ? 'var(--accent)' : 'var(--accent-2)'};">${esc(s.type)}${s.lanban ? ' <span title="烂板出妖 · 换手≥20% + 炸板" style="color:#fbbf24;">★</span>' : ''}</span>
        </td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-family:monospace;font-size:13px;color:${scoreColor(s.score)};">${s.score}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-size:12px;color:var(--ink-2);">明日 09:30 开盘可买 · 断板即卖</td>
      </tr>`).join('');
    return `
      <div class="card" style="margin-bottom:12px;">
        <div class="card-head">
          <span>抓取信号 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(今日已锁板 → 明日 09:30 集合竞价可买 · ${signals.length} 条)</span></span>
        </div>
        <div style="padding:8px 12px 4px;overflow-x:auto;">
          <table class="data-table" style="width:100%;min-width:560px;font-size:12.5px;border-collapse:collapse;">
            <thead><tr style="font-size:11.5px;color:var(--ink-2);background:var(--bg-1,#f8fafc);">
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:90px;">代码</th>
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:110px;">名称</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:100px;">信号</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:80px;">妖性分</th>
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);">提示</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  // ── 断板预警 ──
  function renderWatch(watch) {
    if (!watch || !watch.length) return '';
    const rows = watch.map(w => `
      <tr style="cursor:pointer;" onclick="gotoStock('${w.code}')">
        <td style="padding:6px 10px;border-bottom:1px solid var(--line-soft);font-family:monospace;font-size:12.5px;color:var(--color-danger);">${w.code}</td>
        <td style="padding:6px 10px;border-bottom:1px solid var(--line-soft);font-size:13px;">${esc(w.name)}</td>
        <td style="padding:6px 10px;border-bottom:1px solid var(--line-soft);font-size:12px;color:var(--ink-2);">${esc(w.note)} · <b style="color:var(--color-danger);">断板即走</b> (调研: 断板后 5 日胜率仅 26%)</td>
      </tr>`).join('');
    return `
      <div class="card" style="margin-bottom:12px;">
        <div class="card-head"><span>断板预警 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(昨日连板今日未涨停 → 持有者今日收盘卖)</span></span></div>
        <div style="padding:8px 12px 4px;overflow-x:auto;">
          <table class="data-table" style="width:100%;min-width:480px;font-size:12.5px;border-collapse:collapse;">
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  // ── 妖股榜单 ──
  function renderList(stocks) {
    const rows = stocks.map(s => `
      <tr style="cursor:pointer;" onclick="gotoStock('${s.code}')">
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-family:monospace;font-size:13px;color:var(--accent);">${s.code}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-size:13px;font-weight:600;">${esc(s.name)}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-family:monospace;font-size:14px;font-weight:700;color:var(--color-danger);">${s.streak}板</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-family:monospace;font-size:14px;font-weight:700;color:${scoreColor(s.score)};">${s.score}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;">${stageChip(s.stage)}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-family:monospace;font-size:12.5px;">${s.turnover != null ? s.turnover + '%' : '—'}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-family:monospace;font-size:12.5px;">${s.fund_ratio != null ? s.fund_ratio : '—'}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:center;font-size:12px;">${s.burst ? `<span style="color:var(--color-danger);">${s.burst}炸</span>` : '—'}${s.one_word ? ' <span style="color:var(--ink-3);">一字</span>' : ''}${s.lanban ? ' <span title="烂板出妖 · 换手≥20% + 炸板" style="color:#fbbf24;font-weight:600;">烂⭐</span>' : ''}</td>
        <td style="padding:8px 10px;border-bottom:1px solid var(--line-soft);font-size:12px;color:var(--ink-2);">${esc(s.sector || '')}</td>
      </tr>`).join('');
    return `
      <div class="card">
        <div class="card-head">
          <span>妖股榜单 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(妖性评分 6 维: 连板/量能/市值/资金/题材/环境)</span></span>
          <span style="font-size:11px;color:var(--ink-3);">共 ${stocks.length} 只连板</span>
        </div>
        <div style="padding:8px 12px 4px;overflow-x:auto;">
          <table class="data-table" style="width:100%;min-width:760px;font-size:12.5px;border-collapse:collapse;">
            <thead><tr style="font-size:11.5px;color:var(--ink-2);background:var(--bg-1,#f8fafc);">
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:90px;">代码</th>
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:100px;">名称</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:60px;">连板</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:70px;">妖性分</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:90px;">阶段</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:70px;">换手</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:70px;">封单比</th>
              <th style="text-align:center;padding:8px 10px;border-bottom:1px solid var(--line-soft);width:70px;">炸板</th>
              <th style="text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-soft);">板块</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <p style="padding:6px 12px 10px;font-size:11px;color:var(--ink-3);">妖性分 ≥70 核心妖股 · ≥50 妖股候选 · &lt;50 观察。一字板 = 买不进, 当日仅观察。</p>
      </div>`;
  }

  // ── 铁律卡 ──
  function renderRules() {
    return `
      <div class="card" style="margin-top:12px;">
        <div class="card-head"><span>妖股铁律 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(来自 2020-2026 共 27263 连板段的统计分析)</span></span></div>
        <div style="padding:10px 12px;font-size:12.5px;line-height:1.8;color:var(--ink-2);">
          <div><b style="color:var(--color-danger);">① 断板即走</b> — 断板后 5 日平均 -7%~-12%, 胜率仅 22-27%; 别等反包。</div>
          <div><b style="color:var(--color-danger);">② 不做断板低吸/首阴反包</b> — 实测胜率 34%, 平均 -2.91%/笔, 负期望。</div>
          <div><b style="color:var(--color-danger);">③ 不追 ≥6 板</b> — 一字空仓率 61%+, 高位全是买不进和接盘。</div>
          <div><b style="color:var(--accent-2);">④ 2 板介入期望最优</b> — 胜率 42%, 平均 +0.52%/笔 (含 32.7% 一字空仓); 3 板 avg 更高但空仓率 56%。</div>
          <div><b style="color:var(--accent-2);">⑤ 闸门关不抓</b> — 4 态状态机: <span style="color:var(--color-danger);font-weight:600;">极热</span>(涨停≥80+晋级≥30%) / <span style="color:var(--accent-2);font-weight:600;">高潮</span>(涨停≥30) 信号开放; <span style="color:var(--accent);font-weight:600;">回暖</span>(涨停15-30) 观察; <span style="color:var(--ink-3);font-weight:600;">冰点</span>(&lt;15) 空仓。</div>
          <div><b style="color:var(--accent-2);">⑥ 买入执行同 ZT</b> — 明日 09:30 开盘价买, 一字板直接放弃, 双边成本 0.66%。</div>
        </div>
      </div>`;
  }

  // ── 回测卡 ──
  let _btRunning = false;
  function renderBtCard() {
    return `
      <div class="card" style="margin-top:12px;">
        <div class="card-head"><span>妖股回测 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(与 ZT 同口径: T+1 开盘买 + 一字空仓 + 0.66% 成本)</span></span></div>
        <div style="padding:10px 12px;display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;font-size:12.5px;">
          <label>介入连板 <select id="yg-bt-entry" style="padding:3px 6px;border:1px solid var(--line-soft);border-radius:6px;background:var(--bg);color:var(--ink);">
            <option value="1">1板后</option><option value="2" selected>2板后 (推荐)</option><option value="3">3板后</option><option value="4">4板后</option>
          </select></label>
          <label>退出 <select id="yg-bt-exit" style="padding:3px 6px;border:1px solid var(--line-soft);border-radius:6px;background:var(--bg);color:var(--ink);">
            <option value="hard_stop" selected>断板 + -8% 双保险 (R100 推荐)</option>
            <option value="break_close">断板收盘卖 (铁律)</option>
            <option value="hold_n">持有 N 日</option>
            <option value="ma5_stop">破 5 日线卖</option>
            <option value="stop_loss">-8% 止损</option>
          </select></label>
          <label id="yg-bt-sl-wrap" style="display:none;">硬止损 <input type="number" id="yg-bt-sl" value="-8" step="0.5" min="-30" max="-2" style="width:60px;padding:3px 6px;border:1px solid var(--line-soft);border-radius:6px;background:var(--bg);color:var(--ink);font-family:monospace;">%</label>
          <label><input type="checkbox" id="yg-bt-gate" checked> 环境闸门</label>
          <button class="btn-mini primary" id="yg-bt-run" onclick="window.__ygRunBt()">运行回测</button>
          <span id="yg-bt-status" style="color:var(--ink-3);font-size:11.5px;">约 1-2 分钟 (首次构建缓存 ~3 分钟)</span>
        </div>
        <div id="yg-bt-result" style="padding:4px 12px 12px;"></div>
      </div>`;
  }

  function renderBtResult(r) {
    if (!r) return '';
    const s = r.summary || r;
    const gate = s.gate_off ? `
      <div style="margin-top:8px;font-size:12px;color:var(--ink-2);">
        <b>闸门关闭对比:</b> ${s.gate_off.trades} 笔 · 胜率 ${s.gate_off.wr}% · avg ${pct(s.gate_off.avg)} · 累计 ${pct(s.gate_off.cum)}
        <span style="color:var(--ink-3);font-size:11px;">(当前 ${s.trades} 笔, avg ${pct(s.avg)} — 闸门提升 ${s.gate_off.avg ? ((s.avg - s.gate_off.avg) / Math.abs(s.gate_off.avg) * 100).toFixed(0) : '—'}% 单笔期望)</span>
      </div>` : '';
    const byStreak = s.by_streak ? Object.entries(s.by_streak).map(([k, v]) => `
      <span style="display:inline-block;margin:2px 8px 2px 0;font-size:12px;"><b>${k}板</b> ${v.n}笔 ${v.wr}%胜率 avg <b style="color:${v.avg > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(v.avg)}</b></span>
    `).join('') : '';
    return `
      <div style="display:flex;flex-wrap:wrap;gap:8px 22px;padding:10px 12px;background:var(--bg-1,#f8fafc);border-radius:8px;margin-top:6px;">
        <span>交易 <b style="font-family:monospace;font-size:14px;">${s.trades}</b> <span style="color:var(--ink-3);font-size:11px;">(一字空仓 ${s.one_word_pct ?? 0}%)</span></span>
        <span>胜率 <b style="font-family:monospace;font-size:14px;color:${s.wr >= 50 ? 'var(--color-danger)' : 'var(--ink)'};">${s.wr}%</b></span>
        <span>avg <b style="font-family:monospace;font-size:14px;color:${s.avg > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(s.avg)}</b></span>
        <span>累计 <b style="font-family:monospace;font-size:14px;color:${s.cum > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(s.cum)}</b></span>
        <span>盈亏比 <b style="font-family:monospace;font-size:14px;">${s.pf ?? '—'}</b></span>
        <span>best <b style="font-family:monospace;font-size:13px;color:var(--color-danger);">${pct(s.best)}</b> worst <b style="font-family:monospace;font-size:13px;color:var(--ink-3);">${pct(s.worst)}</b></span>
        <span style="color:var(--ink-3);font-size:11px;">${s.elapsed_s ?? ''}s</span>
      </div>
      <div style="padding:8px 12px 2px;">${byStreak}</div>
      ${gate}
      <p style="padding:4px 12px 0;font-size:11px;color:var(--ink-3);">按断板时最终连板数分组 — 持有到断板纪律下, 走出来的票贡献主要收益; 断在介入板数的一批是主要亏损来源。</p>`;
  }

  // ── R101 烂板 A/B 回测卡 ──
  let _abRunning = false;
  let _abResult = null;
  function renderLanbanABCtl() {
    return `
      <div class="card" style="margin-top:12px;">
        <div class="card-head">
          <span>烂板 A/B 回测 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(R101: 烂板出妖 · 调研支撑 · 同时跑"全2板"和"烂板2板"对比胜率)</span></span>
          <button class="btn-mini primary" id="yg-ab-run" onclick="window.__ygRunLanbanAB()" style="margin-left:auto;">跑 A/B</button>
        </div>
        <div id="yg-ab-result" style="padding:8px 12px 12px;font-size:12.5px;"></div>
      </div>`;
  }
  function renderLanbanAB(r) {
    if (!r) return '<div style="color:var(--ink-3);padding:6px 0;">尚未运行 — 点 "跑 A/B" 启动。</div>';
    const all = r.all, lb = r.lanban;
    const liftWr = lb.wr - all.wr;
    const liftAvg = lb.avg - all.avg;
    return `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div style="padding:10px 12px;background:var(--bg-1,#f8fafc);border-radius:8px;">
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">A: 全 2 板信号 (基线)</div>
          <div>交易 <b style="font-family:monospace;font-size:13px;">${all.trades}</b> · 胜率 <b style="font-family:monospace;font-size:14px;">${all.wr}%</b></div>
          <div>avg <b style="font-family:monospace;color:${all.avg > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(all.avg)}</b> · 累计 <b style="font-family:monospace;color:${all.cum > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(all.cum)}</b></div>
          <div style="font-size:11px;color:var(--ink-3);margin-top:4px;">worst ${pct(all.worst)} · best ${pct(all.best)}</div>
        </div>
        <div style="padding:10px 12px;background:rgba(251,191,36,0.07);border:1px solid rgba(251,191,36,0.3);border-radius:8px;">
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">B: 烂板 2 板 (turnover≥20% + 炸板)</div>
          <div>交易 <b style="font-family:monospace;font-size:13px;">${lb.trades}</b> · 胜率 <b style="font-family:monospace;font-size:14px;color:${lb.wr >= 50 ? 'var(--color-danger)' : 'var(--ink)'};">${lb.wr}%</b></div>
          <div>avg <b style="font-family:monospace;color:${lb.avg > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(lb.avg)}</b> · 累计 <b style="font-family:monospace;color:${lb.cum > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${pct(lb.cum)}</b></div>
          <div style="font-size:11px;color:var(--ink-3);margin-top:4px;">worst ${pct(lb.worst)} · best ${pct(lb.best)}</div>
        </div>
      </div>
      <div style="padding:8px 0 2px;font-size:12px;">
        烂板相对全 2 板: 胜率 <b style="color:${liftWr > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${liftWr > 0 ? '+' : ''}${liftWr.toFixed(1)}pp</b>
        · avg <b style="color:${liftAvg > 0 ? 'var(--color-danger)' : 'var(--ink-3)'};">${liftAvg > 0 ? '+' : ''}${liftAvg.toFixed(2)}pp</b>
        · 样本缩减 <b style="font-family:monospace;">${((1 - lb.trades / Math.max(1, all.trades)) * 100).toFixed(0)}%</b>
      </div>
      <p style="font-size:11px;color:var(--ink-3);padding:0;">烂板代理 = 涨停 + 非一字 + 当日有过博弈 (OHLC 波动)。⚠️ cache_db turnover 数据稀疏(覆盖 1%), 严格 turnover 版无样本, 此处用 OHLC 宽松代理 — 实盘请以实时 turnover+炸板为准。</p>`;
  }

  // ── R103 首阴战法证伪对比卡 ──
  function renderFirstYinCard() {
    return `
      <div class="card" style="margin-top:12px;">
        <div class="card-head"><span>禁: 首阴战法证伪 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(R103 · 市场流行的"断板低吸/首阴反包"在历史数据上被证伪)</span></span></div>
        <div style="padding:10px 12px;font-size:12.5px;">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div style="padding:10px 12px;background:rgba(220,38,38,0.06);border:1px solid rgba(220,38,38,0.25);border-radius:8px;">
              <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">⚠ 首阴低吸 (断板日收盘买 → 次日卖)</div>
              <div>胜率 <b style="font-family:monospace;font-size:18px;color:var(--color-danger);">34%</b></div>
              <div>avg/笔 <b style="font-family:monospace;color:var(--color-danger);font-size:14px;">-2.91%</b></div>
              <div>累计 <b style="font-family:monospace;color:var(--color-danger);">-4709%</b> <span style="color:var(--ink-3);font-size:10.5px;">(1616 笔)</span></div>
              <div style="font-size:11px;color:var(--ink-3);margin-top:6px;">负期望 · 严格避坑</div>
            </div>
            <div style="padding:10px 12px;background:var(--bg-1,#f8fafc);border-radius:8px;">
              <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">✓ 我们的纪律: 2 板介入 + 断板卖 + 闸门</div>
              <div>胜率 <b style="font-family:monospace;font-size:18px;color:var(--accent-2);">40%</b></div>
              <div>avg/笔 <b style="font-family:monospace;color:var(--accent-2);font-size:14px;">+0.60%</b></div>
              <div>累计 <b style="font-family:monospace;color:var(--color-danger);">+773%</b> <span style="color:var(--ink-3);font-size:10.5px;">(1289 笔)</span></div>
              <div style="font-size:11px;color:var(--ink-3);margin-top:6px;">正期望 · 持有到断板 · 闸门过滤低质机会</div>
            </div>
          </div>
          <div style="padding:10px 0 4px;font-size:12px;line-height:1.7;color:var(--ink-2);">
            <b style="color:var(--color-danger);">为什么"首阴战法"在历史数据上亏钱:</b>
            <ul style="margin:4px 0 4px 18px;padding:0;font-size:11.5px;color:var(--ink-2);">
              <li>断板日的"反包预期"是幸存者偏差 — 大多数断板后持续阴跌</li>
              <li>断板后 5 日平均 -7%~-12%, 胜率仅 22-27%</li>
              <li>看似"低吸便宜", 实则接了最后一棒</li>
            </ul>
            <b style="color:var(--accent-2);">为什么我们坚持"断板即走":</b>
            <ul style="margin:4px 0 0 18px;padding:0;font-size:11.5px;color:var(--ink-2);">
              <li>2 板介入期望最优 (胜率 42%, avg +0.52%, 一字空仓率 32.7%)</li>
              <li>持有到断板 — 走出来的票 (5板+/6+) 贡献主要收益</li>
              <li>环境闸门过滤 62% 低质机会, 单笔期望从 +0.52% 提到 +0.60%</li>
            </ul>
          </div>
          <div style="padding-top:6px;font-size:11px;color:var(--ink-3);">数据来源: YAOGU_500_SURVEY §6b.3 · 2020-2026 · 1616 笔断板低吸 vs 3410 笔 2 板介入同窗口对比</div>
        </div>
      </div>`;
  }

  async function runLanbanAB() {
    if (_abRunning) return;
    _abRunning = true;
    const st = document.getElementById('yg-ab-result');
    if (st) st.innerHTML = '<div style="padding:8px;color:var(--ink-3);">A/B 运行中…</div>';
    try {
      // 跑两次: A=全 2 板信号(闸门开), B=烂板 2 板(闸门开)
      // 通过 query 强制 zt_min=1 promo_min=0 避开闸门数据稀疏
      const urlA = `/api/yaogu/backtest?entry=2&exit_rule=break_close&gate=1&zt_min=1&promo_min=0`;
      const rA = await fetch(urlA, { signal: AbortSignal.timeout(300000) });
      const jA = await rA.json();
      if (!jA.ok) throw new Error(jA.error || 'A 失败');
      // B 走专门 endpoint (回测核心暂未支持, 先占位用 A 数据 + 一个二次筛选标记)
      // → 简化: 暂以 A 总览作为 baseline, B 用新加的 /api/yaogu/backtest_lanban
      const rB = await fetch('/api/yaogu/backtest_lanban?entry=2&exit_rule=break_close&gate=1&zt_min=1&promo_min=0',
                              { signal: AbortSignal.timeout(300000) });
      const jB = await rB.json();
      if (!jB.ok) throw new Error(jB.error || 'B 失败');
      _abResult = { all: jA.data, lanban: jB.data };
      if (st) st.innerHTML = renderLanbanAB(_abResult);
    } catch (e) {
      if (st) st.innerHTML = `<div style="color:var(--color-danger);padding:8px;">A/B 失败: ${esc(e.message)}</div>`;
    } finally {
      _abRunning = false;
    }
  }
  window.__ygRunLanbanAB = runLanbanAB;

// ── 权重徽章 (寻优来源) ──
  function renderWeightsBadge(weights, meta) {
    if (!weights) return '';
    const w = weights;
    const src = meta && meta.source === 'optimized'
      ? `<b style="color:var(--accent-2);">1000 轮寻优</b>
         <span style="color:var(--ink-3);font-size:10.5px;">in ${meta.in_sample_score} / out ${meta.out_of_sample_score} · ${esc(meta.optimized_at || '')}</span>`
      : `<b style="color:var(--ink-2);">hard-code</b><span style="color:var(--ink-3);font-size:10.5px;">(25/20/15/20/10/10)</span>`;
    return `
      <div class="card" style="margin-bottom:12px;">
        <div class="card-head"><span>评分权重 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(妖性 6 维 · ${src})</span></span></div>
        <div style="display:flex;flex-wrap:wrap;gap:6px 14px;padding:8px 12px;font-size:12px;color:var(--ink-2);">
          <span>连板 <b style="font-family:monospace;color:var(--ink);">${w.streak}</b></span>
          <span>量能 <b style="font-family:monospace;color:var(--ink);">${w.turn}</b></span>
          <span>市值 <b style="font-family:monospace;color:var(--ink);">${w.mcap}</b></span>
          <span>资金 <b style="font-family:monospace;color:var(--ink);">${w.fund}</b></span>
          <span>题材 <b style="font-family:monospace;color:var(--ink);">${w.topic}</b></span>
          <span>情绪 <b style="font-family:monospace;color:var(--ink);">${w.env}</b></span>
        </div>
      </div>`;
  }

  // ── 盘后提醒 banner ──
  function renderPostMarketBanner(d) {
    if (!_isPostMarket() || !d) return '';
    const suppressed = d.signals_suppressed;
    const review = d.review_mode;
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (suppressed && !review) return '';
    return `<div style="background:linear-gradient(135deg,#fef3c7 0%,#fde68a 100%);border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px;">
      <span style="font-size:20px;">⚠️</span>
      <div style="flex:1;font-size:13px;color:#92400e;line-height:1.5;">
        <b>盘后观察模式</b> · 当前 ${time} · 闸门${d.env?.gate ? '开' : '关'} · 下列信号<b style="color:#dc2626;">仅供次日参考,不适合立即买入</b>
        <span style="color:#78350f;font-size:11.5px;">(龙虎榜 18:00 后公布,9:30 集合竞价后才有真实可买价)</span>
      </div>
    </div>`;
  }

  // ── 主渲染 ──
  function render() {
    const d = _live;
    mount.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <span style="font-size:12px;color:var(--ink-3);">${d ? '数据日: ' + esc(d.date) : '加载中…'}</span>
        <button class="btn-mini" onclick="window.__ygRefresh()">刷新</button>
      </div>
      ${renderPostMarketBanner(d)}
      ${d ? renderEnv(d.env) : '<div class="card"><div style="padding:24px;text-align:center;color:var(--ink-3);">加载中…</div></div>'}
      ${d ? renderWeightsBadge(d.weights, d.weights_meta) : ''}
      ${d ? renderSignals(d.signals) : ''}
      ${d ? renderWatch(d.watch) : ''}
      ${d ? renderList(d.stocks) : ''}
      ${renderGbmReport()}
      ${renderRules()}
      ${renderBtCard()}
      ${renderLanbanABCtl()}
      ${renderFirstYinCard()}
    `;
    // 回测结果挂载点常驻 (避免 rerender 丢失)
    const resEl = document.getElementById('yg-bt-result');
    if (resEl && _btResult) resEl.innerHTML = renderBtResult(_btResult);
    // 默认选项是 hard_stop → 显示硬止损输入框
    const slWrap = document.getElementById('yg-bt-sl-wrap');
    if (slWrap) {
      const exitSel = document.getElementById('yg-bt-exit');
      slWrap.style.display = exitSel && (exitSel.value === 'stop_loss' || exitSel.value === 'hard_stop') ? 'inline-block' : 'none';
    }
  }
  let _btResult = null;
  let _gbmReport = null;

  // ── R116 GBR 150 维训练报告卡 ──
  function renderGbmReport() {
    if (!_gbmReport) {
      return `<div class="card" style="margin-bottom:12px;">
        <div class="card-head"><span>GBR 150 维训练报告 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(R116: 横截面+多周期+龙虎榜 · 离线回测)</span></span></div>
        <div style="padding:14px 12px;color:var(--ink-3);font-size:12.5px;">加载中…</div>
      </div>`;
    }
    const r = _gbmReport;
    const f1 = r.top_k_oos_5fold?.fwd_1d || {};
    const t1 = r.top_k_oos_ts?.fwd_1d || {};
    const f2 = r.top_k_oos_5fold?.fwd_2d || {};
    const t2 = r.top_k_oos_ts?.fwd_2d || {};
    const f5 = r.top_k_oos_5fold?.fwd_5d || {};
    const t5 = r.top_k_oos_ts?.fwd_5d || {};
    const topFeats = (r.top_features || []).slice(0, 10);

    return `
      <div class="card" style="margin-bottom:12px;">
        <div class="card-head">
          <span>GBR 150 维训练报告 <span style="font-size:11px;font-weight:400;color:var(--ink-3);">(R116: lhb+reason+interp+seat+r105横截面+r106多周期 · 离线回测 · ${esc(r.generated_at)})</span></span>
        </div>
        <div style="padding:10px 12px;font-size:12.5px;">
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px 14px;margin-bottom:10px;">
            <div><span style="color:var(--ink-3);">特征维度</span> <b style="font-family:monospace;font-size:14px;">${r.n_features}</b></div>
            <div><span style="color:var(--ink-3);">总事件数</span> <b style="font-family:monospace;font-size:14px;">${r.n_events_total}</b></div>
            <div><span style="color:var(--ink-3);">训练区间</span> <b style="font-family:monospace;">${esc(r.train_date_range?.[0] || '—')} → ${esc(r.train_date_range?.[1] || '—')}</b></div>
            <div><span style="color:var(--ink-3);">测试区间</span> <b style="font-family:monospace;">${esc(r.test_date_range?.[0] || '—')} → ${esc(r.test_date_range?.[1] || '—')}</b></div>
            <div><span style="color:var(--ink-3);">5-fold IC (GBR)</span> <b style="font-family:monospace;font-size:14px;color:var(--accent-2);">+${r.ic_5fold_gbr?.toFixed(4) || '—'}</b></div>
            <div><span style="color:var(--ink-3);">5-fold IC (Ridge)</span> <b style="font-family:monospace;">+${r.ic_5fold_ridge?.toFixed(4) || '—'}</b></div>
          </div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">随机 OOS top-K 胜率 (5-fold, mild leakage):</div>
          <table style="width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px;">
            <thead><tr style="color:var(--ink-3);font-size:11px;">
              <th style="text-align:left;padding:4px 6px;">区间</th>
              <th style="text-align:right;padding:4px 6px;">top-10</th>
              <th style="text-align:right;padding:4px 6px;">top-20</th>
              <th style="text-align:right;padding:4px 6px;">top-50</th>
            </tr></thead>
            <tbody>
              <tr><td style="padding:4px 6px;">上榜后 1 日</td>
                <td style="text-align:right;font-family:monospace;">${f1.top_10 ? f1.top_10.wr + '% / ' + f1.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f1.top_20 ? f1.top_20.wr + '% / ' + f1.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f1.top_50 ? f1.top_50.wr + '% / ' + f1.top_50.avg_ret + '%' : '—'}</td></tr>
              <tr><td style="padding:4px 6px;">上榜后 2 日</td>
                <td style="text-align:right;font-family:monospace;">${f2.top_10 ? f2.top_10.wr + '% / ' + f2.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f2.top_20 ? f2.top_20.wr + '% / ' + f2.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f2.top_50 ? f2.top_50.wr + '% / ' + f2.top_50.avg_ret + '%' : '—'}</td></tr>
              <tr><td style="padding:4px 6px;">上榜后 5 日</td>
                <td style="text-align:right;font-family:monospace;">${f5.top_10 ? f5.top_10.wr + '% / ' + f5.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f5.top_20 ? f5.top_20.wr + '% / ' + f5.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${f5.top_50 ? f5.top_50.wr + '% / ' + f5.top_50.avg_ret + '%' : '—'}</td></tr>
            </tbody>
          </table>
          <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">时间序列 OOS top-K 胜率 (前 60% 训练 → 后 40% 测试, 严格无泄漏):</div>
          <table style="width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px;">
            <thead><tr style="color:var(--ink-3);font-size:11px;">
              <th style="text-align:left;padding:4px 6px;">区间</th>
              <th style="text-align:right;padding:4px 6px;">top-10</th>
              <th style="text-align:right;padding:4px 6px;">top-20</th>
              <th style="text-align:right;padding:4px 6px;">top-50</th>
              <th style="text-align:right;padding:4px 6px;">top-100</th>
            </tr></thead>
            <tbody>
              <tr><td style="padding:4px 6px;">上榜后 1 日</td>
                <td style="text-align:right;font-family:monospace;color:${t1.top_10 && t1.top_10.wr >= 70 ? 'var(--color-danger)' : 'var(--ink)'};">${t1.top_10 ? t1.top_10.wr + '% / ' + t1.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t1.top_20 ? t1.top_20.wr + '% / ' + t1.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t1.top_50 ? t1.top_50.wr + '% / ' + t1.top_50.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t1.top_100 ? t1.top_100.wr + '% / ' + t1.top_100.avg_ret + '%' : '—'}</td></tr>
              <tr><td style="padding:4px 6px;">上榜后 2 日</td>
                <td style="text-align:right;font-family:monospace;">${t2.top_10 ? t2.top_10.wr + '% / ' + t2.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t2.top_20 ? t2.top_20.wr + '% / ' + t2.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t2.top_50 ? t2.top_50.wr + '% / ' + t2.top_50.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t2.top_100 ? t2.top_100.wr + '% / ' + t2.top_100.avg_ret + '%' : '—'}</td></tr>
              <tr><td style="padding:4px 6px;">上榜后 5 日</td>
                <td style="text-align:right;font-family:monospace;">${t5.top_10 ? t5.top_10.wr + '% / ' + t5.top_10.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t5.top_20 ? t5.top_20.wr + '% / ' + t5.top_20.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t5.top_50 ? t5.top_50.wr + '% / ' + t5.top_50.avg_ret + '%' : '—'}</td>
                <td style="text-align:right;font-family:monospace;">${t5.top_100 ? t5.top_100.wr + '% / ' + t5.top_100.avg_ret + '%' : '—'}</td></tr>
            </tbody>
          </table>
          ${topFeats.length ? `<div style="font-size:11.5px;color:var(--ink-3);margin-bottom:6px;">Top 10 特征重要度 (GBR):</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px 6px;font-family:monospace;font-size:11.5px;">
            ${topFeats.map(f => `<span style="display:inline-block;padding:2px 8px;border-radius:8px;background:var(--bg-1,#f8fafc);border:1px solid var(--line-soft);"><span style="color:var(--ink-3);">${esc(f.name)}</span> <b style="color:var(--accent);">${f.imp}</b></span>`).join('')}
          </div>` : ''}
        </div>
      </div>`;
  }

  async function loadGbmReport() {
    try {
      const r = await fetch('/api/yaogu/gbm_report', { signal: AbortSignal.timeout(8000) });
      const j = await r.json();
      if (j.ok) {
        _gbmReport = j.data;
        render();
      }
    } catch (e) { /* 静默 */ }
  }

  // 判断是否盘后 (15:00 之后) — 盘后自动用 review mode
  function _isPostMarket() {
    const h = new Date().getHours();
    return h >= 15 || h < 9;
  }

  async function loadLive(force) {
    try {
      const url = new URL('/api/yaogu/live', location.origin);
      if (force) url.searchParams.set('force', '1');
      // 盘后 (15:00-次日 09:00) 自动切 review mode
      if (_isPostMarket()) url.searchParams.set('mode', 'review');
      const r = await fetch(url.pathname + url.search, { signal: AbortSignal.timeout(20000) });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || '加载失败');
      _live = j.data;
      render();
    } catch (e) {
      mount.innerHTML = `<div class="card"><div style="padding:20px;color:var(--color-danger);">妖股榜单加载失败: ${esc(e.message)} <button class="btn-mini primary" onclick="window.__ygRefresh()">重试</button></div></div>`;
    }
  }

  async function runBt() {
    if (_btRunning) return;
    _btRunning = true;
    const st = document.getElementById('yg-bt-status');
    const entry = document.getElementById('yg-bt-entry').value;
    const exit = document.getElementById('yg-bt-exit').value;
    const gate = document.getElementById('yg-bt-gate').checked ? 1 : 0;
    const slEl = document.getElementById('yg-bt-sl');
    const sl = (exit === 'stop_loss' || exit === 'hard_stop') && slEl ? slEl.value : '';
    if (st) st.textContent = '运行中… 首次需 1-3 分钟 (构建事件缓存), 之后秒级';
    const resEl = document.getElementById('yg-bt-result');
    if (resEl) resEl.innerHTML = '<div style="padding:12px;color:var(--ink-3);">回测运行中…</div>';
    try {
      let url = `/api/yaogu/backtest?entry=${entry}&exit_rule=${exit}&gate=${gate}`;
      if (sl !== '') url += `&stop_loss=${encodeURIComponent(sl)}`;
      const r = await fetch(url, { signal: AbortSignal.timeout(300000) });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || '回测失败');
      _btResult = j.data;
      if (st) st.textContent = '完成 ' + new Date().toLocaleTimeString();
      if (resEl) resEl.innerHTML = renderBtResult(_btResult);
    } catch (e) {
      if (st) st.textContent = '失败: ' + e.message;
      if (resEl) resEl.innerHTML = `<div style="padding:12px;color:var(--color-danger);">${esc(e.message)}</div>`;
    } finally {
      _btRunning = false;
    }
  }
  window.__ygRunBt = runBt;
  window.__ygRefresh = () => loadLive(true);
  window.loadYaogu = loadLive;
  // 退出规则切换 → 动态显示硬止损输入框
  document.addEventListener('change', (e) => {
    if (e.target && e.target.id === 'yg-bt-exit') {
      const wrap = document.getElementById('yg-bt-sl-wrap');
      if (wrap) wrap.style.display = (e.target.value === 'stop_loss' || e.target.value === 'hard_stop') ? 'inline-block' : 'none';
    }
  });

  loadLive(false);
  loadGbmReport();
})();
