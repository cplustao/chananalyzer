import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { Search, Star, Trash2, TrendingUp } from "lucide-react"
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader } from "@/components/common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, put } from "@/lib/api"
import type { Watchlist, WatchlistItem } from "@/types/api"
import "./WatchlistPage.css"

type EditableField = "tags" | "note"
type EditingTarget = { item: WatchlistItem; field: EditableField } | null
const researchStatusLabels: Record<string, string> = { pending: "待验证", watching: "观察中", confirmed: "已确认", invalidated: "已失效" }

function tagsText(tags: string[]) {
  return tags.join("，")
}

export function WatchlistPage() {
  const [filter, setFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("all")
  const [editing, setEditing] = useState<EditingTarget>(null)
  const client = useQueryClient()
  const watchlist = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => api<Watchlist>("/watchlists/default"),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api<void>(`/watchlists/default/items/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["watchlist"] }),
  })
  const visibleItems = useMemo(() => {
    const keyword = filter.trim().toLowerCase()
    return (watchlist.data?.items ?? []).filter((item) =>
      (statusFilter === "all" || item.research_status === statusFilter) &&
      (!keyword ||
      [
        item.instrument.code,
        item.instrument.name,
        item.instrument.industry,
        item.note,
        item.thesis,
        item.confirmation_trigger,
        item.invalidation_condition,
        item.next_action,
        ...item.tags,
      ].some((value) => String(value ?? "").toLowerCase().includes(keyword))),
    )
  }, [filter, statusFilter, watchlist.data?.items])
  const startEditing = (item: WatchlistItem, field: EditableField) => setEditing({ item, field })
  const keyboardEdit = (event: React.KeyboardEvent, item: WatchlistItem, field: EditableField) => {
    if (event.key !== "Enter" && event.key !== " ") return
    event.preventDefault()
    startEditing(item, field)
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="自选股"
        description="用投资逻辑、确认与失效条件管理研究对象，并按复盘日期推动下一步动作。"
        help="watchlist"
        meta={
          <>
            <span>{watchlist.data?.items.length ?? 0} 只股票</span>
            <span>结构化字段仅记录研究计划，不产生交易指令</span>
          </>
        }
      />
      <section className="panel filter-panel">
        <div className="inline-search watchlist-search">
          <Search size={15} />
          <Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索代码、名称、标签或备注" />
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="watchlist-status-filter" aria-label="按研究状态筛选"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(researchStatusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
        </Select>
      </section>
      {watchlist.isLoading ? <LoadingPanel rows={6} /> : null}
      {watchlist.error ? <ErrorPanel error={watchlist.error} retry={() => watchlist.refetch()} /> : null}
      {visibleItems.length ? (
        <section className="panel table-panel">
          <div className="panel-title"><span><Star size={17} />{watchlist.data?.name ?? "默认自选"}</span><Badge>{visibleItems.length}</Badge></div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>股票</th><th>状态 / 复盘</th><th>投资逻辑</th><th>下一步动作</th><th /></tr></thead>
              <tbody>
                {visibleItems.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.instrument.code}</strong><div className="muted">{item.instrument.name ?? "—"}</div></td>
                    <td><Badge variant="outline" className={`research-${item.research_status}`}>{researchStatusLabels[item.research_status] ?? item.research_status}</Badge><div className="muted">{item.next_review_date ? `复盘 ${item.next_review_date}` : "未设复盘日"}</div></td>
                    <td className="watchlist-note">
                      <div
                        className="editable-watchlist-cell"
                        role="button"
                        tabIndex={0}
                        title="双击编辑研究计划；键盘按 Enter"
                        aria-label={`编辑 ${item.instrument.code} ${item.instrument.name ?? ""} 的研究计划`}
                        onDoubleClick={() => startEditing(item, "note")}
                        onKeyDown={(event) => keyboardEdit(event, item, "note")}
                      >
                        {item.thesis || item.note || <span className="muted">尚未填写投资逻辑</span>}
                        <div className="tag-list">{item.tags.map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}</div>
                      </div>
                    </td>
                    <td className="watchlist-note">
                      <div
                        className="editable-watchlist-cell"
                        role="button"
                        tabIndex={0}
                        title="双击编辑研究备注；键盘按 Enter"
                        aria-label={`编辑 ${item.instrument.code} ${item.instrument.name ?? ""} 的研究备注`}
                        onDoubleClick={() => startEditing(item, "note")}
                        onKeyDown={(event) => keyboardEdit(event, item, "note")}
                      >
                        {item.next_action || <span className="muted">暂无下一步动作</span>}
                        {item.invalidation_condition ? <div className="muted">失效：{item.invalidation_condition}</div> : null}
                      </div>
                    </td>
                    <td>
                      <div className="row-actions">
                        <Button variant="ghost" size="sm" asChild><Link to={`/stocks?id=${item.instrument.id}`}><TrendingUp />研究</Link></Button>
                        <Button variant="ghost" size="sm" className="danger-action" onClick={() => remove.mutate(item.id)} disabled={remove.isPending}><Trash2 />移除</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : !watchlist.isLoading ? <EmptyPanel title="没有匹配的自选股" detail={filter ? "请调整搜索条件。" : "可在个股分析页把股票加入自选。"} /> : null}
      <WatchlistEditor target={editing} onClose={() => setEditing(null)} />
    </div>
  )
}

function WatchlistEditor({ target, onClose }: { target: EditingTarget; onClose: () => void }) {
  const item = target?.item ?? null
  const [draft, setDraft] = useState({ id: "", note: "", tags: "", thesis: "", confirmation: "", invalidation: "", action: "", reviewDate: "", status: "watching" })
  const client = useQueryClient()
  const activeDraft = item?.id === draft.id
    ? draft
    : {
        id: item?.id ?? "", note: item?.note ?? "", tags: tagsText(item?.tags ?? []),
        thesis: item?.thesis ?? "", confirmation: item?.confirmation_trigger ?? "",
        invalidation: item?.invalidation_condition ?? "", action: item?.next_action ?? "",
        reviewDate: item?.next_review_date ?? "", status: item?.research_status ?? "watching",
      }
  const save = useMutation({
    mutationFn: () => put<Watchlist>(`/watchlists/default/items/${item?.id}`, {
      note: activeDraft.note.trim() || null,
      tag_names: activeDraft.tags.split(/[\s,，]+/).map((value) => value.trim()).filter(Boolean),
      position: item?.position,
      thesis: activeDraft.thesis.trim() || null,
      confirmation_trigger: activeDraft.confirmation.trim() || null,
      invalidation_condition: activeDraft.invalidation.trim() || null,
      next_action: activeDraft.action.trim() || null,
      next_review_date: activeDraft.reviewDate || null,
      research_status: activeDraft.status,
    }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["watchlist"] })
      onClose()
    },
  })
  const updateDraft = (key: keyof typeof activeDraft, value: string) => {
    setDraft({ ...activeDraft, [key]: value })
  }
  return (
    <Sheet open={Boolean(item)} onOpenChange={(open) => { if (!open) onClose() }}>
      <SheetContent className="editor-sheet">
        <SheetHeader><SheetTitle>编辑自选研究信息</SheetTitle></SheetHeader>
        {item ? (
          <div className="editor-form">
            <div className="selected-stock"><strong>{item.instrument.code} {item.instrument.name}</strong><span>{item.instrument.industry ?? "—"}</span></div>
            <label>
              <span>研究标签</span>
              <Input
                autoFocus={target?.field === "tags"}
                value={activeDraft.tags}
                onChange={(event) => updateDraft("tags", event.target.value)}
                placeholder="例如：核心观察，三买候选"
              />
            </label>
            <label>
              <span>研究状态</span>
              <Select value={activeDraft.status} onValueChange={(value) => updateDraft("status", value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{Object.entries(researchStatusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
            </label>
            <label>
              <span>投资逻辑</span>
              <Textarea value={activeDraft.thesis} onChange={(event) => updateDraft("thesis", event.target.value)} placeholder="为什么值得持续跟踪？" rows={3} />
            </label>
            <label>
              <span>确认条件</span>
              <Textarea value={activeDraft.confirmation} onChange={(event) => updateDraft("confirmation", event.target.value)} placeholder="出现什么事实才升级判断？" rows={2} />
            </label>
            <label>
              <span>失效条件</span>
              <Textarea value={activeDraft.invalidation} onChange={(event) => updateDraft("invalidation", event.target.value)} placeholder="什么变化意味着原逻辑不成立？" rows={2} />
            </label>
            <label>
              <span>下一步动作</span>
              <Input value={activeDraft.action} onChange={(event) => updateDraft("action", event.target.value)} placeholder="例如：等待日线三买并复核成交量" />
            </label>
            <label>
              <span>下次复盘日期</span>
              <Input type="date" value={activeDraft.reviewDate} onChange={(event) => updateDraft("reviewDate", event.target.value)} />
            </label>
            <label>
              <span>研究备注</span>
              <Textarea
                autoFocus={target?.field === "note"}
                value={activeDraft.note}
                onChange={(event) => updateDraft("note", event.target.value)}
                placeholder="记录加入原因、等待确认、失效条件和下一步动作"
                rows={8}
              />
              <small className="field-help">补充主观上下文；事实、确认条件和失效条件请分别填写，所有内容都不参与系统评分。</small>
            </label>
            <Button onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "保存中" : "保存研究信息"}</Button>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
