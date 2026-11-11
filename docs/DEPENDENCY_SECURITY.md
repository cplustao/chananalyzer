# 依赖漏洞处理记录（2026-11-11）

GitHub Dependabot 在默认分支上报了 66 个告警（严重 1、高危 23、中危 32、低危 10）。本机 `gh` 没有登录，读不到告警列表，所以用本地工具按同一批锁文件重新扫描：Python 用 `pip-audit`，前端用 `npm audit`。两边的计数口径和 Dependabot 不同（npm 按包聚合，Dependabot 按“包 × 公告”计），但覆盖的是同一批包和公告。

## 处理后状态

| 检查 | 处理前 | 处理后 |
| --- | --- | --- |
| `pip-audit --skip-editable` | 5 个包、19 条公告 | 0 |
| `npm audit`（`frontend/`） | 24 个包：严重 1、高危 12、中危 10、低危 1 | 3 个包，全部低危，同一个根因 |

## Python（`pyproject.toml`、`pylock.toml`）

`pylock.toml` 用 `uv pip compile ... -P <包>` 只升级下面 5 个包，其余版本不变。

| 包 | 原版本 → 新版本 | 最高级别 | 依赖类型 | 用途 |
| --- | --- | --- | --- | --- |
| certifi | 2026.7.22 → 2026.10.23 | 严重（内置的 emSign 根证书对应的中间 CA 密钥泄露） | 传递：httpx、requests、curl-cffi | 运行时（数据源与 AI 请求的 TLS 校验） |
| urllib3 | 2.7.0 → 2.8.0 | 高危（chunk 行无上限缓冲；HTTPS 代理的 TLS 配置被忽略） | 传递：requests、akshare | 运行时 |
| msgpack | 1.2.2 → 1.2.4 | 高危（C 扩展内存泄漏） | 传递：cachecontrol ← pip-audit | 仅开发 |
| filelock | 3.32.4 → 4.2.10 | 高危（ReadWriteLock 的符号链接问题等 12 条） | 传递：cachecontrol[filecache] ← pip-audit | 仅开发 |
| setuptools | 83.0.0 → 84.2.1 | 低危（Windows 下旧式 wheel 安装的路径逃逸） | 直接（dev 附加项） | 仅开发 |

`pyproject.toml` 中 setuptools 的约束从 `>=83,<84` 改为 `>=84.1,<85`。运行时依赖的版本范围没有变，`requirements.txt` 不用改。

## 前端（`frontend/package.json`、`package-lock.json`）

前端是 Vite 构建的静态页面，`node_modules` 里的包只在开发和构建时运行，只有被打包进 `dist/` 的代码会到浏览器。下面的漏洞包都不会进入打包产物。

| 处理方式 | 包 | 级别 | 依赖链 |
| --- | --- | --- | --- |
| 直接依赖升级 shadcn `^4.16.0` → `^4.23.0` | proxy-addr（严重）、body-parser、@modelcontextprotocol/sdk、zod 3.x、hono、@hono/node-server、ip-address、qs、postcss-selector-parser | 严重 1、高危 3、中危若干 | shadcn CLI 自带的 MCP 服务器（express/hono）。4.23 改用 `@modelcontextprotocol/server` 和 zod 4，这一整串依赖随之移除。项目只用到 `shadcn/tailwind.css` 这一个样式文件 |
| 直接依赖升级 vite `^8.1.1` → `^8.3.8` | vite（launch-editor、`server.fs.allow`、跨域脚本） | 高危 | 开发服务器 |
| 直接依赖升级 vitest `^4.1.10` → `^4.1.12` | vitest、@vitest/mocker | 中危 | 单元测试 |
| 锁文件内的同范围升级 | undici 8.9.0 → 8.12.4（jsdom），fast-uri → 3.1.9，systeminformation → 5.33.19，js-yaml → 4.3.2，brace-expansion 2.1.4 → 2.1.7，@redocly/openapi-core → 1.34.24，source-map-js | 高危 | jsdom（测试）、shadcn CLI、openapi-typescript（类型生成）、tailwind 构建 |
| `overrides` | `@ts-morph/common` 下的 minimatch 10.2.6 → `^11.0.1`（连带 brace-expansion 5.0.12） | 中危 | shadcn CLI → ts-morph。修复只在 11.x，ts-morph 26 声明的是 `^10`；11 的导出 API 相同，CLI 只用它做路径匹配 |

锁文件是用 npm 11 生成的：npm 10.9 在解析 vitest/jsdom 的可选 peer 依赖时会崩溃（`Cannot read properties of null (reading 'edgesOut')`）。格式仍是 `lockfileVersion 3`，已确认 CI 使用的 npm 10 可以直接 `npm ci`。

## 暂不处理

| 包 | 级别 | 原因 | 实际影响 |
| --- | --- | --- | --- |
| deepmerge 4.3.1（npm audit 同时把依赖它的 shadcn、@shadcn/registry 计为低危） | 低危，GHSA-gcqx-w48m-w2h4：`mergeObject()` 的返回值可被原型污染 | 所有 4.2.0 以上版本都受影响，上游没有修复版本。`npm audit fix --force` 给出的“修复”是把 shadcn 降到 1.0.0，会破坏现有组件配置 | 只有在开发者手动运行 `npx shadcn add ...` 时才会执行，合并的是 shadcn 官方组件库的配置，不处理外部输入；不进入打包产物，也和监听 127.0.0.1 的 API 无关。上游发布修复后随 shadcn 升级 |

## 验证

- 后端：`pytest tests/backend` 195 项通过；`ruff`、`mypy`（CI 范围）、`bandit` 通过；`pip-audit --skip-editable` 无告警。
- 前端：`npx tsc -b`、`npm run lint`、`npx vitest run`（18 项）、`npm run build`、`npm run check:budget`、`npx playwright test`（29 项通过，1 项按设计只在移动端运行）均通过；重新导出 `openapi.json` 并运行 `npm run generate:api` 后无差异；`npm audit --audit-level=high` 退出码为 0。
