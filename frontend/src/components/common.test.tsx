import { describe, expect, it } from "vitest"
import { formatDate } from "@/lib/format-date"

describe("formatDate", () => {
  it("treats timezone-less backend datetimes as UTC and displays Shanghai time", () => {
    expect(formatDate("2026-07-30T03:02:21.490419")).toContain("11:02:21")
    expect(formatDate("2026-07-30T11:02:21+08:00")).toContain("11:02:21")
  })

  it("keeps business dates date-only", () => {
    expect(formatDate("2026-07-30")).toBe("2026-07-30")
  })
})