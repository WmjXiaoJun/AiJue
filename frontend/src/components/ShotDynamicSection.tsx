import { useEffect, useState } from "react";
import { api, errorText, mediaUrl } from "../lib/api";
import type { Job, Shot } from "../types";
import { isShotDynamic } from "../lib/format";
import { useToast } from "./Toast";
import { ProgressBar, Spinner } from "./ui";
import { IconAlert, IconSpark, IconVideo } from "./icons";
import { ShotFrames } from "./ShotFrames";

interface Props {
  shot: Shot;
  onChanged: () => void;
}

/** 单镜动态版本：首尾帧 + 生成动态 + 进度 + H3 预览 */
export function ShotDynamicSection({ shot, onChanged }: Props) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const dynamic = isShotDynamic(shot);
  const h3Url = previewUrl || (shot.h3_video_url ? mediaUrl(shot.h3_video_url) : "");
  const running = !!job && (job.status === "queued" || job.status === "running");

  // 轮询单镜动态任务进度
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed" || job.status === "canceled") return;
    let alive = true;
    const tick = async () => {
      try {
        const next = await api.job(job.id);
        if (!alive) return;
        setJob(next);
        if (next.status === "done") {
          if (next.output_url) setPreviewUrl(mediaUrl(next.output_url));
          notify("镜头 #" + String(shot.no).padStart(3, "0") + " 动态生成完成", "success");
          onChanged();
        } else if (next.status === "failed") {
          notify("动态生成失败：" + (next.error || next.message || "未知原因"), "error");
        }
      } catch {
        /* 网络抖动：保留上次进度 */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1800);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.status, notify, onChanged, shot.no]);

  const handleGenerate = async () => {
    setBusy(true);
    try {
      const created = await api.generateDynamicShot(shot.id);
      setJob(created);
      notify("已提交镜头 #" + String(shot.no).padStart(3, "0") + " 的动态生成（H3）", "success");
      onChanged();
    } catch (err) {
      notify("动态生成失败：" + errorText(err), "error");
      setJob(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="detail-block dyn-section">
      <div className="dyn-head">
        <h4>
          <IconVideo size={14} /> 动态版本
        </h4>
        <span className={"dyn-badge" + (dynamic ? " active" : "")}>{dynamic ? "已生成动态" : "静态回退"}</span>
      </div>

      <ShotFrames shot={shot} />
      <p className="dyn-hint">
        H3 动态版本按首帧 + 尾帧控制角色与构图连续；后端首尾帧字段未回填时，用 <code>clips/{shot.id}.mp4</code> 抽帧兜底。
      </p>

      <div className="dyn-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void handleGenerate()} disabled={busy || running}>
          {busy || running ? <Spinner size={13} /> : <IconSpark size={14} />}
          {running ? "动态生成中…" : busy ? "提交中…" : "生成动态"}
        </button>
        <span className="muted">{dynamic ? "已具备 H3 动态版本" : "当前为静态版本，可生成动态"}</span>
      </div>

      {running && (
        <div className="dyn-progress">
          <ProgressBar value={job?.progress || 0} label={Math.round(job?.progress || 0) + "%"} />
          <span>{job?.message || "H3 动态生成中（口型 / 首尾帧）…"}</span>
        </div>
      )}

      {job?.status === "failed" && (
        <div className="dyn-error">
          <IconAlert size={13} /> {job.error || job.message || "动态生成失败，可重试"}
        </div>
      )}

      {h3Url && (
        <div className="dyn-preview">
          <div className="dyn-preview-label">
            <IconVideo size={13} /> H3 动态版本预览
          </div>
          <video
            className="dyn-video"
            src={h3Url + (h3Url.includes("?") ? "&" : "?") + "v=" + (job?.id || "shot")}
            controls
            playsInline
            preload="metadata"
          />
        </div>
      )}
    </section>
  );
}
