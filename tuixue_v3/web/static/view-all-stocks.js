// view-all-stocks.js · 2026-07-15
//
// 此文件已迁入 app.js (app.js:7706+ IIFE),app.js 先加载,本文件的 IIFE 在
// _allStocksInit 守卫处直接 early-return,所有逻辑都在 app.js 里。
//
// 保留此文件仅为兼容旧 SW cache / 老 URL 引用,所有代码已 no-op。
if (window._allStocksInit) {
  // app.js 已注册 — 什么都不做
} else {
  window._allStocksInit = true;
  // 极端 fallback:app.js 还没跑完就 import 此文件 — 暴露 noop 防止 undefined error
  window.initAllStocks = window.initAllStocks || function() { /* no-op */ };
}