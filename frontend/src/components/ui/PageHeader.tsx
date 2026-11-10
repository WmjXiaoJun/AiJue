// R5 · 统一页头：标题 + 副标题 + 元信息 + 右侧 actions。
// 只负责排版与间距；数据与交互由调用方传入，避免各页面各写一套页头。
import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  subtitle?: string;
  /** 标题下方的一行元信息（chip / 小字），按需传 */
  meta?: ReactNode;
  /** 右侧操作区（按钮、筛选等） */
  actions?: ReactNode;
}

export function PageHeader({ title, subtitle, meta, actions }: PageHeaderProps) {
  return (
    <header className="ui-page-head">
      <div className="ui-page-head-text">
        <h2 className="ui-page-title">{title}</h2>
        {subtitle && <p className="ui-page-sub">{subtitle}</p>}
        {meta && <div className="ui-page-meta">{meta}</div>}
      </div>
      {actions && <div className="ui-page-actions">{actions}</div>}
    </header>
  );
}
