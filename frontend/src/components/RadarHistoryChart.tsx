import { useEffect, useRef } from "react"
import type { ECharts } from "echarts/core"
import type { RadarHistoryItem } from "@/types/api"

export function RadarHistoryChart({ items }: { items: RadarHistoryItem[] }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let chart: ECharts | undefined
    let disposed = false
    const resize = () => chart?.resize()
    window.addEventListener("resize", resize)

    void import("@/lib/radar-charts").then(({ echarts }) => {
      if (disposed || !ref.current) return
      chart = echarts.init(ref.current)
      chart.setOption({
        animationDuration: 350,
        color: ["#f3b33d", "#e45b63", "#ef4444"],
        grid: { left: 42, right: 48, top: 45, bottom: 30 },
        legend: {
          top: 4,
          textStyle: { color: "#8998ab" },
          data: ["当日评分", "决策评分 MA5", "可执行仓位中枢"],
        },
        tooltip: {
          trigger: "axis",
          backgroundColor: "#141b26",
          borderColor: "#3a2b32",
          textStyle: { color: "#d8e1ed" },
          formatter: (params: Array<{ dataIndex: number }>) => {
            const index = params[0]?.dataIndex ?? 0
            const item = items[index]
            const position = item?.executable_position
            if (!item) return ""
            return [
              `<strong>${item.trade_date}</strong>`,
              `当日评分：${item.score.toFixed(1)}（${item.status_label}）`,
              `决策评分 MA5：${position?.decision_score?.toFixed(1) ?? "—"}`,
              `可执行仓位：${position ? `${Math.round(position.min * 100)}%–${Math.round(position.max * 100)}%` : "—"}`,
              `调仓动作：${position?.action === "increase" ? "提高一档" : position?.action === "decrease" ? "降低一档" : "保持"}`,
            ].join("<br/>")
          },
        },
        xAxis: {
          type: "category",
          data: items.map((item) => item.trade_date.slice(5)),
          axisLabel: { color: "#738096", hideOverlap: true },
          axisLine: { lineStyle: { color: "#263244" } },
        },
        yAxis: [
          {
            type: "value",
            min: 0,
            max: 100,
            name: "评分",
            nameTextStyle: { color: "#738096" },
            splitLine: { lineStyle: { color: "#1d2735" } },
            axisLabel: { color: "#738096" },
          },
          {
            type: "value",
            min: 0,
            max: 100,
            name: "仓位 %",
            nameTextStyle: { color: "#a97d83" },
            splitLine: { show: false },
            axisLabel: { color: "#a97d83", formatter: "{value}%" },
          },
        ],
        series: [
          {
            name: "当日评分",
            type: "line",
            data: items.map((item) => item.score),
            smooth: 0.12,
            showSymbol: false,
            lineStyle: { width: 1.5 },
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
            data: items.map((item) => item.executable_position?.decision_score ?? null),
            smooth: 0.3,
            showSymbol: false,
            lineStyle: { width: 2 },
          },
          {
            name: "可执行仓位中枢",
            type: "line",
            yAxisIndex: 1,
            step: "middle",
            data: items.map((item) => (item.executable_position?.mid ?? 0) * 100),
            showSymbol: false,
            lineStyle: { width: 2.5 },
            areaStyle: { color: "rgba(239,68,68,.08)" },
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
      aria-label="市场雷达评分与可执行仓位历史折线图"
    />
  )
}
