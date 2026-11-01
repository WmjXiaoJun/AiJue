import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** V14：崩溃兜底页改用新设计语言（cm-crash-*，样式在 styles/ui-consistency.css）。 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[造片局] 界面渲染异常", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="cm-crash">
          <div className="cm-crash-card">
            <span className="cm-crash-kicker">SYSTEM · 界面异常</span>
            <div className="cm-crash-mark">造片局</div>
            <h2>工作台遇到一个异常</h2>
            <p>界面已受到保护，不会白屏。可以刷新重试；若持续出现，请把下面的信息发给开发。</p>
            <pre>{this.state.error.message}</pre>
            <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => window.location.reload()}>
              重新加载
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
