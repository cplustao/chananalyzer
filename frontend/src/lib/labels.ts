const JOB_KIND_LABELS: Record<string, string> = {
  "market_radar.refresh": "重新计算市场雷达",
  "ipo.refresh": "刷新新股原始数据",
  "limit_up.refresh": "刷新涨停原始事件",
  "scan.buy": "缠论买点扫描",
  "scan.sell": "缠论卖点扫描",
  "screen.hot": "热门股票缠论扫描",
  "screen.smart": "智能条件筛选",
  "limit_up.analyze": "涨停股批量分析",
  "ipo.analyze": "新股批量分析",
  "stock.analyze": "个股缠论分析",
  "data.refresh": "行情数据更新",
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  retrying: "重试中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
}

const SYSTEM_STATUS_LABELS: Record<string, string> = {
  ok: "运行正常",
  healthy: "运行正常",
  ready: "已就绪",
  local: "本地免登录",
  admin: "管理员登录",
  sqlite: "SQLite 本地数据库",
  postgresql: "PostgreSQL 数据库",
  required: "独立进程",
  embedded: "内嵌运行",
  environment: "环境变量",
  database: "加密数据库",
  none: "未配置",
}

export const DATA_COUNT_LABELS: Record<string, string> = {
  instruments: "股票主数据",
  bars: "K 线数据",
  users: "研究用户",
  jobs: "后台任务",
  analysis_runs: "分析记录",
}

export const SECRET_LABELS: Record<string, string> = {
  TUSHARE_TOKEN: "Tushare 数据令牌",
  OPENAI_API_KEY: "OpenAI API 密钥",
  DEEPSEEK_API_KEY: "DeepSeek API 密钥",
  SILICONFLOW_API_KEY: "硅基流动 API 密钥",
}

export function jobKindLabel(value: string) {
  return JOB_KIND_LABELS[value] ?? value
}

export function jobStatusLabel(value: string) {
  return JOB_STATUS_LABELS[value] ?? value
}

export function systemLabel(value?: string | null) {
  if (!value) return "—"
  return SYSTEM_STATUS_LABELS[value.toLowerCase()] ?? value
}
const ANALYSIS_KIND_LABELS: Record<string, string> = {
  stock: "个股缠论",
  limit_up: "涨停分析",
  ipo: "新股分析",
}

const ANALYSIS_ROLE_LABELS: Record<string, string> = {
  analyst: "研究员报告",
  decision: "决策报告",
}

export function analysisKindLabel(value: string) {
  return ANALYSIS_KIND_LABELS[value] ?? value
}

export function analysisRoleLabel(value: string) {
  return ANALYSIS_ROLE_LABELS[value] ?? value
}
