import { lazy, Suspense, type ReactNode } from "react"
import { Navigate, RouterProvider, createBrowserRouter } from "react-router-dom"
import { AppErrorBoundary } from "@/components/AppErrorBoundary"

const AppShell = lazy(() =>
  import("@/components/layout/AppShell").then((module) => ({ default: module.AppShell })),
)
const RadarPage = lazy(() =>
  import("@/pages/RadarPage").then((module) => ({ default: module.RadarPage })),
)
const StocksPage = lazy(() =>
  import("@/pages/StocksPage").then((module) => ({ default: module.StocksPage })),
)
const WatchlistPage = lazy(() =>
  import("@/pages/WatchlistPage").then((module) => ({ default: module.WatchlistPage })),
)
const EventAnalysisPage = lazy(() =>
  import("@/pages/EventAnalysisPage").then((module) => ({ default: module.EventAnalysisPage })),
)
const ScansPage = lazy(() =>
  import("@/pages/ScansPage").then((module) => ({ default: module.ScansPage })),
)
const HelpPage = lazy(() =>
  import("@/pages/HelpPage").then((module) => ({ default: module.HelpPage })),
)
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((module) => ({ default: module.SettingsPage })),
)

function RouteLoading() {
  return (
    <div className="panel space-y-3" aria-label="正在加载">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="h-10 w-full animate-pulse rounded-md bg-muted/50" />
      ))}
    </div>
  )
}

function page(node: ReactNode) {
  return <Suspense fallback={<RouteLoading />}>{node}</Suspense>
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppErrorBoundary>{page(<AppShell />)}</AppErrorBoundary>,
    children: [
      { index: true, element: <Navigate to="/radar" replace /> },
      { path: "decision", element: <Navigate to="/radar" replace /> },
      { path: "radar", element: page(<RadarPage />) },
      { path: "stocks", element: page(<StocksPage />) },
      { path: "watchlist", element: page(<WatchlistPage />) },
      { path: "limit-ups", element: page(<EventAnalysisPage kind="limit-up" />) },
      { path: "ipos", element: page(<EventAnalysisPage kind="ipo" />) },
      { path: "scans", element: page(<ScansPage />) },
      { path: "screeners", element: page(<ScansPage screener />) },
      { path: "help", element: page(<HelpPage />) },
      { path: "settings", element: page(<SettingsPage />) },
      { path: "*", element: <Navigate to="/radar" replace /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}