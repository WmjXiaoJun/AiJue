// V5 子组件共用：多级回退图片、色板、标签、QC/风格徽标、视频弹层
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import type { V5Output, V5Palette } from "../../lib/v5";
import { mediaSrc } from "../../lib/v5";
import { IconAlert, IconClose, IconExternal, IconPlay } from "../icons";

/** 依次尝试 sources（AI 资产 → 程序化预览 → 占位），全部失败显示 fallback。 */
export function SmartImage({
  sources,
  alt,
  className,
  style,
  fallback,
  onClick,
}: {
  sources: (string | null | undefined)[];
  alt: string;
  className?: string;
  style?: CSSProperties;
  fallback?: ReactNode;
  onClick?: () => void;
}) {
  const list = sources.filter((item): item is string => Boolean(item));
  const key = list.join("|");
  const [index, setIndex] = useState(0);
  useEffect(() => {
    setIndex(0);
  }, [key]);
  if (!list.length || index >= list.length) {
    return (
      <div className={"v5-img-fallback" + (className ? " " + className : "")} style={style}>
        {fallback || <IconAlert size={18} />}
      </div>
    );
  }
  return (
    <img
      className={className}
      style={style}
      src={list[index]}
      alt={alt}
      loading="lazy"
      onClick={onClick}
      onError={() => setIndex((current) => current + 1)}
    />
  );
}

export function flattenPalette(palette: V5Palette | undefined, max = 8): string[] {
  if (!palette) return [];
  const order = ["bg", "fg", "accent", "paper", "shadow", "highlight"];
  const colors: string[] = [];
  const keys = [...order.filter((key) => key in palette), ...Object.keys(palette)];
  for (const key of keys) {
    const value = palette[key];
    const items = Array.isArray(value) ? value : [value];
    for (const item of items) {
      if (typeof item === "string" && /^#[0-9a-fA-F]{3,8}$/.test(item) && !colors.includes(item)) {
        colors.push(item);
      }
      if (colors.length >= max) return colors;
    }
  }
  return colors;
}

export function Palette({ palette, size = 18 }: { palette?: V5Palette; size?: number }) {
  const colors = flattenPalette(palette, 8);
  if (!colors.length) return <span className="v5-muted">—</span>;
  return (
    <span className="v5-palette" title={colors.join(" ")}>
      {colors.map((color) => (
        <i key={color} className="v5-swatch" style={{ background: color, width: size, height: size }} />
      ))}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="v5-tag">{children}</span>;
}

export function StyleBadge({ label }: { label: string }) {
  return (
    <span className="v5-style-badge">
      <i className="v5-style-dot" />
      {label}
    </span>
  );
}

export function QcBadge({ ok, checks, total }: { ok: boolean | null; checks?: number; total?: number }) {
  if (ok === null) return <span className="v5-qc v5-qc-none">无 QC</span>;
  return (
    <span className={"v5-qc " + (ok ? "v5-qc-ok" : "v5-qc-fail")}>
      {ok ? "QC 通过" : "QC 失败"}
      {typeof checks === "number" && typeof total === "number" && total > 0
        ? ` · ${checks}/${total}`
        : ""}
    </span>
  );
}

export function VideoModal({
  output,
  onClose,
  onOpenTimeline,
  onOpenQc,
}: {
  output: V5Output | null;
  onClose: () => void;
  onOpenTimeline: (output: V5Output) => void;
  onOpenQc: (output: V5Output) => void;
}) {
  useEffect(() => {
    if (!output) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [output, onClose]);
  if (!output) return null;
  return (
    <div className="v5-overlay" onMouseDown={onClose}>
      <div className="v5-video-modal" onMouseDown={(event) => event.stopPropagation()}>
        <div className="v5-video-head">
          <div>
            <b>{output.label}</b>
            <span className="v5-muted">
              {output.style_label} · {output.resolution || "?"} · {output.fps || "?"}fps
              {output.duration ? ` · ${output.duration}s` : ""}
            </span>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </div>
        {output.final_url ? (
          <video
            className="v5-video"
            controls
            autoPlay
            playsInline
            preload="metadata"
            src={mediaSrc(output.final_url)}
          />
        ) : (
          <div className="v5-video-missing">该模板还没有 final.mp4</div>
        )}
        <div className="v5-video-actions">
          <span className="v5-muted">
            {output.shots} 镜 · 场景 {output.scenes.length} · 角色 {output.characters.length} · {output.mtime}
          </span>
          <div className="v5-spacer" />
          <button type="button" className="btn btn-ghost" onClick={() => onOpenTimeline(output)}>
            <IconExternal size={14} /> 时间线
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onOpenQc(output)}>
            <IconExternal size={14} /> QC 报告
          </button>
        </div>
      </div>
    </div>
  );
}

export function PlayOverlay() {
  return (
    <span className="v5-play">
      <IconPlay size={22} />
    </span>
  );
}
