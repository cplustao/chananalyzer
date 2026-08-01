import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, BookmarkPlus, Check, RefreshCw } from "lucide-react"
import { useSearchParams } from "react-router-dom"
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
  Job,
  JobAccepted,
  Watchlist,
} from "@/types/api"

const defaultLayers: ChanLayerVisibility = {
  bi: true,
  segments: true,
  centers: true,
  signals: true,
  divergence: true,
}

const layerLabels: Array<[keyof ChanLayerVisibility, string]> = [
  ["bi", "笔"],
  ["segments", "线段"],
  ["centers", "中枢"],
  ["signals", "买卖点"],
  ["divergence", "背离"],
]

export function StocksPage() {
  const [params, setParams] = useSearchParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [layers, setLayers] = useState<ChanLayerVisibility>(defaultLayers)
  const requested = useRef(new Set<number>())
  const selectedId = Number(params.get("id") || 0)
  const client = useQueryClient()
  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api<Watchlist>("/watchlists/default"),
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
  const analyze = useMutation<JobAccepted, Error, boolean>({
    mutationFn: (force) =>
      post<JobAccepted>(`/instruments/${selectedId}/analyses?force=${force}`, {}),
    onSuccess: (accepted) => {
      setJobId(accepted.job_id)
      client.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
  const analysisJob = useQuery({
    queryKey: ["stock-analysis-job", jobId],
    queryFn: () => api<Job>(`/jobs/${jobId}`),
    enabled: Boolean(jobId),
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
    analyze.mutate(false)
  }, [analyze, selectedId, structure.data, structure.isSuccess])

  useEffect(() => {
    if (!["completed", "partial"].includes(analysisJob.data?.status ?? "")) return
    void client.invalidateQueries({ queryKey: ["chan-structure", selectedId] })
  }, [analysisJob.data?.status, client, selectedId])

  useEffect(() => {
    setJobId(null)
    setLayers(defaultLayers)
  }, [selectedId])

  const toggleLayer = (layer: keyof ChanLayerVisibility) => {
    setLayers((current) => ({ ...current, [layer]: !current[layer] }))
  }
  const analysis = structure.data?.analysis
  const divergences = detectChanDivergences(analysis?.bi_list ?? [])
  const jobActive = ["queued", "running", "retrying"].includes(analysisJob.data?.status ?? "") || analyze.isPending
  const isWatched = watchlist.data?.items.some((item) => item.instrument.id === selectedId) ?? false
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
              <Button onClick={() => analyze.mutate(true)} disabled={jobActive}>
                <RefreshCw className={jobActive ? "spin" : ""} />
                {jobActive ? "计算缠论中" : "重新计算缠论"}
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
            {bars.data.adjustment === "NONE" ? (
              <section className="radar-notice" role="note">
                <AlertTriangle />
                <div><strong>行情口径：不复权</strong><p>当前股票暂无稳定前复权数据，图表和缠论按原始日线计算；跨除权日期比较时请留意价格跳变。</p></div>
              </section>
            ) : null}
            <section className="panel">
              <div className="panel-title kline-title">
                <span>
                  价格、成交量、MACD 与缠论结构
                  {structure.data?.algorithm_version ? <Badge variant="outline">{structure.data.algorithm_version}</Badge> : null}
                  {analysisJob.data ? <Badge variant="outline" className={`status-${analysisJob.data.status}`}>{jobStatusLabel(analysisJob.data.status)}</Badge> : null}
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
      {selectedId > 0 ? <AnalysisHistoryPanel history={history.data} error={history.error} retry={() => history.refetch()} title="个股研究记录" /> : null}
    </div>
  )
}
