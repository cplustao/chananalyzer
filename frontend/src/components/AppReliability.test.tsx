import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AppErrorBoundary } from "./AppErrorBoundary"
import { GlobalSearch } from "./layout/AppShell"

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } })
}

function LocationView() {
  const location = useLocation()
  return <output>{location.pathname}{location.search}</output>
}

afterEach(() => vi.unstubAllGlobals())

describe("frontend reliability controls", () => {
  it("debounces global search and supports keyboard selection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      items: [
        { id: 1, code: "600519", ts_code: null, exchange: "SH", name: "贵州茅台", area: null, status: "active" },
        { id: 2, code: "600000", ts_code: null, exchange: "SH", name: "浦发银行", area: null, status: "active" },
      ],
      total: 2,
      page: 1,
      page_size: 8,
    }))
    vi.stubGlobal("fetch", fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <GlobalSearch />
          <Routes><Route path="*" element={<LocationView />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const input = screen.getByRole("combobox", { name: "全局股票搜索" })
    await user.type(input, "600")
    expect(fetchMock).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2), { timeout: 1500 })
    await user.keyboard("{ArrowDown}{Enter}")
    expect(screen.getByText("/stocks?id=2")).toBeInTheDocument()
  })

  it("shows a recoverable route-level fallback", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined)
    function Broken(): never { throw new Error("render failed") }
    render(<AppErrorBoundary><Broken /></AppErrorBoundary>)
    expect(screen.getByRole("alert")).toHaveTextContent("页面暂时无法显示")
    expect(screen.getByRole("link", { name: "返回市场雷达" })).toHaveAttribute("href", "/radar")
    consoleError.mockRestore()
  })
})