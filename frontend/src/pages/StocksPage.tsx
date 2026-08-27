import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, BookmarkPlus, Check, Database, RefreshCw, Sparkles } from "lucide-react"
import { Link, useSearchParams } from "react-router-dom"
import {
  CandlestickChart,
  type ChanLayerVisibility,
} from "@/components/StockChart"
import { AnalysisHistoryPanel } from "@/components/AnalysisHistoryPanel"
import { WatchlistResearchQueue } from "@/components/WatchlistResearchQueue"
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, StatCard } from "@/components/common"
import { formatDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, post } from "@/lib/api"
import { detectChanDivergences } from "@/lib/chan-divergence"
import { jobStatusLabel } from "@/lib/labels"
import type {
  AnalysisPage,
  BarSeries,
  ChanStructureResponse,
  DataHealth,
  Job,
  JobAccepted,
  Watchlist,
} from "@/types/api"

const defaultLayers: ChanLayerVisibility = {
  bi: true,
  fractals: true,
  segments: true,
  centers: true,
  signals: true,
  divergence: true,
}

const layerLabels: Array<[keyof ChanLayerVisibility, string]> = [
  ["bi", "笔"],
  ["fractals", "分型"],
  ["segments", "线段"],
  ["centers", "中枢"],
  ["signals", "买卖点"],
  ["divergence", "背离"],
]

export function StocksPage() {
  const [params, setParams] = useSearchParams()
  const [analysisJobId, setAnalysisJobId] = useState<string | null>(null)
  const [refreshJobId, setRefreshJobId] = useState<string | null>(null)
  const [analysisMode, setAnalysisMode] = useState<"chan" | "ai">("chan")
  const [autoOpenRunId, setAutoOpenRunId] = useState<string | null>(null)
  const [continueAiAfterRefresh, setContinueAiAfterRefresh] = useState(false)
  const handledRefreshJobs = useRef(new Set<string>())
  const [layers, setLayers] = useState<ChanLayerVisibility>(defaultLayers)
  const requested = useRef(new Set<number>())
  const selectedId = Number(params.get("id") || 0)
  const client = useQueryClient()
  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api<Watchlist>("/watchlists/default"),
    staleTime: 30_000,
  })
  const dataHealth = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api<DataHealth>("/system/data-health"),
    staleTime: 30_000,
  })
  const bars = useQuery({
    queryKey: ["bars", selectedId],
    queryFn: () => api<BarSeries>(`/instruments/${selectedId}/bars?limit=500`),
    enabled: selectedId > 0,
  })
  const structure = useQuery({
    queryKey: ["chan-structure", selectedId],
    queryFn: () => api<ChanStructureResponse>(`/instruments/${selectedId}/chan-structure`),
    enabled: selectedId > 0,
  })
  const history = useQuery({
    queryKey: ["analysis-history", "stock", selectedId],
    queryFn: () => api<AnalysisPage>(`/analyses?kind=stock&instrument_id=${selectedId}&page_size=30`),
    enabled: selectedId > 0,
  })
  const analyze = useMutation<
    JobAccepted,
    Error,
    { force: boolean; includeAi: boolean }
  >({
    mutationFn: ({ force, includeAi }) =>
      post<JobAccepted>(
        `/instruments/${selectedId}/analyses?force=${force}&include_ai=${includeAi}`,
        {},
      ),
    onSuccess: (accepted, variables) => {
      setAnalysisMode(variables.includeAi ? "ai" : "chan")
      setAnalysisJobId(accepted.job_id)
      client.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
  const refreshStock = useMutation<JobAccepted, Error>({
    mutationFn: () => post<JobAccepted>(`/instruments/${selectedId}/refresh`, {}),
    onSuccess: (accepted) => {
      setRefreshJobId(accepted.job_id)
      client.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
  const analysisJob = useQuery({
    queryKey: ["stock-analysis-job", analysisJobId],
    queryFn: () => api<Job>(`/jobs/${analysisJobId}`),
    enabled: Boolean(analysisJobId),
    refetchInterval: (query) =>
      ["completed", "partial", "failed", "cancelled"].includes(query.state.data?.status ?? "")
        ? false
        : 1200,
  })
  const refreshJob = useQuery({
    queryKey: ["stock-refresh-job", refreshJobId],
    queryFn: () => api<Job>(`/jobs/${refreshJobId}`),
    enabled: Boolean(refreshJobId),
    refetchInterval: (query) =>
      ["completed", "partial", "failed", "cancelled"].includes(query.state.data?.status ?? "")
        ? false
        : 1200,
  })
  const watch = useMutation({
    mutationFn: () => post("/watchlists/default/items", { instrument_id: selectedId, tag_names: [] }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
  })

  const firstWatchId = watchlist.data?.items
    .reduce(
      (first, item) => !first || item.position < first.position ? item : first,
      undefined as Watchlist["items"][number] | undefined,
    )
    ?.instrument.id

  useEffect(() => {
    if (selectedId || !firstWatchId) return
    setParams({ id: String(firstWatchId) }, { replace: true })
  }, [firstWatchId, selectedId, setParams])

  useEffect(() => {
    if (!selectedId || !structure.isSuccess || structure.data.analysis || requested.current.has(selectedId)) return
    requested.current.add(selectedId)
    analyze.mutate({ force: false, includeAi: false })
  }, [analyze, selectedId, structure.data, structure.isSuccess])

  useEffect(() => {
    if (!["completed", "partial"].includes(analysisJob.data?.status ?? "")) return
    void client.invalidateQueries({ queryKey: ["chan-structure", selectedId] })
    void client.invalidateQueries({ queryKey: ["analysis-history", "stock", selectedId] })
    const runId = analysisJob.data?.result?.analysis_run_id
    if (analysisMode === "ai" && typeof runId === "string") setAutoOpenRunId(runId)
  }, [analysisJob.data?.result, analysisJob.data?.status, analysisMode, client, selectedId])

  useEffect(() => {
    if (refreshJob.data?.status !== "completed" || !refreshJobId) return
    if (handledRefreshJobs.current.has(refreshJobId)) return
    handledRefreshJobs.current.add(refreshJobId)
    void client.invalidateQueries({ queryKey: ["bars", selectedId] })
    void client.invalidateQueries({ queryKey: ["data-health"] })
    const includeAi = continueAiAfterRefresh
    setContinueAiAfterRefresh(false)
    analyze.mutate({ force: true, includeAi })
  }, [analyze, client, continueAiAfterRefresh, refreshJob.data?.status, refreshJobId, selectedId])

  useEffect(() => {
    if (!continueAiAfterRefresh || !["partial", "failed", "cancelled"].includes(refreshJob.data?.status ?? "")) return
    setContinueAiAfterRefresh(false)
  }, [continueAiAfterRefresh, refreshJob.data?.status])

  useEffect(() => {
    setAnalysisJobId(null)
    setRefreshJobId(null)
    setAnalysisMode("chan")
    setAutoOpenRunId(null)
    setContinueAiAfterRefresh(false)
    setLayers(defaultLayers)
  }, [selectedId])

  const toggleLayer = (layer: keyof ChanLayerVisibility) => {
    setLayers((current) => ({ ...current, [layer]: !current[layer] }))
  }
  const analysis = structure.data?.analysis
  const divergences = detectChanDivergences(analysis?.bi_list ?? [])
  const analysisActive = ["queued", "running", "retrying"].includes(analysisJob.data?.status ?? "") || analyze.isPending
  const refreshActive = ["queued", "running", "retrying"].includes(refreshJob.data?.status ?? "") || refreshStock.isPending
  const selectedCode = bars.data?.instrument.code ?? ""
  const isCanonicalStock = /^\d{6}$/.test(selectedCode)
  const expectedTradeDate = dataHealth.data?.expected_trade_date ?? null
  const latestBarDate = bars.data?.items.at(-1)?.bar_time.slice(0, 10) ?? null
  const stockNeedsRefresh = Boolean(
    isCanonicalStock && expectedTradeDate && (!latestBarDate || latestBarDate < expectedTradeDate),
  )
  const startAiAnalysis = () => {
    if (!isCanonicalStock) return
    if (stockNeedsRefresh) {
      setAnalysisMode("ai")
      setContinueAiAfterRefresh(true)
      refreshStock.mutate()
      return
    }
    analyze.mutate({ force: true, includeAi: true })
  }
  const isWatched = watchlist.data?.items.some((item) => item.instrument.id === selectedId) ?? false
  const selectedWatchItem = watchlist.data?.items.find((item) => item.instrument.id === selectedId)
  const selectStock = (instrumentId: number) => setParams({ id: String(instrumentId) })
  const warmupStock = (instrumentId: number) => {
    if (instrumentId === selectedId) return
    void client.prefetchQuery({
      queryKey: ["bars", instrumentId],
      queryFn: () => api<BarSeries>(`/instruments/${instrumentId}/bars?limit=500`),
      staleTime: 30_000,
    })
    void client.prefetchQuery({
      queryKey: ["chan-structure", instrumentId],
      queryFn: () => api<ChanStructureResponse>(`/instruments/${instrumentId}/chan-structure`),
      staleTime: 30_000,
    })
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="个股分析"
        description="从自选研究队列连续切换股票，在同一工作区核对 K 线、MACD、缠论结构和历史研究。"
        help="stock-analysis"
        actions={
          selectedId > 0 ? (
            <>
              <Button variant="outline" onClick={() => watch.mutate()} disabled={isWatched || watch.isPending}>
                {isWatched ? <Check /> : <BookmarkPlus />}
                {isWatched ? "已在自选" : watch.isPending ? "加入中" : "加入自选"}
              </Button>
              <Button
                variant="outline"
                onClick={() => refreshStock.mutate()}
                disabled={!isCanonicalStock || refreshActive || analysisActive}
                title={isCanonicalStock ? "只同步当前股票最近 30 天行情" : "历史指数或非六位个股不支持单股更新"}
              >
                <Database />
                {refreshActive ? "更新本股中" : "更新本股数据"}
              </Button>
              <Button
                variant="outline"
                onClick={() => analyze.mutate({ force: true, includeAi: false })}
                disabled={analysisActive || refreshActive}
              >
                <RefreshCw className={analysisActive && analysisMode === "chan" ? "spin" : ""} />
                {analysisActive && analysisMode === "chan" ? "计算缠论中" : "重新计算缠论"}
              </Button>
              <Button
                onClick={startAiAnalysis}
                disabled={!isCanonicalStock || analysisActive || refreshActive}
                title={isCanonicalStock ? "数据过期时先更新本股，再生成分析师与独立风控复核报告" : "请选择六位 A 股代码后再进行 AI 分析"}
              >
                <Sparkles className={(analysisActive && analysisMode === "ai") || (refreshActive && continueAiAfterRefresh) ? "spin" : ""} />
                {refreshActive && continueAiAfterRefresh ? "先更新数据 · " : null}
                {analysisActive && analysisMode === "ai" ? "AI 分析中" : "AI 分析"}
              </Button>
            </>
          ) : null
        }
      />
      <WatchlistResearchQueue
        watchlist={watchlist.data}
        selectedId={selectedId}
        isLoading={watchlist.isLoading}
        onSelect={selectStock}
        onWarmup={warmupStock}
      />
      <section className="stock-main">
        {!selectedId ? (
          <EmptyPanel title="请选择一只股票" detail="点击上方自选研究队列，或使用顶部全局股票搜索。" />
        ) : null}
        {selectedId > 0 && bars.isLoading ? <LoadingPanel rows={7} /> : null}
        {bars.error ? <ErrorPanel error={bars.error} retry={() => bars.refetch()} /> : null}
        {bars.data ? (
          <>
            <section className="stats-grid compact">
              <StatCard label="股票" value={`${bars.data.instrument.code} ${bars.data.instrument.name ?? ""}`} detail={bars.data.instrument.exchange} />
              <StatCard label="数据周期" value={bars.data.timeframe} detail={bars.data.adjustment === "NONE" ? "不复权" : bars.data.adjustment === "QFQ" ? "前复权" : bars.data.adjustment} />
              <StatCard label="K 线数量" value={bars.data.items.length} detail={`更新至 ${bars.data.items.at(-1)?.bar_time.slice(0, 10) ?? "—"}`} />
              <StatCard
                label="最近收盘"
                value={bars.data.items.at(-1)?.close?.toFixed(2) ?? "—"}
                tone={(bars.data.items.at(-1)?.close ?? 0) >= (bars.data.items.at(-1)?.open ?? 0) ? "up" : "down"}
              />
            </section>
            {!isCanonicalStock ? (
              <section className="radar-notice" role="note">
                <AlertTriangle />
                <div><strong>当前是历史非个股记录</strong><p>{selectedCode} 不属于规范六位 A 股代码，不能执行单股行情更新。请使用顶部搜索选择六位股票代码。</p></div>
              </section>
            ) : null}
            {bars.data.adjustment === "NONE" ? (
              <section className="radar-notice" role="note">
                <AlertTriangle />
                <div><strong>行情口径：不复权</strong><p>当前股票暂无稳定前复权数据，图表和缠论按原始日线计算；跨除权日期比较时请留意价格跳变。</p></div>
              </section>
            ) : null}
            {stockNeedsRefresh ? (
              <section className="radar-notice" role="note">
                <AlertTriangle />
                <div><strong>本股行情尚未更新至目标交易日</strong><p>当前截至 {latestBarDate ?? "暂无数据"}，目标为 {expectedTradeDate}。启动 AI 分析时会先更新本股行情，避免基于过期数据生成报告。</p></div>
              </section>
            ) : null}
            {analyze.error ? (
              <section className="radar-notice" role="alert">
                <AlertTriangle />
                <div><strong>AI 分析提交失败</strong><p>{analyze.error.message}</p></div>
              </section>
            ) : null}
            {analysisMode === "ai" && analysisJob.data?.status === "partial" ? (
              <section className="radar-notice" role="alert">
                <AlertTriangle />
                <div><strong>AI 分析仅部分完成</strong><p>确定性缠论结果已经保存，但 AI 报告或独立风控复核未全部通过校验，请在任务中心查看失败原因。</p></div>
              </section>
            ) : null}
            {analysisMode === "ai" && ["partial", "failed", "cancelled"].includes(refreshJob.data?.status ?? "") ? (
              <section className="radar-notice" role="alert">
                <AlertTriangle />
                <div><strong>行情更新失败，已停止 AI 分析</strong><p>{refreshJob.data?.error ?? refreshJob.data?.message ?? "请检查数据源后重试。"}</p></div>
              </section>
            ) : null}
            <section className="stock-research-summary">
              <article className="panel"><div className="panel-title">事实数据</div><p>行情截至 {latestBarDate ?? "—"} · {bars.data.adjustment === "QFQ" ? "前复权" : "不复权"}</p><p>最近收盘 {bars.data.items.at(-1)?.close?.toFixed(2) ?? "—"} · 成交量 {bars.data.items.at(-1)?.volume?.toLocaleString() ?? "—"} 股</p></article>
              <article className="panel"><div className="panel-title">规则推导</div><p>笔 {analysis?.bi_list?.length ?? 0} · 线段 {analysis?.seg_list?.length ?? 0} · 中枢 {analysis?.zs_list?.length ?? 0}</p><p>买点 {(analysis?.buy_signals?.length ?? 0)} · 卖点 {(analysis?.sell_signals?.length ?? 0)} · 背离 {divergences.length}</p></article>
              <article className="panel"><div className="panel-title"><span>个人研究计划</span>{selectedWatchItem ? <Button asChild variant="ghost" size="sm"><Link to="/watchlist">编辑</Link></Button> : null}</div>{selectedWatchItem ? <><p><strong>逻辑：</strong>{selectedWatchItem.thesis || selectedWatchItem.note || "待补充"}</p><p><strong>确认：</strong>{selectedWatchItem.confirmation_trigger || "待补充"}</p><p><strong>失效：</strong>{selectedWatchItem.invalidation_condition || "待补充"}</p><p><strong>下一步：</strong>{selectedWatchItem.next_action || "待补充"}</p></> : <p className="muted">加入自选后可记录投资逻辑、确认条件、失效条件和复盘动作。</p>}</article>
            </section>
            <section className="panel">
              <div className="panel-title kline-title">
                <span>
                  价格、成交量、MACD 与缠论结构
                  {structure.data?.algorithm_version ? <Badge variant="outline">{structure.data.algorithm_version}</Badge> : null}
                  {analysisJob.data ? <Badge variant="outline" className={`status-${analysisJob.data.status}`}>{analysisMode === "ai" ? "AI " : "缠论 "}{jobStatusLabel(analysisJob.data.status)}</Badge> : null}
                  {refreshJob.data ? <Badge variant="outline" className={`status-${refreshJob.data.status}`}>本股更新 {jobStatusLabel(refreshJob.data.status)}</Badge> : null}
                </span>
                <small>{structure.data?.calculated_at ? `结构计算 ${formatDate(structure.data.calculated_at)}` : "首次打开会自动计算缠论结构"}</small>
              </div>
              <div className="chart-toolbar" aria-label="缠论图层开关">
                {layerLabels.map(([key, label]) => (
                  <Button
                    key={key}
                    size="sm"
                    variant={layers[key] ? "default" : "outline"}
                    aria-pressed={layers[key]}
                    onClick={() => toggleLayer(key)}
                  >
                    {label}
                    {key === "bi" && analysis?.bi_list ? ` ${analysis.bi_list.length}` : ""}
                    {key === "segments" && analysis?.seg_list ? ` ${analysis.seg_list.length}` : ""}
                    {key === "centers" && analysis?.zs_list ? ` ${analysis.zs_list.length}` : ""}
                    {key === "signals" && analysis ? ` ${(analysis.buy_signals?.length ?? 0) + (analysis.sell_signals?.length ?? 0)}` : ""}
                    {key === "divergence" ? ` ${divergences.length}` : ""}
                  </Button>
                ))}
                <span className="muted">红涨绿跌 · 金色笔 · 蓝色线段 · 金框中枢 · 红买绿卖 · 红底背离/绿顶背离</span>
              </div>
              {bars.data.items.length ? (
                <CandlestickChart bars={bars.data.items} analysis={analysis} layers={layers} />
              ) : (
                <EmptyPanel title="暂无 v2 K 线" detail="完成全量迁移或运行数据刷新任务后显示。" />
              )}
            </section>
          </>
        ) : null}
      </section>
      {selectedId > 0 ? <AnalysisHistoryPanel history={history.data} error={history.error} retry={() => history.refetch()} title="个股研究记录" autoOpenRunId={autoOpenRunId} /> : null}
    </div>
  )
}
