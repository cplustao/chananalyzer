# ChanAnalyzer v2 部署

## 本地研究模式

```powershell
py -3.12 -m pip install -e ".[dev]"
cd frontend
npm install
npm run build
cd ..
py -3.12 -m backend.app.start_local
```

访问 `http://127.0.0.1:8011`。`AUTH_MODE=local` 只接受本机来源并使用固定本地用户。API 与 Worker 始终是独立进程。

严格复现 Python 依赖可使用：

```powershell
py -3.12 -m pip install -r pylock.toml
py -3.12 -m pip install --no-deps -e .
```

## 单服务器模式

```dotenv
ENVIRONMENT=server
AUTH_MODE=admin
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-long-random-password
APP_SECRET_KEY=replace-with-at-least-48-random-characters
COOKIE_SECURE=true
ALLOWED_ORIGINS=https://research.example.com
DATABASE_URL=sqlite:////opt/chananalyzer/data/chan_v2.db
API_HOST=127.0.0.1
API_PORT=8011
```

用 systemd、Supervisor 或宝塔分别守护：

```bash
python -m backend.app.main
python -m backend.app.worker.main
```

Nginx 终止 HTTPS 并反向代理到 `127.0.0.1:8011`。不要直接把 API 暴露到公网。`TRUSTED_PROXIES` 只能填写实际直连 API 的代理地址；应用仅在直连来源命中该清单时读取 `X-Forwarded-For`。

在 Nginx 的 `http` 块定义登录限流区：

```nginx
limit_req_zone $binary_remote_addr zone=chan_login:10m rate=2r/m;
```

站点配置至少包含以下规则（域名和证书路径按实际环境调整）：

```nginx
location = /api/v1/auth/login {
    limit_req zone=chan_login burst=8 nodelay;
    limit_req_status 429;
    proxy_pass http://127.0.0.1:8011;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}

location /api/v1/jobs/ {
    proxy_pass http://127.0.0.1:8011;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 1h;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}

location / {
    proxy_pass http://127.0.0.1:8011;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

应用内登录限制仍按“用户名 + 可信客户端地址”执行 5 分钟 10 次；Nginx 是独立的第一层防护。PostgreSQL 部署只需更换 `DATABASE_URL` 并安装对应 SQLAlchemy 驱动。

## 运维检查

- 存活：`GET /health/live`
- 就绪：`GET /health/ready`
- 系统状态：`GET /api/v1/system/status`
- OpenAPI：`GET /docs`
- Worker：`/api/v1/system/status` 的 `worker_status` 应为 `healthy`，`last_heartbeat` 应在 180 秒内，且任务中心无长期 `running` 项。
- 数据刷新：设置页测试有效的 Tushare Token 后再执行。

设置接口不返回密钥旧值。服务器必须长期保持同一个 `APP_SECRET_KEY`，否则无法解密数据库中已保存的密钥。数据库文件、`.env`、任务日志和分析报告应纳入定期备份。

## 回滚

应用版本回滚不应切回 8001。发布前备份当前 v2 数据库，回滚时只回滚 v2 代码版本和对应 Alembic 版本。旧系统及旧数据库已永久删除。
## 数据库迁移

API 与 Worker 不会自行创建或修改表结构。每次发布都必须在启动两个进程前执行：

```bash
python -m alembic upgrade head
```

发布门禁应同时执行 `python -m alembic check`。只有迁移成功后才能启动或重启 API 与 Worker，避免两个进程并发修改数据库结构。

## SQLite 备份与离线恢复

在线创建、列表、校验和下载可从设置页或 `/api/v1/system/backups` 系列接口完成。备份包不会包含 `secret_settings`；API 密钥仍应通过安全的外部密钥管理或人工重新配置。

备份仍包含自选股、研究逻辑和复盘记录等个人数据。应用在 Unix 系统上会把备份目录和文件权限收紧为仅当前用户可访问；仍应将备份目录放在加密磁盘或加密的外部存储中，不得放入 Web 静态目录。服务器模式默认禁止网页下载备份，确需下载时显式设置 `BACKUP_DOWNLOAD_ENABLED=true`，完成后建议立即关闭。

命令行也可执行：

```bash
chan-backup list
chan-backup create
chan-backup verify <backup-name.zip>
```

恢复必须离线进行：

1. 停止 API 和 Worker，确认没有进程持有 SQLite 文件。
2. 执行 `chan-backup verify <backup-name.zip>`。
3. 执行 `chan-backup restore <backup-name.zip>`。
4. 执行 `python -m alembic upgrade head`，再启动 API 与 Worker。

恢复会检查压缩包白名单、路径、SHA-256、SQLite 完整性和 Alembic revision，并在替换前创建 `.pre-restore-*` 回滚副本。若恢复失败，工具会尝试恢复原数据库；仍应保留异机备份。

## PostgreSQL 外部备份

应用内备份接口对 PostgreSQL 返回 `external_required`。推荐在数据库主机使用官方工具，并让备份文件进入独立、加密的存储：

```bash
pg_dump --format=custom --file=chananalyzer.dump "$DATABASE_URL"
pg_restore --list chananalyzer.dump
```

恢复到空数据库或经确认可覆盖的目标数据库：

```bash
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" chananalyzer.dump
python -m alembic upgrade head
```

执行 `--clean` 会删除目标数据库中的已有对象，必须先核对连接目标并保留可用备份。恢复期间停止 API 与 Worker，完成后再运行数据健康检查和任务中心检查。

## 数据健康检查

- 汇总接口：`GET /api/v1/system/data-health`
- `fresh` 可用于新的雷达与确定性 AI 报告；`stale` 只允许展示历史数据。
- 涨停、新股等单源类别会明确标记来源，不应把单源误读为已经具备降级能力。
