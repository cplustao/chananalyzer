import { describe, expect, it } from "vitest"
import type { RadarHistoryItem } from "@/types/api"
import { withGaps } from "@/lib/radar-history"

function item(trade_date: string, score: number, session_gap?: number): RadarHistoryItem {
  return {
    trade_date, score, algorithm_version: "2.2", status: "neutral", status_label: "震荡", calculated_at: trade_date,
    executable_position: session_gap === undefined ? null : ({ decision_score: score, session_gap } as RadarHistoryItem["executable_position"]),
  }
}

describe("withGaps", () => {
  it("breaks the line where sessions are missing instead of joining months apart", () => {
    const points = withGaps([item("2026-09-18", 79, 1), item("2026-11-06", 30, 33), item("2026-11-09", 57, 1)], (row) => row.score)

    expect(points).toEqual([["2026-09-18", 79], ["2026-11-06", null], ["2026-11-06", 30], ["2026-11-09", 57]])
  })

  it("keeps consecutive sessions joined across a long holiday", () => {
    const points = withGaps([item("2026-09-30", 50, 1), item("2026-10-08", 52, 1)], (row) => row.score)

    expect(points).toHaveLength(2)
  })

  it("falls back to calendar days when the session count is unknown", () => {
    const points = withGaps([item("2026-07-30", 36), item("2026-08-26", 57)], (row) => row.score)

    expect(points[1]).toEqual(["2026-08-26", null])
  })
})
