import { typedApi } from "@/lib/typed-api"

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown,
    public code?: string,
    public requestId?: string,
  ) { super(message) }
}

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE" | "PATCH"
type LooseResult = { data?: unknown; error?: unknown; response: Response }
type LooseRequest = (
  method: HttpMethod,
  path: string,
  options?: { body?: unknown; headers?: HeadersInit; signal?: AbortSignal },
) => Promise<LooseResult>

const request = typedApi.request as unknown as LooseRequest

function notify(tone: "success" | "error", message: string) {
  window.dispatchEvent(new CustomEvent("chanalyzer:toast", { detail: { tone, message } }))
}

function errorPayload(value: unknown): {
  code?: string
  message?: string
  detail?: string
  details?: unknown
  request_id?: string
} {
  return value && typeof value === "object" ? value as ReturnType<typeof errorPayload> : {}
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method?.toUpperCase() ?? "GET") as HttpMethod
  let body: unknown
  if (typeof init?.body === "string" && init.body.length > 0) {
    body = JSON.parse(init.body)
  }
  const { data, error, response } = await request(method, path, {
    body,
    headers: init?.headers,
    signal: init?.signal ?? undefined,
  })
  if (!response.ok) {
    const payload = errorPayload(error)
    if (method !== "GET") notify("error", payload.message ?? payload.detail ?? "操作失败")
    throw new ApiError(
      payload.message ?? payload.detail ?? `请求失败 (${response.status})`,
      response.status,
      payload.details,
      payload.code,
      payload.request_id,
    )
  }
  return data as T
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const value = await api<T>(path, { method: "POST", body: JSON.stringify(body) })
  notify("success", "操作已提交")
  return value
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const value = await api<T>(path, { method: "PUT", body: JSON.stringify(body) })
  notify("success", "设置已保存")
  return value
}