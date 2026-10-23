import { useEffect, type CSSProperties, type ReactNode } from "react";
import {
  CHARACTER_STATUS_LABEL,
  EPISODE_STATUS_LABEL,
  JOB_STATUS_LABEL,
  SHOT_STATUS_LABEL,
  firstChar,
  label,
} from "../lib/format";
import { IconAlert, IconInfo, IconSpin } from "./icons";

export type Tone = "neutral" | "info" | "accent" | "success" | "danger" | "warn";

const SHOT_TONE: Record<string, Tone> = {
  pending: "neutral",
  queued: "info",
  generating: "accent",
  ready: "success",
  failed: "danger",
};
const JOB_TONE: Record<string, Tone> = {
  queued: "info",
  running: "accent",
  done: "success",
  failed: "danger",
  canceled: "neutral",
};
const EPISODE_TONE: Record<string, Tone> = {
  draft: "neutral",
  storyboard: "info",
  generating: "accent",
  rendering: "accent",
  ready: "success",
  rendered: "success",
  done: "success",
  failed: "danger",
};
const CHARACTER_TONE: Record<string, Tone> = {
  pending: "neutral",
  draft: "neutral",
  queued: "info",
  generating: "accent",
  running: "accent",
  ready: "success",
  done: "success",
  failed: "danger",
};

const STATUS_SETS = {
  shot: { tone: SHOT_TONE, labels: SHOT_STATUS_LABEL },
  job: { tone: JOB_TONE, labels: JOB_STATUS_LABEL },
  episode: { tone: EPISODE_TONE, labels: EPISODE_STATUS_LABEL },
  character: { tone: CHARACTER_TONE, labels: CHARACTER_STATUS_LABEL },
} as const;

export function StatusBadge({
  status,
  kind = "shot",
  labelText,
}: {
  status: string;
  kind?: keyof typeof STATUS_SETS;
  labelText?: string;
}) {
  const set = STATUS_SETS[kind] || STATUS_SETS.shot;
  const tone = set.tone[status] || "neutral";
  const text = labelText || label(set.labels, status);
  return (
    <span className={"badge badge-" + tone}>
      <i className="badge-dot" />
      {text}
    </span>
  );
}

export function ProgressBar({
  value,
  tone = "accent",
  size = "md",
  label,
}: {
  value: number;
  tone?: Tone;
  size?: "sm" | "md";
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <span className={"progress progress-" + tone + (size === "sm" ? " progress-sm" : "")}>
      <span className="progress-track">
        <span className="progress-fill" style={{ width: pct + "%" }} />
      </span>
      {label !== undefined && <span className="progress-label">{label}</span>}
    </span>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span className="spinner" style={{ width: size, height: size }}>
      <IconSpin size={size} />
    </span>
  );
}

export function Avatar({
  name,
  color,
  size = 36,
  square = false,
  dim = false,
}: {
  name?: string | null;
  color?: string | null;
  size?: number;
  square?: boolean;
  dim?: boolean;
}) {
  const accent = color && /^#[0-9a-fA-F]{6}$/.test(color) ? color : "#F5C518";
  const style: CSSProperties = {
    width: size,
    height: size,
    fontSize: Math.round(size * 0.4),
    color: accent,
    background: accent + "1f",
    borderColor: accent + "55",
    borderRadius: square ? Math.max(8, Math.round(size * 0.24)) : "50%",
    opacity: dim ? 0.72 : 1,
  };
  return (
    <span className="avatar" style={style} title={name || undefined}>
      {firstChar(name)}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function SectionHead({
  title,
  en,
  extra,
}: {
  title: string;
  en?: string;
  extra?: ReactNode;
}) {
  return (
    <div className="section-head">
      <div className="section-head-main">
        <h3>{title}</h3>
        {en && <span className="section-en">{en}</span>}
      </div>
      {extra && <div className="section-head-extra">{extra}</div>}
    </div>
  );
}

export function Meta({ label: name, value }: { label: string; value: ReactNode }) {
  return (
    <div className="meta-item">
      <span className="meta-label">{name}</span>
      <span className="meta-value">{value}</span>
    </div>
  );
}

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="overlay" onMouseDown={onClose}>
      <div className={"modal modal-" + size} onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  children,
  width = 460,
}: {
  open: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="overlay overlay-right" onMouseDown={onClose}>
      <aside className="drawer" style={{ width }} onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div className="drawer-title">{title}</div>
          {subtitle && <div className="drawer-subtitle">{subtitle}</div>}
          <button type="button" className="icon-btn drawer-close" onClick={onClose} aria-label="关闭">
            <span aria-hidden="true">✕</span>
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Field({
  label: name,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{name}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "danger"; children: ReactNode }) {
  return (
    <div className={"notice notice-" + tone}>
      {tone === "info" ? <IconInfo size={16} /> : <IconAlert size={16} />}
      <div>{children}</div>
    </div>
  );
}
