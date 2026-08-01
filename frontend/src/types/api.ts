import type { components } from "@/types/openapi"

type Schemas = components["schemas"]

export type Instrument = Schemas["InstrumentView"]
export type InstrumentPage = Schemas["InstrumentPage"]
export type Bar = Schemas["BarView"]
export type BarSeries = Schemas["BarSeries"]
export type Job = Schemas["JobView"]
export type JobItem = Schemas["JobItemView"]
export type JobAccepted = Schemas["JobAccepted"]
export type ScanResult = Schemas["ScanResultView"]
export type WatchlistItem = Schemas["WatchlistItemView"]
export type Watchlist = Schemas["WatchlistView"]
export type SystemStatus = Schemas["SystemStatus"]
export type DataHealthCategory = Schemas["DataHealthCategory"]
export type DataHealth = Schemas["DataHealthView"]
export type JobStage = Schemas["JobStageView"]
export type JobStages = Schemas["JobStagesView"]

export type ChanLine = {
  idx: number
  dir: string
  start_date: string
  end_date: string
  start_price: number
  end_price: number
  is_sure?: boolean
  macd?: number | null
}

export type ChanCenter = {
  idx: number
  start_date: string
  end_date: string
  high: number
  low: number
  center: number
  bi_count?: number
}

export type ChanSignal = {
  type: string
  is_buy: boolean
  date: string
  price: number
}

export type ChanAnalysis = {
  code?: string
  bi_list?: ChanLine[]
  seg_list?: ChanLine[]
  zs_list?: ChanCenter[]
  buy_signals?: ChanSignal[]
  sell_signals?: ChanSignal[]
  [key: string]: unknown
}

export type ChanStructureResponse = {
  instrument: Instrument
  analysis?: ChanAnalysis | null
  algorithm_version?: string | null
  calculated_at?: string | null
}
export type ExecutablePosition = {
  decision_score: number
  band: number
  status: string
  status_label: string
  min: number
  max: number
  mid: number
  action: "increase" | "decrease" | "hold"
  reason: string
  effective: string
}

export type RadarHistoryItem = {
  trade_date: string
  algorithm_version: string
  score: number
  status: string
  status_label: string
  executable_position?: ExecutablePosition | null
  component_scores?: Record<string, number | null>
  calculated_at: string
}

export type RadarComponent = {
  key: string
  label: string
  score: number
  summary?: string
  metrics?: Record<string, number | string | null>
}

export type RadarIndustry = {
  name: string
  limit_up_count: number
  highest_board: number
  amount_yi?: number
  previous_count?: number
  change?: number
  status?: string
}

export type RadarSnapshot = {
  trade_date?: string
  generated_at?: string
  source?: string
  algorithm_version?: string
  regime?: {
    status?: string
    status_label?: string
    score?: number
    position_range?: { min?: number; max?: number }
    instant_position_range?: { min?: number; max?: number }
    executable_position?: ExecutablePosition
  }
  coverage?: Record<string, number>
  breadth?: Record<string, number>
  liquidity?: Record<string, number>
  components?: RadarComponent[]
  methodology?: {
    trend_benchmark?: string
    trend_benchmark_label?: string
    bar_adjustments?: Record<string, number>
    notes?: string[]
  }
  conclusion?: string
  evidence?: unknown[]
  counter_evidence?: unknown[]
  contradictions?: unknown[]
  limit_ecology?: {
    trade_date?: string
    previous_trade_date?: string
    limit_up_count?: number
    previous_limit_up_count?: number
    highest_board?: number
    multi_board_count?: number
    one_word_count?: number
    industry_count?: number
    focus_industry_count?: number
    active_industry_count?: number
    industries?: RadarIndustry[]
    recent?: Array<Record<string, number | string>>
    source?: string
  }
  missing_fields?: string[]
  [key: string]: unknown
}
export type AnalysisRunSummary = {
  id: string
  kind: "stock" | "limit_up" | "ipo"
  instrument?: Instrument | null
  subject_code?: string | null
  subject_name?: string | null
  subject_date?: string | null
  status: string
  algorithm_version: string
  report_count: number
  overall_score?: number | null
  created_at: string
  finished_at?: string | null
}

export type AnalysisPage = {
  items: AnalysisRunSummary[]
  total: number
  page: number
  page_size: number
}

export type AnalysisReport = {
  id: string
  role: string
  provider?: string | null
  model?: string | null
  prompt_version?: string | null
  content: string
  status: string
  created_at: string
  structured_payload?: Record<string, unknown> | null
  input_digest?: string | null
  validation_status?: string
  validation_error?: string | null
}

export type AnalysisMetrics = {
  day_score?: number | null
  week_score?: number | null
  value_score?: number | null
  market_score?: number | null
  timing_score?: number | null
  risk_score?: number | null
  overall_score?: number | null
  day_classification?: string | null
  week_classification?: string | null
  completeness?: number | null
  evidence?: Record<string, unknown> | null
  risk_plan?: Record<string, unknown> | null
}

export type AnalysisDetail = AnalysisRunSummary & {
  config_snapshot?: Record<string, unknown> | null
  input_snapshot?: Record<string, unknown> | null
  error?: string | null
  reports: AnalysisReport[]
  metrics?: AnalysisMetrics | null
}



export type ScanHistoryItem = {
  id: string
  kind: string
  status: string
  progress: number
  result_count: number
  payload: Record<string, unknown>
  message?: string | null
  error?: string | null
  created_at: string
  finished_at?: string | null
}

export type HealthStatus = "fresh" | "partial" | "stale" | "missing"


export type ScanChangeState = "new_hit" | "continued_hit" | "no_longer_hit" | "not_comparable"
export type ScanChangeItem = {
  instrument_id?: number | null
  code?: string | null
  name?: string | null
  signal_type?: string | null
  signal_date?: string | null
  score?: number | null
  state: ScanChangeState
  current_job_id: string
  previous_job_id?: string | null
}
export type ScanChanges = {
  job_id: string
  previous_job_id?: string | null
  comparable: boolean
  reason?: string | null
  algorithm_version: string
  items: ScanChangeItem[]
}

export type ReviewContext = {
  mode: "next_session_preparation" | "historical_review" | "unavailable"
  freshness: HealthStatus
  usable_for_next_session: boolean
  as_of_trade_date?: string | null
  generated_at?: string | null
  expected_trade_date?: string | null
  applicable_to: string
  title: string
  message: string
  blocking_modules: string[]
}

export type MarketFact = { key: string; label: string; value: string; detail?: string | null }

export type RadarChange = {
  key: string
  label: string
  current: number
  previous: number
  delta: number
  unit: string
}

export type DecisionToday = {
  review_context: ReviewContext
  market_facts: MarketFact[]
  market_radar?: {
    id: number
    trade_date: string
    generated_at?: string | null
    algorithm_version: string
    freshness: HealthStatus
    data_time?: string | null
    source?: string | null
    coverage_rate?: number | null
    score: number
    status: string
    status_label: string
    executable_position?: ExecutablePosition | null
    changes: { previous_trade_date?: string | null; items: RadarChange[] }
    snapshot: RadarSnapshot
  } | null
  clustered_industries: { items: RadarIndustry[]; source?: string | null; model: string }
  scan_changes?: ScanChanges | null
  watchlist_hits?: Array<{ watchlist: string; item_id: string; instrument_id: number; change: ScanChangeItem }> | null
  data_health?: DataHealth | null
  attention_jobs?: { failed: Array<Pick<Job, "id" | "kind" | "status" | "message" | "created_at">>; pending: Array<Pick<Job, "id" | "kind" | "status" | "message" | "created_at">> } | null
  missing_or_stale_modules: string[]
}

export type BackupItem = { name: string; size: number; created_at: string }
export type BackupList = { mode: "application" | "external_required"; items: BackupItem[] }
export type BackupResult = { name: string; valid?: boolean; manifest?: Record<string, unknown>; size?: number }
