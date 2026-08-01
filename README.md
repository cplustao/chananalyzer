# ChanAnalyzer v2

ChanAnalyzer v2 是面向个人研究、可部署到单台服务器的 A 股缠论研究工作台。当前主工程已经完成 v2 收敛：React 前端、FastAPI API、独立 Worker、SQLAlchemy/Alembic 数据库，以及不访问数据库和网络的纯缠论核心。

## 当前架构

```text
React + TypeScript + Vite
          |
      /api/v1 + SSE
          |
FastAPI -> Services -> Repositories -> SQLAlchemy -> SQLite / PostgreSQL
             |              |
             |              +-> Tushare / AI Providers
             +-> Worker -> 纯缠论核心
```

- `backend/app/api`：鉴权、参数校验、序列化和健康检查。
- `backend/app/services`：市场雷达、个股、扫描、涨停、新股、行情同步和任务编排。
- `backend/app/repositories`：数据库访问边界。
- `backend/app/providers`：Tushare 与 OpenAI 兼容 AI 服务。
- `backend/app/chan_core`：内存算法接口；上游算法固定在 `vendor`，禁止网络和数据库访问。
- `backend/app/worker`：持久化后台任务、重试、取消和恢复。
- `frontend`：统一深色研究终端、左侧导航、任务中心和帮助中心。
- `migrations`：Alembic 数据库结构迁移。

## 功能

- 市场雷达：市场结论、证据/反证、仓位曲线和历史版本。
- 个股研究：自选研究队列、标签筛选、K 线、笔/线段/中枢/买卖点/背离和分析历史。
- 市场筛选：热门榜单与按条件组合的智能筛选。
- 缠论扫描：买点、卖点和持久化扫描结果。
- 涨停与新股分析：日期区间、批量任务、评分和版本化 AI 报告。
- 自选股：列表内双击编辑标签与研究备注，并与个股研究队列联动。
- 设置与帮助：中文配置、Secret 脱敏、数据源测试和页面级帮助。

## 本地启动

要求 Python 3.11+、Node.js 20+。

```powershell
py -3.12 -m pip install -e ".[dev]"
cd frontend
npm install
npm run build
cd ..
py -3.12 -m backend.app.start_local
```

访问 `http://127.0.0.1:8011`。本地默认 `AUTH_MODE=local`，只接受本机访问且无需登录。API 与 Worker 是两个独立进程；`start_local` 仅负责同时管理它们。

也可以分别启动：

```powershell
py -3.12 -m backend.app.main
py -3.12 -m backend.app.worker.main
```

## 配置

复制 `.env.example` 为 `.env`。常用项：

```dotenv
AUTH_MODE=local
DATABASE_URL=sqlite:///D:/path/to/chananalyzer/data/chan_v2.db
API_HOST=127.0.0.1
API_PORT=8011
APP_SECRET_KEY=replace-with-a-stable-random-secret
```

Tushare、DeepSeek 和 SiliconFlow 密钥可在设置页写入；接口只返回是否已配置，不返回明文。当前行情刷新依赖有效的 Tushare Token。

## 数据库与迁移

新安装先执行：

```powershell
py -3.12 -m alembic upgrade head
```

## 开发与验证

```powershell
py -3.12 -m pytest
py -3.12 -m ruff check backend migrations tests
py -3.12 -m mypy --ignore-missing-imports --follow-imports=silent backend/app/core/errors.py backend/app/core/exception_handlers.py backend/app/services/backups.py backend/app/services/structured_ai.py backend/app/services/scan_changes.py backend/app/services/data_health.py backend/app/db/migrations.py backend/app/start_local.py
py -3.12 -m backend.app.export_openapi
cd frontend
npm run generate:api
npm test
npm run lint
npm run build
npm run test:e2e
```

依赖以 `pyproject.toml` 为准；`requirements.txt` 用于服务器直接安装，`pylock.toml` 用于严格复现。本项目不会在未授权时运行 `npm audit`，因为该命令会把依赖元数据发送给 npm。

## 部署

单服务器模式由 Nginx 提供 HTTPS，并用 systemd、Supervisor 或宝塔分别守护 API 与 Worker。服务器使用 `AUTH_MODE=admin`、Secure HttpOnly Cookie、固定 `APP_SECRET_KEY` 和明确的 `ALLOWED_ORIGINS`。详见 `docs/DEPLOYMENT_V2.md`。

## 旧系统清理

v1 源码、虚拟环境、日志、旧数据库、8001 入口及一次性旧库导入器均已永久删除。主工程只保留 v2，历史结构升级统一由 Alembic 管理。

## 风险提示

所有结论仅用于研究与软件验证，不构成投资建议。缠论结构、评分和 AI 报告都应结合数据质量、市场环境和个人风险承受能力独立判断。