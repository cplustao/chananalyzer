import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { Download, Eye, Flame, History, Play, RotateCw, Search, SlidersHorizontal, Sparkles } from "lucide-react"
import { EmptyPanel, PageHeader } from "@/components/common"
import { formatDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, post } from "@/lib/api"
import { jobKindLabel, jobStatusLabel } from "@/lib/labels"
import type { InstrumentFacets, Job, JobAccepted, ScanHistoryItem, ScanResult } from "@/types/api"

const signalLabels: Record<string, string> = {
  "1": "一类",
  "1p": "盘整背驰",
  "2": "二类",
  "2s": "类二类",
  "3a": "三类 A",
  "3b": "三类 B",
}

const rankLabels: Record<string, string> = {
  top_gainers: "涨幅榜",
  top_losers: "跌幅榜",
  top_amount: "成交额榜",
  top_volume: "成交量榜",
  top_turnover: "换手率榜",
  dragon_tiger: "龙虎榜",
}

function splitValues(value: string) {
  return value.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean)
}

type FacetItem = InstrumentFacets["industries"][number]

function FacetPicker({
  label,
  items,
  selected,
  onChange,
}: {
  label: string
  items: FacetItem[]
  selected: string[]
  onChange: (values: string[]) => void
}) {
  return (
    <label>
      <span>{label}{selected.length ? `（已选 ${selected.length}）` : ""}</span>
      <select
        multiple
        className="facet-select"
        value={selected}
        onChange={(event) => onChange(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}
      >
        {items.map((item) => <option key={item.value} value={item.value}>{item.value}（{item.count}）</option>)}
      </select>
    </label>
  )
}

export function ScansPage({ screener = false }: { screener?: boolean }) {
  const modes = screener ? (["hot", "smart"] as const) : (["buy", "sell"] as const)
  const [mode, setMode] = useState<string>(modes[0])
  const [codes, setCodes] = useState("")
  const [jobId, setJobId] = useState<string | null>(null)
  const [rankType, setRankType] = useState("top_gainers")
  const [scanSide, setScanSide] = useState("buy")
  const [industries, setIndustries] = useState<string[]>([])
  const [areas, setAreas] = useState<string[]>([])
  const [minNetFlow, setMinNetFlow] = useState("")
  const [minMainFlow, setMinMainFlow] = useState("")
  const [excludeSt, setExcludeSt] = useState(true)
  const [types, setTypes] = useState<string[]>(["1", "2", "3a", "3b"])
  const [resultFilter, setResultFilter] = useState("")
  const [selectedResult, setSelectedResult] = useState<ScanResult | null>(null)
  const client = useQueryClient()
  const history = useQuery({
    queryKey: ["scan-history"],
    queryFn: () => api<ScanHistoryItem[]>("/scans/history?limit=30"),
    refetchInterval: 5000,
  })
  const facets = useQuery({
    queryKey: ["instrument-facets"],
    queryFn: () => api<InstrumentFacets>("/instruments/facets"),
    staleTime: 10 * 60 * 1000,
  })
  const effectiveSide = mode === "buy" || mode === "sell" ? mode : mode === "hot" ? "buy" : scanSide
  const toggleType = (type: string) => {
    setTypes((current) =>
      current.includes(type) ? current.filter((item) => item !== type) : [...current, type],
    )
  }
  const submit = useMutation({
    mutationFn: () =>
      post<JobAccepted>("/scans", {
        mode,
        scan_side: effectiveSide,
        codes: splitValues(codes),
        types,
        industries,
        areas,
        min_net_mf_amount: minNetFlow === "" ? undefined : Number(minNetFlow),
        min_main_net_amount: minMainFlow === "" ? undefined : Number(minMainFlow),
        exclude_st: excludeSt,
        rank_type: rankType,
        top_n: 200,
      }),
    onSuccess: (data) => {
      setJobId(data.job_id)
      client.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      ["completed", "partial", "failed", "cancelled"].includes(query.state.data?.status ?? "")
        ? false
        : 1800,
  })
  const results = useQuery({
    queryKey: ["job-results", jobId],
    queryFn: () => api<ScanResult[]>(`/jobs/${jobId}/results`),
    enabled: Boolean(jobId) && ["completed", "partial"].includes(job.data?.status ?? ""),
  })
  const filteredResults = useMemo(() => {
    const keyword = resultFilter.trim().toLowerCase()
    if (!keyword) return results.data ?? []
    return (results.data ?? []).filter((item) =>
      [item.code, item.name, item.signal_type, item.scan_kind].some((value) =>
        String(value ?? "").toLowerCase().includes(keyword),
      ),
    )
  }, [resultFilter, results.data])
  const exportResults = () => {
    if (!filteredResults.length) return
    const rows = [
      ["排名", "代码", "名称", "信号", "日期", "评分"],
      ...filteredResults.map((item, index) => [
        item.rank ?? index + 1,
        item.code ?? "",
        item.name ?? "",
        item.signal_type ?? item.scan_kind,
        item.signal_date ?? "",
        item.score ?? "",
      ]),
    ]
    const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n")}`
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }))
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `chan-scan-${jobId ?? "results"}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const availableTypes = effectiveSide === "buy"
    ? ["1", "1p", "2", "3a", "3b"]
    : ["1", "1p", "2", "2s", "3a", "3b"]

  return (
    <div className="page-stack">
      <PageHeader
        title={screener ? "市场筛选" : "缠论扫描"}
        description={
          screener
            ? "热门聚焦行情榜单，智能筛选聚焦行业、地区与缠论条件；两者都在后台执行。"
            : "在后台批量识别买卖点，任务不会阻塞普通查询。"
        }
        help={screener ? "market-screening" : "chan-scanner"}
      />
      <section className="panel filter-panel">
        <Tabs value={mode} onValueChange={setMode}>
          <TabsList>
            {modes.map((item) => (
              <TabsTrigger key={item} value={item}>
                {({ buy: "买点", sell: "卖点", hot: "热门", smart: "智能筛选" } as Record<string, string>)[item]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {screener ? (
          <div className={`mode-explanation ${mode === "hot" ? "hot" : "smart"}`}>
            {mode === "hot" ? <Flame /> : <Sparkles />}
            <div>
              <strong>{mode === "hot" ? "热门：行情热度 → 缠论买点" : "智能筛选：研究条件 → 全市场缠论扫描"}</strong>
              <p>
                {mode === "hot"
                  ? "先从涨幅、成交额、换手率或龙虎榜取前 200 只，再扫描所选买点，适合捕捉当日资金焦点。"
                  : "先按行业、地区和 ST 条件缩小股票池，再选择买点或卖点类型，适合验证明确的研究假设。"}
              </p>
            </div>
          </div>
        ) : null}

        <div className="scan-options-grid">
          {mode === "hot" ? (
            <label>
              <span>热门排名依据</span>
              <Select value={rankType} onValueChange={setRankType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(rankLabels).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          ) : null}
          {mode === "smart" ? (
            <>
              <label>
                <span>扫描方向</span>
                <Select value={scanSide} onValueChange={setScanSide}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="buy">买点扫描</SelectItem>
                    <SelectItem value="sell">卖点扫描</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <FacetPicker
                label="行业（按 Ctrl/Cmd 可多选）"
                items={facets.data?.industries ?? []}
                selected={industries}
                onChange={setIndustries}
              />
              <FacetPicker
                label="地区（按 Ctrl/Cmd 可多选）"
                items={facets.data?.areas ?? []}
                selected={areas}
                onChange={setAreas}
              />
              <label>
                <span>近一日净流入下限（万元，可选）</span>
                <Input type="number" value={minNetFlow} onChange={(event) => setMinNetFlow(event.target.value)} placeholder="例如：1000" />
              </label>
              <label>
                <span>近一日主力净流入下限（万元，可选）</span>
                <Input type="number" value={minMainFlow} onChange={(event) => setMinMainFlow(event.target.value)} placeholder="例如：500" />
              </label>
            </>
          ) : null}
          {mode === "buy" || mode === "sell" ? (
            <label className="scan-codes-field">
              <span>股票范围（可选）</span>
              <Input value={codes} onChange={(event) => setCodes(event.target.value)} placeholder="留空扫描全市场；或输入 000001, 600519" />
            </label>
          ) : null}
        </div>

        <div className="signal-picker">
          <span>{effectiveSide === "buy" ? "买点类型" : "卖点类型"}</span>
          <div>
            {availableTypes.map((type) => (
              <Button
                key={`${effectiveSide}-${type}`}
                type="button"
                size="sm"
                variant={types.includes(type) ? "default" : "outline"}
                aria-pressed={types.includes(type)}
                onClick={() => toggleType(type)}
              >
                {signalLabels[type]}
              </Button>
            ))}
          </div>
        </div>
        {mode === "smart" ? (
          <Button
            type="button"
            size="sm"
            variant={excludeSt ? "default" : "outline"}
            aria-pressed={excludeSt}
            onClick={() => setExcludeSt((value) => !value)}
          >
            <SlidersHorizontal />{excludeSt ? "已排除 ST" : "包含 ST"}
          </Button>
        ) : null}
        <div className="scan-submit-row">
          <p className="muted">重复条件会复用活动任务；执行进度和失败明细可在右上角任务中心查看。</p>
          <Button onClick={() => submit.mutate()} disabled={submit.isPending || types.length === 0}>
            <Play />{submit.isPending ? "正在提交" : "开始任务"}
          </Button>
        </div>
      </section>

      {history.data?.length ? (
        <section className="panel table-panel scan-history-panel">
          <div className="panel-title"><span><History size={17} />最近扫描任务</span><Badge>{history.data.length}</Badge></div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>创建时间</th><th>类型</th><th>条件</th><th>状态</th><th>结果</th><th /></tr></thead>
              <tbody>
                {history.data.map((item) => (
                  <tr key={item.id} className={jobId === item.id ? "row-selected" : ""}>
                    <td>{formatDate(item.created_at)}</td>
                    <td>{jobKindLabel(item.kind)}</td>
                    <td className="muted scan-condition">{Object.entries(item.payload).filter(([, value]) => value !== false && value != null && String(value).length > 0).slice(0, 3).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("/") : String(value)}`).join(" · ") || "默认条件"}</td>
                    <td><Badge variant="outline" className={`status-${item.status}`}>{jobStatusLabel(item.status)}</Badge></td>
                    <td>{item.result_count}</td>
                    <td><Button variant="ghost" size="sm" onClick={() => setJobId(item.id)}>查看结果</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {job.data ? (
        <section className="stats-grid compact">
          <div className="stat-card">
            <span>任务</span>
            <strong className="small-value">{jobKindLabel(job.data.kind)}</strong>
            <small>{job.data.message ?? job.data.id}</small>
          </div>
          <div className="stat-card">
            <span>任务状态</span>
            <strong>{jobStatusLabel(job.data.status)}</strong>
            <small>{Math.round(job.data.progress)}%</small>
          </div>
          <div className="stat-card">
            <span>完成 / 失败</span>
            <strong>{job.data.completed} / {job.data.failed}</strong>
            <small>总数 {job.data.total || "—"}</small>
          </div>
          <div className="stat-card">
            <span>创建时间</span>
            <strong className="small-value">{formatDate(job.data.created_at)}</strong>
            <Button variant="ghost" size="sm" onClick={() => job.refetch()}><RotateCw />刷新</Button>
          </div>
        </section>
      ) : null}

      {filteredResults.length ? (
        <section className="panel table-panel">
          <div className="panel-title result-panel-title">
            <span>扫描结果 <Badge>{filteredResults.length}</Badge></span>
            <div><div className="inline-search result-search"><Search size={14} /><Input value={resultFilter} onChange={(event) => setResultFilter(event.target.value)} placeholder="筛选代码、名称或信号" /></div><Button variant="outline" size="sm" onClick={exportResults}><Download />导出 CSV</Button></div>
          </div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>排名</th><th>代码</th><th>名称</th><th>信号</th><th>日期</th><th>评分</th><th /></tr></thead>
              <tbody>
                {filteredResults.map((result, index) => (
                  <tr key={`${result.code}-${index}`}>
                    <td>{result.rank ?? index + 1}</td>
                    <td>{result.code}</td>
                    <td>{result.name}</td>
                    <td><Badge variant="outline">{result.signal_type ?? result.scan_kind}</Badge></td>
                    <td>{result.signal_date ?? "—"}</td>
                    <td>{result.score?.toFixed(2) ?? "—"}</td>
                    <td><Button type="button" variant="ghost" size="sm" onClick={() => setSelectedResult(result)}><Eye size={14} />查看详情</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : job.data && ["completed", "partial"].includes(job.data.status) ? (
        <EmptyPanel title="本次没有匹配结果" detail="可调整股票范围、行情榜单或信号类型后再次运行。" />
      ) : null}

      <Sheet open={Boolean(selectedResult)} onOpenChange={(open) => { if (!open) setSelectedResult(null) }}>
        <SheetContent className="job-sheet scan-result-sheet">
          <SheetHeader>
            <SheetTitle>扫描结果详情</SheetTitle>
          </SheetHeader>
          {selectedResult ? (
            <div className="scan-result-detail">
              <div className="scan-result-identity">
                <div><strong>{selectedResult.code || "未知代码"}</strong><span>{selectedResult.name || "未命名股票"}</span></div>
                <Badge variant="outline">{signalLabels[selectedResult.signal_type ?? ""] ?? selectedResult.signal_type ?? selectedResult.scan_kind}</Badge>
              </div>
              <dl>
                <div><dt>扫描类型</dt><dd>{selectedResult.scan_kind}</dd></div>
                <div><dt>信号日期</dt><dd>{selectedResult.signal_date ?? "—"}</dd></div>
                <div><dt>结果排名</dt><dd>{selectedResult.rank ?? "—"}</dd></div>
                <div><dt>综合评分</dt><dd>{selectedResult.score?.toFixed(2) ?? "—"}</dd></div>
              </dl>
              {selectedResult.payload && Object.keys(selectedResult.payload).length ? (
                <section>
                  <h3>规则证据与原始快照</h3>
                  <pre>{JSON.stringify(selectedResult.payload, null, 2)}</pre>
                </section>
              ) : <p className="muted">该结果没有附加规则快照。</p>}
              {selectedResult.instrument_id ? (
                <Button asChild><Link to={`/stocks?id=${selectedResult.instrument_id}`}>进入个股分析</Link></Button>
              ) : null}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>    </div>
  )
}
