import type { ReactNode } from "react"
import { AlertTriangle, CircleHelp, RefreshCw } from "lucide-react"
import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
export function PageHeader({ title, description, meta, actions, help }: { title: string; description: string; meta?: ReactNode; actions?: ReactNode; help?: string }) { return <header className="page-header"><div className="min-w-0"><div className="flex items-center gap-2"><h1>{title}</h1>{help && <Tooltip><TooltipTrigger asChild><Link aria-label="查看帮助" className="help-link" to={`/help?section=${help}`}><CircleHelp size={16}/></Link></TooltipTrigger><TooltipContent>打开本页说明</TooltipContent></Tooltip>}</div><p>{description}</p>{meta && <div className="page-meta">{meta}</div>}</div>{actions && <div className="page-actions">{actions}</div>}</header> }
export function LoadingPanel({ rows = 4 }: { rows?: number }) { return <div className="panel space-y-3" aria-label="正在加载">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-10 w-full bg-muted/50" />)}</div> }
export function ErrorPanel({ error, retry }: { error: unknown; retry?: () => void }) { return <div className="panel empty-state"><AlertTriangle className="text-amber-400"/><strong>暂时无法加载</strong><span>{error instanceof Error ? error.message : "未知错误"}</span>{retry && <Button variant="outline" size="sm" onClick={retry}><RefreshCw/>重试</Button>}</div> }
export function EmptyPanel({ title = "暂无数据", detail = "调整条件或刷新后再试。" }: { title?: string; detail?: string }) { return <div className="panel empty-state"><strong>{title}</strong><span>{detail}</span></div> }
export function StatCard({ label, value, detail, tone = "neutral" }: { label: string; value: ReactNode; detail?: ReactNode; tone?: "up" | "down" | "neutral" | "warn" }) { return <div className={`stat-card tone-${tone}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div> }
