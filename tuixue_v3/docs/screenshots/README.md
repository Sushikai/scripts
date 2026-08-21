# screenshots/ · 历史截图归档

> 2026-08-19 由 `OPTIMIZATION_100_FIRST_PRINCIPLES.md` A10 整理
> 来自根目录的 373 张调试截图（77M），按时间戳命名（`01_intraday_default.png` ~ `15_intraday_today2.png`、`R1-` ~ `R5-dash-*.png`、`all_stocks_*.png` 等）

## 归档策略

- **不入 git**：根 `.gitignore` 已包含 `*.png`，本目录自动忽略
- **按需回看**：需要查看历史 UI 状态时直接打开本地文件
- **保留目录**：不放对象存储（成本低、查询频次低）

## 清理原则（后续）

- 单次任务产生的多版本（`_v2` / `_full2`）仅保留最终版
- 连续 30 天未访问的截图批量打包移入对象存储
- 单文件 > 1MB 的截图转 WebP（同级画质 1/3 体积）