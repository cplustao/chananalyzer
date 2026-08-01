# ChanAnalyzer v2 最终架构

## 运行边界

- `api` 只处理鉴权、参数、序列化和 HTTP/SSE 协议。
- `services` 编排研究、刷新、扫描、评分和报告流程。
- `repositories` 是业务数据库访问入口。
- `providers` 隔离 Tushare 与 AI 服务，统一超时、重试和 DTO。
- `worker` 独立领取持久化任务，Web API 不执行长任务。
- `chan_core` 只接收内存 K 线并返回结构化结果，不访问数据库、网络或环境变量。
- `frontend` 使用 React、TanStack Query 和 ECharts；服务端状态不复制到全局客户端仓库。

依赖方向：

```text
api -> services -> repositories -> db models
                  -> providers
                  -> chan_core
worker -> services
```

`chan_core/vendor` 是从原项目保留的算法实现，已改为包内导入；外围配置、数据加载和序列化由 v2 适配层负责。

## 数据与任务

默认 `sqlite:///data/chan_v2.db`，连接启用外键、WAL、NORMAL synchronous 和 30 秒 busy timeout。模型与 Alembic 同时兼容 PostgreSQL。

- `instruments.id` 是市场数据稳定主键。
- `bars` 唯一键为 `(instrument_id, timeframe, adjustment, bar_time)`。
- 自选股使用 watchlist/item/tag 关系表。
- 分析使用 run/report/metric，并保留算法、提示词、配置与输入快照。
- 雷达按 `(trade_date, algorithm_version)` 保存。
- job/item/event 持久化后台进度、重试、取消和恢复。

刷新、扫描和 AI 操作返回 `202 + job_id`。相同任务有去重键；前端用查询接口与 SSE 接收状态。

## 安全边界

本地模式只允许回环来源。服务器模式强制管理员登录，使用 Argon2 密码哈希和 HttpOnly/Secure/SameSite Cookie。Secret 由稳定的 `APP_SECRET_KEY` 派生密钥加密保存，API 只返回 configured/source 状态。
