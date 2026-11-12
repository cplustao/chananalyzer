import { useEffect, useRef } from "react"
import type { ECharts } from "echarts/core"
import { withGaps, type Point } from "@/lib/radar-history"
import type { RadarHistoryItem } from "@/types/api"

export function RadarHistoryChart({ items }: { items: RadarHistoryItem[] }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let chart: ECharts | undefined
    let disposed = false
    const resize = () => chart?.resize()
    window.addEventListener("resize", resize)
    const byDate = new Map(items.map((item) => [item.trade_date, item]))
    const percent = (value: number | undefined | null) => (value === undefined || value === null ? null : Math.round(value * 100))

    void import("@/lib/radar-charts").then(({ echarts }) => {
      if (disposed || !ref.current) return
      chart = echarts.init(ref.current)
      chart.setOption({
        animationDuration: 350,
        grid: [
          { left: 46, right: 24, top: 40, height: "44%" },
          { left: 46, right: 24, top: "64%", bottom: 28 },
        ],
        legend: {
          top: 4,
          textStyle: { color: "#8998ab" },
          data: ["当日评分", "决策评分 MA5", "可执行仓位"],
        },
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        tooltip: {
          trigger: "axis",
          backgroundColor: "#141b26",
          borderColor: "#2b3a4c",
          textStyle: { color: "#d8e1ed" },
          formatter: (params: Array<{ value: Point }>) => {
            const date = params[0]?.value?.[0]
            const item = date ? byDate.get(date) : undefined
            if (!item) return ""
            const position = item.executable_position
            return [
              `<strong>${item.trade_date}</strong>${item.backfilled ? "（回填：事后按当时数据计算）" : ""}`,
              `当日评分：${item.score.toFixed(1)}（${item.status_label}）`,
              `决策评分 MA5：${position?.decision_score?.toFixed(1) ?? "—"}`,
              `可执行仓位：${position ? `${percent(position.min)}%–${percent(position.max)}%` : "—（覆盖不足）"}`,
              position?.reason ? `原因：${position.reason}` : "",
            ].filter(Boolean).join("<br/>")
          },
        },
        xAxis: [
          { type: "time", gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#263244" } } },
          { type: "time", gridIndex: 1, axisLabel: { color: "#738096", hideOverlap: true }, axisLine: { lineStyle: { color: "#263244" } } },
        ],
        yAxis: [
          {
            type: "value", gridIndex: 0, min: 0, max: 100, name: "评分（不是仓位）",
            nameTextStyle: { color: "#738096", align: "left" },
            splitLine: { lineStyle: { color: "#1d2735" } }, axisLabel: { color: "#738096" },
          },
          {
            type: "value", gridIndex: 1, min: 0, max: 100, name: "可执行仓位",
            nameTextStyle: { color: "#6aaee0", align: "left" },
            splitLine: { lineStyle: { color: "#1d2735" } },
            axisLabel: { color: "#6aaee0", formatter: "{value}%" },
          },
        ],
        series: [
          {
            name: "当日评分",
            type: "line",
            xAxisIndex: 0,
            yAxisIndex: 0,
            color: "#f3b33d",
            data: withGaps(items, (item) => item.score),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { width: 1.2 },
            markLine: {
              silent: true,
              symbol: "none",
              data: [{ yAxis: 68 }, { yAxis: 35 }],
              lineStyle: { color: "#3d3439", type: "dashed" },
            },
          },
          {
            name: "决策评分 MA5",
            type: "line",
            xAxisIndex: 0,
            yAxisIndex: 0,
            color: "#e45b63",
            data: withGaps(items, (item) => item.executable_position?.decision_score ?? null),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { width: 2 },
          },
          {
            // Invisible base of the stacked band: the lower edge of the position range.
            name: "仓位下沿",
            type: "line",
            xAxisIndex: 1,
            yAxisIndex: 1,
            stack: "position",
            step: "end",
            data: withGaps(items, (item) => percent(item.executable_position?.min)),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            tooltip: { show: false },
          },
          {
            name: "可执行仓位",
            type: "line",
            xAxisIndex: 1,
            yAxisIndex: 1,
            stack: "position",
            step: "end",
            color: "#4b9fd8",
            data: withGaps(items, (item) => {
              const position = item.executable_position
              return position ? Math.round((position.max - position.min) * 100) : null
            }),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { width: 0 },
            areaStyle: { color: "rgba(75,159,216,.35)" },
          },
          {
            name: "仓位中枢",
            type: "line",
            xAxisIndex: 1,
            yAxisIndex: 1,
            step: "end",
            color: "#7cc0ef",
            data: withGaps(items, (item) => percent(item.executable_position?.mid)),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { width: 1.5 },
          },
        ],
      })
    })

    return () => {
      disposed = true
      window.removeEventListener("resize", resize)
      chart?.dispose()
    }
  }, [items])

  return (
    <div
      ref={ref}
      className="chart radar-history-chart"
      role="img"
      aria-label="市场雷达评分（上）与可执行仓位区间（下）历史图，缺少数据的日期断开"
    />
  )
}
