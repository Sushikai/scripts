/* 素材库视图 (R12 — 实装,扫多个输出根) */

(function () {
  'use strict';

  var KIND_ICON = { video: '🎬', audio: '🎵', image: '🖼️', other: '📄' };

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-library' });

    var head = flow.el('div', { class: 'view-head' });
    head.appendChild(flow.el('h1', { class: 'view-title', text: '🎬 素材库' }));
    var headBack = flow.el('a', { href: '#dashboard', class: 'back-link', text: '← Dashboard' });
    head.appendChild(headBack);
    root.appendChild(head);

    root.appendChild(flow.el('p', { class: 'view-sub', text: '扫 ~/ai_video_project / ~/ai_video_upload / ~/tiktok_automation 多根素材,按 mtime 倒序。' }));

    // 来源 filter
    var filterBar = flow.el('div', { class: 'filter-bar' });
    filterBar.appendChild(flow.el('span', { class: 'muted', text: '来源:' }));
    var srcSel = flow.el('select', { class: 'form-input', 'data-source-sel': '' });
    srcSel.innerHTML = '<option value="">全部</option>';
    filterBar.appendChild(srcSel);
    filterBar.appendChild(flow.el('span', { class: 'muted', text: '类型:' }));
    var kindSel = flow.el('select', { class: 'form-input', 'data-kind-sel': '' });
    kindSel.innerHTML = '<option value="">全部</option><option value="video">🎬 视频</option><option value="audio">🎵 音频</option><option value="image">🖼️ 图片</option>';
    filterBar.appendChild(kindSel);
    var countLabel = flow.el('span', { class: 'muted', 'data-count-label': '', text: '' });
    filterBar.appendChild(countLabel);
    root.appendChild(filterBar);

    var grid = flow.el('div', { class: 'library-grid', 'data-library-grid': '' });
    grid.innerHTML = '<p class="muted">加载中…</p>';
    root.appendChild(grid);

    host.appendChild(root);

    var cached = null;
    function load() {
      var src = srcSel.value;
      var kind = kindSel.value;
      var qs = '?limit=200' + (src ? '&source=' + src : '') + (kind ? '&kind=' + kind : '');
      flow.api('GET', '/api/assets' + qs).then(function (res) {
        if (!res.ok) {
          grid.innerHTML = '<p class="muted">素材库读取失败</p>';
          return;
        }
        cached = res.data;
        render();
      });
    }
    function render() {
      if (!cached) return;
      // 填 source 下拉
      if (srcSel.options.length <= 1) {
        Object.keys(cached.by_source).forEach(function (s) {
          var o = document.createElement('option');
          o.value = s; o.textContent = s + ' (' + cached.by_source[s] + ')';
          srcSel.appendChild(o);
        });
      }
      var items = cached.items || [];
      countLabel.textContent = items.length + ' 项 · 根 ' + cached.roots.filter(function (r) { return r.exists; }).length + '/' + cached.roots.length + ' 存在';
      grid.innerHTML = '';
      if (!items.length) {
        grid.innerHTML = '<p class="muted">无匹配素材</p>';
        return;
      }
      items.forEach(function (a) {
        var card = flow.el('div', { class: 'asset-card' });
        card.appendChild(flow.el('div', { class: 'asset-thumb', text: KIND_ICON[a.kind] || '📄' }));
        card.appendChild(flow.el('div', { class: 'asset-title', text: a.title || a.name }));
        var meta = flow.el('div', { class: 'asset-meta muted' });
        meta.innerHTML = '<span class="chip">' + flow.escapeHtml(a.source) + '</span> '
          + '<span class="mono">' + flow.escapeHtml(a.size_human) + '</span> · '
          + flow.fmtTime(a.mtime);
        card.appendChild(meta);
        var pathEl = flow.el('div', { class: 'asset-path mono muted' });
        pathEl.textContent = a.path;
        pathEl.title = a.path;
        card.appendChild(pathEl);
        grid.appendChild(card);
      });
    }
    srcSel.addEventListener('change', load);
    kindSel.addEventListener('change', load);
    load();

    return { name: 'library', leave: function () {}, enter: function () { load(); } };
  }

  flow.route(/^library$/, view);
})();