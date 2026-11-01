import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/base.css";
import "./styles/components.css";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
// V14：一致性补丁最后引入（Toast / BootScreen / 层级与滚动修正），保证覆盖新页面组件样式
import "./styles/ui-consistency.css";

const root = document.getElementById("root");

if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );
}
