export function normalizeTradingDate(value: string) {
  return value.slice(0, 10).replaceAll("/", "-")
}
