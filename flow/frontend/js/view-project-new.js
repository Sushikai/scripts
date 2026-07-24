/* 新建项目视图:选工具 → 填参数 → 启动 */

(function () {
  'use strict';

  function view(match, host) {
    host.innerHTML = '';
    var root = flow.el('div', { class: 'view-new' });

    root.appendChild(flow.el('h1', { class: 'view-title', text: '✨ 新建项目' }));
    root.appendChild(flow.el('p', { class: 'view-sub', text: '选择工具 → 填参数 → 启动项目。' }));

    var toolSection = flow.el('div', { class: 'flow-card' });
    toolSection.appendChild(flow.el('h2', { class: 'card-title', text: '① 选择工具' }));
    var toolGrid = flow.el('div', { class: 'tools-grid', 'data-tool-grid': '' });
    toolSection.appendChild(toolGrid);
    root.appendChild(toolSection);

    var formSection = flow.el('div', { class: 'flow-card hidden', 'data-form-section': '' });
    formSection.appendChild(flow.el('h2', { class: 'card-title', text: '② 配置参数' }));
    var formBody = flow.el('div', { class: 'form-body', 'data-form-body': '' });
    formSection.appendChild(formBody);
    root.appendChild(formSection);

    host.appendChild(root);

    loadTools();

    var state = { tool: null, params: {}, dryRun: true };

    return {
      name: 'new',
      enter: function () {
        var q = _query();
        // 等工具卡片渲染完(loadTools async)再操作
        var waitAndApply = function () {
          if (q.tool) {
            var node = document.querySelector('[data-tool-card="' + q.tool + '"]');
            if (node) {
              node.click();
            } else {
              setTimeout(waitAndApply, 100);
              return;
            }
          }
          // fengge_url 来源链接预填 — 等 selectTool 渲染完 source_url input
          if (q.source_url) {
            var tries = 0;
            var tryPrefill = function () {
              var input = document.querySelector('[data-input-source-url]');
              if (input) {
                input.value = q.source_url;
                var nameInput = document.querySelector('[data-input-name]');
                if (nameInput && (!nameInput.value || nameInput.value.indexOf('峰哥粘贴链接_') === 0)) {
                  var slug = (q.source_url.split('?')[0].split('/').pop() || 'paste').slice(0, 30);
                  nameInput.value = 'fengge_url_' + slug + '_' + _stamp();
                }
              } else if (tries++ < 20) {
                setTimeout(tryPrefill, 100);
              }
            };
            tryPrefill();
          }
        };
        waitAndApply();
      },
      leave: function () {},
    };
  }

  function _query() {
    var h = window.location.hash;
    var q = h.split('?')[1] || '';
    var out = {};
    q.split('&').forEach(function (kv) {
      if (!kv) return;
      var p = kv.split('=');
      out[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || '');
    });
    return out;
  }

  function loadTools() {
    flow.api('GET', '/api/tools').then(function (res) {
      var grid = document.querySelector('[data-tool-grid]');
      if (!grid || !res.ok) return;
      grid.innerHTML = '';
      (res.data.tools || []).forEach(function (t) {
        var card = flow.el('div', { class: 'tool-card', 'data-tool-card': t.tool_id });
        card.appendChild(flow.el('div', { class: 'tool-icon', text: _icon(t.tool_id) }));
        card.appendChild(flow.el('div', { class: 'tool-name', text: t.name }));
        card.appendChild(flow.el('div', { class: 'tool-desc', text: t.description || '' }));
        card.appendChild(flow.el('div', { class: 'tool-meta' }, [
          flow.el('span', { class: 'chip', text: (t.steps || []).length + ' 步' }),
        ]));
        card.addEventListener('click', function () { selectTool(t); });
        grid.appendChild(card);
      });
    });
  }

  function selectTool(t) {
    var cards = document.querySelectorAll('[data-tool-card]');
    cards.forEach(function (c) { c.classList.remove('selected'); });
    var me = document.querySelector('[data-tool-card="' + t.tool_id + '"]');
    if (me) me.classList.add('selected');

    var section = document.querySelector('[data-form-section]');
    var body = document.querySelector('[data-form-body]');
    if (!section || !body) return;
    section.classList.remove('hidden');

    var state = { tool: t, params: {}, dryRun: true };

    body.innerHTML = '';

    var nameRow = flow.el('div', { class: 'form-row' });
    nameRow.appendChild(flow.el('label', { class: 'form-label', text: '项目名称' }));
    var nameInput = flow.el('input', { class: 'form-input', type: 'text', value: t.name + '_' + _stamp(), 'data-input-name': '' });
    nameRow.appendChild(nameInput);
    body.appendChild(nameRow);

    // fengge_url 专属:粘贴链接输入框
    var sourceUrlInput = null;
    if (t.tool_id === 'fengge_url') {
      var urlRow = flow.el('div', { class: 'form-row' });
      urlRow.appendChild(flow.el('label', { class: 'form-label', text: '视频链接' }));
      sourceUrlInput = flow.el('input', {
        class: 'form-input', type: 'text',
        placeholder: 'https://www.bilibili.com/video/BV1xxx',
        'data-input-source-url': '',
      });
      urlRow.appendChild(sourceUrlInput);
      urlRow.appendChild(flow.el('div', { class: 'form-hint muted', text: '支持 B站 / 抖音 / YouTube,会自动 yt-dlp 下载。' }));
      body.appendChild(urlRow);
    }

    var dryRow = flow.el('div', { class: 'form-row form-row-check' });
    var dryInput = flow.el('input', { type: 'checkbox', checked: 'checked', 'data-input-dry': '' });
    dryRow.appendChild(dryInput);
    dryRow.appendChild(flow.el('label', { class: 'form-label-inline', text: 'Dry-run 模式(不调外部 API/ffmpeg,只跑 mock)' }));
    body.appendChild(dryRow);

    var stepsPreview = flow.el('div', { class: 'form-row' });
    stepsPreview.appendChild(flow.el('label', { class: 'form-label', text: '将依次执行步骤' }));
    var stepsLine = flow.el('div', { class: 'steps-pipeline' });
    (t.steps || []).forEach(function (s, i) {
      stepsLine.appendChild(flow.el('span', { class: 'step-chip', text: (i + 1) + '. ' + s }));
    });
    stepsPreview.appendChild(stepsLine);
    body.appendChild(stepsPreview);

    var submit = flow.el('button', {
      class: 'btn-primary btn-large',
      text: '🚀 启动项目',
      on: { click: function () { submitProject(t, state, nameInput, dryInput, sourceUrlInput); } },
    });
    body.appendChild(submit);
  }

  function submitProject(tool, state, nameInput, dryInput, sourceUrlInput) {
    var name = (nameInput.value || '').trim();
    if (!name) { flow.toast('请输入项目名称', 'error'); return; }
    var dryRun = dryInput.checked;
    var params = { dry_run: dryRun };
    if (sourceUrlInput) {
      var url = (sourceUrlInput.value || '').trim();
      if (!/^https?:\/\//i.test(url)) {
        flow.toast('请输入 http(s):// 视频链接', 'error');
        return;
      }
      params.source_url = url;
    }
    flow.api('POST', '/api/projects', {
      tool_id: tool.tool_id,
      name: name,
      params: params,
    }).then(function (res) {
      if (!res.ok) { flow.toast('创建失败: ' + (res.error && res.error.message), 'error'); return; }
      var project = res.data;
      flow.toast('项目已创建,正在启动…', 'ok');
      // 串行提交所有 step,带 params
      submitStepChain(project, tool, 0, params);
    });
  }

  function submitStepChain(project, tool, idx, baseParams) {
    if (idx >= tool.steps.length) {
      flow.toast('全部步骤已提交', 'ok');
      flow.navigate('projects/' + encodeURIComponent(project.id));
      return;
    }
    var step = tool.steps[idx];
    // 第一个 step 带 source_url / dry_run,后续 step 复用 _results 不需要
    var stepParams = idx === 0 ? baseParams : {};
    flow.api('POST', '/api/jobs', {
      tool_id: tool.tool_id,
      project_id: project.id,
      step: step,
      params: stepParams,
    }).then(function (res) {
      if (!res.ok) { flow.toast(step + ' 提交失败', 'error'); return; }
      // 串行等待 done 再下一步
      waitForJob(res.data.job_id, function () {
        submitStepChain(project, tool, idx + 1, baseParams);
      });
    });
  }

  function waitForJob(jobId, done) {
    var tries = 0;
    function tick() {
      flow.api('GET', '/api/job/' + jobId).then(function (res) {
        if (!res.ok) { setTimeout(tick, 1500); return; }
        var s = res.data.status;
        if (s === 'done' || s === 'failed' || s === 'cancelled') {
          done(s);
          return;
        }
        if (tries++ > 120) { done('timeout'); return; }
        setTimeout(tick, 800);
      });
    }
    tick();
  }

  function _stamp() {
    var d = new Date();
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getMonth() + 1 + '' + pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes());
  }

  function _icon(toolId) {
    return {
      info_gap: '🎬',
      fengge: '📺',
      tiktok_story: '🎵',
      material_collector: '🎞️',
    }[toolId] || '⚙️';
  }

  flow.route(/^new$/, view);
})();