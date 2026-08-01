import { afterEach, describe, expect, it, vi } from "vitest"
import { api, post } from "./api"

afterEach(() => vi.unstubAllGlobals())

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("api client", () => {
  it("returns JSON and sends credentials through the generated client", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal("fetch", fetchMock)
    await expect(api<{ ok: boolean }>("/system/status")).resolves.toEqual({ ok: true })
    const request = fetchMock.mock.calls[0][0] as Request
    expect(new URL(request.url).pathname).toBe("/api/v1/system/status")
    expect(request.credentials).toBe("include")
    expect(request.headers.get("X-Requested-With")).toBe("ChanAnalyzer")
  })

  it("preserves unified API error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse({ message: "参数错误", details: { field: "code" } }, 422),
    ))
    await expect(api("/broken")).rejects.toMatchObject({
      message: "参数错误",
      status: 422,
      details: { field: "code" },
    })
  })

  it("serializes job submission", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ job_id: "1" }, 202))
    vi.stubGlobal("fetch", fetchMock)
    await post("/jobs", { kind: "scan.buy" })
    const request = fetchMock.mock.calls[0][0] as Request
    expect(request.method).toBe("POST")
    expect(await request.json()).toEqual({ kind: "scan.buy" })
  })
})