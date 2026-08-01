# ChanAnalyzer v2 退役验收记录

## 结论

v1 已从主工作区退役。主工程只保留 React/Vite、FastAPI、独立 Worker、v2 数据库和纯缠论核心；8001 不再是可启动入口。

## 已完成门禁

- API 与 Worker 不导入 `web.*`、旧 `ChanAnalyzer` 业务封装或旧脚本。
- 行情刷新直接写入 v2 instruments/bars/calendar/ingestion_runs。
- 市场雷达、热门筛选、涨停、新股、扫描和个股分析只读取 v2 Repository/Provider。
- 纯缠论核心位于 `backend/app/chan_core`，黄金样本覆盖 K 线、笔、线段、中枢、买卖点及评分一致性。
- K 线 5,538,212 条，主数据、涨停、新股、雷达、报告、自选股均已迁移并完成计数审计。
- 根目录不存在 `chan.db`，运行时配置不存在旧数据库路径。
- React 页面已统一左侧导航、研究队列、中文任务/设置、日期区间、缠论图层和帮助入口。

## 清理范围

已移出主工作区：

- `ChanAnalyzer/`、`web/`、`scripts/` 和旧 GUI/调试目录。
- `main.py`、旧扫描脚本和 8001 启动入口。
- v1 虚拟环境、运行缓存、旧日志和根目录旧数据库。
- 新工程中仅用于过渡的 LegacyService、LegacyMarketProvider、LegacyChanEngine 及旧系统测试。

上游算法中仍需的纯实现保存在 `backend/app/chan_core/vendor`，不属于旧运行系统。

## 最终销毁

经用户明确批准，仓库外 v1 归档 `D:\path\to\chananalyzer-v1-archive-20260729` 已于 2026-07-29 永久删除，共清理 28,666 个文件、约 2.32GB。一次性旧库导入器及其测试同时从 v2 工程移除。

后续发布回滚只基于 v2 代码版本、Alembic 迁移和 v2 数据库备份，不再恢复 8001 或旧运行栈。

## 已知外部配置项

当前本机保存的 Tushare Token 已被服务端判定无效。代码管道与假数据测试均已通过，但真实行情刷新需用户在设置页更新有效 Token。该问题不影响现有 553 万条数据、离线缠论分析与 v2 架构验收。