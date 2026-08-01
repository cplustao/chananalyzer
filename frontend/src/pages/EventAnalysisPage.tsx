import { useMemo, useState } from "react"
import { subDays } from "date-fns"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BookmarkPlus, Eye, Play, RefreshCw, Search, TrendingUp } from "lucide-react"
import { Link } from "react-router-dom"
import { AnalysisHistoryPanel } from "@/components/AnalysisHistoryPanel"
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader } from "@/components/common"
import {
  ResearchDateRangePicker,
  type ResearchDateRange,
} from "@/components/ResearchDateRangePicker"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, post } from "@/lib/api"
import { jobStatusLabel } from "@/lib/labels"
import { compactDate } from "@/lib/research-date"
import type { AnalysisPage, JobAccepted } from "@/types/api"

type Row = Record<string, unknown>
type RowAnalysis = {
  run_id?: string
  status?: string
  day_score?: number | null
  week_score?: number | null
  overall_score?: number | null
  day_classification?: string | null
  week_classification?: string | null
  ten_x_score?: number | null
  hundred_x_score?: number | null
}

type Props = {
  kind: "limit-up" | "ipo"
}

function dateOf(row: Row, isLimit: boolean) {
  return String(isLimit ? row.trade_date ?? "" : row.list_date ?? row.listing_date ?? "")
    .replaceAll("-", "")
    .slice(0, 8)
}

function displayDate(value: string) {
  return value.length === 8 ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}` : value || "—"
}

function codeOf(row: Row) {
  return String(row.code ?? row.ts_code ?? row.symbol ?? "").split(".")[0]
}

function analysisOf(row: Row): RowAnalysis {
  return row.analysis && typeof row.analysis === "object" ? row.analysis as RowAnalysis : {}
}

function numberOf(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function fixed(value: unknown, digits = 1, suffix = "") {
  const number = numberOf(value)
  return number == null ? "—" : `${number.toFixed(digits)}${suffix}`
}

function money(value: unknown) {
  const number = numberOf(value)
  if (number == null) return "—"
  if (Math.abs(number) >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿`
  if (Math.abs(number) >= 10_000) return `${(number / 10_000).toFixed(1)} 万`
  return number.toLocaleString("zh-CN")
}

function AnalysisScore({ score, classification, scale = 100 }: { score?: number | null; classification?: string | null; scale?: number }) {
  if (score == null) return <span className="event-score empty">—</span>
  const tone = score >= scale * 0.7 ? "high" : score >= scale * 0.4 ? "medium" : "low"
  return <span className={`event-score ${tone}`}>{score.toFixed(score % 1 ? 1 : 0)}<small>/{scale}</small>{classification ? <em>{classification}</em> : null}</span>
}

function AnalysisStatus({ status }: { status?: string }) {
  const normalized = status || "pending"
  const label = normalized === "pending" ? "待分析" : jobStatusLabel(normalized)
  return <Badge variant="outline" className={`status-${normalized}`}>{label}</Badge>
}

export function EventAnalysisPage({ kind }: Props) {
  const isLimit = kind === "limit-up"
  const today = new Date()
  const [range, setRange] = useState<ResearchDateRange>(() => ({
    from: subDays(today, isLimit ? 14 : 90),
    to: today,
  }))
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [keyword, setKeyword] = useState("")
  const startDate = compactDate(range.from)
  const endDate = compactDate(range.to)
  const path = isLimit
    ? `/limit-ups?start_date=${startDate}&end_date=${endDate}`
    : `/ipos?start_date=${startDate}&end_date=${endDate}`
  const query = useQuery({
    queryKey: [kind, startDate, endDate],
    queryFn: () => api<Record<string, unknown>>(path),
  })
  const analysisKind = isLimit ? "limit_up" : "ipo"
  const history = useQuery({
    queryKey: ["analysis-history", analysisKind, startDate, endDate],
    queryFn: () => api<AnalysisPage>(
      `/analyses?kind=${analysisKind}&start_date=${startDate.slice(0, 4)}-${startDate.slice(4, 6)}-${startDate.slice(6)}&end_date=${endDate.slice(0, 4)}-${endDate.slice(4, 6)}-${endDate.slice(6)}&page_size=100`,
    ),
  })
  const rows = useMemo(() => {
    const raw = query.data?.items ?? query.data?.stocks ?? query.data?.data ?? []
    return Array.isArray(raw) ? (raw as Row[]) : []
  }, [query.data])
  const filteredRows = useMemo(() => {
    const search = keyword.trim().toLowerCase()
    if (!search) return rows
    return rows.filter((row) => [
      codeOf(row), row.name, row.stock_name, row.industry, row.market, row.theme, row.reason,
    ].some((value) => String(value ?? "").toLowerCase().includes(search)))
  }, [keyword, rows])
  const rowKey = (row: Row, index: number) => `${dateOf(row, isLimit)}:${codeOf(row)}:${index}`
  const client = useQueryClient()
  const submitRows = async (chosen: Row[]) => {
    if (!isLimit) {
      return [
        await post<JobAccepted>("/ipos/analyses", {
          codes: [...new Set(chosen.map(codeOf).filter(Boolean))],
          trade_date: endDate,
        }),
      ]
    }
    const groups = new Map<string, Set<string>>()
    for (const row of chosen) {
      const tradeDate = dateOf(row, true)
      if (!tradeDate) continue
      const codes = groups.get(tradeDate) ?? new Set<string>()
      codes.add(codeOf(row))
      groups.set(tradeDate, codes)
    }
    return Promise.all(
      [...groups.entries()].map(([tradeDate, codes]) =>
        post<JobAccepted>("/limit-ups/analyses", {
          codes: [...codes].filter(Boolean),
          trade_date: tradeDate,
        }),
      ),
    )
  }
  const analyze = useMutation({
    mutationFn: submitRows,
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  })
  const watch = useMutation({
    mutationFn: (instrumentId: number) => post("/watchlists/default/items", { instrument_id: instrumentId, tag_names: [] }),
  })
  const toggle = (key: string) => {
    setSelected((old) => {
      const next = new Set(old)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }
  const changeRange = (next: ResearchDateRange) => {
    setRange(next)
    setSelected(new Set())
  }
  const selectedRows = rows.filter((row, index) => selected.has(rowKey(row, index)))
  const openReports = () => document.getElementById("event-analysis-history")?.scrollIntoView({ behavior: "smooth" })

  return (
    <div className="page-stack">
      <PageHeader
        title={isLimit ? "涨停分析" : "新股分析"}
        description={
          isLimit
            ? "保留涨停频次、封板数据、主升浪评分和分析状态，支持跨交易日批量研究。"
            : "保留发行价、发行市盈率、潜力评分和历史分析状态。"
        }
        help={isLimit ? "limit-up-analysis" : "ipo-analysis"}
        meta={
          <>
            <span>研究周期 {startDate}—{endDate}</span>
            <span>共 {rows.length} 条记录</span>
          </>
        }
        actions={
          <Button onClick={() => analyze.mutate(selectedRows)} disabled={!selected.size || analyze.isPending}>
            <Play />
            {analyze.isPending ? "正在提交" : `分析已选（${selected.size}）`}
          </Button>
        }
      />
      <section className="panel filter-panel date-filter-panel event-filter-panel">
        <div>
          <span className="field-label">研究日期范围</span>
          <ResearchDateRangePicker value={range} onChange={changeRange} />
        </div>
        <label className="event-search">
          <span className="field-label">搜索列表</span>
          <div className="inline-search"><Search size={15} /><Input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder={isLimit ? "代码、名称、行业、题材或涨停原因" : "代码、名称、行业或市场"} /></div>
        </label>
        <Button variant="outline" onClick={() => query.refetch()} disabled={query.isFetching}>
          <RefreshCw className={query.isFetching ? "spin" : ""} />
          刷新列表
        </Button>
      </section>
      {query.isLoading ? <LoadingPanel rows={8} /> : null}
      {query.error ? <ErrorPanel error={query.error} retry={() => query.refetch()} /> : null}
      {filteredRows.length ? (
        <section className="panel table-panel event-table-panel">
          <div className="panel-title">
            <span>{isLimit ? "全量 A 股涨停名单" : "新股列表"}</span>
            <Badge>{filteredRows.length}</Badge>
          </div>
          <div className="table-scroll">
            <table className="event-table">
              <thead>
                {isLimit ? (
                  <tr>
                    <th className="check-col" /><th>股票</th><th>涨停日期</th><th>7日</th><th>30日</th><th>90日</th><th>180日</th><th>市场</th><th>行业 / 题材</th><th>涨停原因</th><th>连板</th><th>换手率</th><th>封单</th><th>日线主升浪</th><th>周线主升浪</th><th>综合分</th><th>状态</th><th>操作</th>
                  </tr>
                ) : (
                  <tr>
                    <th className="check-col" /><th>代码</th><th>名称</th><th>上市日期</th><th>行业</th><th>市场</th><th>发行价</th><th>发行 PE</th><th>10倍评分</th><th>100倍评分</th><th>状态</th><th>操作</th>
                  </tr>
                )}
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const originalIndex = rows.indexOf(row)
                  const code = codeOf(row)
                  const key = rowKey(row, originalIndex)
                  const analysis = analysisOf(row)
                  const instrumentId = numberOf(row.instrument_id)
                  return (
                    <tr key={key} onClick={() => toggle(key)} className={selected.has(key) ? "row-selected" : ""}>
                      <td><input type="checkbox" aria-label={`选择 ${code}`} checked={selected.has(key)} onChange={() => toggle(key)} onClick={(event) => event.stopPropagation()} /></td>
                      {isLimit ? (
                        <>
                          <td><strong className="event-code">{code || "—"}</strong><small>{String(row.name ?? row.stock_name ?? "—")}</small></td>
                          <td>{displayDate(dateOf(row, true))}</td>
                          <td className="tone-up-text">{String(row.limit_up_count_7d ?? 0)}</td>
                          <td>{String(row.limit_up_count_30d ?? 0)}</td>
                          <td>{String(row.limit_up_count_90d ?? 0)}</td>
                          <td>{String(row.limit_up_count_180d ?? 0)}</td>
                          <td>{String(row.market ?? "—")}</td>
                          <td>{String(row.industry ?? "—")}<small>{String(row.theme ?? "")}</small></td>
                          <td className="event-reason" title={String(row.reason ?? "")}>{String(row.reason ?? "—")}</td>
                          <td><Badge variant="outline" className={(numberOf(row.consecutive_boards) ?? 1) >= 2 ? "status-failed" : ""}>{String(row.consecutive_boards ?? 1)} 板</Badge></td>
                          <td>{fixed(row.turnover_rate, 1, "%")}</td>
                          <td>{money(row.limit_order)}</td>
                          <td><AnalysisScore score={analysis.day_score} classification={analysis.day_classification} /></td>
                          <td><AnalysisScore score={analysis.week_score} classification={analysis.week_classification} /></td>
                          <td><AnalysisScore score={analysis.overall_score} /></td>
                          <td><AnalysisStatus status={analysis.status} /></td>
                        </>
                      ) : (
                        <>
                          <td><strong className="event-code">{code || "—"}</strong></td>
                          <td>{String(row.name ?? row.stock_name ?? "—")}</td>
                          <td>{displayDate(dateOf(row, false))}</td>
                          <td>{String(row.industry ?? "—")}</td>
                          <td>{String(row.market ?? "—")}</td>
                          <td>{fixed(row.ipo_price ?? row.issue_price, 2)}</td>
                          <td>{fixed(row.ipo_pe ?? row.issue_pe, 1)}</td>
                          <td><AnalysisScore score={analysis.ten_x_score} scale={10} /></td>
                          <td><AnalysisScore score={analysis.hundred_x_score} scale={10} /></td>
                          <td><AnalysisStatus status={analysis.status} /></td>
                        </>
                      )}
                      <td onClick={(event) => event.stopPropagation()}>
                        <div className="event-row-actions">
                          <Button variant="ghost" size="sm" onClick={() => analyze.mutate([row])} disabled={analyze.isPending}><TrendingUp />{analysis.run_id ? "重分析" : "分析"}</Button>
                          {analysis.run_id ? <Button variant="ghost" size="sm" onClick={openReports}><Eye />报告</Button> : null}
                          {instrumentId ? <Button variant="ghost" size="sm" asChild><Link to={`/stocks?id=${instrumentId}`}>K 线</Link></Button> : null}
                          {instrumentId ? <Button variant="ghost" size="sm" onClick={() => watch.mutate(instrumentId)}><BookmarkPlus />自选</Button> : null}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : !query.isLoading && !query.error ? (
        <EmptyPanel title={isLimit ? "该周期暂无涨停记录" : "该周期暂无新股"} detail="可调整开始和结束日期，或刷新外部数据源。" />
      ) : null}
      <div id="event-analysis-history">
        <AnalysisHistoryPanel history={history.data} error={history.error} retry={() => history.refetch()} title={isLimit ? "涨停研究报告" : "新股研究报告"} />
      </div>
    </div>
  )
}