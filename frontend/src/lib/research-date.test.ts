import { describe, expect, it } from "vitest"
import { beijingToday } from "./research-date"

describe("beijingToday", () => {
  it("uses the Beijing date before 08:00, when the UTC date is still yesterday", () => {
    expect(beijingToday(new Date("2026-11-10T23:30:00Z"))).toBe("2026-11-11")
  })

  it("matches the UTC date later in the day", () => {
    expect(beijingToday(new Date("2026-11-11T09:00:00Z"))).toBe("2026-11-11")
  })
})
