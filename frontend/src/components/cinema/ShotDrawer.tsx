// Cinema Studio · 镜头台 → 单镜抽屉：预览 / 抽帧 / 台词 / 3D brief 编辑 / 单镜重生成 / meta。
// 仅由 Shots.tsx 引用；样式见 styles/cinema-shots.css。
import { useEffect, useState, type ChangeEvent } from "react";
import "../../styles/cinema-shots.css";
import {
  SOURCE_HINT,
  SOURCE_LABEL,
  cinemaApi,
  fmtDuration,
  fmtTimecode,
  shotMediaUrl,
  type CinemaBrief,
  type CinemaShot,
} from "../../lib/cinema";
import { errorText } from "../../lib/api";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconCheck, IconClose, IconFilm, IconSpark } from "../icons";

type Provider = "local" | "metaso";

interface Draft {
  subject: string;
  camera: string;
  ambience: string;
  env: string;
}

interface ShotDrawerProps {
  shot: CinemaShot;
  onClose: () => void;
  /** 保存成功：父级用返回的 brief 本地更新列表（无需整页重拉） */
  onSaved: (shotId: string, brief: CinemaBrief) => void;
  /** 触发生成成功：父级安排 5 秒后刷新镜头列表 */
  onRefresh: () => void;
}

function padIndex(index: number): string {
  return String(Math.max(0, Math.floor(Number(index) || 0))).padStart(3, "0");
}

function draftFrom(brief: CinemaBrief | undefined): Draft {
  return {
    subject: brief?.subject ?? "",
    camera: brief?.camera ?? "",
    ambience: brief?.ambience ?? "",
    env: brief?.env ?? "",
  };
}

function prettyMeta(meta: Record<string, unknown> | undefined): string {
  try {
    return JSON.stringify(meta ?? {}, null, 2);
  } catch {
    return "（meta 无法序列化）";
  }
}

export function ShotDrawer({ shot, onClose, onSaved, onRefresh }: ShotDrawerProps) {
  const notify = useToast();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(shot.brief));
  const [saving, setSaving] = useState(false);
  const [genBusy, setGenBusy] = useState<Provider | null>(null);
  const [mediaFailed, setMediaFailed] = useState(false);
  const [sheetFailed, setSheetFailed] = useState(false);

  const pad = padIndex(shot.index);
  const saved = draftFrom(shot.brief);
  const dirty =
    draft.subject !== saved.subject ||
    draft.camera !== saved.camera ||
    draft.ambience !== saved.ambience ||
    draft.env !== saved.env;
  const media = shot.url ? shotMediaUrl(shot.url) : "";
  const sheet = shot.sheet_url ? shotMediaUrl(shot.sheet_url) : "";
  const chars = shot.brief?.chars ?? [];
  // 复用/缺失镜没有自己的 mp4（后端 url 会 404），只有校验通过的镜头才可播放
  const playable = shot.valid && media.length > 0;

  const previewFallback =
    shot.source === "reuse"
      ? "该镜为素材复用，没有独立视频文件；成片里由母素材按时间码剪出，可在下方查看抽帧图"
      : shot.source === "missing"
        ? "该镜还没有视频素材，可先保存 brief 再触发重生成"
        : "视频无法加载，可尝试刷新或重生成该镜";

  // Esc 关闭
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 抽屉打开时锁住页面滚动
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  const updateField =
    (key: keyof Draft) => (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const value = event.target.value;
      setDraft((prev) => ({ ...prev, [key]: value }));
    };

  const handleSave = async () => {
    if (saving || !dirty) return;
    setSaving(true);
    try {
      const res = await cinemaApi.patchShot(shot.id, {
        subject: draft.subject.trim(),
        camera: draft.camera.trim(),
        ambience: draft.ambience.trim(),
        env: draft.env.trim(),
      });
      setDraft(draftFrom(res.brief));
      onSaved(shot.id, res.brief);
      notify(`镜头 #${pad} 的 3D brief 已保存`, "success");
    } catch (err) {
      notify(`镜头 #${pad} 的 brief 保存失败：${errorText(err)}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async (provider: Provider) => {
    if (genBusy) return;
    const label = provider === "local" ? "本地 H3" : "云端 H3";
    const confirmed = window.confirm(
      `确认用${label}重生成镜头 #${pad}？\n将强制覆盖该镜已有素材，提交后可在「生成队列」查看进度。`
    );
    if (!confirmed) return;
    setGenBusy(provider);
    try {
      await cinemaApi.generate({ provider, shots: [shot.id], force: true });
      notify(`镜头 #${pad} 的${label}任务已提交，到「生成队列」查看进度`, "success");
      onRefresh();
    } catch (err) {
      notify(`镜头 #${pad} 的${label}任务提交失败：${errorText(err)}`, "error");
    } finally {
      setGenBusy(null);
    }
  };

  return (
    <div className="cm-shots-drawer-layer" role="presentation" onMouseDown={onClose}>
      <aside
        className="cm-shots-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`镜头 #${pad} 详情`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="cm-shots-drawer-head">
          <div className="cm-shots-drawer-title">
            <span className="cm-shots-index cm-mono">#{pad}</span>
            <span className="cm-shots-source" title={SOURCE_HINT[shot.source]}>
              <i className={`cm-shots-dot is-${shot.source}`} />
              {SOURCE_LABEL[shot.source]}
            </span>
            <span className={"cm-shots-status " + (shot.valid ? "is-valid" : "is-invalid")}>
              {shot.valid ? "素材就绪" : "无素材"}
            </span>
          </div>
          <button type="button" className="cm-shots-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </header>

        <div className="cm-scroll cm-shots-drawer-body">
          {/* 预览 */}
          <section className="cm-shots-section">
            {playable && !mediaFailed ? (
              <video
                key={media}
                className="cm-shots-player"
                src={media}
                poster={sheetFailed ? undefined : sheet || undefined}
                controls
                preload="metadata"
                playsInline
                onError={() => setMediaFailed(true)}
              />
            ) : (
              <div className="cm-shots-preview-fallback">
                <IconFilm size={26} />
                <span>{previewFallback}</span>
              </div>
            )}
            <div className="cm-shots-preview-meta">
              <span className="cm-shots-kv">
                <em>时长</em>
                <b className="cm-mono">{fmtDuration(shot.duration)}</b>
              </span>
              <span className="cm-shots-kv">
                <em>时间码</em>
                <b className="cm-mono">{fmtTimecode(shot.edit_start)}</b>
              </span>
              <span className="cm-shots-kv is-scene">
                <em>场景</em>
                <b title={shot.scene || shot.brief?.env || undefined}>{shot.scene || shot.brief?.env || "—"}</b>
              </span>
            </div>
          </section>

          {/* 抽帧图 */}
          {sheet && !sheetFailed ? (
            <section className="cm-shots-section">
              <h3 className="cm-shots-section-title">抽帧图</h3>
              <figure className="cm-shots-still">
                <a href={sheet} target="_blank" rel="noreferrer">
                  <img
                    src={sheet}
                    alt={`镜头 #${pad} 抽帧联系表`}
                    loading="lazy"
                    onError={() => setSheetFailed(true)}
                  />
                </a>
                <figcaption>抽帧联系表 · 点击在新窗口查看原图</figcaption>
              </figure>
            </section>
          ) : null}

          {/* 台词 */}
          <section className="cm-shots-section">
            <h3 className="cm-shots-section-title">台词</h3>
            <p className={"cm-shots-utterance" + (shot.line ? "" : " is-empty")}>
              {shot.line || "（无台词 · 纯画面镜头）"}
            </p>
          </section>

          {/* 3D brief 编辑 */}
          <section className="cm-shots-section">
            <h3 className="cm-shots-section-title">3D 镜头 brief</h3>
            <p className="cm-shots-hint">保存后回写 briefs JSON；重生成时使用这份 brief。</p>
            {(chars.length > 0 || shot.brief?.local_setting_key) && (
              <div className="cm-shots-tags">
                {shot.brief?.local_setting_key && (
                  <span className="cm-chip cm-shots-tag">场景键 {shot.brief.local_setting_key}</span>
                )}
                {chars.map((key) => (
                  <span className="cm-chip cm-shots-tag" key={key}>
                    角色 {key}
                  </span>
                ))}
              </div>
            )}
            <label className="cm-shots-field">
              <span>主体 subject</span>
              <textarea
                value={draft.subject}
                onChange={updateField("subject")}
                placeholder="画面主体、动作、光线与镜头运动…"
                spellCheck={false}
              />
            </label>
            <div className="cm-shots-field-row">
              <label className="cm-shots-field">
                <span>运镜 camera</span>
                <input value={draft.camera} onChange={updateField("camera")} placeholder="slow push-in…" spellCheck={false} />
              </label>
              <label className="cm-shots-field">
                <span>环境音 ambience</span>
                <input
                  value={draft.ambience}
                  onChange={updateField("ambience")}
                  placeholder="deep low rumble…"
                  spellCheck={false}
                />
              </label>
            </div>
            <label className="cm-shots-field">
              <span>环境 env</span>
              <input value={draft.env} onChange={updateField("env")} placeholder="环境键或完整环境描述" spellCheck={false} />
            </label>
            <div className="cm-shots-form-actions">
              <button
                type="button"
                className="cm-btn cm-btn-primary cm-shots-btn"
                onClick={() => void handleSave()}
                disabled={saving || !dirty}
              >
                {saving ? <Spinner size={14} /> : <IconCheck size={15} />}
                保存 brief
              </button>
              <button
                type="button"
                className="cm-btn cm-btn-ghost cm-shots-btn"
                onClick={() => setDraft(saved)}
                disabled={saving || !dirty}
              >
                还原
              </button>
              {dirty && <span className="cm-shots-dirty">未保存</span>}
            </div>
          </section>

          {/* 单镜重生成 */}
          <section className="cm-shots-section">
            <h3 className="cm-shots-section-title">重生成</h3>
            <p className="cm-shots-hint">强制覆盖该镜素材；任务会出现在「生成队列」，列表稍后自动刷新。</p>
            <div className="cm-shots-gen-actions">
              <button
                type="button"
                className="cm-btn cm-shots-btn"
                onClick={() => void handleGenerate("local")}
                disabled={!!genBusy}
              >
                {genBusy === "local" ? <Spinner size={14} /> : <IconFilm size={15} />}
                本地重生成
              </button>
              <button
                type="button"
                className="cm-btn cm-btn-primary cm-shots-btn"
                onClick={() => void handleGenerate("metaso")}
                disabled={!!genBusy}
              >
                {genBusy === "metaso" ? <Spinner size={14} /> : <IconSpark size={15} />}
                云端重生成
              </button>
            </div>
          </section>

          {/* meta */}
          <section className="cm-shots-section">
            <details className="cm-shots-meta">
              <summary>镜头 meta（JSON）</summary>
              <pre className="cm-mono">{prettyMeta(shot.meta)}</pre>
            </details>
          </section>
        </div>
      </aside>
    </div>
  );
}
