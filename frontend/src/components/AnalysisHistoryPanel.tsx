import { lazy, Suspense, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { FileText, ScrollText } from "lucide-react"
import { EmptyPanel, ErrorPanel, LoadingPanel } from "@/components/common"
import { formatDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { api } from "@/lib/api"
import { analysisKindLabel, analysisRoleLabel, jobStatusLabel } from "@/lib/labels"
import type { AnalysisDetail, AnalysisPage } from "@/types/api"

const MarkdownReport = lazy(() => import("@/components/MarkdownReport").then((module) => ({ default: module.MarkdownReport })))

export function AnalysisHistoryPanel({
  history,
  title = "研究报告",
  error,
  retry,
}: {
  history?: AnalysisPage
  title?: string
  error?: unknown
  retry?: () => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const detail = useQuery({
    queryKey: ["analysis-detail", selectedId],
    queryFn: () => api<AnalysisDetail>(`/analyses/${selectedId}`),
    enabled: Boolean(selectedId),
  })

  if (error) return <ErrorPanel error={error} retry={retry} />
  if (!history) return <LoadingPanel rows={4} />
  if (history.items.length === 0) {
    return <EmptyPanel title="暂无历史研究报告" detail="完成一次分析任务后，报告和输入快照会显示在这里。" />
  }

  return (
    <>
      <section className="panel table-panel">
        <div className="panel-title">
          <span><ScrollText size={17} />{title}</span>
          <Badge>{history.total}</Badge>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>研究日期</th><th>股票</th><th>类型</th><th>评分</th><th>报告</th><th>状态</th><th /></tr>
            </thead>
            <tbody>
              {history.items.map((run) => (
                <tr key={run.id}>
                  <td>{run.subject_date ?? "—"}</td>
                  <td>{run.instrument ? `${run.instrument.code} ${run.instrument.name ?? ""}` : `${run.subject_code ?? "—"} ${run.subject_name ?? ""}`}</td>
                  <td>{analysisKindLabel(run.kind)}</td>
                  <td>{run.overall_score?.toFixed(1) ?? "—"}</td>
                  <td>{run.report_count} 份</td>
                  <td><Badge variant="outline" className={`status-${run.status}`}>{jobStatusLabel(run.status)}</Badge></td>
                  <td><Button variant="ghost" size="sm" onClick={() => setSelectedId(run.id)}><FileText />查看</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <Sheet open={Boolean(selectedId)} onOpenChange={(open) => { if (!open) setSelectedId(null) }}>
        <SheetContent className="report-sheet">
          <SheetHeader>
            <SheetTitle>研究报告详情</SheetTitle>
          </SheetHeader>
          {detail.isLoading ? <LoadingPanel rows={6} /> : null}
          {detail.data ? <AnalysisDetailContent detail={detail.data} /> : null}
        </SheetContent>
      </Sheet>
    </>
  )
}

function AnalysisDetailContent({ detail }: { detail: AnalysisDetail }) {
  const metrics = detail.metrics
  return (
    <div className="report-detail">
      <div className="report-meta-grid">        <div><span>股票</span><strong>{detail.instrument ? [detail.instrument.code, detail.instrument.name].filter(Boolean).join(" ") : [detail.subject_code ?? "—", detail.subject_name].filter(Boolean).join(" ")}</strong></div>
        <div><span>研究日期</span><strong>{detail.subject_date ?? "—"}</strong></div>
        <div><span>算法版本</span><strong>{detail.algorithm_version}</strong></div>
        <div><span>完成时间</span><strong>{formatDate(detail.finished_at)}</strong></div>
      </div>
      {metrics ? (
        <section>
          <h3>量化评分</h3>
          <div className="score-chip-grid">
            {[
              ["综合", metrics.overall_score],
              ["日线", metrics.day_score],
              ["周线", metrics.week_score],
              ["价值", metrics.value_score],
              ["市场", metrics.market_score],
              ["择时", metrics.timing_score],
              ["风险", metrics.risk_score],
            ].map(([label, value]) => (
              <div key={String(label)}><span>{label}</span><strong>{typeof value === "number" ? value.toFixed(1) : "—"}</strong></div>
            ))}
          </div>
        </section>
      ) : null}
      {detail.reports.map((report) => (
        <article key={report.id} className="report-document">
          <div>
            <strong>{analysisRoleLabel(report.role)}</strong>
            <Badge variant="outline">{report.model ?? report.provider ?? "历史模型"}</Badge>
          </div>
          <Suspense fallback={<div className="report-content muted">正在排版报告…</div>}><MarkdownReport content={report.content} /></Suspense>
        </article>
      ))}
      <details className="snapshot-details">
        <summary>查看可复现输入快照</summary>
        <pre>{JSON.stringify(detail.input_snapshot ?? {}, null, 2)}</pre>
      </details>
    </div>
  )
}
