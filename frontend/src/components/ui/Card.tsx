// R5 · 统一卡片：白面 + 1px 线 + 8/12 圆角 + 一档阴影。
// 旧页面里 .pl-panel / .v18-panel / .cm-card 等各自写死背景与圆角，新页面统一走这里。
import type { ReactNode } from "react";

export interface CardProps {
  title?: ReactNode;
  /** 标题右侧的说明文字（如「12 支」「已完成 3/4」） */
  sub?: ReactNode;
  /** 卡片右上角操作（按钮 / 链接） */
  actions?: ReactNode;
  /** flush：不留内边距（用于列表 / 媒体卡） */
  flush?: boolean;
  /** quiet：去掉阴影，只留边框（用于次级信息块） */
  quiet?: boolean;
  className?: string;
  children?: ReactNode;
}

export function Card({ title, sub, actions, flush, quiet, className, children }: CardProps) {
  const cls = [
    "ui-card",
    flush ? "is-flush" : "",
    quiet ? "is-quiet" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <section className={cls}>
      {(title || actions) && (
        <div className="ui-card-head">
          <h3 className="ui-card-title">
            {title}
            {sub && <span className="ui-card-sub">{sub}</span>}
          </h3>
          {actions && <div className="ui-card-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
