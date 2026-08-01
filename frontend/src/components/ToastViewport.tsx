import { useEffect, useState } from "react"

export type ToastDetail = { tone: "success" | "error"; message: string }
type ToastItem = ToastDetail & { id: number }

export function ToastViewport() {
  const [items, setItems] = useState<ToastItem[]>([])
  useEffect(() => {
    let nextId = 0
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<ToastDetail>).detail
      const id = ++nextId
      setItems((current) => [...current.slice(-2), { ...detail, id }])
      window.setTimeout(() => {
        setItems((current) => current.filter((item) => item.id !== id))
      }, 3500)
    }
    window.addEventListener("chanalyzer:toast", receive)
    return () => window.removeEventListener("chanalyzer:toast", receive)
  }, [])
  return (
    <div className="toast-viewport" aria-live="polite" aria-atomic="false">
      {items.map((item) => (
        <div key={item.id} className={`app-toast toast-${item.tone}`} role={item.tone === "error" ? "alert" : "status"}>
          <span className="toast-icon" aria-hidden="true">{item.tone === "success" ? "✓" : "×"}</span>
          <span>{item.message}</span>
        </div>
      ))}
    </div>
  )
}