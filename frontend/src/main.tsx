import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/base.css";
import "./styles/components.css";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
// V14：一致性补丁最后引入（Toast / BootScreen / 层级与滚动修正），保证覆盖新页面组件样式
import "./styles/ui-consistency.css";
// R5：统一设计系统（token + 旧值压缩 + 新外壳/首页），必须最后引入才能覆盖历史页面样式
import "./styles/design.css";
// 2026-11：漫剧工坊风重设计（暖纸底 + 珊瑚主色 + 大圆角卡片），最后引入覆盖 design.css
import "./styles/redesign.css";

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
