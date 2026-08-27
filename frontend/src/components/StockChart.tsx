import { useEffect, useRef, useState } from "react"
import type { ECharts } from "echarts/core"
import type { Bar, ChanAnalysis, ChanLine } from "@/types/api"
import { normalizeTradingDate } from "@/lib/trading-date"
import { detectChanDivergences } from "@/lib/chan-divergence"
import { fractalsFromStrokes } from "@/lib/chan-fractals"

export type ChanLayerVisibility = {
  bi: boolean
  fractals: boolean
  segments: boolean
  centers: boolean
  signals: boolean
  divergence: boolean
}


function lineSeriesData(lines: ChanLine[], dates: string[]) {
  const points = new Map<string, number>()
  for (const line of lines) {
    points.set(normalizeTradingDate(line.start_date), line.start_price)
    points.set(normalizeTradingDate(line.end_date), line.end_price)
  }
  return dates.map((date) => points.get(date) ?? null)
}
function ema(values: number[], period: number) {
  if (!values.length) return []
  const factor = 2 / (period + 1)
  const result = [values[0]]
  for (let index = 1; index < values.length; index += 1) {
    result.push(values[index] * factor + result[index - 1] * (1 - factor))
  }
  return result
}

function macd(values: number[]) {
  const fast = ema(values, 12)
  const slow = ema(values, 26)
  const dif = values.map((_, index) => fast[index] - slow[index])
  const dea = ema(dif, 9)
  return {
    dif,
    dea,
    histogram: dif.map((value, index) => (value - dea[index]) * 2),
  }
}

export function CandlestickChart({
  bars,
  analysis,
  layers,
}: {
  bars: Bar[]
  analysis?: ChanAnalysis | null
  layers: ChanLayerVisibility
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setChartError(null)
    void import("@/lib/stock-charts").then(({ echarts }) => {
      if (cancelled || !ref.current) return
      const created = chartRef.current === null
      const chart = chartRef.current ?? echarts.init(ref.current)
      chartRef.current = chart
      const ordered = [...bars]
      const dates = ordered.map((bar) => normalizeTradingDate(bar.bar_time))
      const centers = analysis?.zs_list ?? []
      const buySignals = analysis?.buy_signals ?? []
      const sellSignals = analysis?.sell_signals ?? []
      const macdValues = macd(ordered.map((bar) => bar.close))
      const divergences = detectChanDivergences(analysis?.bi_list ?? [])
      const fractals = fractalsFromStrokes(analysis?.bi_list ?? [])

      const option = {
        animation: false,
        color: ["#f0b23e", "#377dff"],
        grid: [
          { left: 52, right: 18, top: 25, height: "52%" },
          { left: 52, right: 18, top: "61%", height: "11%" },
          { left: 52, right: 18, top: "76%", height: "13%" },
        ],
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross" },
          backgroundColor: "#141b26",
          borderColor: "#3a2b32",
          textStyle: { color: "#d8e1ed" },
        },
        xAxis: [0, 1, 2].map((_, index) => ({
          type: "category",
          gridIndex: index,
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: "#263244" } },
          axisLabel: { color: "#738096", show: index === 2, hideOverlap: true },
        })),
        yAxis: [
          {
            scale: true,
            splitLine: { lineStyle: { color: "#1d2735" } },
            axisLabel: { color: "#738096" },
          },
          {
            gridIndex: 1,
            scale: true,
            splitNumber: 2,
            splitLine: { show: false },
            axisLabel: { color: "#738096" },
          },
          {
            gridIndex: 2,
            scale: true,
            splitNumber: 3,
            splitLine: { lineStyle: { color: "#1d2735" } },
            axisLabel: { color: "#738096" },
          },
        ],
        ...(created ? { dataZoom: [
          {
            type: "inside",
            xAxisIndex: [0, 1, 2],
            start: Math.max(0, 100 - 12000 / Math.max(ordered.length, 1)),
            end: 100,
          },
          {
            show: true,
            xAxisIndex: [0, 1, 2],
            type: "slider",
            bottom: 4,
            height: 16,
            borderColor: "#3b2930",
            fillerColor: "rgba(239,68,68,.13)",
            handleStyle: { color: "#dc5963" },
            textStyle: { color: "#738096" },
          },
        ] } : {}),
        series: [
          {
            name: "K 线",
            type: "candlestick",
            data: ordered.map((bar) => [bar.open, bar.close, bar.low, bar.high]),
            itemStyle: {
              color: "#ef4444",
              color0: "#22c55e",
              borderColor: "#ef4444",
              borderColor0: "#22c55e",
            },
            markArea: layers.centers
              ? {
                  silent: true,
                  itemStyle: {
                    color: "rgba(226,165,54,.10)",
                    borderColor: "rgba(226,165,54,.58)",
                    borderWidth: 1,
                  },
                  data: centers.map((center) => [
                    {
                      name: `中枢 ${center.idx}`,
                      xAxis: normalizeTradingDate(center.start_date),
                      yAxis: center.low,
                    },
                    {
                      xAxis: normalizeTradingDate(center.end_date),
                      yAxis: center.high,
                    },
                  ]),
                }
              : { data: [] },
            markLine: layers.divergence
              ? {
                  symbol: "none",
                  silent: true,
                  animation: false,
                  lineStyle: { width: 1.6, type: "dashed", opacity: 0.9 },
                  label: { show: true, position: "middle", fontSize: 10, fontWeight: "bold" },
                  data: divergences.map((divergence) => {
                    const label = divergence.kind === "top" ? "顶背离" : "底背离"
                    const color = divergence.kind === "top" ? "#22c55e" : "#ef4444"
                    return [
                      {
                        coord: [divergence.from.date, divergence.from.price],
                        lineStyle: { color },
                      },
                      {
                        coord: [divergence.to.date, divergence.to.price],
                        name: label,
                        label: { color, formatter: label },
                      },
                    ]
                  }),
                }
              : { data: [] },
            markPoint: layers.signals
              ? {
                  symbolSize: 38,
                  data: [
                    ...buySignals.map((signal) => ({
                      name: `${signal.type}买`,
                      coord: [normalizeTradingDate(signal.date), signal.price],
                      symbol: "triangle",
                      symbolOffset: [0, 12],
                      itemStyle: { color: "#ef4444" },
                      label: { color: "#fff", formatter: `${signal.type}买`, fontSize: 9 },
                    })),
                    ...sellSignals.map((signal) => ({
                      name: `${signal.type}卖`,
                      coord: [normalizeTradingDate(signal.date), signal.price],
                      symbol: "triangle",
                      symbolRotate: 180,
                      symbolOffset: [0, -12],
                      itemStyle: { color: "#22c55e" },
                      label: { color: "#fff", formatter: `${signal.type}卖`, fontSize: 9 },
                    })),
                  ],
                }
              : { data: [] },
          },
          {
            name: "成交量",
            type: "bar",
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: ordered.map((bar) => ({
              value: bar.volume,
              itemStyle: {
                color: bar.close >= bar.open
                  ? "rgba(239,68,68,.58)"
                  : "rgba(34,197,94,.58)",
              },
            })),
          },
          ...(layers.fractals && fractals.length
            ? [
                {
                  name: "顶分型",
                  type: "scatter" as const,
                  data: fractals.filter((item) => item.kind === "top").map((item) => ({
                    value: [item.date, item.price],
                    symbol: "triangle",
                    symbolRotate: 180,
                    symbolSize: item.confirmed ? 11 : 8,
                    itemStyle: { color: item.confirmed ? "#a855f7" : "#7e5a91", opacity: item.confirmed ? 1 : .65 },
                  })),
                  z: 12,
                },
                {
                  name: "底分型",
                  type: "scatter" as const,
                  data: fractals.filter((item) => item.kind === "bottom").map((item) => ({
                    value: [item.date, item.price],
                    symbol: "triangle",
                    symbolSize: item.confirmed ? 11 : 8,
                    itemStyle: { color: item.confirmed ? "#38bdf8" : "#557b8c", opacity: item.confirmed ? 1 : .65 },
                  })),
                  z: 12,
                },
              ]
            : []),
          ...(layers.bi && analysis?.bi_list?.length
            ? [{
                name: "笔",
                type: "line" as const,
                data: lineSeriesData(analysis.bi_list, dates),
                connectNulls: true,
                symbol: "none",
                showSymbol: false,
                lineStyle: { color: "#f2b744", width: 1.7 },
                z: 8,
              }]
            : []),
          ...(layers.segments && analysis?.seg_list?.length
            ? [{
                name: "线段",
                type: "line" as const,
                data: lineSeriesData(analysis.seg_list, dates),
                connectNulls: true,
                symbol: "none",
                showSymbol: false,
                lineStyle: { color: "#377dff", width: 2.5 },
                z: 9,
              }]
            : []),
          {
            name: "MACD",
            type: "bar",
            xAxisIndex: 2,
            yAxisIndex: 2,
            data: macdValues.histogram.map((value) => ({
              value,
              itemStyle: { color: value >= 0 ? "rgba(239,68,68,.65)" : "rgba(34,197,94,.65)" },
            })),
          },
          {
            name: "DIF",
            type: "line",
            xAxisIndex: 2,
            yAxisIndex: 2,
            data: macdValues.dif,
            symbol: "none",
            lineStyle: { color: "#f0a526", width: 1.2 },
          },
          {
            name: "DEA",
            type: "line",
            xAxisIndex: 2,
            yAxisIndex: 2,
            data: macdValues.dea,
            symbol: "none",
            lineStyle: { color: "#4c83ff", width: 1.2 },
          },
        ],
      }
      chart.setOption(option, { notMerge: false, lazyUpdate: true, replaceMerge: ["series"] })
    }).catch((error: unknown) => {
      if (!cancelled) setChartError(error instanceof Error ? error.message : "图表组件加载失败")
    })

    return () => { cancelled = true }
  }, [analysis, bars, layers])

  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(() => chartRef.current?.resize())
    observer.observe(ref.current)
    return () => {
      observer.disconnect()
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  if (chartError) {
    return <div className="empty-chart" role="alert">图表暂时无法显示：{chartError}</div>
  }
  return (
    <div
      ref={ref}
      className="chart chart-tall"
      role="img"
      aria-label="股票日 K 线、成交量、MACD、缠论结构与背离标记图"
    />
  )
}
