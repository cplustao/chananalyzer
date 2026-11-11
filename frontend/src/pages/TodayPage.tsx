import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowRight, CalendarCheck2, CheckCircle2, Clock3, Database, LoaderCircle, Play, ShieldCheck, Star } from "lucide-react"
import { Link } from "react-router-dom"
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, StatCard } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useDailyPrepare } from "@/hooks/useDailyPrepare"
import { api, post } from "@/lib/api"
import { jobKindLabel, jobStatusLabel } from "@/lib/labels"
import { beijingToday } from "@/lib/research-date"
import type { AutomationStatus, DecisionToday, TodayCandidates, Watchlist } from "@/types/api"
import "./TodayPage.css"

const moduleLabels: Record<string, string> = {
  master_data: "股票主数据",
  daily_bars: "日线行情",
  analysis_series: "分析可用序列",
  trading_calendar: "交易日历",
  limit_up: "涨停数据",
  radar: "市场雷达",
  unit_contract: "行情单位契约",
}

const signalLabels: Record<string, string> = {
  "1": "一类买点",
  "1p": "盘整背驰",
  "2": "二类买点",
  "2s": "类二类",
  "3a": "三类 A",
  "3b": "三类 B",
}

const scanKindLabels: Record<string, string> = {
  "screen.smart": "全市场",
  "screen.hot": "成交额前 200",
  "scan.buy": "买点扫描",
}

function candidateHeading(candidates: TodayCandidates | null | undefined) {
  if (!candidates || candidates.is_current || !candidates.basis_trade_date) return "今日候选"
  return `候选（基于 ${candidates.basis_trade_date} 的扫描）`
}

function candidateEmpty(candidates: TodayCandidates | null | undefined): { title: string; detail: string } {
  if (candidates?.pending_scan_job_ids.length) {
    return { title: "扫描进行中", detail: "每日准备通过门禁后已自动排队全市场扫描，完成后候选会出现在这里。" }
  }
  if (!candidates || candidates.reason === "no_scan") {
    return { title: "还没有扫描结果", detail: "运行“准备今日数据”，通过门禁后会自动接着扫描全市场买点。" }
  }
  if (candidates.reason === "gate_blocked") {
    return { title: "数据未通过门禁", detail: "门禁通过前不生成今日候选，请先完成今日数据准备。" }
  }
  const window = candidates.signal_window
  return {
    title: "最近 3 个交易日没有买点信号",
    detail: `已检查信号日 ${window.start ?? "—"} 至 ${window.end ?? "—"} 的扫描结果。`,
  }
}

const localTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
})

function formatLocal(value: string | null | undefined) {
  return value ? localTime.format(new Date(value)) : "—"
}

const runStatusLabels: Record<string, string> = {
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
  queued: "排队中",
  running: "运行中",
}

function automationNotes(automation: AutomationStatus) {
  const notes: string[] = []
  const success = automation.last_success
  if (success) {
    const unfilled = success.warnings.reduce((sum, item) => sum + item.failed, 0)
    notes.push(`上次成功 ${formatLocal(success.finished_at)}${success.scheduled ? "（自动）" : ""}${unfilled ? `，${unfilled} 只行情未补齐` : ""}`)
  } else {
    notes.push("还没有成功完成过每日准备")
  }
  const latest = automation.latest_run
  if (latest && latest.id !== success?.id && !["queued", "running"].includes(latest.status)) {
    notes.push(`最近一次${runStatusLabels[latest.status] ?? latest.status}（${formatLocal(latest.finished_at ?? latest.created_at)}）：${latest.error ?? latest.message ?? "请查看任务中心"}`)
  }
  if (automation.enabled && !automation.run_screeners) notes.push("未设置串联扫描，今日候选不会自动更新")
  return notes
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined) return "—"
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`
}

// Status-only messages add nothing next to the status label.
const genericJobMessages = new Set(["任务完成", "任务失败", "部分完成", "任务已取消"])

const statusLabels: Record<string, string> = {
  pending: "待验证",
  watching: "观察中",
  confirmed: "已确认",
  invalidated: "已失效",
}

export function TodayPage() {
  const client = useQueryClient()
  const dailyPrepare = useDailyPrepare()
  const decision = useQuery({
    queryKey: ["decision-today"],
    queryFn: () => api<DecisionToday>("/decision/today"),
    refetchInterval: 60_000,
  })
  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api<Watchlist>("/watchlists/default"),
  })
  const addWatch = useMutation({
    mutationFn: (instrumentId: number) => post<Watchlist>("/watchlists/default/items", {
      instrument_id: instrumentId,
      research_status: "pending",
      tag_names: ["今日候选"],
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
  })

  if (decision.isLoading) return <LoadingPanel rows={9} />
  if (decision.error || !decision.data) return <ErrorPanel error={decision.error} retry={() => decision.refetch()} />

  const data = decision.data
  const health = data.data_health
  const usable = Boolean(health?.decision_usable && data.review_context.usable_for_next_session)
  const preparing = dailyPrepare.isPreparing
  const candidateData = data.today_candidates
  const candidates = candidateData?.items ?? []
  const empty = candidateEmpty(candidateData)
  const scanNotes = (candidateData?.scans ?? []).map((scan) => {
    const coverage = scan.coverage_rate === null || scan.coverage_rate === undefined ? "—" : `${(scan.coverage_rate * 100).toFixed(1)}%`
    return `${scanKindLabels[scan.kind] ?? scan.kind}${scan.status === "partial" ? "（部分完成）" : ""} 覆盖 ${coverage}`
  })
  const today = beijingToday()
  const dueReviews = (watchlist.data?.items ?? []).filter(
    (item) => item.research_status !== "invalidated" && item.next_review_date && item.next_review_date <= today,
  )
  const blocking = health?.blocking_reasons ?? data.review_context.blocking_modules
  const gateCategories = (health?.categories ?? []).filter((item) => item.affects_overall)
  const failedJobs = data.attention_jobs?.failed ?? []
  const activeJobs = data.attention_jobs?.pending ?? []

  return (
    <div className="page-stack today-page">
      <PageHeader
        title="今日研究"
        description="从数据准备、市场判断到候选验证与自选复盘，完成一轮可追溯的盘后研究。"
        help="workflow"
        meta={<><span>数据截至 {health?.as_of_trade_date ?? data.review_context.as_of_trade_date ?? "—"}</span><span>仅用于研究，不构成投资建议</span></>}
        actions={<Button onClick={dailyPrepare.submit} disabled={preparing} aria-busy={preparing}>{preparing ? <LoaderCircle className="spin" /> : <Play />}{dailyPrepare.buttonLabel}</Button>}
      />

      <section className={`decision-gate ${usable ? "ready" : preparing ? "preparing" : "blocked"}`} role="status" aria-live="polite">
        {usable ? <ShieldCheck /> : preparing ? <LoaderCircle className="spin" /> : <AlertTriangle />}
        <div>
          <strong>{usable ? "今日数据已通过决策门禁" : preparing ? "今日数据正在准备" : "当前仅可用于历史复盘"}</strong>
          <p>{usable ? "可以继续进行市场判断与个股验证。" : preparing ? dailyPrepare.activeJob?.message ?? "数据准备任务已进入后台，完成前页面仅展示历史复盘结果。" : health?.recommended_action ?? data.review_context.message}</p>
          {!usable && blocking.length ? <p>阻断项：{blocking.map((key) => moduleLabels[key] ?? key).join("、")}</p> : null}
        </div>
        <Badge variant="outline">{usable ? "可研究" : preparing ? "准备中" : "只读模式"}</Badge>
      </section>

      {data.automation ? (
        <section className={`automation-strip ${data.automation.enabled ? "on" : "off"}`} aria-label="自动准备状态">
          <Clock3 />
          <span>
            <strong>{data.automation.enabled ? `自动准备：工作日 ${String(data.automation.hour ?? 17).padStart(2, "0")}:${String(data.automation.minute ?? 0).padStart(2, "0")}，下次 ${formatLocal(data.automation.next_run_at)}` : "自动准备未开启"}</strong>
            {automationNotes(data.automation).map((note) => <small key={note}>{note}</small>)}
          </span>
          {!data.automation.enabled ? <Button asChild variant="outline" size="sm"><Link to="/settings">去开启</Link></Button> : null}
        </section>
      ) : null}

      <section className="stats-grid compact">
        {data.market_facts.slice(0, 4).map((fact) => <StatCard key={fact.key} label={fact.label} value={fact.value} detail={fact.detail ?? undefined} />)}
      </section>

      <section className="today-grid">
        <article className="panel today-focus">
          <div className="panel-title"><span>市场结论</span><Button asChild variant="ghost" size="sm"><Link to="/radar">查看证据 <ArrowRight /></Link></Button></div>
          <h2>{data.review_context.title}</h2>
          <p>{data.review_context.message}</p>
          <div className="today-evidence">
            <div><strong>支持证据</strong>{(data.market_radar?.snapshot.evidence ?? []).length ? (data.market_radar?.snapshot.evidence ?? []).slice(0, 3).map((item, index) => <p key={index}>• {String(item)}</p>) : <p className="muted">暂无支持证据。</p>}</div>
            <div><strong>风险反证</strong>{(data.market_radar?.snapshot.counter_evidence ?? []).length ? (data.market_radar?.snapshot.counter_evidence ?? []).slice(0, 3).map((item, index) => <p key={index}>• {String(item)}</p>) : <p className="muted">雷达没有列出反证，不代表没有风险。</p>}</div>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title"><span><Database />{data.automation?.last_success ? "数据状态" : "首次准备与数据状态"}</span><Badge>{gateCategories.filter((item) => item.status === "fresh").length}/{gateCategories.length}</Badge></div>
          <div className="readiness-list">
            {gateCategories.map((item) => (
              <div key={item.key}><span>{item.status === "fresh" ? <CheckCircle2 /> : <Clock3 />}<strong>{moduleLabels[item.key] ?? item.key}</strong></span><Badge variant="outline" className={`status-${item.status}`}>{item.status === "fresh" ? "就绪" : "待处理"}</Badge></div>
            ))}
          </div>
          {!usable ? <Button asChild variant="outline" size="sm"><Link to="/settings">打开数据与自动化设置</Link></Button> : null}
        </article>
      </section>

      <section className="panel">
        <div className="panel-title"><span>{candidateHeading(candidateData)}</span><span className="candidate-actions">{candidateData?.total ? <Badge variant="outline">{candidateData.total > candidates.length ? `前 ${candidates.length} / ${candidateData.total}` : candidates.length}</Badge> : null}<Button asChild variant="ghost" size="sm"><Link to="/screeners">打开市场筛选</Link></Button></span></div>
        {candidateData && (candidates.length || candidateData.scans.length) ? (
          <p className="candidate-meta">
            信号日 {candidateData.signal_window.start ?? "—"} 至 {candidateData.signal_window.end ?? "—"} 的买点 · {candidateData.ordering}
            {scanNotes.length ? ` · ${scanNotes.join("，")}` : ""}
            {candidateData.pending_scan_job_ids.length ? " · 新一轮扫描进行中" : ""}
            {candidates.some((item) => item.unconfirmed) ? " · “待确认”表示信号在最新一根 K 线，后续可能改变" : ""}
          </p>
        ) : null}
        {candidateData && !candidateData.is_current && candidates.length ? <p className="candidate-warning">这些候选不是基于最新交易日的数据，仅供历史复盘。</p> : null}
        {candidates.length ? <div className="candidate-grid">{candidates.map((item) => (
          <article key={`${item.job_id}-${item.code}`}>
            <div><strong>{item.code} {item.name}</strong><span className="candidate-badges">{item.unconfirmed ? <Badge variant="outline" className="candidate-unconfirmed" title="信号出现在最新一根 K 线，后续 K 线可能改变或取消它">待确认</Badge> : null}<Badge variant="outline">{signalLabels[item.signal_type ?? ""] ?? item.signal_type ?? "买点"}</Badge></span></div>
            <p>信号日 {item.signal_date} · 信号价 {item.signal_price?.toFixed(2) ?? "—"} · 现价 {item.latest_price?.toFixed(2) ?? "—"}（{formatPercent(item.distance_pct)}）</p>
            <p>{[item.industry, ...item.scan_kinds.map((kind) => scanKindLabels[kind] ?? kind)].filter(Boolean).join(" · ")}</p>
            <div><Button asChild size="sm"><Link to={`/stocks?id=${item.instrument_id}`}>验证个股</Link></Button>{item.instrument_id ? <Button variant="outline" size="sm" onClick={() => addWatch.mutate(item.instrument_id!)}><Star />加入研究</Button> : null}</div>
          </article>
        ))}</div> : <EmptyPanel title={empty.title} detail={empty.detail} />}
      </section>

      <section className="today-grid">
        <article className="panel">
          <div className="panel-title"><span><CalendarCheck2 />待复盘</span><Badge>{dueReviews.length}</Badge></div>
          {dueReviews.length ? <div className="review-list">{dueReviews.slice(0, 6).map((item) => <Link key={item.id} to={`/stocks?id=${item.instrument.id}`}><span><strong>{item.instrument.code} {item.instrument.name}</strong><small>{item.next_action || "核对投资逻辑与失效条件"}</small></span><Badge variant="outline">{statusLabels[item.research_status] ?? item.research_status}</Badge></Link>)}</div> : <p className="muted">今天没有到期的自选复盘事项。</p>}
        </article>
        <article className="panel">
          <div className="panel-title"><span>任务与异常</span><Badge>{failedJobs.length}</Badge></div>
          {failedJobs.length ? failedJobs.slice(0, 4).map((job) => <p key={job.id} className="error-text">{jobKindLabel(job.kind)}（{jobStatusLabel(job.status)}）{job.message && !genericJobMessages.has(job.message) ? `：${job.message}` : ""}</p>) : <p className="muted">没有需要立即处理的失败任务。</p>}
          {activeJobs.length ? <p className="muted">另有 {activeJobs.length} 个任务排队或运行中：{activeJobs.slice(0, 3).map((job) => jobKindLabel(job.kind)).join("、")}</p> : null}
        </article>
      </section>
    </div>
  )
}
