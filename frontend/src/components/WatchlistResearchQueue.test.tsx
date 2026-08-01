import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"
import { WatchlistResearchQueue } from "./WatchlistResearchQueue"
import type { Watchlist } from "@/types/api"

const watchlist: Watchlist = {
  id: "default",
  name: "默认自选",
  is_default: true,
  items: [
    {
      id: "watch-1",
      instrument: { id: 1, code: "600519", ts_code: null, exchange: null, name: "贵州茅台", area: null, status: "listed" },
      position: 1,
      note: "等待日线三买确认",
      tags: ["核心观察"],
    },
    {
      id: "watch-2",
      instrument: { id: 2, code: "000997", ts_code: null, exchange: null, name: "新大陆", area: null, status: "listed" },
      position: 2,
      note: null,
      tags: [],
    },
  ],
}

describe("WatchlistResearchQueue", () => {
  it("keeps watchlist research switching inside the stock workspace", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <MemoryRouter>
        <WatchlistResearchQueue watchlist={watchlist} selectedId={1} onSelect={onSelect} />
      </MemoryRouter>,
    )

    expect(screen.getByText("当前 1 / 2")).toBeInTheDocument()
    expect(screen.getByText("等待日线三买确认")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /600519 贵州茅台/ })).toHaveAttribute("aria-current", "true")

    await user.click(screen.getByRole("button", { name: "下一只自选股" }))
    expect(onSelect).toHaveBeenCalledWith(2)

    await user.click(screen.getByRole("button", { name: /000997 新大陆/ }))
    expect(onSelect).toHaveBeenLastCalledWith(2)

    await user.click(screen.getByRole("button", { name: "筛选标签：核心观察，共 1 只" }))
    expect(screen.queryByRole("button", { name: /000997 新大陆/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "筛选未设置标签的自选股，共 1 只" }))
    expect(onSelect).toHaveBeenLastCalledWith(2)
    expect(screen.getByRole("button", { name: /000997 新大陆/ })).toBeInTheDocument()
  })
})
