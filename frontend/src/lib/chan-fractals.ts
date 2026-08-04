import { normalizeTradingDate } from "@/lib/trading-date"
import type { ChanLine } from "@/types/api"

export type ChanFractal = {
  date: string
  price: number
  kind: "top" | "bottom"
  confirmed: boolean
}

export function fractalsFromStrokes(lines: ChanLine[]): ChanFractal[] {
  if (!lines.length) return []
  const points = new Map<string, ChanFractal>()
  const add = (date: string, price: number, kind: "top" | "bottom", confirmed: boolean) => {
    const normalized = normalizeTradingDate(date)
    points.set(`${normalized}:${kind}`, { date: normalized, price, kind, confirmed })
  }
  const first = lines[0]
  const firstUp = first.dir === "向上" || first.dir.toLowerCase() === "up"
  add(first.start_date, first.start_price, firstUp ? "bottom" : "top", first.is_sure !== false)
  for (const line of lines) {
    const up = line.dir === "向上" || line.dir.toLowerCase() === "up"
    add(line.end_date, line.end_price, up ? "top" : "bottom", line.is_sure !== false)
  }
  return [...points.values()].sort((left, right) => left.date.localeCompare(right.date))
}
