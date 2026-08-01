import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, CalendarClock, CircleCheck, CircleX, Database, KeyRound, Play, Save, Server, Wifi } from "lucide-react"
import { ErrorPanel, LoadingPanel, PageHeader, StatCard } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, post, put } from "@/lib/api"
import { DATA_COUNT_LABELS, SECRET_LABELS, systemLabel } from "@/lib/labels"
import type { BackupList, BackupResult, DataHealth, SystemStatus } from "@/types/api"

type Secret = { key: string; configured: boolean; source: string }
type ConnectionTest = { key: string; ok: boolean; message: string; latency_ms: number }
type AutomationSchedule = {
  id: string
  key: string
  job_kind: "market_radar.refresh" | "limit_up.refresh" | "ipo.refresh" | "data.refresh" | "screen.hot" | "screen.smart"
  hour: number
  minute: number
  weekdays: number[]
  timezone: string
  enabled: boolean
  payload: Record<string, unknown>
  last_run_at?: string | null
  next_run_at?: string | null
  updated_at: string
}
export function SettingsPage() {
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => api<SystemStatus>("/system/status") })
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: () => api<Secret[]>("/settings/secrets") })
  const schedules = useQuery({ queryKey: ["automation-schedules"], queryFn: () => api<AutomationSchedule[]>("/settings/schedules") })
  return (
    <div className="page-stack">
      <PageHeader title="设置" description="查看中文化运行状态，并安全更新数据源与 AI 服务密钥。" help="deployment" />
      {status.isLoading ? <LoadingPanel /> : null}
      {status.error ? <ErrorPanel error={status.error} /> : null}
      {status.data ? (
        <section className="stats-grid">
          <StatCard label="服务状态" value={systemLabel(status.data.status)} tone="up" />
          <StatCard label="系统版本" value={`v${status.data.version}`} />
          <StatCard label="访问模式" value={systemLabel(status.data.auth_mode)} detail={status.data.auth_mode === "local" ? "仅允许本机访问" : "需要管理员登录"} />
          <StatCard label="数据库" value={systemLabel(status.data.database)} detail="v2 主数据与研究记录" />
        </section>
      ) : null}
      <section className="settings-grid">
        <article className="panel">
          <div className="panel-title"><span><Database size={17} />数据概况</span><small>v2 数据库当前记录数</small></div>
          <div className="metric-list">
            {Object.entries(status.data?.data_counts ?? {}).map(([key, value]) => (
              <div key={key}><span>{DATA_COUNT_LABELS[key] ?? key}</span><strong>{value.toLocaleString()}</strong></div>
            ))}
          </div>
        </article>
        <article className="panel">
          <div className="panel-title"><span><Server size={17} />进程职责</span></div>
          <p className="lead-small">Web API 负责查询、鉴权和提交任务；缠论扫描、行情同步与 AI 调用由独立 Worker 执行，长任务不会阻塞页面。</p>
          {status.data && status.data.worker_status !== "healthy" ? (
            <div className="worker-warning" role="alert">
              <AlertTriangle aria-hidden="true" />
              <div>
                <strong>{status.data.worker_status === "missing" ? "Worker 尚未运行" : "Worker 心跳已过期"}</strong>
                <p>扫描、行情刷新和 AI 分析不会执行。请启动独立 Worker 进程后再提交长任务。</p>
              </div>
            </div>
          ) : null}
          <div className="settings-facts">
            <div><span>任务执行器</span><strong>{status.data?.worker_status === "healthy" ? "Worker 在线" : "Worker 不可用"}</strong></div>
            <div><span>本地服务地址</span><strong>127.0.0.1:8011</strong></div>
            <div><span>敏感配置</span><strong>加密保存，不回显明文</strong></div>
          </div>
        </article>
      </section>
      <DataReliabilitySettings />
      <section className="panel">
        <div className="panel-title"><span><CalendarClock size={17} />自动任务</span><small>本地 Worker 每 30 秒检查一次；时间按 Asia/Shanghai 执行</small></div>
        <div className="schedule-list">
          {schedules.data?.map((item) => <ScheduleRow key={`${item.id}-${item.updated_at}`} item={item} />)}
        </div>
      </section>      <section className="panel">
        <div className="panel-title"><span><KeyRound size={17} />密钥与外部服务</span><small>已保存的明文永不回显；留空不会覆盖原值</small></div>
        <div className="secret-list">
          {secrets.data?.map((item) => <SecretRow key={item.key} item={item} />)}
        </div>
      </section>
    </div>
  )
}


function DataReliabilitySettings() {
  const client = useQueryClient()
  const health = useQuery({ queryKey: ["data-health"], queryFn: () => api<DataHealth>("/system/data-health") })
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => api<BackupList>("/system/backups") })
  const create = useMutation({
    mutationFn: () => post<BackupResult>("/system/backups", {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ["backups"] }),
  })
  const verify = useMutation({ mutationFn: (name: string) => post<BackupResult>(`/system/backups/${name}/verify`, {}) })
  const labels: Record<string, string> = { fresh: "新鲜", partial: "部分可用", stale: "已过期", missing: "缺失" }
  return (
    <section className="settings-grid reliability-settings">
      <article className="panel">
        <div className="panel-title"><span><Database size={17} />数据健康</span><Badge variant="outline" className={`status-${health.data?.status ?? "missing"}`}>{labels[health.data?.status ?? "missing"]}</Badge></div>
        {health.isLoading ? <LoadingPanel rows={4} /> : null}
        {health.error ? <ErrorPanel error={health.error} /> : null}
        <div className="health-list">
          {health.data?.categories.map((item) => (
            <div key={item.key} className="health-row">
              <span><strong>{item.key}</strong><small>{item.source ?? "未记录来源"}{item.single_source ? " · 单源" : ""}{item.affects_overall ? "" : " · 独立模块"}</small></span>
              <span><Badge variant="outline" className={`status-${item.status}`}>{labels[item.status]}</Badge><small>{item.coverage_rate == null ? "" : `覆盖 ${Math.round(item.coverage_rate * 100)}%`}</small></span>
              {item.recommendation ? <p>{item.recommendation}</p> : null}
            </div>
          ))}
        </div>
      </article>
      <article className="panel">
        <div className="panel-title"><span><Server size={17} />备份</span><small>{backups.data?.mode === "external_required" ? "外部流程" : "SQLite 在线快照"}</small></div>
        {backups.data?.mode === "external_required" ? (
          <p className="lead-small">当前数据库需要使用 pg_dump/pg_restore；应用内不会尝试在线恢复。</p>
        ) : (
          <>
            <Button onClick={() => create.mutate()} disabled={create.isPending}>{create.isPending ? "创建中…" : "创建脱敏备份"}</Button>
            <div className="backup-list">
              {backups.data?.items.map((item) => (
                <div key={item.name}>
                  <span><strong>{item.name}</strong><small>{(item.size / 1024 / 1024).toFixed(1)} MB · {new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</small></span>
                  <span className="backup-actions"><Button size="sm" variant="outline" onClick={() => verify.mutate(item.name)}>校验</Button><Button size="sm" variant="ghost" asChild><a href={`/api/v1/system/backups/${item.name}/download`}>下载</a></Button></span>
                </div>
              ))}
            </div>
            {verify.data ? <small className="connection-ok">{verify.data.name} 校验通过</small> : null}
          </>
        )}
      </article>
    </section>
  )
}
function SecretRow({ item }: { item: Secret }) {
  const [value, setValue] = useState("")
  const client = useQueryClient()
  const save = useMutation({
    mutationFn: () => put<Secret>(`/settings/secrets/${item.key}`, { value }),
    onSuccess: () => {
      setValue("")
      client.invalidateQueries({ queryKey: ["secrets"] })
    },
  })
  const testConnection = useMutation({
    mutationFn: () => post<ConnectionTest>(`/settings/secrets/${item.key}/test`, {}),
  })
  return (
    <div className="secret-row">
      <div>
        <strong>{SECRET_LABELS[item.key] ?? item.key}</strong>
        <Badge variant="outline" className={item.configured ? "status-completed" : ""}>{item.configured ? "已配置" : "未配置"}</Badge>
        <small>来源：{systemLabel(item.source)}</small>
      </div>
      <Input type="password" value={value} onChange={(event) => setValue(event.target.value)} placeholder="输入新值（不会显示旧值）" aria-label={`${SECRET_LABELS[item.key] ?? item.key} 新值`} />
      <div className="secret-actions">
        <Button onClick={() => save.mutate()} disabled={!value || save.isPending}><Save />{save.isPending ? "保存中" : "保存"}</Button>
        <Button variant="outline" onClick={() => testConnection.mutate()} disabled={!item.configured || testConnection.isPending}><Wifi />{testConnection.isPending ? "测试中" : "测试连接"}</Button>
        {testConnection.data ? <small className={testConnection.data.ok ? "connection-ok" : "connection-failed"}>{testConnection.data.ok ? <CircleCheck /> : <CircleX />}{testConnection.data.message} · {testConnection.data.latency_ms}ms</small> : null}
      </div>
    </div>
  )
}
const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]
const SCHEDULE_LABELS: Record<AutomationSchedule["job_kind"], string> = {
  "data.refresh": "工作日行情更新",
  "limit_up.refresh": "工作日涨停事件刷新",
  "ipo.refresh": "工作日新股数据刷新",
  "screen.hot": "工作日热门股票扫描",
  "screen.smart": "工作日智能条件筛选",
  "market_radar.refresh": "工作日市场雷达刷新",
}
const SCHEDULE_DESCRIPTIONS: Record<AutomationSchedule["job_kind"], string> = {
  "data.refresh": "同步全部在市股票的最新行情",
  "limit_up.refresh": "同步当日涨停原始事件，供市场雷达计算使用",
  "ipo.refresh": "同步过去一年及未来 90 天已公布上市日期的新股记录",
  "screen.hot": "先取涨幅榜前 200 名，再扫描二买和三买信号",
  "screen.smart": "全市场排除 ST，扫描二买和三买信号",
  "market_radar.refresh": "基于最新交易数据生成雷达快照",
}

function ScheduleRow({ item }: { item: AutomationSchedule }) {
  const [time, setTime] = useState(`${String(item.hour).padStart(2, "0")}:${String(item.minute).padStart(2, "0")}`)
  const [weekdays, setWeekdays] = useState(item.weekdays)
  const [enabled, setEnabled] = useState(item.enabled)
  const client = useQueryClient()
  const [hour, minute] = time.split(":").map(Number)
  const save = useMutation({
    mutationFn: () => put<AutomationSchedule>(`/settings/schedules/${item.key}`, {
      job_kind: item.job_kind,
      hour,
      minute,
      weekdays,
      enabled,
      payload: item.payload,
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["automation-schedules"] }),
  })
  const run = useMutation({
    mutationFn: () => post(`/settings/schedules/${item.key}/run`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  })
  const toggleDay = (day: number) => {
    setWeekdays((current) => current.includes(day) ? current.filter((value) => value !== day) : [...current, day].sort())
  }
  return (
    <article className="schedule-row">
      <div>
        <strong>{SCHEDULE_LABELS[item.job_kind]}</strong>
        <Badge variant="outline" className={enabled ? "status-completed" : ""}>{enabled ? "已启用" : "已停用"}</Badge>
        <small>{item.next_run_at ? `下次执行 ${new Date(item.next_run_at).toLocaleString("zh-CN", { hour12: false })}` : "启用并保存后计算下次时间"}</small>
        <small>{SCHEDULE_DESCRIPTIONS[item.job_kind]}</small>
      </div>
      <Input type="time" value={time} onChange={(event) => setTime(event.target.value)} aria-label={`${item.key} 执行时间`} />
      <div className="weekday-picker" aria-label="执行星期">
        {WEEKDAY_LABELS.map((label, index) => <Button key={label} type="button" size="sm" variant={weekdays.includes(index + 1) ? "default" : "outline"} aria-pressed={weekdays.includes(index + 1)} onClick={() => toggleDay(index + 1)}>{label}</Button>)}
      </div>
      <div className="schedule-actions">
        <Button variant={enabled ? "default" : "outline"} size="sm" onClick={() => setEnabled((value) => !value)}>{enabled ? "已启用" : "启用"}</Button>
        <Button variant="outline" size="sm" onClick={() => save.mutate()} disabled={!weekdays.length || save.isPending}><Save />保存</Button>
        <Button variant="ghost" size="sm" onClick={() => run.mutate()} disabled={run.isPending}><Play />立即执行</Button>
      </div>
    </article>
  )
}
