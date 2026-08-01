import { Component, type ErrorInfo, type ReactNode } from "react"

type Props = { children: ReactNode }
type State = { error: Error | null }

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Unhandled route render error", error, info.componentStack)
  }

  private retry = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="route-error" role="alert">
        <span className="route-error-icon" aria-hidden="true">!</span>
        <h1>页面暂时无法显示</h1>
        <p>页面渲染遇到异常。你可以重试当前页面，或返回市场雷达继续工作。</p>
        <div>
          <button type="button" className="route-error-primary" onClick={this.retry}>重试</button>
          <a href="/radar">返回市场雷达</a>
        </div>
        <details><summary>错误详情</summary><pre>{this.state.error.message}</pre></details>
      </main>
    )
  }
}