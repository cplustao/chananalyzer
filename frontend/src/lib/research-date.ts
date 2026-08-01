import { format } from "date-fns"

export function compactDate(date: Date) {
  return format(date, "yyyyMMdd")
}