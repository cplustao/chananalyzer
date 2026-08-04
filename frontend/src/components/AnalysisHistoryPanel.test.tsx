import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { AnalysisHistoryPanel } from "./AnalysisHistoryPanel"
import type { AnalysisDetail, AnalysisPage } from "@/types/api"

const mocks = vi.hoisted(() => ({ api: vi.fn() }))

vi.mock("@/lib/api", () => ({ api: mocks.api }))

const history = {
  items: [{
    id: "run-1",
    kind: "stock",
    instrument: { id: 1, code: "600519", name: "贵州茅台" },
    subject_code: "600519",
    subject_name: "贵州茅台",
    subject_date: "2026-08-03",
    status: "completed",
    algorithm_version: "chan-test",
    report_count: 3,
    overall_score: null,
    created_at: "2026-08-04T00:00:00Z",
    finished_at: "2026-08-04T00:00:01Z",
  }],
  total: 1,
  page: 1,
  page_size: 30,
} as unknown as AnalysisPage

const detail = {
  ...history.items[0],
  config_snapshot: { include_ai: true },
  input_snapshot: {
    code: "600519", input_freshness: "fresh",
    realtime_quote: { status: "fresh" }, money_flow: { status: "fresh" },
  },
  reports: [
    {
      id: "report-1",
      role: "analyst",
      provider: "fake",
      model: "fake-model",
      prompt_version: "test",
      content: "## 结论\n\n保持观察，等待有效买点确认。",
      status: "completed",
      created_at: "2026-08-04T00:00:01Z",
      validation_status: "validated",
    },
    {
      id: "report-3",
      role: "decision",
      provider: "fake",
      model: "fake-model",
      prompt_version: "test",
      content: "## 综合结论\n\n谨慎观察，等待有效买点确认。",
      status: "completed",
      created_at: "2026-08-04T00:00:02Z",
      validation_status: "validated",
    },
  ],
  metrics: null,
} as unknown as AnalysisDetail

describe("AnalysisHistoryPanel", () => {
  beforeEach(() => {
    mocks.api.mockReset()
    mocks.api.mockResolvedValue(detail)
  })

  it("automatically opens and renders the AI report returned by a completed job", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <AnalysisHistoryPanel history={history} autoOpenRunId="run-1" />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith("/analyses/run-1"))
    expect(await screen.findByText("研究报告详情")).toBeInTheDocument()
    expect(await screen.findByText("保持观察，等待有效买点确认。", {}, { timeout: 5_000 })).toBeInTheDocument()
    expect(await screen.findByText("最新 AI 综合结论")).toBeInTheDocument()
    expect((await screen.findAllByText("谨慎观察，等待有效买点确认。")).length).toBeGreaterThan(0)
  })
})
