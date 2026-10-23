import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

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
        <div className="crash">
          <div className="crash-card">
            <div className="crash-mark">造片局</div>
            <h2>工作台遇到一个异常</h2>
            <p>界面已受到保护，不会白屏。可以刷新重试；若持续出现，请把下面的信息发给开发。</p>
            <pre>{this.state.error.message}</pre>
            <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
              重新加载
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
