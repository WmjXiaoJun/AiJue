import { useEffect, useRef, useState } from "react";
import { mediaUrl } from "../lib/api";
import type { Shot } from "../types";
import { Spinner } from "./ui";

type FrameKind = "first" | "last";

interface FrameThumbProps {
  shot: Shot;
  kind: FrameKind;
  compact?: boolean;
}

function clipFallbackUrl(shot: Shot): string {
  return shot.video_url || "/media/episodes/" + shot.episode_id + "/clips/" + shot.id + ".mp4";
}

/** 无首尾帧字段时：用 clips/{sid}.mp4 抽首帧 / 尾帧兜底 */
function ClipFrame({ shot, kind, src, compact }: { shot: Shot; kind: FrameKind; src: string; compact?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setReady(false);
    setFailed(false);
    const video = videoRef.current;
    if (!video) return;
    const seek = () => {
      const at = kind === "first" ? 0.04 : Math.max(0, (video.duration || 0) - 0.12);
      try {
        video.currentTime = at;
      } catch {
        /* metadata 未就绪时忽略 */
      }
    };
    const onSeeked = () => setReady(true);
    const onError = () => setFailed(true);
    if (video.readyState >= 1) seek();
    video.addEventListener("loadedmetadata", seek);
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError);
    return () => {
      video.removeEventListener("loadedmetadata", seek);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    };
  }, [kind, src]);

  const label = kind === "first" ? "首帧" : "尾帧";
  return (
    <figure className={"frame-thumb frame-thumb-video" + (compact ? " is-compact" : "")}>
      {!failed ? (
        <video ref={videoRef} className="frame-video" src={src} muted playsInline preload="metadata" />
      ) : (
        <div className="frame-empty">无画面</div>
      )}
      {!ready && !failed && (
        <span className="frame-loading">
          <Spinner size={12} />
        </span>
      )}
      <figcaption>
        <b>{label}</b>
        <span>clips 抽帧</span>
      </figcaption>
    </figure>
  );
}

/** 首帧 / 尾帧缩略图：优先后端字段，缺失时按 clips/{sid}.mp4 抽帧兜底 */
export function FrameThumb({ shot, kind, compact }: FrameThumbProps) {
  const explicit =
    kind === "first" ? shot.first_frame_url || shot.first_frame : shot.last_frame_url || shot.last_frame;
  const [imgFailed, setImgFailed] = useState(false);

  useEffect(() => {
    setImgFailed(false);
  }, [explicit]);

  const label = kind === "first" ? "首帧" : "尾帧";
  if (explicit && !imgFailed) {
    return (
      <figure className={"frame-thumb" + (compact ? " is-compact" : "")}>
        <img
          className="frame-image"
          src={mediaUrl(explicit)}
          alt={label}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
        <figcaption>
          <b>{label}</b>
          <span>后端字段</span>
        </figcaption>
      </figure>
    );
  }
  return <ClipFrame shot={shot} kind={kind} src={mediaUrl(clipFallbackUrl(shot))} compact={compact} />;
}

export function ShotFrames({ shot, compact }: { shot: Shot; compact?: boolean }) {
  return (
    <div className={"frame-grid" + (compact ? " is-compact" : "")}>
      <FrameThumb shot={shot} kind="first" compact={compact} />
      <FrameThumb shot={shot} kind="last" compact={compact} />
    </div>
  );
}
