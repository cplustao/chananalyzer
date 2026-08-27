import * as echarts from "echarts/core"
import { BarChart, CandlestickChart, LineChart, ScatterChart } from "echarts/charts"
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkPointComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components"
import { CanvasRenderer } from "echarts/renderers"

echarts.use([
  LineChart,
  CandlestickChart,
  BarChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkPointComponent,
  MarkLineComponent,
  CanvasRenderer,
])

export { echarts }
