import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowRight, CalendarCheck2, CheckCircle2, Clock3, Database, LoaderCircle, Play, ShieldCheck, Star } from "lucide-react"
import { Link } from "react-router-dom"
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, StatCard } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useDailyPrepare } from "@/hooks/useDailyPrepare"
import { api, post } from "@/lib/api"
import type { DecisionToday, Watchlist } from "@/types/api"
import "./TodayPage.css"

const moduleLabels: Record<string, string> = {
  master_data: "股票主数据",
  daily_bars: "日线行情",
  trading_calendar: "交易日历",
  limit_up: "涨停数据",
  radar: "市场雷达",
  unit_contract: "行情单位契约",
}

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
  const candidates = (data.scan_changes?.items ?? []).filter((item) => item.state === "new_hit").slice(0, 8)
  const today = new Date().toISOString().slice(0, 10)
  const dueReviews = (watchlist.data?.items ?? []).filter(
    (item) => item.research_status !== "invalidated" && item.next_review_date && item.next_review_date <= today,
  )
  const blocking = health?.blocking_reasons ?? data.review_context.blocking_modules

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

      <section className="stats-grid compact">
        {data.market_facts.slice(0, 4).map((fact) => <StatCard key={fact.key} label={fact.label} value={fact.value} detail={fact.detail ?? undefined} />)}
      </section>

      <section className="today-grid">
        <article className="panel today-focus">
          <div className="panel-title"><span>市场结论</span><Button asChild variant="ghost" size="sm"><Link to="/radar">查看证据 <ArrowRight /></Link></Button></div>
          <h2>{data.review_context.title}</h2>
          <p>{data.review_context.message}</p>
          <div className="today-evidence">
            <div><strong>支持证据</strong>{(data.market_radar?.snapshot.evidence ?? []).slice(0, 3).map((item, index) => <p key={index}>• {String(item)}</p>)}</div>
            <div><strong>风险反证</strong>{(data.market_radar?.snapshot.counter_evidence ?? []).slice(0, 3).map((item, index) => <p key={index}>• {String(item)}</p>)}</div>
          </div>
        </article>

        <article className="panel">
          <div className="panel-title"><span><Database />首次准备与数据状态</span><Badge>{health?.categories.filter((item) => item.status === "fresh").length ?? 0}/{health?.categories.length ?? 0}</Badge></div>
          <div className="readiness-list">
            {(health?.categories ?? []).filter((item) => item.affects_overall).map((item) => (
              <div key={item.key}><span>{item.status === "fresh" ? <CheckCircle2 /> : <Clock3 />}<strong>{moduleLabels[item.key] ?? item.key}</strong></span><Badge variant="outline" className={`status-${item.status}`}>{item.status === "fresh" ? "就绪" : "待处理"}</Badge></div>
            ))}
          </div>
          {!usable ? <Button asChild variant="outline" size="sm"><Link to="/settings">打开数据与自动化设置</Link></Button> : null}
        </article>
      </section>

      <section className="panel">
        <div className="panel-title"><span>今日新增候选</span><Button asChild variant="ghost" size="sm"><Link to="/screeners">打开市场筛选</Link></Button></div>
        {candidates.length ? <div className="candidate-grid">{candidates.map((item) => (
          <article key={`${item.current_job_id}-${item.code}`}>
            <div><strong>{item.code} {item.name}</strong><Badge variant="outline">{item.signal_type ?? "新命中"}</Badge></div>
            <p>信号日期 {item.signal_date ?? "—"} · 评分 {item.score?.toFixed(1) ?? "—"}</p>
            <div><Button asChild size="sm"><Link to={`/stocks?id=${item.instrument_id}`}>验证个股</Link></Button>{item.instrument_id ? <Button variant="outline" size="sm" onClick={() => addWatch.mutate(item.instrument_id!)}><Star />加入研究</Button> : null}</div>
          </article>
        ))}</div> : <EmptyPanel title="暂无新增候选" detail="运行市场筛选后，新命中会在这里进入个股验证流程。" />}
      </section>

      <section className="today-grid">
        <article className="panel">
          <div className="panel-title"><span><CalendarCheck2 />待复盘</span><Badge>{dueReviews.length}</Badge></div>
          {dueReviews.length ? <div className="review-list">{dueReviews.slice(0, 6).map((item) => <Link key={item.id} to={`/stocks?id=${item.instrument.id}`}><span><strong>{item.instrument.code} {item.instrument.name}</strong><small>{item.next_action || "核对投资逻辑与失效条件"}</small></span><Badge variant="outline">{statusLabels[item.research_status] ?? item.research_status}</Badge></Link>)}</div> : <p className="muted">今天没有到期的自选复盘事项。</p>}
        </article>
        <article className="panel">
          <div className="panel-title"><span>任务与异常</span><Badge>{(data.attention_jobs?.failed.length ?? 0) + (data.attention_jobs?.pending.length ?? 0)}</Badge></div>
          {data.attention_jobs?.failed.length ? data.attention_jobs.failed.slice(0, 4).map((job) => <p key={job.id} className="error-text">{job.kind}：{job.message ?? "执行失败"}</p>) : <p className="muted">没有需要立即处理的失败任务。</p>}
        </article>
      </section>
    </div>
  )
}
