import { render, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { CandlestickChart, type ChanLayerVisibility } from "./StockChart"
import { fractalsFromStrokes } from "@/lib/chan-fractals"
import type { Bar } from "@/types/api"

const mocks = vi.hoisted(() => ({
  init: vi.fn(),
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))

vi.mock("@/lib/stock-charts", () => ({
  echarts: {
    init: mocks.init,
  },
}))

class ResizeObserverMock {
  observe = vi.fn()
  disconnect = vi.fn()
  constructor(_callback: ResizeObserverCallback) {}
}

const bars: Bar[] = [{
  bar_time: "2026-07-30T00:00:00",
  open: 10,
  high: 11,
  low: 9,
  close: 10.5,
  volume: 100,
  amount: null,
  turnover_rate: null,
}]
const layers: ChanLayerVisibility = { bi: true, fractals: true, segments: true, centers: true, signals: true, divergence: true }

describe("CandlestickChart lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal("ResizeObserver", ResizeObserverMock)
    mocks.init.mockReturnValue({ setOption: mocks.setOption, resize: mocks.resize, dispose: mocks.dispose })
  })

  it("initializes once, updates series, and disposes only on unmount", async () => {
    const view = render(<CandlestickChart bars={bars} layers={layers} />)
    await waitFor(() => expect(mocks.setOption).toHaveBeenCalledTimes(1))
    view.rerender(<CandlestickChart bars={bars} layers={{ ...layers, bi: false }} />)
    await waitFor(() => expect(mocks.setOption).toHaveBeenCalledTimes(2))
    expect(mocks.init).toHaveBeenCalledTimes(1)
    expect(mocks.setOption.mock.calls[1][1]).toMatchObject({ replaceMerge: ["series"] })
    expect(mocks.dispose).not.toHaveBeenCalled()
    view.unmount()
    expect(mocks.dispose).toHaveBeenCalledTimes(1)
  })
})

describe("fractal layer", () => {
  it("derives alternating top and bottom fractals from stroke endpoints", () => {
    const fractals = fractalsFromStrokes([
      {
        idx: 1, dir: "向上", start_date: "2026/07/28", end_date: "2026/07/29",
        start_price: 9, end_price: 12, is_sure: true,
      },
      {
        idx: 2, dir: "向下", start_date: "2026/07/29", end_date: "2026/07/30",
        start_price: 12, end_price: 10, is_sure: false,
      },
    ])

    expect(fractals).toEqual([
      { date: "2026-07-28", price: 9, kind: "bottom", confirmed: true },
      { date: "2026-07-29", price: 12, kind: "top", confirmed: true },
      { date: "2026-07-30", price: 10, kind: "bottom", confirmed: false },
    ])
  })
})
