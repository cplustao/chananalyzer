import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowDown, ArrowUp, CalendarClock, Database, RefreshCw, ShieldCheck } from "lucide-react"
import { Link } from "react-router-dom"
import { RadarHistoryChart } from "@/components/RadarHistoryChart"
import { ErrorPanel, LoadingPanel, PageHeader, StatCard } from "@/components/common"
import { formatDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { api, post } from "@/lib/api"
import { jobKindLabel, jobStatusLabel } from "@/lib/labels"
import type { DecisionToday, HealthStatus, JobAccepted, RadarHistoryItem, RadarSnapshot } from "@/types/api"

const componentLabels: Record<string, string> = {
  equal_weight_trend: "全市场等权趋势",
  market_breadth: "市场宽度",
  turnover_liquidity: "成交活跃度",
  limit_ecology: "涨跌停生态",
  industry_diffusion: "行业扩散",
}

const industryStatusLabels: Record<string, string> = {
  focus: "聚焦",
  active: "活跃",
  normal: "观察",
  retreat: "退潮",
}

const healthLabels: Record<HealthStatus, string> = {
  fresh: "新鲜",
  partial: "部分可用",
  stale: "已过期",
  missing: "缺失",
}

const moduleLabels: Record<string, string> = {
  daily_bars: "日线行情",
  master_data: "股票主数据",
  trading_calendar: "交易日历",
  market_radar: "市场雷达",
  radar: "市场雷达",
  limit_up: "涨停数据",
  ipo: "新股数据",
  ai: "AI 配置",
}

function percent(value: unknown, digits = 1) {
  const number = Number(value)
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : "—"
}

function textOf(value: unknown) {
  if (typeof value === "string") return value
  if (value && typeof value === "object") return JSON.stringify(value)
  return String(value ?? "—")
}

export function RadarPage() {
  const client = useQueryClient()
  const radar = useQuery({ queryKey: ["radar"], queryFn: () => api<RadarSnapshot>("/market/radar") })
  const history = useQuery({
    queryKey: ["radar-history"],
    queryFn: () => api<{ items: RadarHistoryItem[] }>("/market/radar/history?limit=90"),
  })
  const decision = useQuery({
    queryKey: ["decision-today"],
    queryFn: () => api<DecisionToday>("/decision/today"),
    refetchInterval: 60_000,
  })
  const refresh = useMutation({
    mutationFn: () => post<JobAccepted>("/jobs", { kind: "market_radar.refresh", payload: {} }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  })

  if (radar.isLoading) return <LoadingPanel rows={10} />
  if (radar.error || !radar.data) return <ErrorPanel error={radar.error} retry={() => radar.refetch()} />

  const item = radar.data
  const regime = item.regime ?? {}
  const coverage = item.coverage ?? {}
  const breadth = item.breadth ?? {}
  const liquidity = item.liquidity ?? {}
  const ecology = item.limit_ecology ?? {}
  const methodology = item.methodology
  const adjustmentCounts = methodology?.bar_adjustments ?? {}
  const position = regime.executable_position
  const positionRange = regime.position_range
  const evidence = Array.isArray(item.evidence) ? item.evidence : []
  const counterEvidence = Array.isArray(item.counter_evidence)
    ? item.counter_evidence
    : Array.isArray(item.contradictions)
      ? item.contradictions
      : []
  const components = item.components ?? []
  const industries = ecology.industries ?? []
  const recent = ecology.recent ?? []
  const coverageCount = coverage.current_count ?? coverage.valid_count ?? coverage.count
  const conclusion = typeof item.conclusion === "string"
    ? item.conclusion
    : regime.status === "strong"
      ? "市场处于强势环境，可在风险预算内维持进攻仓位，并优先研究主线方向。"
      : regime.status === "warm"
        ? "市场环境偏强，适合结构性参与，但需要保留对次日分化的应对空间。"
        : regime.status === "risk"
          ? "市场处于风险环境，应以控制回撤和等待宽度修复为主。"
          : "市场信号尚未形成一致方向，保持均衡或较低仓位并等待确认。"

  return (
    <div className="page-stack">
      <PageHeader
        title="市场雷达"
        description="先判断市场环境和可执行仓位，再决定个股研究与策略执行优先级。"
        help="market-radar"
        meta={
          <>
            <Badge variant="outline">{item.source === "v2_cache" ? "v2 历史快照" : "实时适配器"}</Badge>
            <span>数据日期 {item.trade_date ?? "—"}</span>
            <span>生成 {formatDate(item.generated_at)}</span>
            <span>算法 {item.algorithm_version ?? "current"}</span>
          </>
        }
        actions={
          <Button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <RefreshCw className={refresh.isPending ? "spin" : ""} />
            {refresh.isPending ? "任务已提交" : "重新计算"}
          </Button>
        }
      />

      {decision.data?.review_context ? (
        <section className={`review-context health-${decision.data.review_context.freshness}`} role="status">
          <div className="review-context-icon">
            {decision.data.review_context.usable_for_next_session ? <ShieldCheck /> : <AlertTriangle />}
          </div>
          <div className="review-context-copy">
            <div>
              <strong>{decision.data.review_context.title}</strong>
              <Badge variant="outline" className={`status-${decision.data.review_context.freshness}`}>{healthLabels[decision.data.review_context.freshness]}</Badge>
            </div>
            <p>{decision.data.review_context.message}</p>
            <dl>
              <div><dt>数据截至</dt><dd>{decision.data.review_context.as_of_trade_date ?? "—"}</dd></div>
              <div><dt>适用范围</dt><dd>{decision.data.review_context.applicable_to}</dd></div>
              <div><dt>目标交易日</dt><dd>{decision.data.review_context.expected_trade_date ?? "交易日历缺失"}</dd></div>
            </dl>
          </div>
        </section>
      ) : null}

      <section className="stats-grid">
        <StatCard label="市场状态" value={regime.status_label ?? "待判断"} tone={regime.status === "risk" ? "down" : regime.status === "strong" || regime.status === "warm" ? "up" : "warn"} />
        <StatCard label="环境评分" value={regime.score?.toFixed?.(1) ?? "—"} detail="满分 100" />
        <StatCard
          label="下一交易日可执行仓位"
          value={positionRange ? `${Math.round((positionRange.min ?? 0) * 100)}%–${Math.round((positionRange.max ?? 0) * 100)}%` : "—"}
          detail={position ? `决策评分 MA5 ${position.decision_score.toFixed(1)}` : "等待历史序列"}
        />
        <StatCard label="有效样本" value={coverageCount ?? "—"} detail={coverage.rate ? `覆盖率 ${percent(coverage.rate)}` : "全市场覆盖"} />
      </section>

      <section className="analysis-grid">
        <article className="panel conclusion-panel">
          <div className="panel-title">
            <span>核心结论</span>
            <Badge className={`regime-${regime.status ?? "unknown"}`}>{regime.status_label ?? "未知"}</Badge>
          </div>
          <p className="lead">{conclusion}</p>
          <div className="position-band">
            <span>执行仓位</span>
            <strong>{positionRange ? `${Math.round((positionRange.min ?? 0) * 100)}%–${Math.round((positionRange.max ?? 0) * 100)}%` : "—"}</strong>
          </div>
          {position ? (
            <div className="position-detail">
              <span>{position.action === "increase" ? <ArrowUp /> : position.action === "decrease" ? <ArrowDown /> : null}</span>
              <div><strong>{position.action === "increase" ? "提高一档" : position.action === "decrease" ? "降低一档" : "保持仓位"}</strong><p>{position.reason}；{position.effective}生效。</p></div>
            </div>
          ) : null}
        </article>
        <article className="panel">
          <div className="panel-title">证据 / 反证</div>
          <div className="evidence-columns">
            <div>
              <h3>支持证据</h3>
              {evidence.length ? evidence.map((value, index) => <p key={index}>• {textOf(value)}</p>) : <p className="muted">暂无独立支持证据</p>}
            </div>
            <div>
              <h3>风险反证</h3>
              {counterEvidence.length ? counterEvidence.map((value, index) => <p key={index}>• {textOf(value)}</p>) : <p className="muted">暂无显式反证</p>}
            </div>
          </div>
        </article>
      </section>

      <section className="stats-grid radar-market-stats">
        <StatCard label="上涨 / 下跌" value={`${breadth.advancers ?? "—"} / ${breadth.decliners ?? "—"}`} detail={`上涨占比 ${percent(breadth.advance_rate)}`} />
        <StatCard label="站上 MA20" value={percent(breadth.above_ma20_rate)} detail={`${breadth.above_ma20_count ?? "—"} 只股票`} />
        <StatCard label="全市场成交额" value={liquidity.amount_yi ? `${Number(liquidity.amount_yi).toLocaleString()} 亿` : "—"} detail={`环比 ${percent((liquidity.amount_ratio ?? 1) - 1)}`} />
        <StatCard label="涨停 / 跌停估算" value={`${ecology.limit_up_count ?? "—"} / ${breadth.limit_down_count ?? "—"}`} detail={`最高 ${ecology.highest_board ?? "—"} 板 · 连板 ${ecology.multi_board_count ?? "—"}`} />
      </section>

      <section className="panel">
        <div className="panel-title"><span>五维环境评分</span><small>趋势、宽度、流动性、涨停生态、行业扩散</small></div>
        <div className="radar-components">
          {components.map((component) => (
            <article key={component.key}>
              <div><strong>{componentLabels[component.key] ?? component.label}</strong><span>{component.score.toFixed(1)}</span></div>
              <Progress value={component.score} />
              <p>{component.summary ?? "暂无摘要"}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-title"><span>历史评分与可执行仓位</span><small>最近 90 个快照 · 仓位为平滑、确认和滞回后的次日执行值</small></div>
        {history.data?.items?.length ? <RadarHistoryChart items={history.data.items} /> : <div className="empty-chart">暂无历史序列</div>}
      </section>

      <section className="radar-detail-grid">
        <article className="panel table-panel">
          <div className="panel-title"><span>涨停行业扩散</span><small>Top {Math.min(industries.length, 15)}</small></div>
          <div className="table-scroll radar-table-scroll">
            <table>
              <thead><tr><th>行业</th><th>涨停</th><th>最高板</th><th>较前日</th><th>成交额</th><th>状态</th></tr></thead>
              <tbody>
                {industries.slice(0, 15).map((industry) => (
                  <tr key={industry.name}>
                    <td>{industry.name}</td>
                    <td className="tone-up-text">{industry.limit_up_count}</td>
                    <td>{industry.highest_board}</td>
                    <td className={(industry.change ?? 0) >= 0 ? "tone-up-text" : "tone-down-text"}>{(industry.change ?? 0) > 0 ? "+" : ""}{industry.change ?? "—"}</td>
                    <td>{industry.amount_yi?.toFixed(2) ?? "—"} 亿</td>
                    <td><Badge variant="outline">{industryStatusLabels[industry.status ?? ""] ?? "观察"}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
        <article className="panel">
          <div className="panel-title"><span>近期涨停生态</span><small>{recent.length} 个交易日</small></div>
          <div className="radar-recent-list">
            {recent.map((value, index) => (
              <div key={`${value.trade_date}-${index}`}>
                <span>{String(value.trade_date ?? "—")}</span>
                <strong className="tone-up-text">涨停 {String(value.limit_up_count ?? "—")}</strong>
                <small>最高 {String(value.highest_board ?? "—")} 板</small>
              </div>
            ))}
          </div>
        </article>
      </section>

      {decision.data ? (
        <details className="panel review-operations">
          <summary>
            <span><Database size={17} />数据与任务待办</span>
            <Badge variant="outline">{decision.data.missing_or_stale_modules.length + (decision.data.attention_jobs?.failed.length ?? 0)} 项</Badge>
          </summary>
          <div className="review-operations-body">
            {decision.data.missing_or_stale_modules.length ? (
              <div><strong>缺失或过期模块</strong><p>{decision.data.missing_or_stale_modules.map((key) => moduleLabels[key] ?? key).join("、")}</p></div>
            ) : <div><strong>数据模块</strong><p>当前没有缺失或过期模块。</p></div>}
            {decision.data.attention_jobs?.failed.length ? (
              <div className="decision-list">
                {decision.data.attention_jobs.failed.map((job) => (
                  <div key={job.id}><span><strong>{jobKindLabel(job.kind)}</strong><small>提交时间 {formatDate(job.created_at)}</small></span><Badge variant="outline" className={`status-${job.status}`}>{jobStatusLabel(job.status)}</Badge></div>
                ))}
              </div>
            ) : <div><strong>任务</strong><p>当前没有失败或部分完成任务。</p></div>}
            <Button asChild variant="outline" size="sm"><Link to="/settings"><CalendarClock />查看数据健康与刷新建议</Link></Button>
          </div>
        </details>
      ) : null}

      {methodology ? (
        <section className="radar-notice" role="note">
          <AlertTriangle />
          <div>
            <strong>计算口径：{methodology.trend_benchmark_label ?? "全市场等权趋势"}</strong>
            <p>前复权样本 {adjustmentCounts.QFQ ?? 0} 只 · 不复权样本 {adjustmentCounts.NONE ?? 0} 只</p>
            {methodology.notes?.map((note) => <p key={note}>{note}</p>)}
          </div>
        </section>
      ) : null}

      {item.missing_fields?.length ? (
        <section className="radar-notice">
          <AlertTriangle />
          <div><strong>数据口径提示</strong>{item.missing_fields.map((field) => <p key={field}>{field}</p>)}</div>
        </section>
      ) : null}
    </div>
  )
}
