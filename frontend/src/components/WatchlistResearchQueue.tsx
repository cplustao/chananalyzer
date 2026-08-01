import { useState } from "react"
import { ChevronLeft, ChevronRight, Settings2, Star, Tags } from "lucide-react"
import { Link } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { Watchlist, WatchlistItem } from "@/types/api"
import "./WatchlistResearchQueue.css"
import "./WatchlistResearchQueueTags.css"

const ALL_FILTER = "__all__"
const UNTAGGED_FILTER = "__untagged__"

type WatchlistResearchQueueProps = {
  watchlist?: Watchlist
  selectedId: number
  isLoading?: boolean
  onSelect: (instrumentId: number) => void
  onWarmup?: (instrumentId: number) => void
}

function matchesTagFilter(item: WatchlistItem, filter: string) {
  if (filter === ALL_FILTER) return true
  if (filter === UNTAGGED_FILTER) return item.tags.length === 0
  return item.tags.includes(filter)
}

export function WatchlistResearchQueue({
  watchlist,
  selectedId,
  isLoading = false,
  onSelect,
  onWarmup,
}: WatchlistResearchQueueProps) {
  const [selectedTag, setSelectedTag] = useState(ALL_FILTER)
  const items = [...(watchlist?.items ?? [])].sort((left, right) => left.position - right.position)
  const tagCounts = new Map<string, number>()
  let untaggedCount = 0
  for (const item of items) {
    if (!item.tags.length) untaggedCount += 1
    for (const tag of item.tags) tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1)
  }
  const tags = [...tagCounts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], "zh-CN"))
  const filterAvailable = selectedTag === ALL_FILTER
    || selectedTag === UNTAGGED_FILTER && untaggedCount > 0
    || tagCounts.has(selectedTag)
  const activeTag = filterAvailable ? selectedTag : ALL_FILTER
  const visibleItems = items.filter((item) => matchesTagFilter(item, activeTag))
  const currentIndex = visibleItems.findIndex((item) => item.instrument.id === selectedId)
  const currentItem = items.find((item) => item.instrument.id === selectedId)
  const previous = visibleItems.length
    ? visibleItems[(currentIndex > 0 ? currentIndex : visibleItems.length) - 1]
    : undefined
  const next = visibleItems.length
    ? visibleItems[currentIndex >= 0 && currentIndex < visibleItems.length - 1 ? currentIndex + 1 : 0]
    : undefined
  const chooseTag = (filter: string) => {
    setSelectedTag(filter)
    const matches = items.filter((item) => matchesTagFilter(item, filter))
    if (matches.length && !matches.some((item) => item.instrument.id === selectedId)) {
      onSelect(matches[0].instrument.id)
    }
  }

  return (
    <section className="panel watchlist-research-queue" aria-label="自选研究队列">
      <div className="watchlist-queue-head">
        <div className="watchlist-queue-heading">
          <span><Star aria-hidden="true" />自选研究队列</span>
          <small>
            {visibleItems.length
              ? currentIndex >= 0
                ? `当前 ${currentIndex + 1} / ${visibleItems.length}${activeTag === ALL_FILTER ? "" : ` · 全部 ${items.length} 只`}`
                : `当前股票不在此标签 · ${visibleItems.length} 只`
              : "把常看的股票组成连续研究清单"}
          </small>
        </div>
        <div className="watchlist-queue-actions">
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="上一只自选股"
            disabled={!previous}
            onMouseEnter={() => previous && onWarmup?.(previous.instrument.id)}
            onFocus={() => previous && onWarmup?.(previous.instrument.id)}
            onClick={() => previous && onSelect(previous.instrument.id)}
          >
            <ChevronLeft />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="下一只自选股"
            disabled={!next}
            onMouseEnter={() => next && onWarmup?.(next.instrument.id)}
            onFocus={() => next && onWarmup?.(next.instrument.id)}
            onClick={() => next && onSelect(next.instrument.id)}
          >
            <ChevronRight />
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link to="/watchlist"><Settings2 />管理备注与标签</Link>
          </Button>
        </div>
      </div>

      {items.length ? (
        <div className="watchlist-tag-filters" role="group" aria-label="按研究标签筛选自选股">
          <span><Tags aria-hidden="true" />标签</span>
          <button
            type="button"
            className={activeTag === ALL_FILTER ? "selected" : ""}
            aria-pressed={activeTag === ALL_FILTER}
            aria-label={`筛选全部自选股，共 ${items.length} 只`}
            onClick={() => chooseTag(ALL_FILTER)}
          >
            全部 <small>{items.length}</small>
          </button>
          {tags.map(([tag, count]) => (
            <button
              type="button"
              key={tag}
              className={activeTag === tag ? "selected" : ""}
              aria-pressed={activeTag === tag}
              aria-label={`筛选标签：${tag}，共 ${count} 只`}
              onClick={() => chooseTag(tag)}
            >
              {tag} <small>{count}</small>
            </button>
          ))}
          {untaggedCount ? (
            <button
              type="button"
              className={activeTag === UNTAGGED_FILTER ? "selected" : ""}
              aria-pressed={activeTag === UNTAGGED_FILTER}
              aria-label={`筛选未设置标签的自选股，共 ${untaggedCount} 只`}
              onClick={() => chooseTag(UNTAGGED_FILTER)}
            >
              未标签 <small>{untaggedCount}</small>
            </button>
          ) : null}
        </div>
      ) : null}

      {isLoading ? <div className="watchlist-queue-empty">正在读取自选股…</div> : null}
      {!isLoading && !items.length ? (
        <div className="watchlist-queue-empty">自选股为空，可先用顶部搜索选择股票后加入。</div>
      ) : null}
      {visibleItems.length ? (
        <div className="watchlist-queue-track" role="group" aria-label="自选股票快捷切换">
          {visibleItems.map((item) => {
            const selected = item.instrument.id === selectedId
            return (
              <button
                type="button"
                aria-label={`${item.instrument.code} ${item.instrument.name ?? "未命名股票"}`}
                key={item.id}
                className={`watchlist-queue-item${selected ? " selected" : ""}`}
                aria-current={selected ? "true" : undefined}
                onMouseEnter={() => onWarmup?.(item.instrument.id)}
                onFocus={() => onWarmup?.(item.instrument.id)}
                onClick={() => onSelect(item.instrument.id)}
              >
                <strong>{item.instrument.code}</strong>
                <span>{item.instrument.name ?? "未命名股票"}</span>
                {item.tags.length ? <small>{item.tags.slice(0, 2).join(" · ")}</small> : null}
              </button>
            )
          })}
        </div>
      ) : null}

      {currentItem ? (
        <div className="watchlist-queue-context">
          <span>研究备注</span>
          <p>{currentItem.note || "暂无备注，可在自选股管理页补充加入原因、确认条件、风险位和下一步动作。"}</p>
          {currentItem.tags.map((tag) => <Badge key={tag} variant="outline">{tag}</Badge>)}
        </div>
      ) : null}
    </section>
  )
}
