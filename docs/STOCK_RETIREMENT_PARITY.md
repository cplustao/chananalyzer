# Stock → ChanAnalyzer 功能对账与退役门禁

## 结论

`D:\CodeHub\Stock` 的行情、市场研究、扫描、个股研究、自选、任务、调度、密钥配置和备份能力，已经迁移到 ChanAnalyzer，或由新的实现替代。

Stock 的以下五组用户能力没有进入 ChanAnalyzer，并于 2026-08-28 经产品裁决正式下线：

1. 组合持仓、现金、成本、盈亏与集中度管理。
2. 手工交易流水、CSV/XLSX 导入、导出与持仓重算。
3. 交易复盘、计划关联和纪律记录。
4. 带风险预算的交易计划、人工接受/暂缓/驳回和券商条件单草稿。
5. 独立对话式 AI 助手、会话历史和组合问答。

因此，Stock 已满足“技术上可停用”和“产品能力可退役”。这些能力不再作为删除门禁，也不要求继续迁移到 ChanAnalyzer。

## 对账范围与基线

- 对账日期：2026-08-28。
- 来源项目：`D:\CodeHub\Stock`，Git `main` 提交 `c61eba3`，版本标签 `v1.1.2`。
- 目标项目：`D:\CodeHub\chananalyzer`，Git `main` 提交 `c5e1589`。
- 来源产品基线：Stock 的 `README.md`、产品/流程文档、前后端路由、模型和测试。
- 目标验收基线：本仓库 `README.md`、`frontend/src/App.tsx`、API 路由、数据库模型和自动测试。

本对账只判断功能去向，不要求保留 Stock 的 Git 历史、旧数据库、用户数据或 Windows 凭据。用户已于 2026-08-28 明确表示这四类内容不需要作为删除门禁。本文档本身不执行目录删除。

## 状态定义

- `MIGRATED`：目标项目存在等价能力，并有代码或测试证据。
- `REPLACED`：用户目标被新流程覆盖，但页面、交互、算法或数据模型不同。
- `RETIRED`：经产品确认不再保留；删除 Stock 不需要补实现。
- `GAP`：Stock 存在而目标项目没有等价能力；必须实现或明确改为 `RETIRED` 后才能删除。

本表最初将重大缺失能力记为 `GAP`；产品接受专业退役建议后，相关项已统一裁决为 `RETIRED`。

## 一级页面对账

| ID | Stock 页面或入口 | ChanAnalyzer 去向 | 状态 | 差异与证据 |
|---|---|---|---|---|
| PAGE-001 | 决策中心 | `TodayPage`（今日研究） | REPLACED | 已覆盖数据门禁、市场事实/反证、候选变化、自选复盘和研究待办；真实组合资产与持仓风险未迁移。 |
| PAGE-002 | 市场雷达 | `RadarPage` | MIGRATED | 支持市场结论、证据/反证、风险仓位曲线和历史版本。 |
| PAGE-003 | 机会中心 | `EventAnalysisPage`、`ScansPage`、今日研究 | REPLACED | 涨停、新股、热门榜单、智能筛选、缠论扫描和候选变化均有入口；候选交易计划链路未迁移。 |
| PAGE-004 | 个股研究 | `StocksPage` | MIGRATED | 覆盖搜索、日 K、缠论、实时行情/资金流、结构化 AI 报告和历史；规则交易计划与条件单见 PLAN-001。 |
| PAGE-005 | 自选中心 | `WatchlistPage` | MIGRATED | 支持加入理由、逻辑、确认、失效、下一步动作、复盘日期和研究状态。 |
| PAGE-006 | 组合持仓 | 无 | RETIRED | 当前路由和模型没有 Portfolio、Position、Transaction；正式放弃维护第二套资产账本。 |
| PAGE-007 | 交易复盘 | 无 | RETIRED | 当前没有 Journal 页面、模型或交易与计划关联接口；正式下线成交复盘产品线。 |
| PAGE-008 | AI 助手 | 个股/涨停/新股结构化分析 | REPLACED | 专项 AI 研究和持久化报告已覆盖；独立会话、连续追问、组合问答未覆盖。 |
| PAGE-009 | 数据设置 | `SettingsPage` | MIGRATED | 覆盖 Secret、连接测试、调度、数据健康、任务和备份。 |
| PAGE-010 | 首次配置向导 | 设置页和健康门禁 | REPLACED | 当前没有强制向导，配置通过设置页逐项完成。 |

当前路由证据见 `frontend/src/App.tsx`。Stock 原页面基线包括决策中心、市场雷达、机会中心、个股研究、自选、组合持仓、交易复盘、AI 助手、设置和首次配置。

## 研究与决策能力对账

| ID | Stock 能力 | 状态 | ChanAnalyzer 去向与差异 |
|---|---|---|---|
| RESEARCH-001 | 最新完整交易日与数据新鲜度门禁 | MIGRATED | 每日数据准备、`DataHealthSnapshot`、数据可靠性样本和今日研究门禁阻止过期数据产生确定性结论。 |
| RESEARCH-002 | 市场状态、证据/反证和建议总仓位 | MIGRATED | 市场雷达保存版本化快照，并展示证据、反证和风险仓位曲线。 |
| RESEARCH-003 | 行业强度与重点行业 | MIGRATED | 市场雷达和行业数据支持强弱、涨停分布及重点方向展示。 |
| RESEARCH-004 | 涨停、连板生态和风险提示 | MIGRATED | `limit_up_events`、雷达快照、涨停分析页和批量 AI 分析覆盖。 |
| RESEARCH-005 | 收盘后主动发现候选 | REPLACED | 当前以热门榜单、智能筛选、缠论扫描和扫描变化代替 Stock 的 `candidate_signals` 工作流；不生成 Stock 式可执行交易计划。 |
| RESEARCH-006 | 个股 K 线和基础行情 | MIGRATED | 当前数据库使用规范化 `bars`，支持复权、来源、质量状态及日期区间。 |
| RESEARCH-007 | 缠论、分型、笔、线段、中枢、买卖点和背离 | MIGRATED | 由 `backend/app/chan_core`、`DatabaseChanEngine` 和 `StockChart` 提供，并有黄金样本与前端生命周期测试。 |
| RESEARCH-008 | 盘中行情和资金流进入个股 AI 上下文 | MIGRATED | `StockMarketContextService` 记录实时行情、交易状态、资金流来源和缺失原因。 |
| RESEARCH-009 | 五层评分与显式股票状态机 | REPLACED | 当前使用缠论评分、市场雷达、筛选得分和研究计划状态；不保留 Stock 的 `score_snapshots` / `state_transitions` 数据模型。 |
| RESEARCH-010 | AI 主分析、独立反证与结构化校验 | REPLACED | 当前使用技术研究员、独立风控和综合决策的结构化报告链路；任一前置失败不会生成伪决策。 |
| RESEARCH-011 | 分析历史、输入快照和版本追溯 | MIGRATED | `analysis_runs`、`analysis_reports` 和研究历史接口保存输入摘要、算法/提示词版本、校验状态及结果。 |

## 自选、交易和复盘能力对账

| ID | Stock 能力 | 状态 | ChanAnalyzer 去向与差异 |
|---|---|---|---|
| WATCH-001 | 自选分组与证券加入/移除 | MIGRATED | 当前提供默认自选列表和增删改接口。 |
| WATCH-002 | 加入理由、逻辑、确认、失效、目标和标签 | MIGRATED | 当前自选研究计划保留主要研究字段；标签以自选研究队列筛选方式实现。 |
| WATCH-003 | 下一步动作、复盘日期和研究状态 | MIGRATED | 当前模型与页面均支持，并进入今日研究待办。 |
| PORT-001 | 组合、现金和总资产 | RETIRED | 正式放弃应用内组合账户和资产汇总，真实资产以券商为准。 |
| PORT-002 | 持仓数量、可卖数量、成本、盈亏和仓位 | RETIRED | 正式放弃第二套持仓账本及 T+1 数量重算。 |
| PORT-003 | 保护位、逻辑状态、趋势持有和风险待办 | RETIRED | 不再基于真实持仓生成风险动作；保留自选研究计划的失效条件。 |
| TRADE-001 | 手工录入买卖、分红、送转和费用 | RETIRED | 正式放弃应用内交易流水。 |
| TRADE-002 | CSV/XLSX 预览、去重、确认导入 | RETIRED | 正式放弃券商流水导入。 |
| TRADE-003 | 交易导出、现金与持仓重算 | RETIRED | 正式放弃组合账本导出和重算。 |
| JOURNAL-001 | 成交与计划关联 | RETIRED | 正式放弃成交与计划关联。 |
| JOURNAL-002 | 简版复盘、错误分类和纪律记录 | RETIRED | 正式放弃成交复盘；当前“自选复盘”仅保留研究状态回顾。 |
| JOURNAL-003 | 个人交易画像与策略表现 | RETIRED | Stock 将其列为长期或 P1 方向，当前版本没有形成完整可用闭环；本次退役不要求迁移未完成的未来能力。 |

## 交易计划与条件单对账

| ID | Stock 能力 | 状态 | ChanAnalyzer 去向与差异 |
|---|---|---|---|
| PLAN-001 | 触发区间、仓位、止损、目标和失效条件的交易计划 | RETIRED | 正式下线可执行 TradePlan；继续使用研究结论、买卖点和自选研究计划。 |
| PLAN-002 | 1% 单笔风险预算、5%—8% 初始仓位和 25% 单股上限 | RETIRED | 正式下线基于真实组合资产的单笔仓位计算；保留市场层面风险仓位区间。 |
| PLAN-003 | 人工接受、暂缓、驳回及留痕 | RETIRED | 正式下线交易计划决策状态机。 |
| PLAN-004 | 券商 App 条件单草稿和用户状态 | RETIRED | 正式下线条件单草稿，系统继续坚持不控制券商订单。 |
| PLAN-005 | 不连接券商、不自动下单 | MIGRATED | 两个项目都坚持只提供研究辅助，不控制券商订单。 |

## AI 能力对账

| ID | Stock 能力 | 状态 | ChanAnalyzer 去向与差异 |
|---|---|---|---|
| AI-001 | 个股专项分析和反证复核 | MIGRATED | 个股页可创建持久化后台分析，展示结构化报告和历史。 |
| AI-002 | 独立对话式助手、会话历史和连续追问 | RETIRED | 正式下线通用聊天入口，保留可追溯的专项结构化 AI 报告。 |
| AI-003 | 当前组合分析与问答 | RETIRED | 随组合账本一并下线，不再构造可能失真的组合上下文。 |
| AI-004 | 涨停与新股专项 AI 分析 | MIGRATED | 目标项目新增了 Stock 没有独立拆出的专项页面、批量任务和版本化报告。 |
| AI-005 | 密钥缺失或数据过期时失败关闭 | MIGRATED | 当前 Secret 状态、连接测试、数据门禁和结构化报告校验均明确返回不可用原因。 |

## 数据、任务与运维对账

| ID | Stock 能力 | 状态 | ChanAnalyzer 去向与差异 |
|---|---|---|---|
| OPS-001 | Tushare → BaoStock → AKShare 日线降级 | MIGRATED | `FallbackMarketDataProvider` 保留三层降级，并记录来源、错误和研究序列约束。 |
| OPS-002 | 行业、主数据、交易日历和涨停数据 | MIGRATED | 当前使用独立 Repository/Provider 和规范化 v2 表。 |
| OPS-003 | 持久化任务、进度、重试和失败恢复 | MIGRATED | 独立 Worker、`jobs`、`job_items`、`job_events` 支持补跑、检查点和恢复。 |
| OPS-004 | 应用内 APScheduler | REPLACED | 当前由数据库 `automation_schedules` 和独立 Worker 轮询到期任务，避免 API 多实例重复调度。 |
| OPS-005 | Windows 凭据库 | REPLACED | 当前使用加密数据库 Secret 或环境变量；页面不回显明文，备份主动移除 Secret。旧 `stock-decision-terminal` 凭据无需迁移。 |
| OPS-006 | SQLite WAL 与 Alembic | MIGRATED | 当前继续使用 SQLite WAL，并通过 Alembic 管理结构。 |
| OPS-007 | 备份创建、校验和恢复 | MIGRATED | 当前支持 SQLite 在线快照、白名单、哈希、完整性和 revision 校验；离线 CLI 支持恢复。 |
| OPS-008 | Windows 单端口本地生产托管 | REPLACED | 当前默认由独立 API 与 Worker 启动，前端可由服务端/反向代理托管；同时覆盖本机与单服务器。 |
| OPS-009 | 单用户本地使用 | REPLACED | 本地 `AUTH_MODE=local` 保持无登录体验；服务器支持管理员认证和安全 Cookie。 |

## 自动测试证据

ChanAnalyzer 已迁移或替代能力的主要测试包括：

- `tests/backend/test_stock_actions.py`
- `tests/backend/test_stock_market_context.py`
- `tests/backend/test_market_radar_service.py`
- `tests/backend/test_market_screening.py`
- `tests/backend/test_money_flow_screen.py`
- `tests/backend/test_decision_readiness_plan.py`
- `tests/backend/test_research_workspace.py`
- `tests/backend/test_iteration_reliability.py`
- `frontend/src/components/WatchlistResearchQueue.test.tsx`
- `frontend/src/components/AnalysisHistoryPanel.test.tsx`

### 退役验收执行记录（2026-08-28）

- 后端核心研究、行情上下文、市场雷达、筛选、研究工作区和任务可靠性：37 项测试全部通过。
- 前端单元测试：9 个测试文件、16 项测试全部通过。
- 前端生产构建：通过。
- 前端资源预算：通过；初始 JavaScript gzip 97.4 KB / 100 KB，CSS gzip 20.8 KB / 25 KB。
- 验收期间发现并修复 SSE 任务事件接口的鉴权数据库会话生命周期问题；10 路并发订阅回归测试通过。
- 唯一提示为 Starlette `TestClient` 关于未来迁移到 `httpx2` 的弃用警告，不影响当前功能、构建或本次退役判断。

Stock 中未迁移能力的原实现和测试证据主要位于：

- `backend/app/models/portfolio.py`
- `backend/app/models/analysis.py` 中的 AssistantThread / AssistantMessage
- `backend/app/api/v1/routes/product.py`
- `backend/app/api/v1/routes/portfolio_risk.py`
- `backend/app/api/v1/routes/ai.py`
- `backend/tests/test_portfolio_risk.py`
- `backend/tests/test_product_api.py`
- `backend/tests/test_ai_analysis.py`
- `frontend/src/pages/PortfolioPage.tsx`
- `frontend/src/pages/JournalPage.tsx`
- `frontend/src/pages/AssistantPage.tsx`

## 非功能性退役检查

| 项目 | 结果 | 是否阻塞删除 |
|---|---|---|
| Stock 运行进程 | 未发现从 `D:\CodeHub\Stock` 启动的进程 | 否 |
| Stock 计划任务 | 未发现引用 `D:\CodeHub\Stock` 的 Windows 计划任务 | 否 |
| ChanAnalyzer 对绝对路径的引用 | 未发现对 `D:\CodeHub\Stock` 的运行时引用 | 否 |
| Stock Git 历史和未提交改动 | 用户明确不要求保留 | 否 |
| Stock 数据库、WAL、备份和 AI 会话 | 用户明确不要求迁移或保留 | 否 |
| `stock-decision-terminal` Windows 凭据 | 用户明确不要求迁移或清理 | 否 |

## 最终删除门禁

产品功能门禁和技术验证门禁均已通过：

1. Stock 未运行，ChanAnalyzer 核心研究流程冒烟验证已于 2026-08-28 通过。
2. 待删除目标已锁定为精确目录 `D:\CodeHub\Stock`，不得扩大到 `D:\CodeHub`。

专业结论：Stock 已完成退役，不需要再做代码、数据、凭据或历史迁移。用户明确授权后，精确目录 `D:\CodeHub\Stock` 已于 2026-08-28 永久删除并验证不存在，状态为 `DELETED`；`D:\CodeHub` 下其他项目未纳入删除范围。
