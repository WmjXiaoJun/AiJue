// R5 · 统一指标：一个数字 + 一行说明；横向排成一行用 StatRow。
import type { ReactNode } from "react";

export type StatTone = "default" | "ok" | "warn" | "accent";

export interface StatProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: StatTone;
  /** 悬浮说明（技术细节放这里，不占正文） */
  title?: string;
}

export function Stat({ label, value, hint, tone = "default", title }: StatProps) {
  const cls = ["ui-stat", tone !== "default" ? `is-${tone}` : ""].filter(Boolean).join(" ");
  return (
    <div className={cls} title={title}>
      <span className="ui-stat-label">{label}</span>
      <b className="ui-stat-value">{value}</b>
      {hint && <span className="ui-stat-hint">{hint}</span>}
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="ui-stat-row">{children}</div>;
}
