/* web/static/bv-frontend.js
   视频战法 · Bryan交易随笔 — 仓位管理战法
   挂在 #bv-mount,自初始化 IIFE; 5 个端点 /api/bv/{meta, rules, live_pick, scan, backtest}
   风格: 沿用项目主设计 token, 卡片密集展示 + 原话溯源 + 阶段感知 banner
   移动端 ≤768px: .bv-rules-host 折叠, 战法哲学默认收起

   R267 (2026-08-21) / SW v634
*/
(function(){
  'use strict';
  var $root = document.querySelector('.view-bv');
  if (!$root) return;

  // ── 状态 ──
  var _meta = null;
  var _rules = [];
  var _rulesById = {};
  var _picks = [];
  var _phase = 'close';
  var _loading = false;
  var _reqId = 0;
  var _dataTs = 0;   // R27: 当前 picks 快照时间戳 (秒)
  var _ageTick = null;   // R35: stale 时每秒更新 strip 文案
  var _ruleFilter = null;  // R48: 命中的规则过滤 — 点击 chip 切换
  var _scrollClose = null;   // R254: popover 打开时的 scroll 关闭监听 — 滚动=焦点转移
  var _outsideClick = null;   // R276: popover 打开时的 document click 监听 — 点 popover 外关闭
  var _escClose = null;   // R277: popover 打开时的 keydown Escape 监听 — 键盘可访问
  var _bodyScrollLock = null;   // R278: popover 打开时锁 body scroll — 避免双滚动冲突
  var _bvCurrentChip = null; // R274: popover 来源 chip 引用 — close 时给这个 chip flash
  var _popList = [];   // R262: 当前锚定卡片命中的全部规则 id 列表 (popover 内逐条浏览)
  var _popIdx = -1;    // R262: 当前展示规则在 _popList 中的下标
  var _autoPausedUntil = 0;  // R56: 暂停自动刷新截止时间 (ms) — 用户长按 strip 触发
  var _lastPicksHash = '';   // R57: 上一份 picks 哈希 — 用于判断"静默更新"
  var _lastSortedCodes = [];  // R70: 上次渲染的排序后代码列表 — prev/next 导航
  var _silentUpdateFlag = null;  // R57: 最近一次是静默更新 — {at, shown}

  // ── helpers ──
  function $(s){ return $root.querySelector(s); }
  function $$(s){ return Array.from($root.querySelectorAll(s)); }
  function esc(s){
    if (s == null) return '';
    return String(s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  function fmtPct(v, noPct){
    if (v == null || isNaN(v)) return '—';
    // R247: noPct 时省略 '%' — 涨幅是专属列, 列头语义已含单位, '%' 是同列重复噪声
    //   '+10.03%' 61px > 56px 盒被截成 '+10.0…' (5px 精度丢失); '+10.03' 48px 完整.
    //   方向由 +/- 与红绿双编码, 去 % 零信息损失.
    return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + (noPct ? '' : '%');
  }
  function fmtNum(v, d){
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  // R2004.1: 封板时间统一格式化为 HH:MM ("092500" / "09:25:00" → "09:25")
  function fmtTime(t){
    if (!t) return '—';
    var s = String(t).trim();
    if (s.indexOf(':') !== -1) {
      var p = s.split(':');
      return p.length >= 2 ? p[0] + ':' + p[1] : s;
    }
    if (s.length >= 4 && /^\d+$/.test(s)) {
      return s.slice(0, 2) + ':' + s.slice(2, 4);
    }
    return s;
  }
  // R2004.1: 封单比 (小数 0.15 → "15%")
  function fmtSeal(v){
    if (v == null || isNaN(v) || v <= 0) return '—';
    return Math.round(v * 100) + '%';
  }

  // ── 战法哲学 chip ──
  // R91: mobile 折叠为单行 + 展开切换 — 次要上下文不占主屏 (推票优先)
  var _creedExpanded = false;
  function renderCreed(){
    var host = $('#bv-creed-list');
    if (!host) return;
    if (!_meta || !_meta.philosophy || !_meta.philosophy.length){
      host.innerHTML = '<div class="dim center" style="padding:.5rem">战法哲学加载中…</div>';
      return;
    }
    var isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    var list = _meta.philosophy;
    var html = '<ul class="bv-philo-list">';
    if (isMobile && !_creedExpanded) {
      // 折叠态: 只显示第一条 (最大信息密度), 加展开按钮
      html += '<li><span class="bv-philo-dot">•</span>' + esc(list[0]) + '</li>';
      html += '</ul>';
      html += '<button class="bv-creed-more" data-creed-toggle>展开 ' + (list.length - 1) + ' 条 ▾</button>';
    } else {
      list.forEach(function(p){
        html += '<li><span class="bv-philo-dot">•</span>' + esc(p) + '</li>';
      });
      html += '</ul>';
      if (isMobile) html += '<button class="bv-creed-more" data-creed-toggle>收起 ▴</button>';
    }
    // UP主元信息
    if (_meta.up) {
      $('#bv-up-meta').textContent = 'UP主: ' + _meta.up + ' · ' + _meta.bvid + ' · ' + (_meta.extracted_at || '');
    }
    host.innerHTML = html;
    var toggle = host.querySelector('[data-creed-toggle]');
    if (toggle && !toggle.dataset.bvBound) {
      toggle.dataset.bvBound = '1';
      toggle.addEventListener('click', function(){
        _creedExpanded = !_creedExpanded;
        renderCreed();
      });
    }
  }

  // ── 阶段 banner ──
  var _phaseTtlSec = 60;
  var _countdownTimer = null;
  var _countdownSec = null;
  function renderPhase(){
    var banner = $('#bv-phase-banner');
    var icon = $('#bv-phase-icon');
    var label = $('#bv-phase-label');
    var ttl = $('#bv-phase-ttl');
    var buy = $('#bv-buy-window');
    if (!banner) return;
    var PHASE_LABEL = {
      pre_market:     { icon: '🟡', label: '集合竞价监控',   tone: 'warn' },
      early:          { icon: '🟢', label: '早盘实时推票',   tone: 'good' },
      midday:         { icon: '🟡', label: '午间守候',       tone: 'warn' },
      late_afternoon: { icon: '🔴', label: '尾盘抢筹',       tone: 'bad'  },
      closing:        { icon: '🟣', label: '收盘集合竞价',   tone: 'info' },
      close:          { icon: '⚫', label: '盘后守候',       tone: 'mute' },
    };
    var info = PHASE_LABEL[_phase] || PHASE_LABEL.close;
    icon.textContent = info.icon;
    label.textContent = info.label;
    banner.className = 'bv-phase-banner bv-tone-' + info.tone;
    // TTL 估算 (30s/60s/...)
    var ttlMap = { early: 30, late_afternoon: 20, closing: 10, midday: 60, pre_market: 60, close: 300 };
    _phaseTtlSec = ttlMap[_phase] || 60;
    renderCountdown();
    // R14: 买点窗口徽章 — 可买阶段亮色 pulse, 其他灰暗禁用。永不 hidden (用户始终知道可买状态)
    var isBuyWindow = (_phase === 'early' || _phase === 'late_afternoon');
    buy.hidden = false;
    if (isBuyWindow) {
      buy.className = 'bv-buy-window is-buy';
      buy.textContent = (_phase === 'early') ? '📍 可买 10:40前' : '📍 可买 14:40后';
    } else {
      buy.className = 'bv-buy-window is-not-buy';
      buy.textContent = '⛔ 观望中';
    }
  }

  // R2002.5: 倒计时 — view-leave 需清掉
  function renderCountdown(){
    var ttl = $('#bv-phase-ttl');
    if (!ttl) return;
    if (_countdownSec == null) _countdownSec = _phaseTtlSec;
    ttl.textContent = 'TTL ' + _countdownSec + 's';
  }
  function _startCountdown(){
    _stopCountdown();
    _countdownSec = _phaseTtlSec;
    _countdownTimer = setInterval(function(){
      _countdownSec--;
      if (_countdownSec <= 0) _countdownSec = _phaseTtlSec;
      renderCountdown();
    }, 1000);
  }
  function _stopCountdown(){
    if (_countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
  }

  // R3: mobile 把 banner detach 到 body 顶层,绕开 .view-bv transform + .view-head overflow-x:auto
  //      否则 position:fixed 在 transform 祖先里降级成 absolute,跟着滚动消失
  function _maybePinBanner(){
    var isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    var banner = document.querySelector('.view-bv .bv-phase-banner');
    if (!banner) return;
    if (isMobile && banner.parentElement !== document.body) {
      var clone = banner.cloneNode(true);
      clone.classList.add('is-pinned');
      document.body.appendChild(clone);
      document.body.classList.add('has-pinned-banner');
      // 同步内容 (后续 renderMeta 改源 banner 时,也推一份给 pinned clone)
      var syncPinned = function(){
        var src = document.querySelector('.view-bv .bv-phase-banner');
        var dst = document.body.querySelector('.bv-phase-banner.is-pinned');
        if (src && dst) {
          dst.innerHTML = src.innerHTML;
          // R7: 同步色调 class — pinned banner 边框/背景靠 tone class 切换
          var tones = ['bv-tone-good','bv-tone-warn','bv-tone-bad','bv-tone-info','bv-tone-mute'];
          tones.forEach(function(t){ dst.classList.remove(t); });
          var srcTone = tones.filter(function(t){ return src.classList.contains(t); })[0];
          if (srcTone) dst.classList.add(srcTone);
        }
      };
      // 每次 renderMeta 完成后调一次 — 用 MutationObserver 监听 src 内容变化
      var mo = new MutationObserver(syncPinned);
      mo.observe(banner, {childList: true, subtree: true, characterData: true});
      // resize 切桌面 → 移除 pinned
      var mqListener = function(){
        if (!window.matchMedia('(max-width: 768px)').matches) {
          var p = document.body.querySelector('.bv-phase-banner.is-pinned');
          if (p) p.remove();
          document.body.classList.remove('has-pinned-banner');
          mo.disconnect();
        }
      };
      window.matchMedia('(max-width: 768px)').addEventListener('change', mqListener);
    }
  }

  // R4: 回到顶部 FAB — scroll > 3 屏 (2400px) 时浮出, 点击 smooth scroll 0
  function _installTopFab(){
    var isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    if (!isMobile) return;
    if (document.body.querySelector('.bv-top-fab')) return;  // 幂等
    var btn = document.createElement('button');
    btn.className = 'bv-top-fab';
    btn.type = 'button';
    btn.setAttribute('aria-label', '回到顶部');
    btn.title = '回到顶部';
    btn.innerHTML = '↑';
    document.body.appendChild(btn);
    btn.addEventListener('click', function(){
      window.scrollTo({top: 0, behavior: 'smooth'});
    });
    var THRESHOLD = window.innerHeight * 3;
    var raf = null;
    var onScroll = function(){
      if (raf) return;
      raf = requestAnimationFrame(function(){
        raf = null;
        var y = window.scrollY || document.documentElement.scrollTop;
        if (y > THRESHOLD) btn.classList.add('is-visible');
        else btn.classList.remove('is-visible');
      });
    };
    window.addEventListener('scroll', onScroll, {passive: true});
    // resize 重算阈值 (横竖屏切换)
    var onResize = function(){
      THRESHOLD = window.innerHeight * 3;
      onScroll();
    };
    window.addEventListener('resize', onResize);
  }

  // R39: 滚动到底自动加载更多 picks
  var _loadingMore = false;
  var _hasMore = true;
  function _installInfiniteScroll(){
    var isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    if (!isMobile) return;
    // R45: footer 渲染助手 — 集中管理 loaded/hasmore/error 三态
    var renderEndFooter = function(state, extra){
      var _end = $('#bv-loadmore-end');
      if (!_end) return;
      if (state === 'loaded') {
        // R87: 已加载数量必须等于用户实际看到的 (过滤后) — 否则"已加载全部 50 只"但只显示 12 只是在说谎
        var _visCount = _filterPicks(_picks).length;
        var _visTxt = _visCount < _picks.length ? ('过滤后 ' + _visCount + ' / 全部 ' + _picks.length) : String(_picks.length);
        _end.innerHTML = '已加载全部 ' + _visTxt + ' 只 · <a class="bv-end-top" href="javascript:void(0)">↑ 返回顶部</a>';
        _end.hidden = false;
        var _topLink = _end.querySelector('.bv-end-top');
        if (_topLink && !_topLink.dataset.bvClickable) {
          _topLink.dataset.bvClickable = '1';
          _topLink.addEventListener('click', function(){
            window.scrollTo({top: 0, behavior: 'smooth'});
          });
        }
      } else if (state === 'hasmore') {
        _end.innerHTML = '<button class="bv-loadmore-btn">↓ 加载更多</button>';
        _end.hidden = false;
        var _btn = _end.querySelector('.bv-loadmore-btn');
        if (_btn && !_btn.dataset.bvClickable) {
          _btn.dataset.bvClickable = '1';
          _btn.addEventListener('click', function(){
            _loadingMore = true;
            var _lm = $('#bv-loadmore');
            if (_lm) _lm.hidden = false;
            loadLivePick(true, _picks.length).finally(function(){
              _loadingMore = false;
              if (_lm) _lm.hidden = true;
            });
          });
        }
      } else if (state === 'error') {
        // R45: 失败态 — 给用户重试入口, 避免"点了没反应"挫败
        var msg = (extra && extra.msg) || '加载失败';
        _end.innerHTML = '<span class="bv-end-err">⚠ ' + msg + '</span> · <button class="bv-loadmore-btn bv-retry-btn">↻ 重试</button>';
        _end.hidden = false;
        var _rbtn = _end.querySelector('.bv-retry-btn');
        if (_rbtn && !_rbtn.dataset.bvClickable) {
          _rbtn.dataset.bvClickable = '1';
          _rbtn.addEventListener('click', function(){
            _loadingMore = true;
            var _lm = $('#bv-loadmore');
            if (_lm) _lm.hidden = false;
            _end.hidden = true;  // 先隐藏, finally 中按真实结果再渲染
            loadLivePick(true, _picks.length).finally(function(){
              _loadingMore = false;
              if (_lm) _lm.hidden = true;
            });
          });
        }
      } else {
        _end.hidden = true;
      }
    };
    var onScroll = function(){
      if (_loadingMore || !_hasMore || !_picks || !_picks.length) return;
      // 距底部 200px 内触发
      var sc = window.scrollY || document.documentElement.scrollTop;
      var vh = window.innerHeight;
      var docH = document.documentElement.scrollHeight;
      if (sc + vh >= docH - 200) {
        _loadingMore = true;
        // R40: 显示加载提示
        var _lm = $('#bv-loadmore');
        if (_lm) _lm.hidden = false;
        loadLivePick(true, _picks.length).finally(function(){
          _loadingMore = false;
          if (_lm) _lm.hidden = true;
          // R41: 加载完成 footer — 显示进度
          var _end = $('#bv-loadmore-end');
          if (_end) {
            if (!_hasMore && _picks.length > 15) {
              renderEndFooter('loaded');
            } else if (_hasMore && _picks.length > 15) {
              renderEndFooter('hasmore');
            } else {
              _end.hidden = true;
            }
          }
        });
      }
    };
    window.addEventListener('scroll', onScroll, {passive: true});
  }

  // ── 推票主表 ──
  // R2002.3: 列排序状态 — 默认按 score 降序
  var _pickSort = { key: 'score', dir: 'desc' };
  // R6: 筛选 state — all/hot/streak2/first/chg5/sealh
  var _pickFilter = 'all';
  // R13: 加载/失败态 — _pickLoading true 显示骨架屏, _pickError 显示重试
  var _pickLoading = true;
  var _pickError = '';
  // R9: sector 着色 — 把 sector 字符串 hash 到 hue (180-320 区间,避开红绿)
  function _sectorHue(s){
    if (!s || s === '—') return null;
    var h = 0;
    for (var i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return 180 + (Math.abs(h) % 140);
  }

  // R9: render 后批量给每行设置 inline sector hue (CSS attr() 无法用于 hsl())
  function _applySectorHues(){
    if (!window.matchMedia || !window.matchMedia('(max-width: 768px)').matches) return;
    var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-sector]');
    rows.forEach(function(tr){
      var s = tr.getAttribute('data-sector');
      if (!s) return;
      var hue = _sectorHue(s);
      if (hue != null) {
        tr.style.borderLeft = '3px solid hsla(' + hue + ', 65%, 60%, 0.85)';
        tr.style.paddingLeft = '9px';   // 补回 3px 边距
      }
    });
  }
  // R8: 多选 mode — 长按进入多选模式, 点击切换选中, 再次长按其他卡片扩展选中
  var _multiMode = false;
  var _multiSelected = {};   // {code: name}

  function _enterMultiMode(seedCode, seedName){
    _multiMode = true;
    _multiSelected[seedCode] = seedName;
    _renderMultiToolbar();
    _updateMultiVisual();
  }
  function _exitMultiMode(){
    _multiMode = false;
    _multiSelected = {};
    var tb = document.getElementById('bv-multi-toolbar');
    if (tb) tb.remove();
    _updateMultiVisual();
  }
  function _toggleMulti(code, name){
    if (_multiSelected[code]) delete _multiSelected[code];
    else _multiSelected[code] = name;
    if (Object.keys(_multiSelected).length === 0) { _exitMultiMode(); return; }
    _renderMultiToolbar();
    _updateMultiVisual();
  }
  function _renderMultiToolbar(){
    var tb = document.getElementById('bv-multi-toolbar');
    var n = Object.keys(_multiSelected).length;
    if (!tb) {
      tb = document.createElement('div');
      tb.id = 'bv-multi-toolbar';
      tb.className = 'bv-multi-toolbar';
      document.body.appendChild(tb);
    }
    tb.innerHTML =
      '<span class="bv-multi-count">已选 <b>' + n + '</b> 只</span>' +
      '<button class="bv-multi-btn" id="bv-multi-all">全选</button>' +
      '<button class="bv-multi-btn" id="bv-multi-add">＋加自选</button>' +
      '<button class="bv-multi-btn bv-multi-cancel" id="bv-multi-cancel">取消</button>';
    document.getElementById('bv-multi-all').onclick = function(){
      _picks.forEach(function(p){ _multiSelected[p.code] = p.name; });
      _renderMultiToolbar(); _updateMultiVisual();
    };
    document.getElementById('bv-multi-add').onclick = _addMultiToWatchlist;
    document.getElementById('bv-multi-cancel').onclick = _exitMultiMode;
  }
  async function _addMultiToWatchlist(){
    var codes = Object.keys(_multiSelected);
    if (!codes.length) return;
    var ok = 0, skipped = 0, fail = 0;
    // R165 2026-08-20: 之前走 api('/api/watchlist/{code}', POST) — 该 endpoint 不存在
    // (server 只暴露 POST /api/watchlist JSON body, {code, name}), 多 mode 一直 silent 404。
    // 改用 wlToggle (app.js 统一入口, 正确 POST + JSON + 同步 _wlCodeSet + dispatch event)。
    // 注意 wlToggle 语义: 已存在则 DELETE 移除 — 多 mode 这里必须先 batch 检测, 否则刚加的就
    // 被切走了。一次性 wlGetCodes → existing set, 只对未存在的调 wlToggle (永远 add 分支)。
    var existing = await window.wlGetCodes().catch(function(){ return new Set(); });
    for (var i = 0; i < codes.length; i++) {
      var code = codes[i];
      var name = _multiSelected[code] || code;
      if (existing.has(code)) { skipped++; continue; }
      try {
        var added = await window.wlToggle(code, name);
        if (added) ok++; else { skipped++; existing.add(code); /* 竞态: 已被人加 */ }
      } catch(e) { fail++; }
    }
    var msg = '✓ 已加 ' + ok + ' 只';
    if (skipped) msg += ' · 跳过 ' + skipped + ' (已在自选)';
    if (fail) msg += ' · 失败 ' + fail;
    if (typeof toast === 'function') toast(msg, 'success', 2500);
    _exitMultiMode();
  }
  function _updateMultiVisual(){
    var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
    rows.forEach(function(tr){
      var code = tr.dataset.code;
      if (_multiSelected[code]) tr.classList.add('bv-multi-selected');
      else tr.classList.remove('bv-multi-selected');
      // R20: 选中卡片右上角加 ✓ 角标 (用户一眼看到选了哪几个)
      var badge = tr.querySelector('.bv-multi-badge');
      if (_multiSelected[code] && !badge) {
        badge = document.createElement('div');
        badge.className = 'bv-multi-badge';
        badge.textContent = '✓';
        tr.appendChild(badge);
      } else if (!_multiSelected[code] && badge) {
        badge.remove();
      }
    });
    document.body.classList.toggle('bv-multi-active', _multiMode);
  }

  // R6: 筛选函数 — 根据 _pickFilter 过滤 picks
  function _filterPicks(arr){
    if (!arr) return [];
    if (_pickFilter === 'all') return arr;
    if (_pickFilter === 'hot') return arr.filter(function(p){ return (p.matched_rules || []).length >= 3; });
    if (_pickFilter === 'streak2') return arr.filter(function(p){ return (p.streak || 0) >= 2; });
    if (_pickFilter === 'first') return arr.filter(function(p){ return (p.streak || 0) === 1; });
    if (_pickFilter === 'chg5') return arr.filter(function(p){ return (p.change_pct || 0) >= 5; });
    if (_pickFilter === 'sealh') return arr.filter(function(p){ return (p.seal_ratio || 0) >= 0.30; });
    if (_pickFilter === 'main10') return arr.filter(function(p){ return !p.is_20cm; });
    // R24: 板块过滤 (sector:xxx 格式)
    if (_pickFilter.indexOf && _pickFilter.indexOf('sector:') === 0) {
      var s = _pickFilter.slice(7);
      return arr.filter(function(p){ return (p.sector || '其他') === s; });
    }
    return arr;
  }

  // R6: 更新筛选条每个 chip 的数字
  function _updateFilterCounts(){
    if (!_picks || !_picks.length) return;
    var counts = {
      all: _picks.length,
      hot: _picks.filter(function(p){ return (p.matched_rules || []).length >= 3; }).length,
      streak2: _picks.filter(function(p){ return (p.streak || 0) >= 2; }).length,
      first: _picks.filter(function(p){ return (p.streak || 0) === 1; }).length,
      chg5: _picks.filter(function(p){ return (p.change_pct || 0) >= 5; }).length,
      sealh: _picks.filter(function(p){ return (p.seal_ratio || 0) >= 0.30; }).length,
      main10: _picks.filter(function(p){ return !p.is_20cm; }).length,
    };
    var map = {all:'all', hot:'hot', streak2:'streak2', first:'first', chg5:'chg5', sealh:'sealh', main10:'main10'};
    Object.keys(map).forEach(function(k){
      var el = document.getElementById('bv-filter-count-' + k);
      if (el) el.textContent = counts[k];
    });
  }

  // R6: 筛选 chip 点击 → 切换 + 重渲染
  function _bindFilterBar(){
    var bar = $('#bv-filter-bar');
    if (!bar || bar._bound) return;
    bar._bound = true;
    // R150: scroll 位置 → has-scroll-* 类 — 隐藏 scrollbar 时右缘渐变提示还有更多筛选
    // (跟 app.js bindTableScrollIndicator 同 idiom: scrollLeft 边界阈值 4px)
    function _syncFilterScroll(){
      if (!bar) return;
      var max = bar.scrollWidth - bar.clientWidth;
      var sl = bar.scrollLeft;
      bar.classList.toggle('has-scroll-left',  sl > 4);
      bar.classList.toggle('has-scroll-right', sl < max - 4);
    }
    var _fadeRaf = false;
    bar.addEventListener('scroll', function(){
      if (_fadeRaf) return;
      _fadeRaf = true;
      requestAnimationFrame(function(){ _syncFilterScroll(); _fadeRaf = false; });
    }, { passive: true });
    window.addEventListener('resize', _syncFilterScroll);
    setTimeout(_syncFilterScroll, 100);
    bar.addEventListener('click', function(ev){
      var chip = ev.target.closest('.bv-filter-chip');
      if (!chip) return;
      var f = chip.getAttribute('data-filter');
      if (!f || f === _pickFilter) return;
      _pickFilter = f;
      bar.querySelectorAll('.bv-filter-chip').forEach(function(c){ c.classList.remove('is-active'); });
      chip.classList.add('is-active');
      renderPicks();
    });
  }

  // R252: 规则 chip tap → 就近 popover — 决策依据就地呈现, 不瞬移 1410px 丢上下文.
  //   第一性原理: 用户点"命中哪条规则"是想看"为什么", 跳到页面深处的规则明细
  //   会丢失刚看的卡片 (R90 "参考材料不掩埋主内容" 的移动端版本). popover 在
  //   卡片上方就地展示规则内容, 点击外部关闭, 内含"过滤此规则"入口 (保留 R48).
  function _closeRulePopover(){
    // R252: popover/mask append 到 document.body, $() helper 只查 $root 子树,
    //   用 $() 永远返回 null → popover 永不关闭 (过滤/✕/外部点击全失效).
    //   第一性原理: 控制面 (关闭/过滤) 必须真正可达 — 查找范围与挂载位置必须同域.
    // R254: 同时移除 scroll 监听 — 避免每次打开 popover 重复 addEventListener 泄漏
    // R272: 关闭动效 — open 有 bv-pop-in fade+translateY(6px→0), close 直接
    //   remove 视觉突兀. 加 bv-pop-closing class 触发 bv-pop-out (140ms, 跟
    //   open 160ms 接近), 等动画结束再 remove. bypass=true 用于 R262 prev/next
    //   切换 (内部换内容, 走 _showRulePopover 自然 fade-in, 不应再 fade-out).
    // R274: 关闭后 chip flash — 用户眼睛聚焦 popover 内容, 关闭后视线需要回到
    //   原 chip (R252 锚点). 给触发本次 popover 的 chip 加 bv-chip-flash
    //   class, animation 1.2s 闪一下. _bvCurrentChip 由 _showRulePopover 设.
    var bypass = arguments[0] === true;
    if (_scrollClose) { window.removeEventListener('scroll', _scrollClose); _scrollClose = null; }
    // R276: tap-outside 监听清理 — 防止 30s 自动刷新或重复打开泄漏 listener
    if (_outsideClick) { document.removeEventListener('click', _outsideClick, true); _outsideClick = null; }
    if (_escClose) { document.removeEventListener('keydown', _escClose, true); _escClose = null; }
    // R278: 解除 body scroll lock — popover 关闭时还原 overflow + 滚动位置
    if (_bodyScrollLock) {
      document.body.style.overflow = _bodyScrollLock.overflow;
      document.body.style.position = _bodyScrollLock.position;
      document.body.style.top = _bodyScrollLock.top;
      document.body.style.width = _bodyScrollLock.width;
      window.scrollTo(0, _bodyScrollLock.scrollY);
      _bodyScrollLock = null;
    }
    var _pv = document.getElementById('bv-rule-popover');
    var _mask = document.getElementById('bv-rule-popover-mask');
    if (bypass || !_pv) {
      if (_pv) _pv.remove();
      if (_mask) _mask.remove();
      // bypass 也 flash chip (R262 prev/next 切换完后, 最后一条也会 close → 也该 flash)
      if (_bvCurrentChip && _bvCurrentChip.parentNode) {
        _bvCurrentChip.classList.remove('bv-chip-flash');
        // force reflow restart animation
        void _bvCurrentChip.offsetWidth;
        _bvCurrentChip.classList.add('bv-chip-flash');
      }
      return;
    }
    // fade-out 完成后 flash chip (跟 close 动效串联)
    var _chipRef = _bvCurrentChip;
    if (_chipRef && _chipRef.parentNode) {
      _chipRef.classList.remove('bv-chip-flash');
      void _chipRef.offsetWidth;
      _chipRef.classList.add('bv-chip-flash');
    }
    _pv.classList.add('bv-pop-closing');
    if (_mask) _mask.classList.add('bv-pop-closing');
    // 闭包 capture _pv/_mask 引用 — 防止 160ms 后 setTimeout 撞上刚 append 的
    // 新 popover (race: #bv-rule-popover id 重用). showRulePopover 入口 bypass=true
    // 路径已经立即 remove, 此处 capture 是双保险.
    var _pvRef = _pv, _maskRef = _mask;
    setTimeout(function(){
      if (_pvRef && _pvRef.parentNode) _pvRef.parentNode.removeChild(_pvRef);
      if (_maskRef && _maskRef.parentNode) _maskRef.parentNode.removeChild(_maskRef);
    }, 160);
  }
  // R262: 同一卡片命中多条规则时, popover 逐条浏览 — 不关 popover 不丢锚点.
  //   第一性原理: 用户对照卡片 N 条命中规则时, 关 popover 再点另一个 chip = 两次
  //   点击 + 重新定位 (上下文丢失, 锚定卡片可能滚出屏). 在 popover 内切换是"同一
  //   注意焦点内的浏览" — 锚点 (卡片) 恒在, 手指不动只换内容. 复用 R70 详情
  //   prev/next 模式 (上一只/下一只 切换).
  function _showRulePopover(rid, anchorEl){
    var r = _rulesById[rid];
    if (!r) return;
    // R272: bypass=true — show 入口要立即移除旧 popover,否则 fade-out 后 160ms
    //   setTimeout 会把刚 append 的新 popover 一起 remove (race: setTimeout 闭包
    //   capture 但查的是 #bv-rule-popover id, 撞上 id 重用 race)
    _closeRulePopover(true);
    // R274: 保存当前 chip 引用 — close 时给这个 chip 加 bv-chip-flash, 让用户
    //   视线从 popover 关闭瞬间锚回点击点 (扫视 top-down → chip 闪一下)
    _bvCurrentChip = anchorEl && anchorEl.classList ? anchorEl : null;
    // R262: 计算该卡片命中规则列表 + 当前下标 — popover 内切换用
    var anchorCode = anchorEl && anchorEl.closest('.bv-row') ? anchorEl.closest('.bv-row').getAttribute('data-code') : null;
    var anchorPick = anchorCode ? (_picks || []).find(function(p){ return p.code === anchorCode; }) : null;
    var mList = (anchorPick && anchorPick.matched_rules && anchorPick.matched_rules.length)
      ? anchorPick.matched_rules.slice() : [rid];
    // R264: 轨道按决策价值排序 — score_weight 降序 (权重=该规则对当前股决策的
    //   贡献度), 并列按 title 稳定排序. 第一性原理: 轨道是导航 map, map 应按
    //   用户决策价值排列 — "最重要的先看到", 用户常找的高权重规则不在轨道末尾
    //   需横向滚动. prev/next 步进跟随同一顺序 (轨道顺序=步进顺序, 一致性).
    if (mList.length > 1) {
      mList.sort(function(a, b){
        var wa = (_rulesById[a] && _rulesById[a].score_weight != null) ? Number(_rulesById[a].score_weight) : 0;
        var wb = (_rulesById[b] && _rulesById[b].score_weight != null) ? Number(_rulesById[b].score_weight) : 0;
        if (wb !== wa) return wb - wa;
        return String(a).localeCompare(String(b));
      });
    }
    // 只在 anchorPick 有完整列表且当前规则在列表内时启用切换
    var enableNav = mList.length > 1;
    _popList = mList;
    _popIdx = Math.max(0, mList.indexOf(rid));
    // R278: body scroll lock — popover 打开时禁止背景滚动, 避免双滚动冲突.
    //   iOS Safari 上 body overflow:hidden 仍能滚动 (bug), 必须 position:fixed + top
    //   保留当前位置. 关闭时还原 overflow + scrollTo 原 scrollY, 视觉无抖动.
    //   第一性: modal = 模态, 屏蔽底层一切交互 (包括滚动).
    _bodyScrollLock = {
      scrollY: window.scrollY || document.documentElement.scrollTop || 0,
      overflow: document.body.style.overflow,
      position: document.body.style.position,
      top: document.body.style.top,
      width: document.body.style.width
    };
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.top = '-' + _bodyScrollLock.scrollY + 'px';
    document.body.style.width = '100%';
    // R254: 滚动 = 注意焦点转移 → popover 过期自动关闭. fixed popover 与滚动
    //   保留会产生语义漂移 (锚定卡片滚走, popover 停在原地 — 用户不知它关于哪张卡).
    //   第一性: popover 是"此刻注意焦点"的就地答案, 焦点变了答案就失效.
    // 注意: 必须 body lock 之后再注册 scroll listener — position:fixed 触发 scrollY
    //   0 的合成 scroll 事件, 先注册会被这个合成事件误触发关闭.
    // R278: body 已锁 (position:fixed), 背景 wheel 不可能真滚 — wheel 是死路径,
    //   不应误关 popover. 第一性: popover 关闭 = 焦点转移; wheel 锁住时焦点没动.
    _scrollClose = function(){
      if (_bodyScrollLock) return;  // body 已锁, scroll 不可达, 不应关闭
      _closeRulePopover();
    };
    window.addEventListener('scroll', _scrollClose, {passive:true});
    var mask = document.createElement('div');
    mask.id = 'bv-rule-popover-mask';
    mask.onclick = function(){ _closeRulePopover(); };
    var box = document.createElement('div');
    box.id = 'bv-rule-popover';
    box.className = 'bv-rule-popover';
    var cat = r.category ? '<span class="bv-pop-cat">' + esc(r.category) + '</span>' : '';
    var weight = r.score_weight != null ? '<span class="bv-pop-weight">权重 ' + esc(String(r.score_weight)) + '</span>' : '';
    var quoteHtml = (r.quote ? '<div class="bv-pop-quote"><span class="bv-rule-quote-mark">"</span>' + esc(r.quote)
      + (r.timestamp ? '<span class="bv-rule-quote-ts">@ ' + esc(r.timestamp) + '</span>' : '') + '</div>' : '');
    // R266: 条件 chip 结构化 — field/op/value 三元组分块视觉分层. 数据字段是结构化
    //   三元组, 连成一行 "streak == 1" 是压缩表达, 移动端窄屏下不可读. 分块:
    //   field (条件主体) 用 ink-1 加粗, op (关系) 用 ink-2 轻量, value (判定值)
    //   accent 高亮 — "什么字段 什么关系 什么值" 一眼可扫 (R259 同信号同视觉)
    var condsHtml = (r.conditions && r.conditions.length) ? '<div class="bv-pop-conds">'
      + r.conditions.map(function(c){ return '<span class="bv-cond-chip"><span class="bv-cond-f">' + esc(c.field) + '</span><span class="bv-cond-op"> ' + esc(c.op) + ' </span><span class="bv-cond-v">' + esc(c.value) + '</span></span>'; }).join('')
      + '</div>' : '';
    // R258: filter 按钮文案缩短 + CSS nowrap 强制单行 — 390px 上 "🔍 过滤只看此规则"
    //   折两行 → 按钮高 44→64px (操作按钮不能换行膨胀, 单行触控目标). 过滤态用
    //   "✓ 过滤中" (当前选中) + 点击取消语义由 aria/底色表达.
    var filterBtn = '<button class="bv-pop-filter" data-rid="' + esc(rid) + '">'
      + (_ruleFilter === rid ? '✓ 过滤中 · 点击取消' : '🔍 过滤此规则') + '</button>';
    // R262: 切换条 — 同一卡片命中 N>1 条规则时提供 prev/next 逐条浏览 (N=1 不显示,
    //   无意义控件不占空间). "1/3" 计数让用户知道总览范围 + 当前位.
    // R263: 规则轨道 (rail) — 同一卡片全部命中规则 mini chip 横排, 当前高亮.
    //   第一性原理: 浏览的 scope 应该可见 — prev/next 只给顺序翻页, 用户不知道还有
    //   哪些规则 (只有 "1/4" 数字). 轨道让全部命中规则一览 + 点任意 chip 直接跳转
    //   (1 tap 从规则1跳规则4, 而非 3 tap 顺序翻). 轨道即导航 map, prev/next 是
    //   相邻步进, 两者互补不冲突.
    var railHtml = enableNav
      ? '<div class="bv-pop-rail">' + mList.map(function(x, xi){
          var xr = _rulesById[x];
          // R265: 轨道 chip 带规则短名 — 编号是引用符号, 短名才是内容. 不熟的用户
          //   扫轨道看不到 "BV03" 是啥, 短名 (title 逗号前主句, R256 同款) 让轨道
          //   一眼可读, 不用逐个 tap 才知道内容. 长名 max-width ellipsis 截断.
          var _short = xr ? (xr.title || '').split(/[,，:：]/)[0].trim() : '';
          return '<button class="bv-pop-rail-chip' + (xi === _popIdx ? ' is-cur' : '') + '" data-rid="' + esc(x)
            + '" title="' + (xr ? esc(xr.title) : esc(x)) + '">' + esc(x)
            + (_short ? '<span class="bv-pop-rail-name">' + esc(_short) + '</span>' : '') + '</button>';
        }).join('') + '</div>'
      : '';
    var navHtml = enableNav
      ? '<div class="bv-pop-nav" data-code="' + esc(anchorCode || '') + '">'
        + '<button class="bv-pop-prev" aria-label="上一条规则">‹</button>'
        + '<span class="bv-pop-pos">' + (_popIdx + 1) + ' / ' + mList.length + '</span>'
        + '<button class="bv-pop-next" aria-label="下一条规则">›</button></div>'
      : '';
    box.innerHTML = '<div class="bv-pop-head">' + railHtml + navHtml
      + '<span class="bv-pop-rid">' + esc(r.id) + '</span>'
      + '<span class="bv-pop-title">' + esc(r.title) + '</span>'
      + '<button class="bv-pop-close" aria-label="关闭">✕</button></div>'
      + '<div class="bv-pop-meta">' + cat + weight + '</div>'
      + '<div class="bv-pop-body">'
      + (r.description ? '<div class="bv-pop-desc">' + esc(r.description) + '</div>' : '')
      + condsHtml + quoteHtml + '</div>'
      + '<div class="bv-pop-ops">' + filterBtn + '</div>';
    // 定位: 垂直跟随 anchor, 水平居中对齐卡片
    var row = anchorEl ? anchorEl.closest('.bv-row') : null;
    var rowR = row ? row.getBoundingClientRect() : null;
    if (rowR) {
      box.style.position = 'fixed';
      box.style.left = '8px';
      box.style.right = '8px';
      var top = rowR.bottom + 6;
      // R261: 高度动态化 — 内容全显示不该滚动. 220px 固定上限在屏高充足时浪费下方
      //   空间 (340px 空闲却只给 220 → 251px 内容需滚). 下方空间充足时给到 360,
      //   内容少时 shrink-wrap 到内容高; 空间不足 (屏小/翻转) 仍退到 220 上限.
      // R267: 翻转 maxH 220→360 — 触发条件 top+260>innerHeight 已保证翻转后卡片
      //   上方空间 ≥576px (rowR.top≥584), 硬 220 过度保守. 翻转 + 多规则卡片时
      //   head (rail+nav) 就占 125px, 220-125-ops53 = body 只剩 ~25px 不可读.
      //   360 让 head 独立行 (R267 CSS) + 长内容仍有充足可读区.
      var maxH = 220;
      if (top + 260 > window.innerHeight) {
        top = Math.max(8, rowR.top - 360);
        maxH = Math.max(120, Math.min(360, rowR.top - 8 - 16));  // 翻转: 卡片上方空间
      } else {
        maxH = Math.min(360, window.innerHeight - top - 8);      // 下方: 有空间就多显示
      }
      box.style.top = top + 'px';
      box.style.maxHeight = maxH + 'px';
      box.style.overflowY = 'auto';
    }
    document.body.appendChild(mask);
    document.body.appendChild(box);
    // R276: tap-outside 关闭 — popover 浮层 = 用户的"此刻注意焦点", 任何点击
    //   popover/mask 之外的区域都是"焦点转移", 应该关闭. 用 capture-phase 监听
    //   document click, 区分: (a) 点 popover/mask 自身 → 让 bubble 自然处理 (mask
    //   onclick / popover 内容); (b) 点其他位置 → close. chip 重触同 rid 由 _showRulePopover
    //   内部 rerender 处理 (race: R262 prev/next chip click 也会冒泡到这里, 但
    //   close 在前/重开在后, 视觉等价于"切换"). 第一性: 浮层外任意点击 = 关闭意图.
    _outsideClick = function(ev){
      var t = ev.target;
      // 点 popover 自身 / mask / 关闭按钮 / 过滤按钮 → 让原始 handler 处理 (不抢)
      if (t.closest && (t.closest('#bv-rule-popover') || t.closest('#bv-rule-popover-mask'))) return;
      _closeRulePopover();
    };
    // capture phase 避免被 stopPropagation 拦截 (chip click handler 不应阻断)
    setTimeout(function(){
      if (_outsideClick) document.addEventListener('click', _outsideClick, true);
    }, 0);
    // R277: Esc 键关闭 — 桌面 web 标准 (Chrome modal / sheet / dialog). 移动端
    //   物理键盘 (平板键盘盖、外接键盘) 也可触发. capture phase 防止表单 input
    //   Esc 拦截 (如搜索框 Esc 取消焦点不该关 popover 跟表单焦点是两个独立意图).
    //   第一性: popover 是 modal 性质浮层, Esc = 取消/关闭, 系统级约定.
    _escClose = function(ev){
      if (ev.key === 'Escape' || ev.keyCode === 27) {
        ev.preventDefault();
        ev.stopPropagation();
        _closeRulePopover();
      }
    };
    document.addEventListener('keydown', _escClose, true);
    // R273: open 动效 origin-aware — 用户眼睛聚焦在 chip, popover 默认从中心
    //   translateY(6→0) 视觉链路断开. 把 popover 的 transform-origin 设到 chip
    //   点击位置 (基于 anchorEl 在视口的相对坐标), scale(0.96→1) + opacity 0→1
    //   就像 popover 从 chip 处"长出来". 视觉锚点 = 用户点击坐标.
    if (anchorEl && anchorEl.getBoundingClientRect) {
      try {
        var ar = anchorEl.getBoundingClientRect();
        var br = box.getBoundingClientRect();
        if (br.width > 0 && br.height > 0) {
          // clamp 到 [5%, 95%] — chip 在 popover 外时 origin 落到最近边缘
          // (popover 在 chip 下方翻转/跨越场景), 至少 5% 保留边缘呼吸
          var ox = Math.max(5, Math.min(95, ((ar.left + ar.width/2) - br.left) / br.width * 100));
          var oy = Math.max(5, Math.min(95, ((ar.top + ar.height/2) - br.top) / br.height * 100));
          box.style.transformOrigin = ox.toFixed(1) + '% ' + oy.toFixed(1) + '%';
        }
      } catch (e) {}
    }
    // R275: swipe-to-dismiss — 拇指向下拖 popover > 阈值触发关闭. iOS/Android
    //   系统级 sheet 约定手势. 比 ✕ 按钮 (R269 44px) 更自然, 不需要看控件.
    //   实现: touchstart 记录起点, touchmove 实时 translateY, touchend 判定
    //   dy>80 或 velocity>0.5 → close, 否则回弹 (transform:none 让 transition 接住)
    (function(){
      var _startY = 0, _curY = 0, _startT = 0, _tracking = false;
      box.addEventListener('touchstart', function(ev){
        if (!ev.touches || ev.touches.length !== 1) return;
        _startY = ev.touches[0].clientY;
        _startT = Date.now();
        _curY = 0;
        _tracking = true;
        box.style.transition = 'none';
      }, {passive: true});
      box.addEventListener('touchmove', function(ev){
        if (!_tracking || !ev.touches || ev.touches.length !== 1) return;
        var dy = ev.touches[0].clientY - _startY;
        if (dy < 0) dy = 0; // 只支持下拉, 上滑 ignore
        if (dy > 0) {
          ev.preventDefault(); // 防止 popover 内部滚动
          _curY = dy;
          box.style.transform = 'translateY(' + dy.toFixed(1) + 'px)';
        }
      }, {passive: false});
      box.addEventListener('touchend', function(ev){
        if (!_tracking) return;
        _tracking = false;
        var dt = Math.max(1, Date.now() - _startT);
        var velocity = _curY / dt;
        var shouldClose = _curY > 80 || velocity > 0.5;
        box.style.transition = '';
        if (shouldClose) {
          _closeRulePopover();
        } else {
          box.style.transform = '';
        }
      });
    })();
    box.querySelector('.bv-pop-close').onclick = function(){ _closeRulePopover(); };
    var _fb = box.querySelector('.bv-pop-filter');
    if (_fb) _fb.onclick = function(){
      _ruleFilter = (_ruleFilter === rid) ? null : rid;
      _closeRulePopover();
      renderPicks();
    };
    // R262: prev/next 切换 — 不重建 DOM (动画不重播, sticky head 不闪), 直接
    //   更新内容区 innerHTML + pos 计数. 锚点规则高亮由 _ruleFilter 参与决定.
    //   注意: 切换时不可 _closeRulePopover — 那会移除 popover (丢失锚点上下文).
    var _rebuild = function(nr){
      var pv = document.getElementById('bv-rule-popover');
      if (!pv) return;
      pv.querySelector('.bv-pop-rid').textContent = nr.id;
      pv.querySelector('.bv-pop-title').textContent = nr.title;
      var metaEl = pv.querySelector('.bv-pop-meta');
      metaEl.innerHTML = (nr.category ? '<span class="bv-pop-cat">' + esc(nr.category) + '</span>' : '')
        + (nr.score_weight != null ? '<span class="bv-pop-weight">权重 ' + esc(String(nr.score_weight)) + '</span>' : '');
      var bodyEl = pv.querySelector('.bv-pop-body');
      bodyEl.innerHTML = (nr.description ? '<div class="bv-pop-desc">' + esc(nr.description) + '</div>' : '')
        + ((nr.conditions && nr.conditions.length) ? '<div class="bv-pop-conds">'
          + nr.conditions.map(function(c){ return '<span class="bv-cond-chip"><span class="bv-cond-f">' + esc(c.field) + '</span><span class="bv-cond-op"> ' + esc(c.op) + ' </span><span class="bv-cond-v">' + esc(c.value) + '</span></span>'; }).join('')
          + '</div>' : '')
        + (nr.quote ? '<div class="bv-pop-quote"><span class="bv-rule-quote-mark">"</span>' + esc(nr.quote)
          + (nr.timestamp ? '<span class="bv-rule-quote-ts">@ ' + esc(nr.timestamp) + '</span>' : '') + '</div>' : '');
      pv.querySelector('.bv-pop-pos').textContent = (_popIdx + 1) + ' / ' + _popList.length;
      // R263: 轨道高亮同步 — 当前规则 chip 加 is-cur, 其它去掉 (map 位置指示)
      pv.querySelectorAll('.bv-pop-rail-chip').forEach(function(rc){
        var on = rc.getAttribute('data-rid') === nr.id;
        rc.classList.toggle('is-cur', on);
      });
      // 过滤按钮同步到当前规则 (语义: 过滤"当前正在看的"这条)
      var fb2 = pv.querySelector('.bv-pop-filter');
      if (fb2) {
        fb2.setAttribute('data-rid', nr.id);
        fb2.textContent = (_ruleFilter === nr.id) ? '✓ 过滤中 · 点击取消' : '🔍 过滤此规则';
      }
    };
    var navEl = box.querySelector('.bv-pop-nav');
    if (navEl) {
      var _prevBtn = navEl.querySelector('.bv-pop-prev');
      var _nextBtn = navEl.querySelector('.bv-pop-next');
      _prevBtn.onclick = function(ev){
        ev.stopPropagation();
        if (_popIdx <= 0) return;
        _popIdx--;
        _rebuild(_rulesById[_popList[_popIdx]]);
      };
      _nextBtn.onclick = function(ev){
        ev.stopPropagation();
        if (_popIdx >= _popList.length - 1) return;
        _popIdx++;
        _rebuild(_rulesById[_popList[_popIdx]]);
      };
    }
    // R263: 轨道 chip 直接跳转 — 1 tap 从任意规则到任意规则 (map 语义),
    //   不必顺序翻页. 更新 _popIdx + _rebuild (同步内容 + rail 高亮).
    box.querySelectorAll('.bv-pop-rail-chip').forEach(function(rc){
      rc.onclick = function(ev){
        ev.stopPropagation();
        var want = _popList.indexOf(rc.getAttribute('data-rid'));
        if (want < 0 || want === _popIdx) return;
        _popIdx = want;
        _rebuild(_rulesById[_popList[_popIdx]]);
      };
    });
  }

  function renderPicks(){
    var tbody = $('#bv-pick-tbody');
    var count = $('#bv-pick-count');
    if (!tbody) return;
    // R25: snapshot 滚动位置 + 焦点卡片 code, 重渲染后恢复
    var _scrollY = 0;
    var _focusCode = null;
    var _firstVisibleCode = null;
    // R72: 重渲染前记住展开的详情卡 — 自动刷新不打断阅读流
    var _expandedCode = null;
    var _expandedTop = false;
    // 只在已有内容时记录 (不要记录空态/加载态)
    if (tbody.querySelector && tbody.querySelector('tr.bv-row')) {
      _scrollY = window.scrollY || document.documentElement.scrollTop || 0;
      var vh = window.innerHeight || 0;
      var rows = tbody.querySelectorAll('tr.bv-row');
      for (var _ri = 0; _ri < rows.length; _ri++) {
        var _r = rows[_ri];
        var _rect = _r.getBoundingClientRect();
        if (_rect.bottom > 0 && _rect.top < vh) {
          _firstVisibleCode = _r.dataset.code;
          break;
        }
      }
      // R72: 用户手动展开的卡 (非 top-1) — 刷新后保持展开
      var _openRow = tbody.querySelector('tr.bv-row.bv-expanded');
      if (_openRow) {
        var _openCode = _openRow.dataset.code;
        var _openDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _openCode + '"]');
        if (_openDetail && !_openDetail.hasAttribute('hidden')) {
          _expandedCode = _openCode;
          _expandedTop = _openRow.classList.contains('is-bv-top');
        }
      }
    }
    if (!_picks || !_picks.length){
      // R13: 失败态 → 重试按钮
      if (_pickError) {
        tbody.innerHTML = '<tr><td colspan="12" class="dim center bv-empty" style="padding:2.5rem">' +
          '<div style="font-size:26px;margin-bottom:8px">⚠️</div>' +
          '<div style="font-size:13px;color:var(--ink-2);margin-bottom:10px">' + esc(_pickError) + '</div>' +
          '<button class="btn-mini bv-retry-btn" onclick="if(window.__bv)window.__bv.refresh(true)" style="padding:8px 20px;background:var(--accent);color:#000;border:0;border-radius:6px;font-weight:700;cursor:pointer">🔄 重试</button>' +
          '</td></tr>';
        if (count) {
          count.textContent = '(加载失败)';
          count.classList.remove('is-stale', 'is-very-stale');
        }
        // R31: 失败时也清掉 stale strip
        var _sfail = $('#bv-stale-strip');
        if (_sfail) { _sfail.hidden = true; _sfail.textContent = ''; _sfail.className = 'bv-stale-strip'; }
        if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
        return;
      }
      // R13: 加载中 → 骨架屏 (3 灰卡 pulse)
      if (_pickLoading) {
        tbody.innerHTML = '<tr><td colspan="12" style="padding:0;border:0">' +
          '<div class="bv-skeleton">' +
          '<div class="bv-skel-card"><div class="bv-skel-line w40"></div><div class="bv-skel-line w70"></div></div>' +
          '<div class="bv-skel-card"><div class="bv-skel-line w40"></div><div class="bv-skel-line w70"></div></div>' +
          '<div class="bv-skel-card"><div class="bv-skel-line w40"></div><div class="bv-skel-line w70"></div></div>' +
          '</div></td></tr>';
        if (count) count.textContent = '(扫描中…)';
        return;
      }
      // R2002.1: 分场景的占位文案 (空态)
      var phaseMsg = {
        pre_market:     '🟡 集合竞价监控中 · 9:30 开盘后出票',
        early:          '🟢 早盘扫描中 · 暂无命中, 继续观察',
        midday:         '🟡 午间守候 · 维持早盘命中, 等待午后异动',
        late_afternoon: '🔴 尾盘抢筹 · 暂无命中',
        closing:        '🟣 收盘集合竞价 · 最后异动',
        close:          '⚫ 盘后守候 · 等待明日 9:30 开盘',
      }[_phase] || '等待行情数据…';
      tbody.innerHTML = '<tr><td colspan="12" class="dim center bv-empty" style="padding:2.5rem">' +
        '<div style="font-size:26px;margin-bottom:8px">📭</div>' +
        '<div style="font-size:14px;color:var(--ink-1);margin-bottom:6px">' + phaseMsg + '</div>' +
        '<div style="font-size:11px;color:var(--ink-3)">扫描全市场 (≈5500 只) · 命中 ≥2 条规则的标的</div>' +
        '<button class="btn-mini" onclick="if(window.__bv)window.__bv.refresh(true)" style="margin-top:12px;padding:8px 20px;background:var(--bg-3);color:var(--ink-1);border:1px solid var(--line-1);border-radius:6px;cursor:pointer">🔄 刷新</button>' +
        '</td></tr>';
      if (count) count.textContent = '(扫描 0 / 命中 0)';
      return;
    }
    var html = '';
    // R21: 过滤后空 (有 _picks 但被 _pickFilter 筛掉) → 给重置快捷
    // R50: _ruleFilter 也参与 — 规则过滤过深时同样给一键清除
    var _filtered21 = _filterPicks(_picks);
    if (_ruleFilter) {
      _filtered21 = _filtered21.filter(function(p){
        return (p.matched_rules || []).indexOf(_ruleFilter) !== -1;
      });
    }
    if (_picks && _picks.length && _filtered21.length === 0) {
      var _stkParts = [];
      if (_ruleFilter) _stkParts.push('规则「' + _ruleFilter + '」');
      if (_pickFilter && _pickFilter !== 'all') _stkParts.push('板块「' + esc(_pickFilter) + '」');
      var _stkDesc = _stkParts.length ? _stkParts.join(' + ') : '当前过滤';
      var _resetCls = _ruleFilter ? 'bv-reset-rule' : 'bv-reset-filter';
      var _resetLabel = _ruleFilter ? (_pickFilter && _pickFilter !== 'all' ? '清除全部' : '清除规则过滤') : '↺ 重置过滤';
      tbody.innerHTML = '<tr><td colspan="12" class="dim center bv-empty" style="padding:2.5rem">' +
        '<div style="font-size:26px;margin-bottom:8px">🔍</div>' +
        '<div style="font-size:14px;color:var(--ink-1);margin-bottom:6px">当前过滤条件下无命中</div>' +
        '<div style="font-size:11px;color:var(--ink-3);margin-bottom:12px">全市场 ' + _picks.length + ' 只命中 · 但 ' + _stkDesc + ' 筛掉全部</div>' +
        '<button class="btn-mini ' + _resetCls + '" style="padding:8px 20px;background:var(--accent);color:#000;border:0;border-radius:6px;font-weight:700;cursor:pointer">' + _resetLabel + '</button>' +
        '</td></tr>';
      if (count) count.textContent = '(扫描 ' + _picks.length + ' / 过滤后 0)';
      var resetBtn = tbody.querySelector('.bv-reset-filter');
      var resetRuleBtn = tbody.querySelector('.bv-reset-rule');
      if (resetRuleBtn) resetRuleBtn.onclick = function(){
        _ruleFilter = null;
        _pickFilter = 'all';
        var bar = $('#bv-filter-bar');
        if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
          c.classList.toggle('is-active', c.getAttribute('data-filter') === 'all');
        });
        renderPicks();
      };
      if (resetBtn) resetBtn.onclick = function(){
        _pickFilter = 'all';
        var bar = $('#bv-filter-bar');
        if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
          c.classList.toggle('is-active', c.getAttribute('data-filter') === 'all');
        });
        renderPicks();
      };
      return;
    }
    // R18: 板块聚合 (按 sector 分组,取每组命中数 + 平均涨幅 + top1 代表股)
    var sectorAgg = {};
    _picks.forEach(function(p){
      var s = p.sector || '其他';
      if (!sectorAgg[s]) sectorAgg[s] = {count:0, codes:[], topChange:-999, topCode:'', topName:''};
      var a = sectorAgg[s];
      a.count++;
      a.codes.push(p.code);
      if ((p.change_pct != null) && p.change_pct > a.topChange) {
        a.topChange = p.change_pct;
        a.topCode = p.code;
        a.topName = p.name;
      }
    });
    var sectorAggArr = Object.entries(sectorAgg).sort(function(a,b){
      if (b[1].count !== a[1].count) return b[1].count - a[1].count;
      return b[1].topChange - a[1].topChange;
    }).slice(0, 8);
    var sectorBar = $('#bv-sector-bar');
    if (sectorBar) {
      if (sectorAggArr.length === 0) {
        sectorBar.innerHTML = '';
        sectorBar.hidden = true;
      } else {
        var shtml = '<span class="bv-sector-bar-label">🔥 板块命中:</span>';
        sectorAggArr.forEach(function(e){
          var s = e[0], a = e[1];
          var hue = _sectorHue(s);
          var chCls = a.topChange >= 0 ? 'bv-pos' : 'bv-neg';
          var chTxt = a.topChange >= 0 ? '+' + a.topChange.toFixed(1) : a.topChange.toFixed(1);
          // R24: 加 is-active 标记如果该板块已被 filter
          var isActive = (_pickFilter === 'sector:' + s);
          var activeCls = isActive ? ' is-active' : '';
          shtml += '<span class="bv-sector-pill' + activeCls + '" data-sector-key="' + esc(s) + '" style="--shue:' + hue + '" title="' + esc(a.topName) + ' ' + chTxt + '% · 命中 ' + a.count + ' 只 · 点击过滤该板块' + (isActive ? ' (再次点击还原)' : '') + '">';
          shtml += '<span class="bv-sector-pill-name">' + esc(s) + '</span>';
          shtml += '<span class="bv-sector-pill-chg ' + chCls + '">' + chTxt + '</span>';
          shtml += '<span class="bv-sector-pill-cnt">×' + a.count + '</span>';
          shtml += '</span>';
        });
        sectorBar.innerHTML = shtml;
        sectorBar.hidden = false;
        // R151: 板块条横向滚动 → 右缘渐变提示 (跟 R150 filter-bar 同 idiom, scrollLeft 边界 4px)
        var _syncSectorScroll = function(){
          if (!sectorBar) return;
          var max = sectorBar.scrollWidth - sectorBar.clientWidth;
          var sl = sectorBar.scrollLeft;
          sectorBar.classList.toggle('has-scroll-left',  sl > 4);
          sectorBar.classList.toggle('has-scroll-right', sl < max - 4);
        };
        if (!sectorBar._scrollBound) {
          sectorBar._scrollBound = true;
          var _raf = false;
          sectorBar.addEventListener('scroll', function(){
            if (_raf) return;
            _raf = true;
            requestAnimationFrame(function(){ _syncSectorScroll(); _raf = false; });
          }, { passive: true });
          window.addEventListener('resize', _syncSectorScroll);
        }
        _syncSectorScroll();
        // R24: 板块 chip 点击 → 过滤该板块 (再次点击还原)
        sectorBar.onclick = function(ev){
          var pill = ev.target.closest('.bv-sector-pill');
          if (!pill) return;
          var key = pill.getAttribute('data-sector-key');
          if (!key) return;
          if (_pickFilter === 'sector:' + key) {
            _pickFilter = 'all';
          } else {
            _pickFilter = 'sector:' + key;
          }
          // R85: 互斥选择只能有一个高亮 — 板块过滤生效时, 筛选条不应显示"全部"高亮
          // (否则出现两个 active: sector pill 青色 + 筛选条"全部"青色, 互相矛盾)
          // 还原时 (sector → all) 才重新点亮"全部"
          var bar = $('#bv-filter-bar');
          if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
            var isAll = c.getAttribute('data-filter') === 'all';
            c.classList.toggle('is-active', _pickFilter === 'all' && isAll);
          });
          renderPicks();
        };
      }
    }
    var sortedPicks = _sortPicks(_filterPicks(_picks), _pickSort);    // R6: 先过滤再排序
    _lastSortedCodes = sortedPicks.map(function(p){ return p.code; }); // R70: prev/next 导航需要当前渲染顺序
    // R48: 规则过滤 — 只显示命中 _ruleFilter 的 picks
    if (_ruleFilter) {
      sortedPicks = sortedPicks.filter(function(p){
        return (p.matched_rules || []).indexOf(_ruleFilter) !== -1;
      });
    }
    // R62: 过滤后平均分 — 绝对数字无意义, 相对位置才有意义
    var _avgScore = 0;
    if (sortedPicks.length) {
      var _sum = 0;
      sortedPicks.forEach(function(p){ _sum += Number(p.score) || 0; });
      _avgScore = _sum / sortedPicks.length;
    }
    // R22: rank0 (最强信号) 默认展开详情 — 一屏看到原话+分数组成, 其它保持折叠
    var _topCode = sortedPicks.length ? sortedPicks[0].code : null;
    sortedPicks.forEach(function(p, i){
      var _isTop = (p.code === _topCode);
      var ruleBadges = (p.matched_rules || []).map(function(rid){
        var r = _rulesById[rid];
        if (!r) return '<span class="chip chip-mute">' + rid + '</span>';
        var tone = {
          '环境': 'chip-good', '仓位': 'chip-info', '选股': 'chip-accent',
          '买入': 'chip-good', '止损': 'chip-warn', '止盈': 'chip-info', '风控': 'chip-bad'
        }[r.category] || 'chip-mute';
        // R48: chip 加点击过滤 + active 高亮
        var _act = (_ruleFilter === rid) ? ' bv-chip-active' : '';
        return '<span class="chip ' + tone + ' bv-rule-chip' + _act + '" title="' + esc(r.title) + ' (点击过滤)" data-rule-id="' + rid + '">' + rid + '</span>';
      });
      // R23: 命中的规则 chips 数量 > 3 时折叠 — 节省水平空间, 防止 chip 出格破坏卡片对齐
      // R251: fold 3→2→1 — rules-cell 可视区 180px, 静态/装饰成分 (10cm 36px + motto 65px)
      //   占满空间后 BV05/BV06/fold 全被横滚推出可视区, fold (展开控制面) 不可达.
      //   第一性原理: 规则行是速览面, 折叠态只承诺 "看到有规则 + 一条锚定规则 + 展开入口可达",
      //   全量详情归展开态 (fold 点击重建全部). fold=1: 板块(50)+命中数(16)+BV03(48)+fold(31)
      //   = 145px < 180px, 20cm 行 +36px 也不溢出; sticky fold 让控制面 pinned 右缘.
      var _RULE_VISIBLE = 1;
      var _ruleTotal = ruleBadges.length;
      var _ruleFold = _ruleTotal > _RULE_VISIBLE
        ? ruleBadges.slice(0, _RULE_VISIBLE).join(' ') + ' <span class="chip chip-mute bv-rule-fold" title="点击展开更多命中规则" data-rule-fold="' + esc(p.code) + '">+' + (_ruleTotal - _RULE_VISIBLE) + '</span>'
        : ruleBadges.join(' ');
      var ruleBadgesJoined = _ruleFold;
      var changeCls = (p.change_pct >= 0) ? 'bv-pos' : 'bv-neg';
      // R16: 涨跌停 (≥9.5% 一字 / ≤-9.5% 跌停) 加重底色, 一眼风险
      var strongCls = '';
      if (p.change_pct != null) {
        if (p.change_pct >= 9.5) strongCls = ' is-up-strong';
        else if (p.change_pct <= -9.5) strongCls = ' is-down-strong';
      }
      var streakTxt = p.streak >= 2 ? p.streak + '板' : (p.streak === 1 ? '首板' : '—');
      // R22: top-1 卡片加 is-bv-top 类 → CSS 顶部细高亮描边 (一眼识别)
      var topCls = _isTop ? ' is-bv-top' : '';
      // R15: 首板加 is-first-board 类 → CSS 红色高亮 (涨停信号最强)
      var firstBoardCls = p.streak === 1 ? ' is-first-board' : '';
      var topQuote = p.top_rule && p.top_rule.quote ? p.top_rule.quote : '';
      var topTs = p.top_rule && p.top_rule.id ? p.top_rule.id : '';
      // R5: 命中总数 badge (≥3 hot, =1 cold) — 放在 tr 内,绝对定位右上角
      var hitN = (p.matched_rules || []).length;
      var badgeCls = hitN >= 3 ? 'bv-hit-badge hot' : (hitN === 1 ? 'bv-hit-badge cold' : 'bv-hit-badge');
      // R113: 命中强度分档 — 加 bv-hit-strong (≥3) / bv-hit-mid (2) / bv-hit-weak (1) class
      // 给卡片左边 3px 着色条 (CSS 实现), 远距离扫视就识别"哪只是真的强", 不用读完 chip 计数
      var hitTier = hitN >= 3 ? ' bv-hit-strong' : (hitN === 2 ? ' bv-hit-mid' : (hitN === 1 ? ' bv-hit-weak' : ''));
      // R2004.1: 12 列与表头严格对齐 (代码/名称/板块/涨幅/换手/连板/封单比/封板/炸板/命中规则/分数/原话)
      // R9: 加 data-sector 给边框着色 + 板块 indicator
      var sectorKey = esc((p.sector || '').replace(/[^\w一-龥]/g, ''));
      html += '<tr class="bv-row' + firstBoardCls + topCls + hitTier + '" data-code="' + esc(p.code) + '" data-sector="' + sectorKey + '">';
      // R245: 板块徽章 (10cm/20cm) 是静态股票属性 (代码前缀即可推断), 不需要占据 col1 身份列
      //   稀缺宽度. 移到规则行 (row3 201px 有余量) 跟 hit-badge/规则 chip 同类并列 — 释放 col1
      //   ~15px 给 col2 (1fr), turnover 三信号单行放得下 (row h 保持 75px 不膨胀).
      // R251: 折叠态只保留 20cm — 10cm 是"主板默认" (未标=主板), 静态可推断, 36px 让位给
      //   fold 展开控制面; 20cm 是高风险例外 (涨跌幅 2×), 必须显式标 (代码前缀 3 开头是创业/
      //   科创混合, 不可靠). 展开态 (fold 点击, 见 1480 行) 仍重建 10cm 徽章全量展示.
      var _boardBadge = '';
      if (p.is_20cm) _boardBadge = '<span class="bv-board-badge bv-board-20" title="创业板/科创板 20cm 单日涨跌幅">20cm</span>';
      // R250: crown 保持在 td 内 (absolute 装饰), td overflow:hidden → visible (见 CSS).
      //   crown 是装饰, 它的视觉宽度 (59px) 不该被 td 裁切 ("TOP1" 丢末字), 而代码文本
      //   本身 45px < col1 地板 56px 不溢出 — overflow 裁的只有 crown. 改 visible 让
      //   crown 完整可见, code 文本仍由 grid track 地板保完整.
      html += '<td><a class="code-link" data-goto-stock="' + esc(p.code) + '" title="查看 ' + esc(p.name) + ' 个股页">' + esc(p.code) + '</a>';
      // R22: top-1 卡片首格放 👑 徽章 (CSS 绝对定位 + glow)
      if (_isTop) html += '<span class="bv-top-crown">👑 TOP1</span>';
      html += '</td>';
      html += '<td>' + esc(p.name);
      // R64: 卡片正面加 top-1 口诀徽章 — "为什么推这只" 一屏扫完不用展开
      // R66: 规则过滤时徽章跟随当前过滤规则 (若有), 避免 "明明只筛 BV02 却标 BV05"
      var _motto = p.top_rule;
      if (_ruleFilter && (p.matched_rules || []).indexOf(_ruleFilter) !== -1) {
        var _fr = _rulesById[_ruleFilter];
        if (_fr) _motto = _fr;
      }
      var _titleTxt = _motto && _motto.title ? _motto.title : '';
      // R245: motto 只给 top-1 行 — 全行同质 (每行相同) 时 8× 重复是噪声不是信号,
      //   top-1 才是决策锚点 ("为什么推这只" 最需要回答的行). 移到规则行后不占 name 格,
      //   不再跟 name 抢 73px (col2 完整交给 name/turnover).
      var _mottoHtml = '';
      if (_titleTxt && _isTop) {
        _mottoHtml = '<span class="bv-motto-badge" title="' + esc(_motto.quote || _titleTxt) + '">' + esc(_titleTxt) + '</span>';
      }
      html += '</td>';
      // R16: sector td 内嵌板块涨幅 (颜色同向,小字号) — 卡片主体一次看全
      var sectorChange = (p.sector_change_pct != null) ? p.sector_change_pct : null;
      var sectorChCls = sectorChange != null ? (sectorChange >= 0 ? 'bv-pos' : 'bv-neg') : '';
      var sectorChTxt = sectorChange != null ? fmtPct(sectorChange) : '';
      // R248: sector-chg 从 col1 移到 rules-cell — 板块涨幅是"板块温度"标签 (R245 模式),
      //   不是 col1 身份 (sector-name 占位). col1 auto=45px 装不下 name(44px)+chg chip(50px),
      //   flex-wrap → row2 两行 → 卡高 71→91px 回归. chip 归位规则行 (overflow-x:auto 只横滚
      //   不撑高 row3), col1 回单行, rowH 回 71px.
      html += '<td class="bv-sector"><span class="bv-sector-name">' + esc(p.sector || '—') + '</span></td>';
      html += '<td class="' + changeCls + strongCls + '">' + fmtPct(p.change_pct, true) + '</td>';
      // R77: 换手格加量比小字 — 量比是资金是否真在进的核心信号, 不该藏详情里
      // 量比 ≥1.5 标亮 (资金放大), <0.8 标弱
      // R79: 再加成交额 — 量比是放大多少倍, 成交额是绝对资金体量; 地量涨停 vs 巨量涨停两种局面必须能一眼区分
      var _vr = Number(p.vol_ratio || 0) || 0;
      var _vrCls = _vr >= 1.5 ? ' bv-vr-hot' : (_vr >= 1.0 ? '' : ' bv-vr-cold');
      var _amt = Number(p.amount_yi || 0) || 0;
      var _amtTxt = _amt > 0 ? (_amt >= 1 ? fmtNum(_amt, 1) + '亿' : '<0.1亿') : '';
      html += '<td>' + fmtNum(p.turnover_pct, 2) + '%' + (_vr > 0 ? '<span class="bv-vr' + _vrCls + '" title="量比 ' + _vr.toFixed(2) + '">' + _vr.toFixed(1) + '</span>' : '') + (_amtTxt ? '<span class="bv-vr bv-vr-amt" title="成交额 ' + _amt.toFixed(2) + ' 亿">' + _amtTxt + '</span>' : '') + '</td>';
      html += '<td>' + streakTxt + '</td>';
      html += '<td title="封单金额/成交金额">' + fmtSeal(p.seal_ratio) + '</td>';
      html += '<td>' + fmtTime(p.first_time) + '</td>';
      // R97: 炸板计数与分数共享右下 burst 格 — 有炸板时炸板胜出 (少/弱信号让位给卡面),
      //      无炸板时空格让位给分数 (决策信号专属工位). 一个格子一个信号.
      html += '<td class="bv-burst' + (p.burst_count > 0 ? '' : ' bv-burst-empty') + '">' + (p.burst_count > 0 ? esc(String(p.burst_count)) : '—') + '</td>';
      // R5: badge + rules chips 同格 (badge 在规则行内嵌首格)
      // R80: badge 不再绝对定位右上角 (原 top:-22px 与连板 chip 抢右上角 448px² 重叠) — 连板独占右上角, 命中数留在规则行
      // R248: sector-chg chip 归位规则行 (R245 标签下沉模式) — board-badge 之后、命中数之前
      var _sectorChgHtml = '';
      if (sectorChTxt) {
        _sectorChgHtml = '<span class="bv-sector-chg ' + sectorChCls + '" title="板块涨幅 ' + sectorChTxt + '">' + sectorChTxt + '</span>';
      }
      html += '<td class="bv-rules-cell">' + _boardBadge + _sectorChgHtml + _mottoHtml + '<span class="' + badgeCls + '" title="命中 ' + hitN + ' 条规则">' + hitN + '</span>' + ruleBadgesJoined + '</td>';
      // R11: 分数 cell → 迷你分数条 (强>60绿/中30-60黄/弱<30红) + 数值
      // R62: 加平均参考线 (▕) + 相对倍率标签 (×N 均) — 绝对数字无意义, 相对位置才有意义
      var sc = Number(p.score) || 0;
      var scTone = sc >= 60 ? 'strong' : (sc >= 30 ? 'mid' : 'weak');
      var _avgLeft = Math.min(100, _avgScore);
      var _scPct = Math.min(100, sc);
      var _rel = _avgScore > 0 ? (sc / _avgScore) : 0;
      var _relTxt = '';
      if (_avgScore > 0) {
        if (_rel >= 1.15) _relTxt = '<span class="bv-score-rel high">×' + _rel.toFixed(1) + ' 均</span>';
        else if (_rel <= 0.85) _relTxt = '<span class="bv-score-rel low">×' + _rel.toFixed(1) + ' 均</span>';
      }
      // R97: 有炸板时分数让位 (卡面只显示炸板计数, 分数组成仍在详情行) — 一个格子一个信号
      html += '<td class="bv-score' + ((Number(p.burst_count) || 0) > 0 ? ' bv-score-yield' : '') + '" title="分数 ' + sc.toFixed(1) + ' · 平均 ' + _avgScore.toFixed(1) + '"><div class="bv-score-bar"><div class="bv-score-fill ' + scTone + '" style="width:' + _scPct + '%"></div>';
      if (_scPct > _avgLeft) html += '<div class="bv-score-avgline" style="left:' + _avgLeft + '%" title="平均 ' + _avgScore.toFixed(1) + ' 分"></div>';
      html += '</div><span class="bv-score-num">' + fmtNum(sc, 0) + '</span>' + _relTxt + '</td>';
      // 原话列: 显示 quote 前 14 字, hover 看全文
      var quoteShort = topQuote.length > 14 ? topQuote.slice(0, 14) + '…' : topQuote;
      html += '<td class="bv-quote" title="' + esc(topQuote) + '">' + esc(quoteShort || topTs) + '</td>';
      // R12: 右上角常驻 📈 跳个股按钮 (放 td 内避免 button 非法直接子元素, CSS absolute 定位)
      // R19: 加 ♥ 自选按钮 (位于 📈 左侧), 走 wlToggle POST
      html += '<td class="bv-jump-btn-cell"><button class="bv-jump-btn" data-goto-stock="' + esc(p.code) + '" aria-label="查看 ' + esc(p.name) + ' 个股页" title="查看 ' + esc(p.name) + ' 个股页">📈</button>';
      html += '<button class="bv-wl-btn" data-wl-add="' + esc(p.code) + '" aria-label="加自选 ' + esc(p.name) + '" title="加自选 ' + esc(p.name) + '">♥</button>';
      // R361 · 一键问 AI 按钮 (跳 yeren-ai tab, 自动填入 "+ code 帮我分析")
      html += '<button class="bv-ai-btn" data-ai-ask="' + esc(p.code) + '" aria-label="问 AI ' + esc(p.name) + '" title="问 AI: ' + esc(p.name) + ' (' + esc(p.code) + ') 的战法判断">💬</button></td>';
      html += '</tr>';
      // R2: mobile 卡片展开详情行 (默认折叠,点击卡片切换)
      var fullQuote = p.top_rule && p.top_rule.quote ? esc(p.top_rule.quote) : '';
      // R63: 分数组成 → 按贡献降序 + 权重条 — "80 分哪来的" 决定下一步操作
      var scoreParts = (p.score_breakdown || []).map(function(sb){
        var _c = Number(sb.contribution || sb.score || 0) || 0;
        return { id: esc(sb.rule_id || sb.id || '?'), c: _c };
      });
      var _maxC = 0;
      scoreParts.forEach(function(s){ if (s.c > _maxC) _maxC = s.c; });
      scoreParts.sort(function(a, b){ return b.c - a.c; });
      var scoreHtml = scoreParts.length ? scoreParts.map(function(s, i){
        var _w = _maxC > 0 ? Math.max(12, Math.round(s.c / _maxC * 100)) : 12;
        return '<div class="bv-scorepart' + (i === 0 ? ' bv-scorepart-top' : '') + '">' +
               '<span class="bv-scorepart-id">' + s.id + '</span>' +
               '<span class="bv-scorepart-bar"><span class="bv-scorepart-fill" style="width:' + _w + '%"></span></span>' +
               '<span class="bv-scorepart-val">+' + fmtNum(s.c, 0) + '</span></div>';
      }).join('') : '';
      // R22: top-1 detail-row 默认展开 (无 hidden), 其它保持折叠
      // R101 (2026-08-20): mobile 默认折叠 top-1 详情 — top-1 详情占 309px 等于 3 张卡高度,
      //      首屏只有 1 张卡可见。桌面保留 R22 自动展开(桌面空间够, 一屏看清 top-1 不影响扫描),
      //      mobile 用户进来是扫列表, 点击其他卡时 R61 accordion 会自动重开 top-1 详情 (line 1357)。
      var _isMobileDefault = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
      var _topExpandedAttr = (_isTop && !_isMobileDefault) ? '' : ' hidden';
      html += '<tr class="bv-detail-row" data-detail-for="' + esc(p.code) + '"' + _topExpandedAttr + '>';
      html += '<td colspan="12">';
      html += '<div class="bv-detail-inner">';
      // R59: mobile 显式折叠按钮 — 不依赖手势也可关闭
      html += '<button class="bv-detail-collapse" data-collapse-for="' + esc(p.code) + '">✕ 收起</button>';
      // R65: quote 加视频时间戳锚点 — 证据链锚定在视频原文, 权威性来自可追溯
      var _qTs = p.top_rule && p.top_rule.timestamp ? p.top_rule.timestamp : '';
      var _qTsLink = '';
      if (_qTs) {
        _qTsLink = ' <a class="bv-quote-ts" data-video-ts="' + esc(_qTs) + '" href="javascript:void(0)" title="在视频 ' + esc(_qTs) + ' 处查看原话">@ ' + esc(_qTs) + '</a>';
      }
      html += '<div class="bv-detail-section"><span class="bv-detail-label">💬 UP 主原话</span><div class="bv-detail-quote">' + (fullQuote || '<span class="dim">— 无 —</span>') + _qTsLink + '</div></div>';
      // R69: breakdown 空但 score>0 时兜底显示 matched_rules 权重 — 不显示误导的"详见命中规则"
      var _scoreFallback = '';
      if (!scoreHtml && sc > 0 && (p.matched_rules || []).length) {
        _scoreFallback = p.matched_rules.map(function(rid){
          var _rw = _rulesById[rid] || {};
          return '<div class="bv-scorepart"><span class="bv-scorepart-id">' + esc(rid) + '</span>' +
                 '<span class="bv-scorepart-bar"><span class="bv-scorepart-fill" style="width:12%"></span></span>' +
                 '<span class="bv-scorepart-val">w' + (_rw.score_weight || '?') + '</span></div>';
        }).join('');
      }
      html += '<div class="bv-detail-section"><span class="bv-detail-label">📊 分数组成</span><div class="bv-detail-scores">' + (scoreHtml || _scoreFallback || '<span class="dim">— 暂无 —</span>') + '</div></div>';
      // R26: 板块跳转链接 (进板块详情看全部个股)
      // R82: 板块跳转并入操作行首格 — "离开这张卡去别处" 的同类动作同排, 不单独占整行撑高详情
      // R82: ops 2×2 grid — 去别处 (板块|个股页) 一行, 扫列表 (←上一只|下一只→) 一行
      html += '<div class="bv-detail-section"><span class="bv-detail-label">⚡ 操作</span><div class="bv-detail-ops">';
      html += '<a class="bv-detail-sector-link bv-detail-op" data-goto-sector="' + esc(p.sector || '') + '" href="javascript:void(0)">🏷️ ' + esc(p.sector || '—') + '</a>';
      html += '<button class="btn-mini bv-detail-jump" data-goto-stock="' + esc(p.code) + '">查看个股页 →</button>';
      // R70: prev/next 切换 — 扫列表是循环动作, 折叠→滚动→展开打断心流; 同高度切相邻 pick
      html += '<button class="btn-mini bv-detail-prev" data-bv-nav="-1" data-code="' + esc(p.code) + '">← 上一只</button>';
      html += '<button class="btn-mini bv-detail-next" data-bv-nav="1" data-code="' + esc(p.code) + '">下一只 →</button>';
      html += '</div></div>';
      html += '</div></td></tr>';
    });
    tbody.innerHTML = html;
    // R27: 快照时间 (HH:MM) 跟 命中数 拼一起 — 用户一眼知道"这数据几点刷的"
    // R29: 阶段标签 (早盘/午休/尾盘...) 也拼上 — 知道时段决策权重差很大
    var _phaseMap = {
      pre_market:     { label: '🟡集合',  cls: 'bv-phase-pre'     },
      early:          { label: '🟢早盘',  cls: 'bv-phase-early'   },
      midday:         { label: '🟡午休',  cls: 'bv-phase-midday'  },
      late_afternoon: { label: '🔴尾盘',  cls: 'bv-phase-late'    },
      closing:        { label: '🟣收盘',  cls: 'bv-phase-closing' },
      close:          { label: '⚫盘后',  cls: 'bv-phase-close'   }
    };
    var _phaseInfo = _phaseMap[_phase] || _phaseMap.close;
    var _tsStr = ' · <span class="' + _phaseInfo.cls + '">' + _phaseInfo.label + '</span>';
    // R102 (2026-08-20): mobile 默认隐藏 "快照 HH:MM" — 信息已在 bv-stale-strip (R31-R38) 单独展示,
    //      卡片表头加它会让 h3 撑成 2 行 (44px), 推票卡整体下移 ~20px。桌面保留完整字符串。
    var _isMobileHead = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    if (_dataTs > 0 && !_isMobileHead) {
      var _d = new Date(_dataTs * 1000);
      var _hh = String(_d.getHours()).padStart(2, '0');
      var _mm = String(_d.getMinutes()).padStart(2, '0');
      _tsStr += ' 快照 ' + _hh + ':' + _mm;
      // R28: 陈旧度提示 — 距快照 > 60s 加 ⚠️ 标记, > 5min 加重
      var _age = Math.floor((Date.now() / 1000) - _dataTs);
      if (_age > 60) {
        if (_age > 300) {
          _tsStr += ' ⚠️ 陈旧 ' + Math.floor(_age / 60) + '分钟';
        } else {
          _tsStr += ' ⚠️ ' + _age + 's';
        }
      }
    }
    if (count) {
      // R36: 阶段 span 需 innerHTML, 整个串安全 (整数 + emoji + <span class=>), 无注入风险
      // R48+R49: 应用规则过滤 — 显示命中过滤后数量 + 过滤栈深度
      if (_ruleFilter) {
        var _fc = 0;
        for (var _fi = 0; _fi < _picks.length; _fi++) {
          if ((_picks[_fi].matched_rules || []).indexOf(_ruleFilter) !== -1) _fc++;
        }
        var _stk = [];
        // R68: 过滤指示自解释 — "🔍 BV02" → "🔍 BV02 弱转强", 规则 ID 无意义标题才有意义
        var _fRuleObj = _rulesById[_ruleFilter];
        var _fRuleLabel = _ruleFilter + (_fRuleObj && _fRuleObj.title ? ' ' + _fRuleObj.title : '');
        if (_ruleFilter) _stk.push('规则 ' + _fRuleLabel);
        if (_pickFilter && _pickFilter !== 'all') _stk.push(_pickFilter);
        var _stkTxt = _stk.length > 1 ? ' · ' + _stk.length + ' 个条件' : '';
        count.innerHTML = '(命中 <b>' + _fc + '</b> / ' + _picks.length + ' · 🔍 ' + _fRuleLabel + _stkTxt + ') <a class="bv-rule-clear" href="javascript:void(0)">清除</a>';
        var _clr = count.querySelector('.bv-rule-clear');
        if (_clr && !_clr.dataset.bvClickable) {
          _clr.dataset.bvClickable = '1';
          _clr.addEventListener('click', function(ev){
            ev.stopPropagation();
            // R85: "清除" = 回到无过滤 — 清规则 + 板块 + pickFilter, 并重置两个高亮条
            _ruleFilter = null;
            _pickFilter = 'all';
            var bar = $('#bv-filter-bar');
            if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
              c.classList.toggle('is-active', c.getAttribute('data-filter') === 'all');
            });
            var sb = $('#bv-sector-bar');
            if (sb) sb.querySelectorAll('.bv-sector-pill.is-active').forEach(function(p){
              p.classList.remove('is-active');
            });
            renderPicks();
          });
        }
      } else {
        // R93: 计数不能说谎 — 有 _pickFilter 时计数必须反映过滤后可见数 (同 R87 footer 口径)
        var _visC = _filterPicks(_picks).length;
        var _cntTxt = (_visC < _picks.length)
          ? ('(过滤后 ' + _visC + ' / 全部 ' + _picks.length + _tsStr + ')')
          : ('(扫描 ' + (_picks.length ? '≥' + _picks.length : 0) + ' / 命中 ' + _picks.length + _tsStr + ')');
        count.innerHTML = _cntTxt;
      }
      // R28: 陈旧度 class — 让超 60s/超 5min 时颜色明显不同
      if (_dataTs > 0) {
        var _ageNow = Math.floor((Date.now() / 1000) - _dataTs);
        count.classList.remove('is-stale', 'is-very-stale');
        if (_ageNow > 300) {
          count.classList.add('is-very-stale');
        } else if (_ageNow > 60) {
          count.classList.add('is-stale');
        }
      } else {
        count.classList.remove('is-stale', 'is-very-stale');
      }
      // R30: count 文本本身可点击 → 触发刷新 — 一步直达
      if (!count.dataset.bvClickable) {
        count.dataset.bvClickable = '1';
        count.style.cursor = 'pointer';
        count.title = '点击刷新';
        count.addEventListener('click', function(){
          if (window.__bv && window.__bv.refresh) window.__bv.refresh(true);
        });
      }
      // R31: 陈旧时顶部闪 strip — 显眼化, 不藏角落
      var _strip = $('#bv-stale-strip');
      if (_strip) {
        // R32: strip 自身可点击 → 触发刷新 — 用户看到陈旧时一步响应
        if (!_strip.dataset.bvClickable) {
          _strip.dataset.bvClickable = '1';
          _strip.title = '点击刷新 · 长按暂停自动刷新 5min';
          _strip.addEventListener('click', function(){
            if (window.__bv && window.__bv.refresh) window.__bv.refresh(true);
          });
          // R56: 长按 strip → 暂停自动刷新 5 分钟, 用户锁定当前陈旧视图
          var _lpTimer = null;
          _strip.addEventListener('touchstart', function(ev){
            _lpTimer = setTimeout(function(){
              _autoPausedUntil = Date.now() + 5 * 60 * 1000;
              _strip.classList.add('bv-stale-paused');
              _strip.title = '⏸ 已暂停自动刷新 (剩余 ' + Math.ceil((_autoPausedUntil - Date.now()) / 60000) + 'min)';
              // R56: 调用 _autoRefresh 实际暂停
              if (typeof _autoRefresh !== 'undefined' && _autoRefresh.stop) _autoRefresh.stop('bv-live');
              if (navigator.vibrate) navigator.vibrate(15);
            }, 600);
          }, {passive: true});
          _strip.addEventListener('touchend', function(){ if (_lpTimer) clearTimeout(_lpTimer); });
          _strip.addEventListener('touchmove', function(){ if (_lpTimer) clearTimeout(_lpTimer); });
        }
        if (_dataTs > 0) {
          var _ageStrip = Math.floor((Date.now() / 1000) - _dataTs);
          if (_ageStrip > 60) {
            _strip.hidden = false;
            // R46: 离线徽章 — 明确告诉用户是网络问题而非上游慢
            var _offline = (typeof navigator !== 'undefined' && navigator.onLine === false) ? '<b class="bv-stale-offline">📡 离线</b> · ' : '';
            // R56: 暂停态显示
            var _paused = (_autoPausedUntil > Date.now()) ? '<b class="bv-stale-paused-badge">⏸ 已暂停</b> · ' : '';
            // R57: 静默更新 — 30s 内有 silentUpdate flag 才显示
            var _silent = (_silentUpdateFlag && (Date.now() - _silentUpdateFlag.at) < 30000)
              ? '<b class="bv-stale-silent">✓ 数据无变化</b> · ' : '';
            if (_ageStrip > 300) {
              _strip.className = 'bv-stale-strip is-very-stale';
              _strip.innerHTML = _paused + _silent + _offline + '📉 <b>数据已陈旧</b> · ' + Math.floor(_ageStrip / 60) + ' 分钟前 <span class="bv-stale-hint">点击刷新</span>';
            } else {
              _strip.className = 'bv-stale-strip is-stale';
              var _remTxt = '';
              if (typeof _autoRefresh !== 'undefined' && _autoRefresh.remaining) {
                var _rem = _autoRefresh.remaining('bv-live');
                if (_rem !== null && _rem !== undefined) _remTxt = ' · 下次刷新 ' + _rem + 's';
              }
              _strip.innerHTML = _paused + _silent + _offline + '📊 <b>数据略旧</b> · ' + _ageStrip + 's' + _remTxt + ' <span class="bv-stale-hint">点击刷新</span>';
            }
            // R35: 陈旧时启动每秒 tick — strip 文案实时更新 age
            if (!_ageTick) {
              _ageTick = setInterval(function(){
                var _s = $('#bv-stale-strip');
                if (!_s || _s.hidden || !_dataTs) return;
                var _a = Math.floor(Date.now() / 1000) - _dataTs;
                if (_a <= 60) {
                  // 回到新鲜, 隐藏
                  _s.hidden = true;
                  _s.textContent = '';
                  if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
                  return;
                }
                var _offline2 = (typeof navigator !== 'undefined' && navigator.onLine === false) ? '<b class="bv-stale-offline">📡 离线</b> · ' : '';
                var _paused2 = (_autoPausedUntil > Date.now()) ? '<b class="bv-stale-paused-badge">⏸ 已暂停</b> · ' : '';
                var _silent2 = (_silentUpdateFlag && (Date.now() - _silentUpdateFlag.at) < 30000) ? '<b class="bv-stale-silent">✓ 数据无变化</b> · ' : '';
                if (_a > 300) {
                  _s.className = 'bv-stale-strip is-very-stale';
                  _s.innerHTML = _paused2 + _silent2 + _offline2 + '📉 <b>数据已陈旧</b> · ' + Math.floor(_a / 60) + ' 分钟前 <span class="bv-stale-hint">点击刷新</span>';
                } else {
                  _s.className = 'bv-stale-strip is-stale';
                  var _remTxt2 = '';
                  if (typeof _autoRefresh !== 'undefined' && _autoRefresh.remaining) {
                    var _rem2 = _autoRefresh.remaining('bv-live');
                    if (_rem2 !== null && _rem2 !== undefined) _remTxt2 = ' · 下次刷新 ' + _rem2 + 's';
                  }
                  _s.innerHTML = _paused2 + _silent2 + _offline2 + '📊 <b>数据略旧</b> · ' + _a + 's' + _remTxt2 + ' <span class="bv-stale-hint">点击刷新</span>';
                }
              }, 1000);
            }
          } else {
            _strip.hidden = true;
            _strip.className = 'bv-stale-strip';
            _strip.textContent = '';
            if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
          }
        } else {
          _strip.hidden = true;
          _strip.className = 'bv-stale-strip';
          _strip.textContent = '';
          if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
        }
      }
    }
    _updateSortIndicators();
    _updateFilterCounts();   // R6: 刷新筛选条计数
    _applySectorHues();     // R9: sector 着色
    // R25: 滚动位置恢复 — 如果 _firstVisibleCode 还在新 DOM 中, 用它的位置恢复; 否则回 _scrollY
    // R100 (2026-08-20): 守护 _scrollY > 0 — 首次渲染 _scrollY=0 时, _focusRow 仍在原位(此时 rect.top ~270px),
    //      算法会把卡片 "放回" 20% 视口位置, 结果空抖一次跳到 270px 把 view-head/creed 全挤到屏外。
    //      只在用户已滚动过(_scrollY>0)的重渲染(自动刷新/筛选切换)才走恢复; 首屏/初次加载保持顶部。
    if (_firstVisibleCode && _scrollY > 0) {
      var _focusRow = tbody.querySelector('tr.bv-row[data-code="' + _firstVisibleCode + '"]');
      if (_focusRow) {
        var _delta = _focusRow.getBoundingClientRect().top;
        var _newY = (_scrollY + _delta) - (window.innerHeight * 0.2);
        window.scrollTo({top: Math.max(0, _newY), behavior: 'auto'});
      } else if (_scrollY > 0) {
        window.scrollTo({top: _scrollY, behavior: 'auto'});
      }
    }
    // R72: 重渲染后保持用户手动展开的详情卡 — 自动刷新不打断阅读流
    if (_expandedCode && !_expandedTop) {
      var _expandedRow = tbody.querySelector('tr.bv-row[data-code="' + _expandedCode + '"]');
      var _expandedDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _expandedCode + '"]');
      if (_expandedRow && _expandedDetail) {
        _expandedDetail.removeAttribute('hidden');
        _expandedRow.classList.add('bv-expanded');
      }
    }
    // 委托点击 + 长按 (mobile 多选模式 R8)
    var _longPressTimer = null;
    var _longPressTriggered = false;
    tbody.addEventListener('contextmenu', function(ev){
      ev.preventDefault();   // 禁用 mobile 长按系统菜单
    });
    tbody.addEventListener('touchstart', function(ev){
      var tr = ev.target.closest('tr.bv-row');
      if (!tr || !tr.dataset.code) return;
      var code = tr.dataset.code;
      var name = tr.querySelector('td:nth-child(2)')?.textContent.trim() || code;
      _longPressTriggered = false;
      _longPressTimer = setTimeout(function(){
        _longPressTriggered = true;
        if (!_multiMode) _enterMultiMode(code, name);
        else _toggleMulti(code, name);
        // 触觉反馈 (mobile 振动 ~10ms)
        if (navigator.vibrate) navigator.vibrate(10);
      }, 500);
    }, {passive: true});
    tbody.addEventListener('touchend', function(){
      if (_longPressTimer) clearTimeout(_longPressTimer);
    });
    tbody.addEventListener('touchmove', function(ev){
      if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
    }, {passive: true});
    // R52: 左滑卡片露出 ❤️ 自选快捷 — touchmove 横向 >40px 时偏移, 释放触发
    var _swipeStartX = 0, _swipeStartY = 0, _swipeTr = null, _swipeDir = null;
    tbody.addEventListener('touchstart', function(ev){
      var tr = ev.target.closest('tr.bv-row');
      if (!tr || !tr.dataset.code) return;
      var t = ev.touches[0];
      _swipeStartX = t.clientX;
      _swipeStartY = t.clientY;
      _swipeTr = tr;
      _swipeDir = null;
    }, {passive: true});
    tbody.addEventListener('touchmove', function(ev){
      if (!_swipeTr) return;
      var t = ev.touches[0];
      var dx = t.clientX - _swipeStartX;
      var dy = t.clientY - _swipeStartY;
      if (!_swipeDir) {
        if (Math.abs(dx) < 12 && Math.abs(dy) < 12) return;
        _swipeDir = (Math.abs(dx) > Math.abs(dy)) ? 'h' : 'v';
        if (_longPressTimer) { clearTimeout(_longPressTimer); _longPressTimer = null; }
      }
      if (_swipeDir === 'h' && dx < 0) {
        // 左滑: 最大 -100px, 超过回弹
        var off = Math.max(-100, dx);
        _swipeTr.style.transform = 'translateX(' + off + 'px)';
        _swipeTr.style.transition = 'none';
        _swipeTr.dataset.swipeOff = off;
        if (off < -30) _swipeTr.classList.add('swiping');
      } else if (_swipeDir === 'h' && dx > 0) {
        // R53: 右滑 — 露出 ↗ 跳个股
        var offR = Math.min(100, dx);
        _swipeTr.style.transform = 'translateX(' + offR + 'px)';
        _swipeTr.style.transition = 'none';
        _swipeTr.dataset.swipeOff = offR;
        if (offR > 30) _swipeTr.classList.add('swiping-right');
      }
    }, {passive: true});
    tbody.addEventListener('touchend', function(ev){
      if (!_swipeTr) return;
      var off = parseFloat(_swipeTr.dataset.swipeOff || 0);
      if (_swipeDir === 'h' && off < -60) {
        // 触发自选
        var code = _swipeTr.dataset.code;
        if (code && typeof wlToggle === 'function') wlToggle(code);
        if (navigator.vibrate) navigator.vibrate(15);
      } else if (_swipeDir === 'h' && off > 60) {
        // R53: 触发跳个股
        var codeR = _swipeTr.dataset.code;
        if (codeR && typeof gotoStock === 'function') gotoStock(codeR);
        if (navigator.vibrate) navigator.vibrate(15);
      }
      // 回弹
      _swipeTr.classList.remove('swiping', 'swiping-right');
      _swipeTr.style.transform = '';
      _swipeTr.style.transition = '';
      delete _swipeTr.dataset.swipeOff;
      _swipeTr = null;
      _swipeDir = null;
    });
    tbody.onclick = function(ev){
      var tr = ev.target.closest('tr.bv-row');
      // R59: ✕ 收起按钮
      var cb = ev.target.closest('.bv-detail-collapse');
      if (cb) {
        ev.stopPropagation();
        ev.preventDefault();
        var cc = cb.getAttribute('data-collapse-for');
        var d = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + cc + '"]');
        if (d) d.setAttribute('hidden', '');
        var r = tbody.querySelector('tr.bv-row[data-code="' + cc + '"]');
        if (r) r.classList.remove('bv-expanded');
        window.__lastBvExpand = null;
        if (navigator.vibrate) navigator.vibrate(5);
        return;
      }
      // R2: 点击详情行的「查看个股页」按钮 → 跳个股
      // R76: 多选态下跳个股前先退出多选 — 临时态不该泄漏到导航目标页
      var jb = ev.target.closest('.bv-detail-jump');
      if (jb) {
        ev.stopPropagation();
        var code = jb.getAttribute('data-goto-stock');
        if (_multiMode) _exitMultiMode();
        if (typeof gotoStock === 'function') gotoStock(code);
        return;
      }
      // R26: 详情行板块链接 → 触发 R24 板块过滤 (收起 detail + 过滤到该板块)
      var sb = ev.target.closest('.bv-detail-sector-link');
      if (sb) {
        ev.stopPropagation();
        var sector = sb.getAttribute('data-goto-sector');
        if (sector) {
          // 收起当前展开的 detail-row
          var openDetail = tbody.querySelector('tr.bv-detail-row:not([hidden])');
          if (openDetail) openDetail.setAttribute('hidden', '');
          // 触发 R24 板块过滤
          _pickFilter = 'sector:' + sector;
          var bar = $('#bv-filter-bar');
          if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
            c.classList.toggle('is-active', c.getAttribute('data-filter') === 'all');
          });
          renderPicks();
          // 滚到顶部让用户看到板块条
          window.scrollTo({top: 0, behavior: 'smooth'});
        }
        return;
      }
      // R70: 详情 prev/next 切换 — 同位置展开相邻 pick, 不打断滚动位置
      var navBtn = ev.target.closest('.bv-detail-prev, .bv-detail-next');
      if (navBtn) {
        ev.stopPropagation();
        var navDir = Number(navBtn.getAttribute('data-bv-nav')) || 0;
        var navCode = navBtn.getAttribute('data-code');
        if (navDir && navCode) {
          var navIdx = _lastSortedCodes.indexOf(navCode);
          var navTarget = navIdx + navDir;
          if (navTarget >= 0 && navTarget < _lastSortedCodes.length) {
            var navTgtCode = _lastSortedCodes[navTarget];
            // 收起当前 detail, 展开目标 detail (target row 保持可见)
            var openD = tbody.querySelector('tr.bv-detail-row:not([hidden])');
            if (openD) openD.setAttribute('hidden', '');
            var tgtRow = tbody.querySelector('tr.bv-row[data-code="' + navTgtCode + '"]');
            var tgtDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + navTgtCode + '"]');
            if (tgtRow) {
              tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
              tgtRow.classList.add('bv-expanded');
            }
            if (tgtDetail) tgtDetail.removeAttribute('hidden');
            window.__lastBvExpand = {code: navTgtCode, ts: Date.now()};
            if (navigator.vibrate) navigator.vibrate(5);
            // R71: 切到新卡自动滚到可见 — 不滚用户不知道切到了哪
            if (tgtRow) {
              var _tRect = tgtRow.getBoundingClientRect();
              if (_tRect.top < 0 || _tRect.bottom > (window.innerHeight || 500)) {
                tgtRow.scrollIntoView({block: 'nearest', behavior: 'smooth'});
              }
            }
          }
        }
        return;
      }
      // R65: 视频时间戳锚点 → 跳视频对应分钟 (open BV 页面, 到 timestamp)
      var vt = ev.target.closest('.bv-quote-ts');
      if (vt) {
        ev.stopPropagation();
        ev.preventDefault();
        var ts = vt.getAttribute('data-video-ts') || '';
        if (ts) {
          // 解析 MM:SS → 秒
          var _parts = String(ts).split(':');
          var _sec = 0;
          for (var i = 0; i < _parts.length; i++) _sec = _sec * 60 + (Number(_parts[i]) || 0);
          // 打开 B 站视频到对应时间点
          window.open('https://www.bilibili.com/video/BV1JoNUzTE2i/?t=' + _sec, '_blank');
        }
        return;
      }
      // R19: ♥ 自选按钮 → 调 wlToggle
      var wb = ev.target.closest('.bv-wl-btn');
      if (wb) {
        ev.stopPropagation();
        var wlCode = wb.getAttribute('data-wl-add');
        if (wlCode) {
          // R165 2026-08-20: 之前走 api('/api/watchlist/{code}', POST) — endpoint 不存在 (404)。
          // 改用 wlToggle (app.js 统一入口, 正确 POST /api/watchlist + JSON body)。
          // wlToggle 返 true=新加, false=已存在 (端点统一处理 dedupe), 视觉同步切。
          wb.classList.add('is-loading');
          var wlName = wb.getAttribute('data-wl-name') || wlCode;
          window.wlToggle(wlCode, wlName).then(function(added){
            wb.classList.remove('is-loading');
            wb.classList.add('is-added');
            wb.textContent = added ? '❤️' : '✓';  // 已存在显 ✓ 不显 ♥ (避免重复心误以为是新加)
          }).catch(function(){
            wb.classList.remove('is-loading');
            wb.classList.add('is-error');
            setTimeout(function(){ wb.classList.remove('is-error'); }, 1200);
          });
        }
        return;
      }
      var a = ev.target.closest('[data-goto-stock]');
      if (a && !_multiMode) {
        var code = a.getAttribute('data-goto-stock');
        if (typeof gotoStock === 'function') gotoStock(code);
        return;
      }
      // R361 · 💬 一键问 AI 按钮 → 切到 yeren-ai tab, 装入 "+code 帮我分析"
      var ab = ev.target.closest('[data-ai-ask]');
      if (ab) {
        ev.stopPropagation();
        ev.preventDefault();
        var aiCode = ab.getAttribute('data-ai-ask');
        if (typeof _bvAskYerenAi === 'function') {
          _bvAskYerenAi(aiCode);
        } else if (typeof switchView === 'function') {
          // 兜底: 切到 yeren-ai view + 写 message
          switchView('yeren-ai');
          setTimeout(function(){
            var msgInput = document.getElementById('yeren-ai-msg');
            if (msgInput) msgInput.value = aiCode + ' 帮我分析下, 战法上值不值得关注?';
          }, 350);
        }
        return;
      }
      if (!tr || !tr.dataset.code) return;
      var code = tr.dataset.code;
      var name = tr.querySelector('td:nth-child(2)')?.textContent.trim() || code;
      // 长按触发后, 短点击应该切换多选状态
      if (_multiMode) {
        if (_longPressTriggered) { _longPressTriggered = false; return; }
        _toggleMulti(code, name);
        return;
      }
      // R2: mobile 普通点击 → 切换展开/收起 detail-row
      if (window.matchMedia && window.matchMedia('(max-width: 768px)').matches) {
        ev.preventDefault();
        var detail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]');
        if (detail) {
          var wasOpen = !detail.hasAttribute('hidden');
          tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
          tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
          // R22: top-1 卡片永保展开 (单卡片模式变成 "top + 选中的另一张")
          var _topReopen = (code === tbody.firstElementChild && tr.classList.contains('is-bv-top'));
          // R54: 撤销逻辑 — 若点的是非 top, 300ms 内再次点同一卡片 → 撤销展开
          var _now = Date.now();
          var _undo = wasOpen && !_topReopen &&
                      window.__lastBvExpand && window.__lastBvExpand.code === code &&
                      (_now - window.__lastBvExpand.ts) < 600;
          if (_undo) {
            window.__lastBvExpand = null;  // 撤销后清掉, 否则第三次点会再次撤销
            if (navigator.vibrate) navigator.vibrate(8);  // R58: 触觉反馈
            return;
          }
          if (!wasOpen || _topReopen) {
            detail.removeAttribute('hidden');
            tr.classList.add('bv-expanded');
            // R61: 非 top 卡片展开时, 让 top-1 重新可见 — 形成 "top + 当前" 双卡模式
            var _firstRow = tbody.firstElementChild;
            if (_firstRow && _firstRow !== tr && _firstRow.classList.contains('is-bv-top')) {
              var _topDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _firstRow.dataset.code + '"]');
              if (_topDetail) _topDetail.removeAttribute('hidden');
              _firstRow.classList.add('bv-expanded');
            }
            window.__lastBvExpand = {code: code, ts: _now};
            if (navigator.vibrate) navigator.vibrate(8);  // R58: 触觉反馈
            // R60: 展开后确保卡片顶部可见 — 若在上方视口外则滚过去
            var _r60Rect = tr.getBoundingClientRect();
            if (_r60Rect.top < 0) {
              tr.scrollIntoView({block: 'start', behavior: 'smooth'});
            }
          } else {
            window.__lastBvExpand = null;
            if (navigator.vibrate) navigator.vibrate(8);  // R58: 触觉反馈
          }
        }
      }
    };
    // R2003.3: 双击整行直接跳个股 (不依赖 code-link)
    tbody.ondblclick = function(ev){
      var tr = ev.target.closest('tr.bv-row');
      if (tr && tr.dataset.code && typeof gotoStock === 'function') {
        gotoStock(tr.dataset.code);
      }
    };
    // R2003.3: 命中规则 chip 点击 → 滚动到对应规则明细
    tbody.querySelectorAll('.chip').forEach(function(chip){
      chip.onclick = function(ev){
        ev.stopPropagation();
        // R48/R252: 规则 chip 点击 → 就近 popover 显示规则内容 (不再直接切过滤,
        //   过滤入口移入 popover). 第一性原理: tap 的默认语义是"看这条规则是啥",
        //   过滤是次级操作, 不该抢占 tap 主行为 (用户误点一次过滤整个列表只剩一规则)
        if (chip.classList.contains('bv-rule-chip')) {
          var rid = chip.getAttribute('data-rule-id');
          _showRulePopover(rid, chip);
          return;
        }
        // R23: 折叠 chip "+N" → 展开全部命中规则
        if (chip.classList.contains('bv-rule-fold')) {
          var cell = chip.closest('.bv-rules-cell');
          if (cell) {
            var code = chip.getAttribute('data-rule-fold');
            var pick = (_picks || []).find(function(p){ return p.code === code; });
            if (pick) {
              var allChips = (pick.matched_rules || []).map(function(rid){
                var r = _rulesById[rid];
                if (!r) return '<span class="chip chip-mute">' + rid + '</span>';
                var tone = {
                  '环境': 'chip-good', '仓位': 'chip-info', '选股': 'chip-accent',
                  '买入': 'chip-good', '止损': 'chip-warn', '止盈': 'chip-info', '风控': 'chip-bad'
                }[r.category] || 'chip-mute';
                // R256: 展开态 chip 带规则短名 — 折叠态裸编号是"引用符号"够速览, 但展开态
                //   承诺"全量规则详情", 编号不是信息. BV03·无异动不做 让用户不用 tap 就懂
                //   每条规则是啥 (popover 只留给想看原话/条件的深入层). 短名取 title 逗号前
                //   主句, 超长省略
                var _short = (r.title || '').split(/[,，:：]/)[0].trim();
                return '<span class="chip ' + tone + '" title="' + esc(r.title) + '" data-rule-id="' + rid + '">' + rid +
                  (_short ? '·<span class="bv-rule-name">' + esc(_short) + '</span>' : '') + '</span>';
              }).join(' ');
              var hitN = (pick.matched_rules || []).length;
              // R251: badgeCls 是 render 作用域局部变量, unfold onclick (独立回调) 闭包取不到
              //   → ReferenceError → 展开永远失败 (既有 bug, fold 从未真正工作). 重建 badge class:
              var ubCls = hitN >= 3 ? 'bv-hit-badge hot' : (hitN === 1 ? 'bv-hit-badge cold' : 'bv-hit-badge');
              var ub = '';
              if (pick.is_20cm) ub += '<span class="bv-board-badge bv-board-20" title="创业板/科创板 20cm 单日涨跌幅">20cm</span>';
              else if (pick.board === '10cm') ub += '<span class="bv-board-badge bv-board-10" title="主板 10cm 单日涨跌幅">10cm</span>';
              // R245: motto 只给 top-1 (决策锚点), unfold 路径与 render 一致 —
              //   以当前实际渲染的第一行 .bv-row 为准 (过滤/排序后就是 top)
              var _topRow = document.querySelector('#bv-pick-tbody tr.bv-row[data-code]');
              var _topCode2 = _topRow ? _topRow.getAttribute('data-code') : null;
              if (pick.top_rule && pick.top_rule.title && _topCode2 && pick.code === _topCode2) {
                ub += '<span class="bv-motto-badge" title="' + esc(pick.top_rule.quote || pick.top_rule.title) + '">' + esc(pick.top_rule.title) + '</span>';
              }
              // R257: fold (收起) absolute pinned 右下角 — 折叠态 fold sticky 右缘固定是
              //   "固定可预期" 的展开入口; R255 展开态 fold 跟随规则末尾在规则多时换行到
              //   第二/三行末尾, 每次展开位置飘忽 (肌肉记忆失效). 收起是"反向操作",
              //   必须可预期. 但 fold 前置第一行 (占文档流) 让规则多一行 → 行高 114→226.
              //   第一性: pinned 不占文档流 — fold absolute right:0 bottom:0 (与折叠态
              //   右缘一致), 规则行不受 fold 宽度影响自然换行 (2 行 ~114px), fold 永远
              //   右下角固定. 位置稳定 + 行高不过度膨胀, 两者兼得.
              var _foldBtn = '<span class="chip chip-mute bv-rule-fold" data-rule-fold="' + esc(code) + '" title="收起">−</span>';
              cell.innerHTML = ub + '<span class="' + ubCls + '" title="命中 ' + hitN + ' 条规则">' + hitN + '</span>' + allChips + _foldBtn;
              cell.classList.add('is-expanded');
              // 重新绑事件 (替换 innerHTML 后原 onclick 失效)
              cell.querySelectorAll('.chip').forEach(function(c){
                if (c.classList.contains('bv-rule-fold')) {
                  c.onclick = function(e){ e.stopPropagation(); cell.classList.remove('is-expanded'); renderPicks(); };
                } else {
                  c.onclick = function(e){
                    e.stopPropagation();
                    // R252: 展开态规则 chip → 就近 popover, 不再 scrollIntoView 瞬移到
                    //   1410px 深的规则明细 (丢失上下文). popover 展示规则内容+过滤入口
                    // R256: 用 data-rule-id 而非 textContent — 短名渲染后 textContent 是
                    //   "BV03·无异动不做", 查 _rulesById 必 miss → popover 永不弹 (回归).
                    //   编号是 chip 的身份属性, 文本是展示 (编号+短名), 身份查找走属性
                    var _rid = c.getAttribute('data-rule-id') || c.getAttribute('data-rid');
                    _showRulePopover(_rid || c.childNodes[0].textContent.trim(), c);
                  };
                }
              });
            }
          }
          return;
        }
        var rid = chip.textContent.trim();
        // R252: 底部规则明细 chip → 就地 popover (内容已在该区块, popover 只是聚焦,
        //   不再 scrollIntoView 滚动到区块中间 — 列表场景 popover 足够, 保留明细区块)
        _showRulePopover(rid, chip);
      };
    });
  }

  function _sortPicks(arr, sort) {
    var k = sort.key, d = sort.dir === 'asc' ? 1 : -1;
    return arr.slice().sort(function(a, b){
      var va = a[k], vb = b[k];
      if (va == null) return 1; if (vb == null) return -1;
      if (typeof va === 'string') return va.localeCompare(vb) * d;
      return (va - vb) * d;
    });
  }
  function _updateSortIndicators(){
    var ths = document.querySelectorAll('.bv-table th[data-sort]');
    ths.forEach(function(th){
      var k = th.getAttribute('data-sort');
      var arrow = th.querySelector('.bv-sort-arrow');
      if (k === _pickSort.key) {
        arrow.textContent = _pickSort.dir === 'asc' ? '▲' : '▼';
        th.classList.add('bv-sort-active');
      } else {
        arrow.textContent = '⇅';
        th.classList.remove('bv-sort-active');
      }
    });
  }

  // ── 规则明细面板 (按 category 折叠) ──
  function renderRules(){
    var host = $('#bv-rules-host');
    var count = $('#bv-rules-count');
    if (!host) return;
    if (!_rules.length){
      host.innerHTML = '<div class="dim center" style="padding:1rem">规则加载中…</div>';
      return;
    }
    // R90: 移动端默认折叠 — 7 大分类 2665px 全展开会掩埋分类汇总/回测;
    // 规则是参考材料, 默认收起, 点击 summary 再展开 (桌面保持 open)
    var _isMobileR90 = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
    // 按 category 分组
    var byCat = {};
    _rules.forEach(function(r){
      byCat[r.category] = byCat[r.category] || [];
      byCat[r.category].push(r);
    });
    var catOrder = ['环境', '仓位', '选股', '买入', '止损', '止盈', '风控'];
    var html = '';
    catOrder.forEach(function(cat){
      var list = byCat[cat] || [];
      if (!list.length) return;
      html += '<details class="bv-cat-details"' + (_isMobileR90 ? '' : ' open') + '>';
      html += '<summary class="bv-cat-summary"><span class="bv-cat-name">' + esc(cat) + '</span><span class="bv-cat-count">' + list.length + ' 条</span></summary>';
      html += '<div class="bv-cat-body">';
      list.sort(function(a,b){ return (a.priority||99) - (b.priority||99); });
      list.forEach(function(r){
        html += '<div class="bv-rule-item" id="bv-rule-' + esc(r.id) + '" data-rid="' + esc(r.id) + '">';
        html += '<div class="bv-rule-head">';
        html += '<span class="bv-rule-rid">' + esc(r.id) + '</span>';
        html += '<span class="bv-rule-title">' + esc(r.title) + '</span>';
        html += '<span class="bv-rule-weight">权重 ' + (r.score_weight || 0) + '</span>';
        html += '<span class="bv-rule-prio">P' + (r.priority || 99) + '</span>';
        html += '</div>';
        html += '<div class="bv-rule-desc">' + esc(r.description) + '</div>';
        if (r.quote) {
          html += '<div class="bv-rule-quote">';
          html += '<span class="bv-rule-quote-mark">"</span>' + esc(r.quote);
          if (r.timestamp) html += '<span class="bv-rule-quote-ts">@ ' + esc(r.timestamp) + '</span>';
          html += '</div>';
        }
        if (r.conditions && r.conditions.length) {
          html += '<div class="bv-rule-conds">';
          r.conditions.forEach(function(c){
            html += '<span class="bv-cond-chip">' + esc(c.field) + ' ' + esc(c.op) + ' ' + esc(c.value) + '</span>';
          });
          html += '</div>';
        }
        html += '</div>';
      });
      html += '</div></details>';
    });
    host.innerHTML = html;
    if (count) count.textContent = '(' + _rules.length + ' 条)';

    // 分类汇总 (右上方)
    var grid = $('#bv-category-grid');
    var summary = $('#bv-category-summary');
    if (grid) {
      var ghtml = '';
      catOrder.forEach(function(cat){
        var list = byCat[cat] || [];
        if (!list.length) return;
        var total = list.reduce(function(s, r){ return s + (r.score_weight || 0); }, 0);
        ghtml += '<div class="bv-cat-card bv-cat-tone-' + esc(cat) + '">';
        ghtml += '<div class="bv-cat-card-name">' + esc(cat) + '</div>';
        ghtml += '<div class="bv-cat-card-count">' + list.length + ' 条</div>';
        ghtml += '<div class="bv-cat-card-weight">权重 ' + total + '</div>';
        ghtml += '</div>';
      });
      grid.innerHTML = ghtml;
    }
    if (summary) {
      summary.textContent = catOrder.filter(function(c){ return (byCat[c] || []).length; }).join(' · ');
    }
  }

  // ── 回测骨架展示 ──
  function renderBacktest(bt){
    var body = $('#bv-backtest-body');
    var status = $('#bv-backtest-status');
    if (!body) return;
    if (!bt) {
      body.innerHTML = '<div class="dim center" style="padding:1rem">回测数据加载中…</div>';
      return;
    }
    if (status) status.textContent = '(' + (bt.status || '—') + ')';
    var html = '';

    // R2003.9: 兼容 ok / no_dailies / no_trades / no_data / no_matched / no_dates / timeout_loading_daily
    if (bt.status === 'ok') {
      // 真实回测 KPI
      html += '<div class="bv-bt-kpi-grid">';
      html += bvKpi('交易', bt.trades, '笔');
      html += bvKpi('胜率', (bt.win_rate_pct != null ? bt.win_rate_pct + '%' : '—'), '');
      html += bvKpi('平均收益', (bt.avg_return_pct != null ? bt.avg_return_pct + '%' : '—'), '');
      html += bvKpi('最大回撤', (bt.max_drawdown_pct != null ? bt.max_drawdown_pct + '%' : '—'), '');
      html += '</div>';
      html += '<div class="bv-bt-msg">' + esc(bt.message || '') + '</div>';
    } else if (bt.status === 'no_dailies') {
      // 降级: 真实样本分析 (无日线)
      html += '<div class="bv-bt-kpi-grid">';
      html += bvKpi('扫到样本', bt.samples_scanned || 0, '条');
      html += bvKpi('覆盖股票', bt.unique_codes || 0, '只');
      html += bvKpi('规则命中率', bt.rules_matched ? Object.keys(bt.rules_matched).length : 0, '条');
      html += bvKpi('口径', '5d-max-high', '');
      html += '</div>';
      html += '<div class="bv-bt-msg">' + esc(bt.message || '上游日线全断,扫到 N 条规则命中样本') + '</div>';
      // 规则命中分布
      if (bt.rules_matched && Object.keys(bt.rules_matched).length) {
        html += '<div class="bv-bt-section"><h4>📊 规则命中分布</h4><div class="bv-bt-tags">';
        Object.entries(bt.rules_matched).sort(function(a,b){return b[1]-a[1];}).forEach(function(e){
          html += '<span class="chip chip-info">' + esc(e[0]) + ' × ' + esc(e[1]) + '</span>';
        });
        html += '</div></div>';
      }
      // 行业分布
      if (bt.sector_dist && Object.keys(bt.sector_dist).length) {
        html += '<div class="bv-bt-section"><h4>🏭 行业分布</h4><div class="bv-bt-tags">';
        Object.entries(bt.sector_dist).sort(function(a,b){return b[1]-a[1];}).forEach(function(e){
          html += '<span class="chip chip-mute">' + esc(e[0]) + ' × ' + esc(e[1]) + '</span>';
        });
        html += '</div></div>';
      }
      // 样本
      if (bt.sample && bt.sample.length) {
        html += '<div class="bv-bt-section"><h4>📋 命中样本 (前 10)</h4><div class="bv-bt-sample">';
        bt.sample.forEach(function(s){
          html += '<div class="bv-bt-sample-row">';
          html += '<span class="bv-bt-code">' + esc(s.code) + '</span> ';
          html += '<span class="dim">' + esc(s.name || '') + '</span> ';
          html += '<span class="dim">' + esc(s.date) + '</span> ';
          html += '<span class="bv-bt-streak">首板/连板 ' + esc(s.streak) + '</span> ';
          html += '<span class="bv-bt-matched">[' + esc((s.matched || []).join('+')) + ']</span>';
          html += '</div>';
        });
        html += '</div></div>';
      }
    } else {
      // 其他状态
      html += '<div class="bv-bt-skeleton">';
      html += '<div class="bv-bt-msg">' + esc(bt.message || '骨架版, 后续 round 接入真实数据') + '</div>';
      html += '<div class="bv-bt-params">参数: ' + esc(JSON.stringify(bt.params_used || {})) + '</div>';
      html += '</div>';
    }

    body.innerHTML = html;
  }

  // ── KPI 单元 ──
  function bvKpi(label, value, unit){
    return '<div class="bv-bt-kpi"><div class="bv-bt-kpi-label">' + esc(label) + '</div>' +
      '<div class="bv-bt-kpi-value">' + esc(value) + ' <span class="bv-bt-kpi-unit">' + esc(unit) + '</span></div></div>';
  }

  // ── 数据加载 ──
  async function loadMeta(refresh){
    try {
      var qs = refresh ? '?refresh=1' : '';
      var data = await api('/api/bv/meta' + qs);
      _meta = data;
      if (_meta.phase && _meta.phase.phase) _phase = _meta.phase.phase;
      renderCreed();
      renderPhase();
      var m = $('#bv-meta');
      if (m) m.textContent = (_meta.name || '') + ' · ' + (_meta.version || '') + ' · ' + (_meta.rule_count || 0) + ' 条规则 · ' + (_meta.extracted_at || '');
    } catch(e) {
      console.warn('[bv] meta load failed:', e);
    }
  }

  async function loadRules(refresh){
    try {
      var qs = refresh ? '?refresh=1' : '';
      var data = await api('/api/bv/rules' + qs);
      _rules = data.rules || [];
      _rulesById = {};
      _rules.forEach(function(r){ _rulesById[r.id] = r; });
      renderRules();
    } catch(e) {
      console.warn('[bv] rules load failed:', e);
    }
  }

  async function loadLivePick(refresh, offset){
    // R2003.7: 去掉 _loading bail — view-enter 跨链时强制重新拉, 避免被旧 in-flight 阻塞
    _loading = true;
    var reqId = ++_reqId;
    // R13: 请求前切 loading 态 (非 refresh 才显示骨架屏, 避免刷新闪屏)
    if (!refresh && offset === undefined) { _pickLoading = true; _pickError = ''; renderPicks(); }
    try {
      // R39: 支持 offset 增量加载 — 滚动到底追加 picks
      var _off = offset || 0;
      var _topn = 15;
      var qs = '?refresh=' + (refresh ? '1' : '0') + '&top_n=' + _topn + '&offset=' + _off;
      var data = await api('/api/bv/live_pick' + qs);
      if (reqId !== _reqId) return; // 过期
      if (_off === 0) {
        var _newPicksArr = data.picks || [];
        // R57: 计算 picks 哈希 — 简单用 code 串拼接
        var _newHash = _newPicksArr.map(function(p){ return p.code + ':' + (p.score || 0); }).join('|');
        var _silentUpdate = (_newHash === _lastPicksHash && _lastPicksHash !== '' && refresh);
        _lastPicksHash = _newHash;
        _picks = _newPicksArr;
        // R41: 全量刷新, 隐藏 footer
        var _endRst = $('#bv-loadmore-end');
        if (_endRst) _endRst.hidden = true;
        _hasMore = true;
        // R57: 静默更新提示 — strip 文案补一句
        if (_silentUpdate) {
          _silentUpdateFlag = {at: Date.now(), shown: 0};
        }
      } else {
        // R39: 增量追加, 去重
        var _existing = {};
        for (var _i = 0; _i < _picks.length; _i++) _existing[_picks[_i].code] = 1;
        var _newPicks = data.picks || [];
        var _addedCount = 0;
        for (var _j = 0; _j < _newPicks.length; _j++) {
          if (!_existing[_newPicks[_j].code]) { _picks.push(_newPicks[_j]); _addedCount++; }
        }
        // R39: 如果新批 0 条, 说明没更多了
        if (_addedCount === 0) _hasMore = false;
      }
      _pickLoading = false;
      _pickError = '';
      if (data.phase) _phase = data.phase;
      // R27: 保存响应顶层 ts 作为快照时间, 卡片表头显示"快照于 HH:MM"
      _dataTs = data.ts || (Date.now() / 1000);
      renderPhase();
      renderPicks();
      // 顶部 badge 显示命中数
      var badge = document.getElementById('bv-badge');
      if (badge) {
        if (_picks.length) {
          badge.textContent = _picks.length;
          badge.hidden = false;
        } else {
          badge.hidden = true;
        }
      }
    } catch(e) {
      console.warn('[bv] live_pick load failed:', e);
      // R13: 失败态 — 若已有旧数据则保留, 否则显示重试
      _pickLoading = false;
      // R45: 增量加载失败时, footer 显示错误+重试按钮
      var _errMsg = e && e.message ? e.message.slice(0, 30) : '网络异常';
      if (offset && _picks.length > 15) {
        var _endErr = $('#bv-loadmore-end');
        if (_endErr && typeof renderEndFooter === 'function') {
          renderEndFooter('error', {msg: _errMsg});
        }
      }
      if (!_picks.length) {
        _pickError = e && e.message ? e.message.slice(0, 60) : '上游数据获取失败';
        renderPicks();
      }
    } finally {
      _loading = false;
    }
  }

  async function loadBacktest(refresh){
    try {
      var qs = refresh ? '?refresh=1' : '';
      var data = await api('/api/bv/backtest' + qs);
      renderBacktest(data);
    } catch(e) {
      console.warn('[bv] backtest load failed:', e);
    }
  }

  function refreshAll(){
    // R21: 刷新按钮加 is-loading (spinner) — 用户知道在跑
    //   至少显示 400ms, 否则 SW cache 命中闪得太快看不到
    var btn = $('#bv-refresh');
    if (btn) {
      btn.classList.add('is-loading');
      btn.disabled = true;
    }
    var _startTs = Date.now();
    var _MIN_SHOW = 400;
    var done = 0, total = 4;
    function _oneDone(){
      done++;
      if (done >= total && btn) {
        var _elapsed = Date.now() - _startTs;
        var _delay = _elapsed < _MIN_SHOW ? (_MIN_SHOW - _elapsed) : 0;
        setTimeout(function(){
          btn.classList.remove('is-loading');
          btn.disabled = false;
        }, _delay);
      }
    }
    var _wrap = function(promise){
      return Promise.resolve(promise).then(_oneDone, _oneDone);
    };
    _wrap(loadMeta(true));
    _wrap(loadRules(true));
    _wrap(loadLivePick(true));
    _wrap(loadBacktest(true));
  }

  // ── 事件绑定 ──
  function bindEvents(){
    var btn = $('#bv-refresh');
    if (btn) btn.addEventListener('click', function(){ refreshAll(); });

    // R2002.3: 表头点击排序
    var thead = document.querySelector('.bv-table thead');
    if (thead) {
      thead.addEventListener('click', function(ev){
        var th = ev.target.closest('th[data-sort]');
        if (!th) return;
        var k = th.getAttribute('data-sort');
        if (_pickSort.key === k) {
          _pickSort.dir = _pickSort.dir === 'asc' ? 'desc' : 'asc';
        } else {
          _pickSort.key = k;
          _pickSort.dir = ['code','name','sector'].includes(k) ? 'asc' : 'desc';
        }
        renderPicks();
      });
    }

    // 自动刷新 (复用 _autoRefresh 组件)
    if (typeof _autoRefresh !== 'undefined' && _autoRefresh.register) {
      _autoRefresh.register('bv-live', function(){ loadLivePick(false); }, {
        btn: $('#bv-refresh'),
        menu: null,
      });
    }

    // R33: 页面回到前台立即刷新 — 切走期间数据已陈旧, 回来时不延迟
    document.addEventListener('visibilitychange', function(){
      if (document.visibilityState === 'visible' && _dataTs > 0) {
        var _awayAge = Math.floor(Date.now() / 1000) - _dataTs;
        // 陈旧超 60s 立即刷新
        if (_awayAge > 60 && window.__bv && window.__bv.refresh) {
          window.__bv.refresh(true);
        }
      }
    });

    // R47: 网络从断到通 → 自动重拉一次 — 用户无需手动
    window.addEventListener('online', function(){
      if (window.__bv && window.__bv.refresh) {
        window.__bv.refresh(true);
      }
    });

    // R55: 键盘导航 — ArrowUp/Down 切换选中, Enter 展开, Esc 收起
    var _kbIdx = -1;
    document.addEventListener('keydown', function(ev){
      var tbody = $('#bv-pick-tbody');
      if (!tbody) return;
      var rows = tbody.querySelectorAll('tr.bv-row');
      if (!rows.length) return;
      // 输入框焦点时不抢
      var ae = document.activeElement;
      if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)) return;
      if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        _kbIdx = Math.min(rows.length - 1, _kbIdx + 1);
        rows.forEach(function(r){ r.classList.toggle('bv-kb-focus', r === rows[_kbIdx]); });
        rows[_kbIdx].scrollIntoView({block: 'nearest', behavior: 'smooth'});
      } else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        _kbIdx = Math.max(0, _kbIdx - 1);
        rows.forEach(function(r){ r.classList.toggle('bv-kb-focus', r === rows[_kbIdx]); });
        rows[_kbIdx].scrollIntoView({block: 'nearest', behavior: 'smooth'});
      } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {
        // R73: 键盘 ←/→ = 详情 prev/next, 复用 R70 同一 _lastSortedCodes 顺序 — 两种导航必须一致
        var _kbCur = _kbIdx >= 0 && _kbIdx < rows.length ? rows[_kbIdx].dataset.code : null;
        if (!_kbCur) return;
        var _kbNavIdx = _lastSortedCodes.indexOf(_kbCur);
        var _kbTarget = _kbNavIdx + (ev.key === 'ArrowRight' ? 1 : -1);
        if (_kbTarget >= 0 && _kbTarget < _lastSortedCodes.length) {
          ev.preventDefault();
          var _kbTgtCode = _lastSortedCodes[_kbTarget];
          // 收起当前, 展开目标
          tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
          tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
          var _kbTgtRow = tbody.querySelector('tr.bv-row[data-code="' + _kbTgtCode + '"]');
          var _kbTgtDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _kbTgtCode + '"]');
          if (_kbTgtRow) { _kbTgtRow.classList.add('bv-expanded'); _kbTgtRow.classList.add('bv-kb-focus'); }
          if (_kbTgtDetail) _kbTgtDetail.removeAttribute('hidden');
          // 更新键盘焦点索引到目标
          for (var _ki = 0; _ki < rows.length; _ki++) {
            rows[_ki].classList.toggle('bv-kb-focus', rows[_ki] === _kbTgtRow);
          }
          _kbIdx = _kbTarget >= 0 && _kbTarget < rows.length ? _kbTarget : _kbIdx;
          window.__lastBvExpand = {code: _kbTgtCode, ts: Date.now()};
          if (_kbTgtRow) {
            var _kbr = _kbTgtRow.getBoundingClientRect();
            if (_kbr.top < 0 || _kbr.bottom > (window.innerHeight || 500)) {
              _kbTgtRow.scrollIntoView({block: 'nearest', behavior: 'smooth'});
            }
          }
        }
      } else if (ev.key === 'Enter') {
        if (_kbIdx < 0 || _kbIdx >= rows.length) return;
        rows[_kbIdx].click();
      } else if (ev.key === 'Escape') {
        // R75: Esc 双职 — 多选态优先退出多选 (临时态必须快速可退), 否则收详情
        if (_multiMode) { _exitMultiMode(); return; }
        tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
        tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
      }
    });
    var sortBtn = $('#bv-sort-btn');
    var sortSheet = $('#bv-sort-sheet');
    // R158: 排序 sheet 挂在 .view-bv 内, .view 有 view-fade-in 动画 (fill-mode both 保留
    //   identity transform) → fixed 子元素被 transform 祖先框住, inset:0 锚到 .view 高度
    //   (685px) 而非视口 (844px), 确定按钮落在屏外 (bottom 1007>844) 且 body 锁滚不可达。
    //   fixed overlay 必须脱离 transform 祖先 → 移到 body (其他固定层 toast/sheet 都在 body 下)。
    if (sortSheet && sortSheet.parentElement !== document.body) {
      document.body.appendChild(sortSheet);
    }
    var sortLabel = $('#bv-sort-label');
    var SORT_LABEL = { score:'分数', change_pct:'涨幅', turnover_pct:'换手', streak:'连板', first_time:'封板时间', rule_count:'命中数' };
    // R84: 排序 label 是唯一陈述 — 启动即从 _pickSort 初始化, 不再依赖静态 eyebrow
    if (sortLabel) {
      var _sArrow = _pickSort.dir === 'asc' ? '↑' : '↓';
      sortLabel.textContent = _sArrow + ' ' + (SORT_LABEL[_pickSort.key] || _pickSort.key);
    }
    if (sortBtn && sortSheet) {
      sortBtn.addEventListener('click', function(){
        sortSheet.hidden = false;
        document.body.classList.add('bv-sort-open');
        // 同步当前选中态
        $$('.bv-sort-opt').forEach(function(o){
          o.classList.toggle('is-active', o.getAttribute('data-sort-key') === _pickSort.key);
        });
        $$('.bv-sort-dir-opt').forEach(function(o){
          o.classList.toggle('is-active', o.getAttribute('data-dir') === _pickSort.dir);
        });
      });
      sortSheet.addEventListener('click', function(ev){
        if (ev.target.hasAttribute('data-sort-close')) {
          sortSheet.hidden = true;
          document.body.classList.remove('bv-sort-open');
        }
      });
      $$('.bv-sort-opt').forEach(function(o){
        o.addEventListener('click', function(){
          $$('.bv-sort-opt').forEach(function(x){ x.classList.remove('is-active'); });
          o.classList.add('is-active');
          _pickSort.key = o.getAttribute('data-sort-key');
          // 应用排序 + 更新标签
          renderPicks();
          if (sortLabel) {
            var arrow = _pickSort.dir === 'asc' ? '↑' : '↓';
            sortLabel.textContent = arrow + ' ' + (SORT_LABEL[_pickSort.key] || _pickSort.key);
          }
        });
      });
      $$('.bv-sort-dir-opt').forEach(function(o){
        o.addEventListener('click', function(){
          $$('.bv-sort-dir-opt').forEach(function(x){ x.classList.remove('is-active'); });
          o.classList.add('is-active');
          _pickSort.dir = o.getAttribute('data-dir');
          renderPicks();
          if (sortLabel) {
            var arrow = _pickSort.dir === 'asc' ? '↑' : '↓';
            sortLabel.textContent = arrow + ' ' + (SORT_LABEL[_pickSort.key] || _pickSort.key);
          }
        });
      });
    }

    // view-enter 触发首次加载 + 启动倒计时 (注: app.js 用 document.dispatchEvent)
    document.addEventListener('view-enter', function(ev){
      if (ev && ev.detail && ev.detail.name === 'bv') {
        _startCountdown();
        if (!_meta) loadMeta(false);
        if (!_rules.length) loadRules(false);
        if (!_picks.length) loadLivePick(false);
        loadBacktest(false);
        _maybePinBanner();   // R3
        _installTopFab();    // R4
        _installInfiniteScroll();   // R39
      }
    });

    // view-leave 停掉倒计时 (兜底 — 上面已经在 enter 里调 start)
    window.addEventListener('view-leave', function(ev){
      if (ev && ev.detail && ev.detail.name === 'bv') {
        _stopCountdown();
        // R279 2026-08-22: 兜底释放 popover body scroll lock — 否则切走页面 body 仍
        //   position:fixed + top:-scrollY, 切回时页面被锁死无法滚动 (R278 ship-without-cleanup bug).
        //   _closeRulePopover() 内部已含 _bodyScrollLock 释放逻辑, 幂等 (无 popover 时直接 return)
        try { _closeRulePopover(); } catch (_) {}
      }
    });
  }

  // ── 初始化 ──
  bindEvents();
  _bindFilterBar();   // R6

  // R2003.4: 暴露给全局(野人 AI / 战法 links 调用)
  window.__bv = {
    get picks(){ return _picks; },
    get rules(){ return _rules; },
    get meta(){ return _meta; },
    refresh: loadLivePick,
    scrollToRule: function(rid){
      var el = document.querySelector('#bv-rule-' + rid);
      if (el) {
        // 先 ensure view 已显示
        if (typeof showView === 'function') showView('bv');
        el.scrollIntoView({behavior: 'smooth', block: 'center'});
        el.style.background = 'rgba(0,240,255,0.08)';
        setTimeout(function(){ el.style.background = ''; }, 1500);
      }
    },
  };

  // R2003.7: 兜底 — showView('bv') 异步加载本脚本时,view-enter 已在脚本就绪前 dispatch,
  //  此时下面的 listener 还没注册 → 首次拉数据丢失。
  // 补救: 监听 view-script-ready(本文件加载完成后),如果当前就是 bv view, 主动拉一次。
  document.addEventListener('view-script-ready', function(ev){
    if (ev && ev.detail && ev.detail.file === 'bv-frontend.js' && $root && !$root.hidden) {
      loadMeta(false);
      loadRules(false);
      loadLivePick(true);  // refresh=1 强制绕 L0 in-proc 30s 缓存,避免显示陈旧 picks
      loadBacktest(false);
    }
  });

  // 如果页面初始就是 bv view (deep link), 立即拉
  var view = $root;
  if (view && !view.hidden) {
    loadMeta(false);
    loadRules(false);
    loadLivePick(false);
    loadBacktest(false);
    _maybePinBanner();   // R175: deep-link 路径补 pin — view-enter 在脚本就绪前 dispatch 会漏,
                         //  否则 pinned banner 不出现 (R174 折叠也不生效), 首次深链只读一次
  }
})();