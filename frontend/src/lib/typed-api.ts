import createClient from "openapi-fetch"
import type { paths } from "@/types/openapi"

const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin

export const typedApi = createClient<paths>({
  baseUrl: `${origin}/api/v1`,
  credentials: "include",
  fetch: (request) => globalThis.fetch(request),
  headers: {
    "Content-Type": "application/json",
    "X-Requested-With": "ChanAnalyzer",
  },
})