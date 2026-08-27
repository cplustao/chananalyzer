import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Job, JobAccepted } from "@/types/api"

const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "retrying"])

function isActiveDailyPrepare(job: Job) {
  return job.kind === "daily.prepare" && ACTIVE_JOB_STATUSES.has(job.status)
}

function notify(message: string) {
  window.dispatchEvent(new CustomEvent("chanalyzer:toast", { detail: { tone: "success", message } }))
}

export function useDailyPrepare() {
  const client = useQueryClient()
  const [acceptedJob, setAcceptedJob] = useState<JobAccepted | null>(null)
  const observedActiveJob = useRef(false)
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs?limit=100"),
    refetchInterval: (query) => {
      const current = query.state.data as Job[] | undefined
      return current?.some(isActiveDailyPrepare) ? 4_000 : 60_000
    },
    refetchIntervalInBackground: false,
  })
  const activeJob = useMemo(() => (jobs.data ?? []).find(isActiveDailyPrepare) ?? null, [jobs.data])
  const prepare = useMutation({
    mutationFn: () => api<JobAccepted>("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "daily.prepare",
        payload: { lookback_days: 10, run_screeners: false, screening_preset: "balanced" },
      }),
    }),
    onSuccess: (result) => {
      setAcceptedJob(result)
      notify(result.deduplicated ? "已有今日数据准备任务正在执行" : "今日数据准备已开始")
      void client.invalidateQueries({ queryKey: ["jobs"] })
    },
  })

  useEffect(() => {
    if (!acceptedJob) return
    const accepted = (jobs.data ?? []).find((job) => job.id === acceptedJob.job_id)
    if (accepted) setAcceptedJob(null)
  }, [acceptedJob, jobs.data])

  useEffect(() => {
    if (activeJob) {
      observedActiveJob.current = true
      return
    }
    if (!observedActiveJob.current) return
    observedActiveJob.current = false
    void client.invalidateQueries({ queryKey: ["data-health"] })
    void client.invalidateQueries({ queryKey: ["decision-today"] })
    void client.invalidateQueries({ queryKey: ["reliability-report"] })
  }, [activeJob, client])

  const isPreparing = prepare.isPending || Boolean(activeJob) || Boolean(acceptedJob)
  const buttonLabel = activeJob?.status === "queued"
    ? "数据准备已排队"
    : activeJob
      ? `数据准备中 ${Math.round(activeJob.progress ?? 0)}%`
      : prepare.isPending || acceptedJob
        ? "正在确认任务…"
        : "准备今日数据"

  return {
    activeJob,
    buttonLabel,
    error: prepare.error,
    isPreparing,
    submit: () => prepare.mutate(),
  }
}
