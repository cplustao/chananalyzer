import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MarkdownReport } from "./MarkdownReport"

describe("MarkdownReport", () => {
  it("renders structured research content and safe external links", () => {
    render(
      <MarkdownReport
        content={[
          "# 研究结论",
          "",
          "| 指标 | 结果 |",
          "| --- | --- |",
          "| 趋势 | 偏强 |",
          "",
          "[数据来源](https://example.com/research)",
        ].join("\n")}
      />,
    )

    expect(screen.getByRole("heading", { name: "研究结论" })).toBeInTheDocument()
    expect(screen.getByRole("table")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "数据来源" })).toHaveAttribute("target", "_blank")
    expect(screen.getByRole("link", { name: "数据来源" })).toHaveAttribute("rel", "noreferrer")
  })

  it("does not mount raw HTML from a report", () => {
    const { container } = render(
      <MarkdownReport content={'<script>alert("unsafe")</script><strong>伪造内容</strong>'} />,
    )

    expect(container.querySelector("script")).toBeNull()
    expect(container.querySelector("strong")).toBeNull()
  })
})
