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
import { api, put } from "@/lib/api"
import type { Watchlist, WatchlistItem } from "@/types/api"
import "./WatchlistPage.css"

type EditableField = "tags" | "note"
type EditingTarget = { item: WatchlistItem; field: EditableField } | null

function tagsText(tags: string[]) {
  return tags.join("，")
}

export function WatchlistPage() {
  const [filter, setFilter] = useState("")
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
    if (!keyword) return watchlist.data?.items ?? []
    return (watchlist.data?.items ?? []).filter((item) =>
      [
        item.instrument.code,
        item.instrument.name,
        item.instrument.industry,
        item.note,
        ...item.tags,
      ].some((value) => String(value ?? "").toLowerCase().includes(keyword)),
    )
  }, [filter, watchlist.data?.items])
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
        description="集中管理研究对象、标签和研究备注，并快速返回个股缠论工作区。"
        help="watchlist"
        meta={
          <>
            <span>{watchlist.data?.items.length ?? 0} 只股票</span>
            <span>双击标签或研究备注可直接编辑</span>
            <span>备注与标签保存到 v2 数据库</span>
          </>
        }
      />
      <section className="panel filter-panel">
        <div className="inline-search watchlist-search">
          <Search size={15} />
          <Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索代码、名称、标签或备注" />
        </div>
      </section>
      {watchlist.isLoading ? <LoadingPanel rows={6} /> : null}
      {watchlist.error ? <ErrorPanel error={watchlist.error} retry={() => watchlist.refetch()} /> : null}
      {visibleItems.length ? (
        <section className="panel table-panel">
          <div className="panel-title"><span><Star size={17} />{watchlist.data?.name ?? "默认自选"}</span><Badge>{visibleItems.length}</Badge></div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>股票</th><th>行业 / 地区</th><th>标签（双击编辑）</th><th>研究备注（双击编辑）</th><th /></tr></thead>
              <tbody>
                {visibleItems.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.instrument.code}</strong><div className="muted">{item.instrument.name ?? "—"}</div></td>
                    <td>{item.instrument.industry ?? "—"}<div className="muted">{item.instrument.area ?? item.instrument.exchange ?? "—"}</div></td>
                    <td>
                      <div
                        className="editable-watchlist-cell"
                        role="button"
                        tabIndex={0}
                        title="双击编辑标签；键盘按 Enter"
                        aria-label={`编辑 ${item.instrument.code} ${item.instrument.name ?? ""} 的标签`}
                        onDoubleClick={() => startEditing(item, "tags")}
                        onKeyDown={(event) => keyboardEdit(event, item, "tags")}
                      >
                        <div className="tag-list">{item.tags.length ? item.tags.map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>) : <span className="muted">未设置</span>}</div>
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
                        {item.note || <span className="muted">暂无研究备注</span>}
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
  const [draft, setDraft] = useState<{ id: string; note: string; tags: string }>({ id: "", note: "", tags: "" })
  const client = useQueryClient()
  const activeDraft = item?.id === draft.id
    ? draft
    : { id: item?.id ?? "", note: item?.note ?? "", tags: tagsText(item?.tags ?? []) }
  const save = useMutation({
    mutationFn: () => put<Watchlist>(`/watchlists/default/items/${item?.id}`, {
      note: activeDraft.note.trim() || null,
      tag_names: activeDraft.tags.split(/[\s,，]+/).map((value) => value.trim()).filter(Boolean),
      position: item?.position,
    }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["watchlist"] })
      onClose()
    },
  })
  const updateDraft = (key: EditableField, value: string) => {
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
              <span>研究备注</span>
              <Textarea
                autoFocus={target?.field === "note"}
                value={activeDraft.note}
                onChange={(event) => updateDraft("note", event.target.value)}
                placeholder="记录加入原因、等待确认、失效条件和下一步动作"
                rows={8}
              />
              <small className="field-help">用于保存个人研究上下文，例如为什么加入自选、当前判断依据、风险位和下次复查动作；不参与系统评分，也不是交易信号。</small>
            </label>
            <Button onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "保存中" : "保存研究信息"}</Button>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
