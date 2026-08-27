import { expect, test, type Page } from "@playwright/test"

async function mockApi(page: Page) {
  let archivedJobId: string | null = null
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === "DELETE" && path.includes("/jobs/history-")) {
      archivedJobId = path.split("/").at(-1) ?? null
      await route.fulfill({ status: 204, body: "" })
      return
    }
    let body: unknown = {}
    if (path.endsWith("/decision/today")) body = {
      review_context: {
        mode: "next_session_preparation", freshness: "fresh", usable_for_next_session: true,
        as_of_trade_date: "2026-07-24", expected_trade_date: "2026-07-24", applicable_to: "下一交易日开盘前",
        title: "最新完整交易日复盘", message: "数据截至 2026-07-24，可用于下一交易日开盘前的研究准备。", blocking_modules: [],
      },
      market_facts: [
        { key: "breadth", label: "上涨 / 下跌", value: "1200 / 3900", detail: "上涨占比 23.0%" },
      ],
      market_radar: {
        id: 1, trade_date: "2026-07-24", algorithm_version: "radar-v1", freshness: "fresh",
        data_time: "2026-07-24", source: "Tushare", coverage_rate: 0.96,
        score: 30.9, status: "risk", status_label: "风险",
        executable_position: { min: 0, max: 0.2, reason: "保持防守仓位" },
        changes: { previous_trade_date: "2026-07-23", items: [{ key: "advance_rate", label: "上涨占比", current: 23, previous: 31, delta: -8, unit: "%" }] },
        snapshot: { evidence: ["趋势样本偏弱"], counter_evidence: ["局部成交回暖"] },
      },
      clustered_industries: { items: [{ name: "软件服务", limit_up_count: 5, highest_board: 3, amount_yi: 188.5, change: 2 }], source: "Tushare", model: "limit_up_aggregation" },
      scan_changes: {
        job_id: "scan-job", previous_job_id: "scan-job-old", comparable: true,
        algorithm_version: "chan-v1", items: [
          { instrument_id: 1, code: "600519", name: "贵州茅台", signal_type: "2", signal_date: "2026-07-24", state: "continued_hit", current_job_id: "scan-job", previous_job_id: "scan-job-old" },
        ],
      },
      watchlist_hits: [],
      data_health: {
        status: "fresh", decision_usable: true, checked_at: "2026-07-28T09:00:00Z",
        expected_trade_date: "2026-07-24", as_of_trade_date: "2026-07-24",
        last_full_refresh: "2026-07-24T17:30:00Z", blocking_reasons: [],
        provider_summary: {}, recommended_action: "可继续今日研究",
        database_backend: "sqlite", backup_mode: "application", categories: [],
      },
      attention_jobs: { failed: [], pending: [] },
      missing_or_stale_modules: [],
    }
    else if (path.endsWith("/system/data-health")) body = {
      status: "fresh", decision_usable: true, checked_at: "2026-07-28T09:00:00Z",
      expected_trade_date: "2026-07-24", as_of_trade_date: "2026-07-24",
      last_full_refresh: "2026-07-24T17:30:00Z", blocking_reasons: [],
      provider_summary: {}, recommended_action: "可继续今日研究",
      database_backend: "sqlite", backup_mode: "application",
      categories: [
        { key: "daily_bars", status: "fresh", source: "Tushare", data_time: "2026-07-24", coverage_rate: 0.96, missing_fields: [], single_source: false },
      ],
    }
    else if (path.endsWith("/system/backups")) body = { mode: "application", items: [] }
    else if (path.endsWith("/system/reliability-report")) body = {
      target_days: 10, observed_days: 1, passed_days: 1, pass_rate: 1,
      average_coverage: 1, burn_in_complete: false,
      items: [{ trade_date: "2026-07-24", decision_usable: true, coverage_rate: 1, status: "fresh", blocking_reasons: [] }],
    }
    else if (path.endsWith("/jobs/job-1/stages")) body = { job_id: "job-1", status: "running", items: [{ stage_key: "scan", status: "progress", processed: 9, coverage_rate: 0.45, updated_at: "2026-07-28T09:01:00Z" }] }
    else if (path.endsWith("/jobs/job-1/items")) body = [{ id: "item-1", subject_key: "600519", status: "failed", attempts: 2, error: "行情数据不足" }]
    else if (path.endsWith("/jobs/job-1/cancel")) body = { id: "job-1", kind: "screen.hot", status: "cancelled", progress: 45, total: 20, completed: 9, failed: 1, created_at: "2026-07-28T09:00:00Z" }
    else if (path.endsWith("/jobs/scan-job/results")) body = [{ instrument_id: 1, code: "600519", name: "贵州茅台", scan_kind: "scan.buy", signal_type: "2", signal_date: "2026-07-24", rank: 1, score: 88.5, payload: { evidence: "二买结构确认" } }]
    else if (path.endsWith("/jobs/scan-job")) body = { id: "scan-job", kind: "scan.buy", status: "completed", progress: 100, total: 1, completed: 1, failed: 0, created_at: "2026-07-28T09:00:00Z" }
    else if (path.endsWith("/jobs")) body = [
      { id: "job-1", kind: "screen.hot", status: "running", progress: 45, total: 20, completed: 9, failed: 1, message: "扫描中 10/20", created_at: "2026-07-28T09:00:00Z" },
      ...Array.from({ length: 50 }, (_, index) => ({
        id: `history-${index}`,
        kind: "data.refresh",
        status: "failed",
        progress: 100,
        total: 1,
        completed: 1,
        failed: 1,
        message: `历史失败 ${index}`,
        created_at: "2026-07-28T08:00:00Z",
      })).filter((job) => job.id !== archivedJobId),
    ]
    else if (path.endsWith("/market/radar")) {
      body = {
        trade_date: "2026-07-24",
        generated_at: "2026-07-28T09:00:00Z",
        source: "v2_cache",
        algorithm_version: "radar-v1",
        regime: {
          status: "risk",
          status_label: "风险",
          score: 30.9,
          position_range: { min: 0, max: 0.2 },
          executable_position: {
            decision_score: 42,
            band: 0,
            status: "risk",
            status_label: "风险",
            min: 0,
            max: 0.2,
            mid: 0.1,
            action: "hold",
            reason: "保持防守仓位",
            effective: "下一交易日",
          },
        },
        coverage: { current_count: 5100, rate: 0.96 },
        breadth: { advancers: 1200, decliners: 3900, advance_rate: 0.23, above_ma20_rate: 0.31 },
        liquidity: { amount_yi: 13500, amount_ratio: 0.92 },
        conclusion: "控制仓位，优先等待结构确认。",
        evidence: ["趋势样本偏弱"],
        counter_evidence: ["局部成交回暖"],
        components: [{ key: "market_breadth", label: "市场宽度", score: 30, summary: "上涨扩散不足" }],
        limit_ecology: { limit_up_count: 35, highest_board: 4, industries: [], recent: [] },
      }
    } else if (path.endsWith("/market/radar/history")) {
      const position = { decision_score: 33, band: 0, status: "risk", status_label: "风险", min: 0, max: 0.2, mid: 0.1, action: "hold", reason: "保持", effective: "下一交易日" }
      body = { count: 2, items: [
        { trade_date: "2026-07-23", algorithm_version: "radar-v1", score: 35, status: "risk", status_label: "风险", executable_position: position, calculated_at: "2026-07-23" },
        { trade_date: "2026-07-24", algorithm_version: "radar-v1", score: 30.9, status: "risk", status_label: "风险", executable_position: position, calculated_at: "2026-07-24" },
      ] }
    } else if (/\/instruments\/\d+\/bars$/.test(path)) {
      const instrumentId = Number(path.split("/").at(-2))
      body = {
        instrument: { id: instrumentId, code: instrumentId === 1 ? "600519" : "000997", name: instrumentId === 1 ? "贵州茅台" : "新大陆", status: "listed" },
        timeframe: "1d",
        adjustment: "qfq",
        items: [
          { bar_time: "2026-07-23", open: 20, high: 21, low: 19.5, close: 20.5, volume: 10000 },
          { bar_time: "2026-07-24", open: 20.5, high: 22, low: 20.2, close: 21.8, volume: 12000 },
        ],
      }
    } else if (/\/instruments\/\d+\/chan-structure$/.test(path)) {
      const instrumentId = Number(path.split("/").at(-2))
      body = { instrument_id: instrumentId, algorithm_version: "chan-v1", calculated_at: "2026-07-28T09:00:00Z", analysis: { bi_list: [], seg_list: [], zs_list: [], buy_signals: [], sell_signals: [] } }
    } else if (path.endsWith("/analyses")) body = { items: [], total: 0, page: 1, page_size: 30 }
    else if (path.endsWith("/scans/history")) body = [{ id: "scan-job", kind: "scan.buy", status: "completed", progress: 100, result_count: 1, payload: { types: ["2"] }, created_at: "2026-07-28T09:00:00Z", finished_at: "2026-07-28T09:03:00Z" }]
    else if (path.endsWith("/watchlists/default")) body = { id: "default", name: "默认自选", is_default: true, items: [{ id: "watch-1", instrument: { id: 1, code: "600519", name: "贵州茅台", status: "listed" }, position: 1, note: "等待日线三买确认", tags: ["核心观察"] }, { id: "watch-2", instrument: { id: 2, code: "000997", name: "新大陆", status: "listed" }, position: 2, note: null, tags: [] }] }
    else if (path.endsWith("/settings/schedules")) body = []
    else if (path.endsWith("/instruments")) body = { items: [], total: 0, page: 1, page_size: 50 }
    else if (path.endsWith("/limit-ups")) body = { items: [{ instrument_id: 1, code: "300001", name: "测试涨停股", trade_date: "20260724", market: "创业板", industry: "软件服务", theme: "自主可控", reason: "业绩增长", consecutive_boards: 3, turnover_rate: 8.5, limit_order: 25000000, limit_up_count_7d: 3, limit_up_count_30d: 5, limit_up_count_90d: 8, limit_up_count_180d: 12, analysis: { run_id: "limit-run", status: "completed", day_score: 82, week_score: 76, overall_score: 80, day_classification: "强主升", week_classification: "主升候选" } }], count: 1 }
    else if (path.endsWith("/ipos")) body = { stocks: [{ instrument_id: 2, code: "688001", name: "测试新股", list_date: "20260718", industry: "软件服务", market: "科创板", ipo_price: 18.8, ipo_pe: 24.5, analysis: { run_id: "ipo-run", status: "completed", ten_x_score: 8.5, hundred_x_score: 6 } }], count: 1 }
    else if (path.endsWith("/settings/secrets")) body = []
    else if (path.endsWith("/system/status")) body = { status: "healthy", version: "2.0.0", auth_mode: "local", database: "sqlite", worker_required: true, data_counts: { instruments: 13467, bars: 5538212 } }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) })
  })
}

test.beforeEach(async ({ page }) => mockApi(page))

test("renders meaningful content without runtime errors or framework overlays", async ({ page }) => {
  const browserErrors: string[] = []
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text())
  })
  page.on("pageerror", (error) => browserErrors.push(error.message))

  await page.goto("/radar")
  await expect(page.getByRole("heading", { name: "市场雷达" })).toBeVisible()
  await expect(page.locator("body")).not.toHaveText("")
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0)
  await page.waitForTimeout(500)
  expect(browserErrors).toEqual([])
})
test("defaults to today's research workbench and exposes unified navigation", async ({ page, isMobile }) => {
  await page.goto("/")
  await expect(page).toHaveURL(/\/today$/)
  await expect(page.getByRole("heading", { name: "今日研究" })).toBeVisible()
  if (!isMobile) {
    for (const label of ["今日研究", "市场雷达", "个股分析", "自选股", "涨停分析", "新股分析", "缠论扫描", "市场筛选", "帮助中心", "设置"]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible()
    }
    await expect(page.getByRole("link", { name: "复盘与次日准备" })).toHaveCount(0)
  }
  await expect(page.getByText("今日数据已通过决策门禁")).toBeVisible()
  await expect(page.getByText("最新完整交易日复盘")).toBeVisible()
  await expect(page.getByText("任务与异常")).toBeVisible()
})

test("context help lands in the versioned help center", async ({ page }) => {
  await page.goto("/radar")
  await page.getByLabel("查看帮助").click()
  await expect(page).toHaveURL(/\/help\?section=market-radar/)
  await expect(page.getByRole("heading", { name: "帮助中心" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "市场雷达" })).toBeVisible()
  await expect(page.getByText("风险提示")).toBeVisible()
})

test("event research uses a shared date range picker", async ({ page, isMobile }) => {
  await page.goto("/limit-ups")
  await page.getByRole("button", { name: "选择研究日期范围" }).click()
  await expect(page.getByRole("button", { name: "近 30 日" })).toBeVisible()
  if (!isMobile) await expect(page.getByText("选择开始和结束日期，列表按整个区间查询")).toBeVisible()
})

test("market screening explains hot and smart modes", async ({ page }) => {
  await page.goto("/screeners")
  await expect(page.getByText("热门：行情热度 → 缠论买点")).toBeVisible()
  await page.getByRole("tab", { name: "智能筛选" }).click()
  await expect(page.getByText("智能筛选：研究条件 → 全市场缠论扫描")).toBeVisible()
})

test("settings presents localized system labels", async ({ page }) => {
  await page.goto("/settings")
  await expect(page.getByText("本地免登录")).toBeVisible()
  await expect(page.getByText("SQLite 本地数据库")).toBeVisible()
  await expect(page.getByText("股票主数据")).toBeVisible()
  await expect(page.getByText("免费源稳定性试运行")).toBeVisible()
  await expect(page.getByText("仍需累计 9 个交易日；每日准备完成后自动记录。")).toBeVisible()
})

test("mobile navigation is available as a drawer", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile project only")
  await page.goto("/radar")
  await page.getByRole("button").filter({ has: page.locator("svg") }).first().click()
  await expect(page.getByRole("link", { name: "帮助中心" }).last()).toBeVisible()
})
test("watchlist management is part of the research workspace", async ({ page }) => {
  await page.goto("/watchlist")
  await expect(page.getByRole("heading", { name: "自选股" })).toBeVisible()
  await expect(page.getByPlaceholder("搜索代码、名称、标签或备注")).toBeVisible()
  await expect(page.getByText("600519")).toBeVisible()
  await expect(page.getByText("等待日线三买确认")).toBeVisible()
  await page.getByRole("button", { name: "编辑 600519 贵州茅台 的研究备注" }).dblclick()
  await expect(page.getByRole("heading", { name: "编辑自选研究信息" })).toBeVisible()
  await expect(page.getByPlaceholder("记录加入原因、等待确认、失效条件和下一步动作")).toBeFocused()
})

test("stock research switches through the integrated watchlist queue", async ({ page }) => {
  const browserErrors: string[] = []
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text())
  })
  page.on("pageerror", (error) => browserErrors.push(error.message))
  await page.goto("/stocks?id=1")
  await expect(page.getByRole("region", { name: "自选研究队列" })).toBeVisible()
  const chart = page.getByLabel("股票日 K 线、成交量、MACD、缠论结构与背离标记图")
  await expect(chart.locator("canvas")).toHaveCount(1)
  await expect(page.getByText("当前 1 / 2")).toBeVisible()
  await expect(page.getByRole("button", { name: "背离 0" })).toBeVisible()
  await page.getByRole("button", { name: "筛选标签：核心观察，共 1 只" }).click()
  await expect(page.getByRole("button", { name: /000997 新大陆/ })).toHaveCount(0)
  await page.getByRole("button", { name: "筛选全部自选股，共 2 只" }).click()
  await page.getByRole("button", { name: "下一只自选股" }).click()
  await expect(page).toHaveURL(/\/stocks\?id=2$/)
  await expect(page.getByText("当前 2 / 2")).toBeVisible()
  await expect(page.getByRole("button", { name: /000997 新大陆/ })).toHaveAttribute("aria-current", "true")
  await expect(chart.locator("canvas")).toHaveCount(1)
  expect(browserErrors).toEqual([])
})
test("data health summary explains which modules cause the overall state", async ({ page }) => {
  await page.goto("/radar")
  await page.getByRole("button", { name: /数据健康：新鲜/ }).click()
  await expect(page.getByText("总状态取所有模块中最差的一项，不代表全部数据都不可用。")).toBeVisible()
  await expect(page.getByText("所有核心模块均可用。")).toBeVisible()
})

test("task center shows localized child status and supports cancellation", async ({ page, isMobile }) => {
  await page.goto("/radar")
  await page.getByRole("button", { name: "任务中心" }).click()
  await expect(page.getByRole("heading", { name: "任务中心" })).toBeVisible()
  const actionGroup = page.locator(".job-row-actions").first()
  const statusBox = await actionGroup.locator("[data-slot=badge]").boundingBox()
  const cancelBox = await actionGroup.getByRole("button").boundingBox()
  expect(statusBox).not.toBeNull()
  expect(cancelBox).not.toBeNull()
  expect(Math.abs((statusBox!.y + statusBox!.height / 2) - (cancelBox!.y + cancelBox!.height / 2))).toBeLessThanOrEqual(1)
  expect(cancelBox!.height).toBe(isMobile ? 40 : 28)
  await page.getByText("查看任务明细").first().click()
  await expect(page.getByText("行情数据不足")).toBeVisible()
  await expect(page.getByText("失败", { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/提交时间 2026\/7\/28 17:00:00/).first()).toBeVisible()
  await page.getByRole("button", { name: "取消热门股票缠论扫描" }).click()
})

test("scan result detail exposes evidence and stock research link", async ({ page }) => {
  await page.goto("/scans")
  await page.getByRole("button", { name: "查看结果" }).click()
  await expect(page.getByText("贵州茅台")).toBeVisible()
  await page.getByRole("button", { name: "证据" }).click()
  await expect(page.getByRole("heading", { name: "扫描结果详情" })).toBeVisible()
  await expect(page.getByText(/二买结构确认/)).toBeVisible()
  await expect(page.getByRole("link", { name: "进入个股分析" })).toHaveAttribute("href", "/stocks?id=1")
})
test("limit-up list preserves the original research fields", async ({ page }) => {
  await page.goto("/limit-ups")
  for (const heading of ["7日", "30日", "90日", "180日", "封单", "日线主升浪", "周线主升浪", "综合分"]) {
    await expect(page.getByRole("columnheader", { name: heading })).toBeVisible()
  }
  await expect(page.getByText("强主升")).toBeVisible()
  await expect(page.getByText("3 板")).toBeVisible()
})

test("ipo list preserves issue and potential-score fields", async ({ page }) => {
  await page.goto("/ipos")
  for (const heading of ["发行价", "发行 PE", "10倍评分", "100倍评分"]) {
    await expect(page.getByRole("columnheader", { name: heading })).toBeVisible()
  }
  await expect(page.getByText("18.80")).toBeVisible()
  await expect(page.locator(".event-score.high").filter({ hasText: "8.5" }).first()).toBeVisible()
})



test("task center scrolls and archives terminal records", async ({ page }) => {
  await page.goto("/radar")
  await page.getByRole("button", { name: "任务中心" }).click()
  const list = page.locator(".job-list")
  await expect(list).toBeVisible()
  expect(await list.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true)

  const removeButtons = page.getByRole("button", { name: "移除行情数据更新" })
  await expect(removeButtons).toHaveCount(50)
  await removeButtons.last().click()
  await expect(page.getByRole("heading", { name: "从任务中心移除记录？" })).toBeVisible()
  await page.getByRole("button", { name: "确认移除" }).click()
  await expect(removeButtons).toHaveCount(49)
})
