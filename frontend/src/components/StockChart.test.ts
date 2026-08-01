import { describe, expect, it } from "vitest"
import { normalizeTradingDate } from "@/lib/trading-date"
import { detectChanDivergences } from "@/lib/chan-divergence"

describe("normalizeTradingDate", () => {
  it("aligns legacy Chan dates with bar-axis dates", () => {
    expect(normalizeTradingDate("2026/07/24")).toBe("2026-07-24")
    expect(normalizeTradingDate("2026-07-24T15:00:00")).toBe("2026-07-24")
  })
})

describe("detectChanDivergences", () => {
  it("restores the legacy top and bottom MACD divergence rules", () => {
    const divergences = detectChanDivergences([
      { idx: 0, dir: "向上", start_date: "2026/01/01", end_date: "2026/01/02", start_price: 8, end_price: 10, macd: 2 },
      { idx: 1, dir: "向下", start_date: "2026/01/02", end_date: "2026/01/03", start_price: 10, end_price: 8, macd: -2 },
      { idx: 2, dir: "向上", start_date: "2026/01/03", end_date: "2026/01/04", start_price: 8, end_price: 12, macd: 1 },
      { idx: 3, dir: "向下", start_date: "2026/01/04", end_date: "2026/01/05", start_price: 12, end_price: 7, macd: -1 },
    ])

    expect(divergences).toEqual([
      expect.objectContaining({ kind: "top", to: expect.objectContaining({ date: "2026-01-04" }) }),
      expect.objectContaining({ kind: "bottom", to: expect.objectContaining({ date: "2026-01-05" }) }),
    ])
  })
})