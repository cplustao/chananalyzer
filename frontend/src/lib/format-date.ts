const BACKEND_DATETIME_WITHOUT_ZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

export function formatDate(value?: string | null) {
  if (!value) return "—"
  if (DATE_ONLY.test(value)) return value
  const normalized =
    BACKEND_DATETIME_WITHOUT_ZONE.test(value) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
      ? `${value}Z`
      : value
  const date = new Date(normalized)
  return Number.isNaN(date.valueOf())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })
}