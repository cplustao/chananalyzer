import { useEffect, useId, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Activity,
  Ban,
  Binoculars,
  BookOpen,
  CircleDot,
  Filter,
  Gauge,
  LoaderCircle,
  CalendarCheck2,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Radar,
  Search,
  Settings,
  Sparkles,
  Star,
  TrendingUp,
  Trash2,
} from "lucide-react"
import { NavLink, Outlet, useNavigate } from "react-router-dom"
import { formatDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover"
import { Progress } from "@/components/ui/progress"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { api, post } from "@/lib/api"
import { useDebouncedValue } from "@/hooks/useDebouncedValue"
import { useDailyPrepare } from "@/hooks/useDailyPrepare"
import { jobKindLabel, jobStatusLabel } from "@/lib/labels"
import type { DataHealth, InstrumentPage, Job, JobItem, JobStages } from "@/types/api"

const groups = [
  { label: "每日研究", items: [{ to: "/today", label: "今日研究", icon: CalendarCheck2 }, { to: "/radar", label: "市场雷达", icon: Radar }] },
  {
    label: "个股研究",
    items: [
      { to: "/stocks", label: "个股分析", icon: TrendingUp },
      { to: "/watchlist", label: "自选股", icon: Star },
      { to: "/limit-ups", label: "涨停分析", icon: Sparkles },
      { to: "/ipos", label: "新股分析", icon: CircleDot },
    ],
  },
  {
    label: "策略工具",
    items: [
      { to: "/scans", label: "缠论扫描", icon: Binoculars },
      { to: "/screeners", label: "市场筛选", icon: Filter },
    ],
  },
  {
    label: "系统",
    items: [
      { to: "/help", label: "帮助中心", icon: BookOpen },
      { to: "/settings", label: "设置", icon: Settings },
    ],
  },
]

const healthLabels = { fresh: "新鲜", partial: "部分可用", stale: "已过期", missing: "缺失" } as const
const healthCategoryLabels: Record<string, string> = {
  master_data: "股票主数据",
  daily_bars: "日线行情",
  trading_calendar: "交易日历",
  limit_up: "涨停数据",
  ipo: "新股数据",
  radar: "市场雷达",
  ai: "AI 配置",
}

type JobResultError = { code: string; error?: string }
function jobResultErrors(job: Job): JobResultError[] {
  const errors = job.result?.errors
  if (!Array.isArray(errors)) return []
  return errors.flatMap((item) => {
    if (!item || typeof item !== "object") return []
    const value = item as Record<string, unknown>
    return value.code ? [{ code: String(value.code), error: value.error ? String(value.error) : undefined }] : []
  })
}

function Navigation({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <nav className="side-nav">
      {groups.map((group) => (
        <section key={group.label}>
          <div className="nav-group-label">{collapsed ? "" : group.label}</div>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onNavigate}
              aria-label={collapsed ? item.label : undefined}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
            >
              <item.icon size={17} />
              {collapsed ? null : <span>{item.label}</span>}
            </NavLink>
          ))}
        </section>
      ))}
    </nav>
  )
}

export function GlobalSearch() {
  const [value, setValue] = useState("")
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const navigate = useNavigate()
  const query = value.trim()
  const debouncedQuery = useDebouncedValue(query, 300)
  const { data, isFetching } = useQuery({
    queryKey: ["global-search", debouncedQuery],
    queryFn: ({ signal }) => api<InstrumentPage>(
      `/instruments?q=${encodeURIComponent(debouncedQuery)}&page_size=8`,
      { signal },
    ),
    enabled: debouncedQuery.length >= 2,
    staleTime: 30_000,
  })
  const items = data?.items ?? []
  const visible = open && query.length >= 2

  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer)
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer)
  }, [])

  useEffect(() => setActiveIndex(0), [debouncedQuery, items.length])

  const select = (id: number) => {
    setValue("")
    setOpen(false)
    navigate(`/stocks?id=${id}`)
  }
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setOpen(false)
      return
    }
    if (!visible || items.length === 0) return
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setActiveIndex((current) => (current + 1) % items.length)
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      setActiveIndex((current) => (current - 1 + items.length) % items.length)
    } else if (event.key === "Enter") {
      event.preventDefault()
      select(items[activeIndex]?.id ?? items[0].id)
    }
  }
  const waiting = query !== debouncedQuery || isFetching
  return (
    <div className="global-search" ref={rootRef}>
      <Search size={15} aria-hidden="true" />
      <Input
        value={value}
        onChange={(event) => { setValue(event.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="搜索代码 / 名称"
        role="combobox"
        aria-label="全局股票搜索"
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-expanded={visible}
        aria-activedescendant={visible && items[activeIndex] ? `${listboxId}-${items[activeIndex].id}` : undefined}
      />
      {visible ? (
        <div className="search-results" id={listboxId} role="listbox" aria-label="股票搜索结果">
          {waiting ? <div className="search-hint" role="status">正在检索…</div> : null}
          {items.map((item, index) => (
            <button
              id={`${listboxId}-${item.id}`}
              key={item.id}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              className={index === activeIndex ? "active" : undefined}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => select(item.id)}
            >
              <span><strong>{item.code}</strong> {item.name}</span>
              <small>{item.exchange ?? "—"}</small>
            </button>
          ))}
          {!waiting && items.length === 0 ? <div className="search-hint">没有匹配股票</div> : null}
        </div>
      ) : null}
    </div>
  )
}

function JobCenter() {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [jobToArchive, setJobToArchive] = useState<Job | null>(null)
  const [open, setOpen] = useState(false)
  const [sseConnected, setSseConnected] = useState(false)
  const queryClient = useQueryClient()
  const { data = [] } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs?limit=100"),
    refetchInterval: open ? (sseConnected ? 15_000 : 4000) : 60_000,
    refetchIntervalInBackground: false,
  })
  const items = useQuery({
    queryKey: ["job-items", selectedJobId],
    queryFn: () => api<JobItem[]>(`/jobs/${selectedJobId}/items`),
    enabled: open && Boolean(selectedJobId),
    refetchInterval: open && selectedJobId && !sseConnected ? 4000 : false,
    refetchIntervalInBackground: false,
  })
  const stages = useQuery({
    queryKey: ["job-stages", selectedJobId],
    queryFn: () => api<JobStages>(`/jobs/${selectedJobId}/stages`),
    enabled: open && Boolean(selectedJobId),
    refetchInterval: open && selectedJobId && !sseConnected ? 4000 : false,
    refetchIntervalInBackground: false,
  })
  const selectedJob = data.find((job) => job.id === selectedJobId)
  const selectedJobStatus = selectedJob?.status

  useEffect(() => {
    const active = ["queued", "running", "retrying"].includes(selectedJobStatus ?? "")
    if (!open || !selectedJobId || !active) {
      setSseConnected(false)
      return
    }
    const source = new EventSource(`/api/v1/jobs/${selectedJobId}/events`, { withCredentials: true })
    const eventTypes = [
      "queued", "running", "retrying", "stage_started", "stage_progress",
      "stage_completed", "stage_failed", "completed", "partial", "failed", "cancelled",
    ]
    const receive = (event: Event) => {
      const message = event as MessageEvent<string>
      let parsed: { event?: string; payload?: Record<string, unknown> } = {}
      try { parsed = JSON.parse(message.data) } catch { return }
      const eventName = parsed.event ?? message.type
      const payload = parsed.payload ?? {}
      queryClient.setQueryData<Job[]>(["jobs"], (current = []) => current.map((job) => {
        if (job.id !== selectedJobId) return job
        const terminal = ["completed", "partial", "failed", "cancelled"].includes(eventName)
        const progress = typeof payload.coverage_rate === "number"
          ? Math.round(payload.coverage_rate * 100)
          : terminal ? 100 : job.progress
        return {
          ...job,
          status: ["running", "retrying", "completed", "partial", "failed", "cancelled"].includes(eventName)
            ? eventName
            : job.status,
          progress,
          message: typeof payload.message === "string" ? payload.message : job.message,
          result: terminal ? payload : job.result,
        }
      }))
      void queryClient.invalidateQueries({ queryKey: ["job-items", selectedJobId] })
      void queryClient.invalidateQueries({ queryKey: ["job-stages", selectedJobId] })
      if (["completed", "partial", "failed", "cancelled"].includes(eventName)) {
        setSseConnected(false)
        source.close()
      }
    }
    source.onopen = () => setSseConnected(true)
    source.onerror = () => setSseConnected(false)
    for (const eventType of eventTypes) source.addEventListener(eventType, receive)
    return () => {
      for (const eventType of eventTypes) source.removeEventListener(eventType, receive)
      source.close()
      setSseConnected(false)
    }
  }, [open, queryClient, selectedJobId, selectedJobStatus])
  const cancel = useMutation({
    mutationFn: (jobId: string) => post<Job>(`/jobs/${jobId}/cancel`, {}),
    onSuccess: (_, jobId) => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
      queryClient.invalidateQueries({ queryKey: ["job-items", jobId] })
    },
  })
  const rerun = useMutation({
    mutationFn: (jobId: string) => post<{ job_id: string }>(`/jobs/${jobId}/rerun`, {}),
    onSuccess: ({ job_id }) => {
      setSelectedJobId(job_id)
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
      queryClient.invalidateQueries({ queryKey: ["decision-today"] })
    },
  })
  const archive = useMutation({
    mutationFn: (jobId: string) => api<void>(`/jobs/${jobId}`, { method: "DELETE" }),
    onSuccess: (_, jobId) => {
      if (selectedJobId === jobId) setSelectedJobId(null)
      setJobToArchive(null)
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
      queryClient.invalidateQueries({ queryKey: ["data-health"] })
      queryClient.invalidateQueries({ queryKey: ["decision-today"] })
    },
  })
  const activeStatuses = ["queued", "running", "retrying"]
  const terminalStatuses = ["completed", "partial", "failed", "cancelled"]
  const active = data.filter((job) => activeStatuses.includes(job.status)).length
  return (
    <>
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="top-action" aria-label="任务中心">
          <Activity size={15} /><span className="hide-narrow">任务</span>{active > 0 ? <Badge>{active}</Badge> : null}
        </Button>
      </SheetTrigger>
      <SheetContent className="job-sheet">
        <SheetHeader><SheetTitle>任务中心</SheetTitle></SheetHeader>
        <div className="job-list">
          {data.length === 0 ? <div className="search-hint">暂无任务</div> : null}
          {data.map((job) => {
            const expanded = selectedJobId === job.id
            const canCancel = activeStatuses.includes(job.status)
            const canRerun = ["partial", "failed"].includes(job.status)
            const canArchive = terminalStatuses.includes(job.status)
            const resultErrors = jobResultErrors(job)
            const succeeded = typeof job.result?.succeeded === "number" ? job.result.succeeded : Math.max(0, job.completed - job.failed)
            return (
              <article key={job.id} className="job-row">
                <div className="job-row-heading">
                  <button
                    type="button"
                    className="job-row-toggle"
                    aria-expanded={expanded}
                    onClick={() => setSelectedJobId(expanded ? null : job.id)}
                  >
                    <span>
                      <strong>{jobKindLabel(job.kind)}</strong>
                      <small>{expanded ? "收起任务明细" : "查看任务明细"}</small>
                    </span>
                  </button>
                  <div className="job-row-actions">
                    <Badge variant="outline" className={`status-${job.status}`}>{jobStatusLabel(job.status)}</Badge>
                    {canCancel ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={`取消${jobKindLabel(job.kind)}`}
                        disabled={cancel.isPending}
                        onClick={() => cancel.mutate(job.id)}
                      >
                        <Ban size={14} />取消
                      </Button>
                    ) : null}
                    {canRerun ? (
                      <Button type="button" variant="outline" size="sm" disabled={rerun.isPending} onClick={() => rerun.mutate(job.id)}>{job.kind === "data.refresh" ? "重试失败标的" : "补跑失败项"}</Button>
                    ) : null}
                    {canArchive ? (
                      <Button type="button" variant="ghost" size="sm" aria-label={`从任务中心移除${jobKindLabel(job.kind)}`} onClick={() => setJobToArchive(job)}><Trash2 size={14} />移除</Button>
                    ) : null}
                  </div>
                </div>
                <Progress value={Math.round(job.progress ?? 0)} />
                <small className="job-summary">
                  {job.message ?? "任务处理中"} · 已处理 {job.completed}/{job.total || "—"} · 成功 {succeeded} · 失败 {job.failed}
                </small>
                <small className="job-time">提交时间 {formatDate(job.created_at)}{job.finished_at ? ` · 完成时间 ${formatDate(job.finished_at)}` : ""}</small>
                {job.error ? <p className="error-text">{job.error}</p> : null}
                {expanded ? (
                  <div className="job-item-list" aria-live="polite">
                    {stages.data?.items.length ? (
                      <div className="job-stage-list">
                        {stages.data.items.map((stage) => (
                          <div key={stage.stage_key} className="job-stage-row">
                            <span><strong>{jobKindLabel(stage.stage_key)}</strong><small>{stage.message ?? `${stage.processed ?? 0} 项`}</small></span>
                            <Badge variant="outline" className={`status-${stage.status}`}>{jobStatusLabel(stage.status)}</Badge>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {items.isLoading ? <small>正在加载子任务…</small> : null}
                    {!items.isLoading && items.data?.length === 0 && resultErrors.length === 0 ? <small>该任务没有拆分子任务。</small> : null}
                    {resultErrors.map((item) => (
                      <div key={item.code} className="job-item-row">
                        <div><strong>{item.code}</strong><Badge variant="outline" className="status-failed">失败</Badge></div>
                        {item.error ? <p className="error-text">{item.error}</p> : null}
                      </div>
                    ))}
                    {items.data?.map((item) => (
                      <div key={item.id} className="job-item-row">
                        <div>
                          <strong>{item.subject_key || `子任务 ${item.id.slice(0, 8)}`}</strong>
                          <Badge variant="outline" className={`status-${item.status}`}>{jobStatusLabel(item.status)}</Badge>
                        </div>
                        <small>尝试 {item.attempts} 次{item.finished_at ? ` · ${formatDate(item.finished_at)}` : ""}</small>
                        {item.error ? <p className="error-text">{item.error}</p> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      </SheetContent>
    </Sheet>
    <Dialog open={Boolean(jobToArchive)} onOpenChange={(open) => { if (!open && !archive.isPending) setJobToArchive(null) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>从任务中心移除记录？</DialogTitle>
          <DialogDescription>“{jobToArchive ? jobKindLabel(jobToArchive.kind) : "该任务"}”将不再显示在任务中心和待办中；扫描结果、研究报告和原始任务数据仍会保留。</DialogDescription>
        </DialogHeader>
        {archive.error ? <p className="error-text">{archive.error instanceof Error ? archive.error.message : "移除失败"}</p> : null}
        <DialogFooter>
          <DialogClose asChild><Button variant="outline" disabled={archive.isPending}>取消</Button></DialogClose>
          <Button variant="destructive" disabled={!jobToArchive || archive.isPending} onClick={() => jobToArchive && archive.mutate(jobToArchive.id)}>{archive.isPending ? "正在移除…" : "确认移除"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}
export function AppShell() {
  const [collapsed, setCollapsed] = useState(false)
  const dailyPrepare = useDailyPrepare()
  const health = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api<DataHealth>("/system/data-health"),
    refetchInterval: 60_000,
  })
  const healthStatus = health.data?.status
  const healthLabel = health.isLoading ? "检查中" : health.isError ? "检查失败" : healthLabels[healthStatus ?? "missing"]
  const healthTone = healthStatus ?? (health.isError ? "error" : "checking")
  const unhealthyCategories = health.data?.categories.filter((item) => item.status !== "fresh") ?? []
  return (
    <TooltipProvider>
      <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">缠</div>
          {collapsed ? null : <div><strong>ChanAnalyzer</strong><span>A-SHARE RESEARCH</span></div>}
        </div>
        <Navigation collapsed={collapsed} />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button className="collapse-button" variant="ghost" aria-label={collapsed ? "展开导航" : "收起导航"} title={collapsed ? "展开导航" : "收起导航"} onClick={() => setCollapsed((value) => !value)}>
              {collapsed ? <PanelLeftOpen /> : <><PanelLeftClose /><span>收起导航</span></>}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">{collapsed ? "展开导航" : "收起导航"}</TooltipContent>
        </Tooltip>
      </aside>
      <header className="topbar">
        <Sheet>
          <SheetTrigger asChild><Button className="mobile-menu" variant="ghost" size="icon" aria-label="打开导航菜单" title="打开导航菜单"><Menu /></Button></SheetTrigger>
          <SheetContent side="left" className="mobile-sheet">
            <SheetHeader><SheetTitle>ChanAnalyzer</SheetTitle></SheetHeader>
            <Navigation collapsed={false} />
          </SheetContent>
        </Sheet>
        <GlobalSearch />
        <div className="top-spacer" />
        <Popover>
          <PopoverTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className={`freshness data-health-indicator health-${healthTone}`} aria-label={`数据健康：${healthLabel}，点击展开具体模块`}>
              <Gauge size={14} /><span className="hide-narrow">数据 {healthLabel}</span>
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="data-health-popover">
            <PopoverHeader>
              <PopoverTitle>数据健康：{healthLabel}</PopoverTitle>
              <p>总状态取所有模块中最差的一项，不代表全部数据都不可用。新股与 AI 作为独立模块单独提示。</p>
            </PopoverHeader>
            {health.isLoading ? (
              <p className="health-all-fresh">正在核对交易日、行情覆盖、涨停事件和雷达快照…</p>
            ) : health.isError ? (
              <div className="health-query-error">
                <p>暂时无法取得数据健康结果，这不等于数据缺失。</p>
                <Button type="button" variant="outline" size="sm" onClick={() => void health.refetch()}>重新检查</Button>
              </div>
            ) : unhealthyCategories.length ? (
              <div className="health-category-list">
                {unhealthyCategories.map((item) => (
                  <div key={item.key}>
                    <span><strong>{healthCategoryLabels[item.key] ?? item.key}</strong><small>{item.data_time ? `数据截至 ${formatDate(item.data_time)}` : "暂无数据"}</small></span>
                    <Badge variant="outline" className={`status-${item.status}`}>{healthLabels[item.status]}</Badge>
                  </div>
                ))}
              </div>
            ) : <p className="health-all-fresh">所有核心模块均可用。</p>}
            {health.data && !health.data.decision_usable ? (
              <>
                <Button type="button" variant="outline" size="sm" onClick={dailyPrepare.submit} disabled={dailyPrepare.isPreparing} aria-busy={dailyPrepare.isPreparing}>
                  {dailyPrepare.isPreparing ? <LoaderCircle className="spin" /> : <Play size={14} />}
                  {dailyPrepare.isPreparing ? dailyPrepare.buttonLabel : "立即更新今日数据"}
                </Button>
                <p className="health-preparation-status" role="status" aria-live="polite">
                  {dailyPrepare.activeJob?.message ?? (dailyPrepare.isPreparing ? "正在确认后台任务…" : "更新将在后台运行，离开当前页面不会中断。")}
                </p>
              </>
            ) : null}
          </PopoverContent>
        </Popover>
        <JobCenter />
      </header>
        <main className="workspace"><Outlet /></main>
      </div>
    </TooltipProvider>
  )
}

