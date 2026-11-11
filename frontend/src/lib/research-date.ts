import { format } from "date-fns"

export function compactDate(date: Date) {
  return format(date, "yyyyMMdd")
}

const beijingDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" })

/** Today in Beijing time as YYYY-MM-DD; toISOString() gives the UTC date, a day behind before 08:00. */
export function beijingToday(now: Date = new Date()) {
  return beijingDate.format(now)
}
