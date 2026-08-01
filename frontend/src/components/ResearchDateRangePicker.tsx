import { useState } from "react"
import { endOfMonth, format, startOfMonth, subDays } from "date-fns"
import { zhCN } from "date-fns/locale"
import { CalendarDays } from "lucide-react"
import type { DateRange } from "react-day-picker"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export type ResearchDateRange = {
  from: Date
  to: Date
}

type Props = {
  value: ResearchDateRange
  onChange: (value: ResearchDateRange) => void
  className?: string
  maxDate?: Date
}

const presets = [
  { label: "近 7 日", days: 6 },
  { label: "近 30 日", days: 29 },
  { label: "近 90 日", days: 89 },
]

export function ResearchDateRangePicker({ value, onChange, className, maxDate = new Date() }: Props) {
  const [open, setOpen] = useState(false)
  const selected: DateRange = { from: value.from, to: value.to }
  const update = (range: DateRange | undefined) => {
    if (!range?.from) return
    const next = { from: range.from, to: range.to ?? range.from }
    onChange(next)
    if (range.to) setOpen(false)
  }
  const selectPreset = (from: Date, to: Date) => {
    onChange({ from, to })
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className={cn("date-range-trigger justify-start font-normal", className)}
          aria-label="选择研究日期范围"
        >
          <CalendarDays size={15} />
          <span>{format(value.from, "yyyy-MM-dd")}</span>
          <span className="muted">至</span>
          <span>{format(value.to, "yyyy-MM-dd")}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="date-range-popover w-auto p-0">
        <div className="date-range-presets">
          {presets.map((preset) => (
            <Button
              key={preset.label}
              size="sm"
              variant="ghost"
              onClick={() => selectPreset(subDays(maxDate, preset.days), maxDate)}
            >
              {preset.label}
            </Button>
          ))}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => selectPreset(startOfMonth(maxDate), endOfMonth(maxDate))}
          >
            本月
          </Button>
          <span>选择开始和结束日期，列表按整个区间查询</span>
        </div>
        <Calendar
          mode="range"
          selected={selected}
          onSelect={update}
          numberOfMonths={2}
          defaultMonth={value.from}
          disabled={{ after: maxDate }}
          locale={zhCN}
          formatters={{ formatCaption: (month) => format(month, "yyyy 年 M 月", { locale: zhCN }) }}
        />
      </PopoverContent>
    </Popover>
  )
}

