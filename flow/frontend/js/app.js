/* flow app.js:8 view 入口(占位,真实功能在 view-*.js 里) */

(function () {
  'use strict';

  // === view-*.js 顺序加载,各自 flow.route() 注册 ===
  // 注:通过 <script> 顺序加载(view-dashboard → ... → view-settings)。
  // 此处不重复注册。

  // 兜底:如果某个 view 没加载成功,显示"加载失败"提示
  flow.route(/^.*$/, function (match, host) {
    if (host.innerHTML.indexOf('flow-skeleton') !== -1) {
      host.innerHTML = '<div class="flow-card">未知视图或加载失败:' + window.location.hash + '</div>';
    }
    return { name: 'fallback', leave: function () {} };
  });
})();