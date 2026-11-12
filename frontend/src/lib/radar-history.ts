import type { RadarHistoryItem } from "@/types/api"

// More than this many sessions between two snapshots is a gap, drawn as a break.
const MAX_SESSION_GAP = 3
const DAY_MS = 86_400_000

export type Point = [string, number | null]

function isGap(previous: RadarHistoryItem, current: RadarHistoryItem) {
  const sessions = current.executable_position?.session_gap
  if (typeof sessions === "number") return sessions > MAX_SESSION_GAP
  // Without a session count, more than ten calendar days apart is a gap even across a holiday.
  return (Date.parse(current.trade_date) - Date.parse(previous.trade_date)) / DAY_MS > 10
}

/** One point per snapshot, with a null between snapshots that are not consecutive sessions. */
export function withGaps(items: RadarHistoryItem[], value: (item: RadarHistoryItem) => number | null): Point[] {
  const points: Point[] = []
  items.forEach((item, index) => {
    if (index > 0 && isGap(items[index - 1], item)) points.push([item.trade_date, null])
    points.push([item.trade_date, value(item)])
  })
  return points
}
