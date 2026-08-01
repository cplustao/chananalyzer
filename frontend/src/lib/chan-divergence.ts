import { normalizeTradingDate } from "@/lib/trading-date"
import type { ChanLine } from "@/types/api"

export type ChanDivergence = {
  kind: "top" | "bottom"
  from: { date: string; price: number; macd: number }
  to: { date: string; price: number; macd: number }
}

export function detectChanDivergences(lines: ChanLine[]): ChanDivergence[] {
  const tops: ChanDivergence["from"][] = []
  const bottoms: ChanDivergence["from"][] = []
  for (const line of lines) {
    if (line.macd == null || !Number.isFinite(line.macd)) continue
    const direction = line.dir.toLowerCase()
    const point = {
      date: normalizeTradingDate(line.end_date),
      price: line.end_price,
      macd: line.macd,
    }
    if (direction.includes("上") || direction === "up") tops.push(point)
    if (direction.includes("下") || direction === "down") bottoms.push(point)
  }

  const divergences: ChanDivergence[] = []
  for (let index = 1; index < tops.length; index += 1) {
    const previous = tops[index - 1]
    const current = tops[index]
    if (current.price > previous.price && current.macd < previous.macd) {
      divergences.push({ kind: "top", from: previous, to: current })
    }
  }
  for (let index = 1; index < bottoms.length; index += 1) {
    const previous = bottoms[index - 1]
    const current = bottoms[index]
    if (current.price < previous.price && current.macd > previous.macd) {
      divergences.push({ kind: "bottom", from: previous, to: current })
    }
  }
  return divergences
}
