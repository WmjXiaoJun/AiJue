// V18 · 制片控制台各步骤面板（Step 1–6）
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CinemaTemplate } from "../../lib/cinema";
import { renderTypeMeta } from "../../lib/cinema";
import { fmtDuration, fmtSize, isRealTemplate } from "../../lib/cinema";
import type { PipelineRun } from "../../lib/pipeline";
import { PIPELINE_STAGE_LABEL, pipelineRunPercent } from "../../lib/pipeline";
import type { CastingMember, VoicePreset } from "../../lib/voice";
import { roleLabel, voiceApi } from "../../lib/voice";
import { characterArtUrl, characterPreviewUrl, mediaSrc } from "../../lib/v5";
import type { Settings } from "../../types";
import { errorText } from "../../lib/api";
import {
  IconAlert,
  IconCheck,
  IconClock,
  IconCopy,
  IconExternal,
  IconFilm,
  IconDownload,
  IconGlobe,
  IconInfo,
  IconLock,
  IconPlay,
  IconRefresh,
  IconScript,
  IconSend,
  IconSpark,
  IconTemplate,
  IconUsers,
  IconVideo,
  IconVolume,
} from "../icons";
import { Spinner } from "../ui";
import { VoicePicker } from "../voice/VoicePicker";

/* ------------------------------ 通用小块 ------------------------------ */
export function ErrorBar({ title, error, onRetry, onCopy }: { title?: string; error: string; onRetry?: () => void; onCopy?: () => void }) {
  return (
    <div className="v18-error">
      <IconAlert size={15} />
      <div className="v18-error-main">
        <b>{title || "操作失败"}</b>
        <p className="v18-error-text">{error}</p>
      </div>
      <div className="v18-error-actions">
        {onCopy && (
          <button type="button" className="cm-btn cm-btn-sm" onClick={onCopy} title="复制错误信息">
            <IconCopy size={12} /> 复制
          </button>
        )}
        {onRetry && (
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRetry}>
            <IconRefresh size={12} /> 重试
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyBlock({
  icon,
  title,
  desc,
  actions,
}: {
  icon?: React.ReactNode;
  title: string;
  desc?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="v18-empty">
      {icon}
      <b>{title}</b>
      {desc && <span>{desc}</span>}
      {actions && <div className="v18-actions">{actions}</div>}
    </div>
  );
}

function StepNav({
  onPrev,
  onNext,
  nextLabel,
  nextDisabled,
  hint,
}: {
  onPrev?: () => void;
  onNext?: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  hint?: string;
}) {
  return (
    <div className="v18-actions" style={{ borderTop: "1px solid var(--c-line)", paddingTop: 12 }}>
      {hint && <span className="v18-crumb">{hint}</span>}
      <div className="v18-actions-right">
        {onPrev && (
          <button type="button" className="cm-btn cm-btn-sm" onClick={onPrev}>
            上一步
          </button>
        )}
        {onNext && (
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={onNext} disabled={nextDisabled}>
            {nextLabel || "下一步"} →
          </button>
        )}
      </div>
    </div>
  );
}

function StatusTag({ template }: { template: CinemaTemplate | null }) {
  if (!template) return <span className="v18-tag is-slate">接口未就绪</span>;
  if (template.status === "done") return <span className="v18-tag is-jade"><IconCheck size={10} /> 已完成</span>;
  if (template.status === "generating") return <span className="v18-tag is-gold">生成中</span>;
  if (template.status === "generated") return <span className="v18-tag is-gold">镜头就绪</span>;
  if (template.status === "assemble_failed") return <span className="v18-tag is-red">合成失败</span>;
  return <span className="v18-tag">待生成</span>;
}

/* ------------------------------ Step 1 · 选模板 ------------------------------ */
export function StepTemplates({
  templates,
  selectedId,
  onSelect,
  loading,
  error,
  onRefresh,
  onNext,
  onOpenCinema,
  totalHint,
}: {
  templates: CinemaTemplate[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onNext: () => void;
  onOpenCinema: () => void;
  totalHint: string;
}) {
  const [filter, setFilter] = useState<"all" | "classic" | "real">("all");
  const filtered = useMemo(
    () =>
      filter === "real"
        ? templates.filter((item) => isRealTemplate(item.id))
        : filter === "classic"
          ? templates.filter((item) => !isRealTemplate(item.id))
          : templates,
    [filter, templates],
  );
  const realCount = templates.filter((item) => isRealTemplate(item.id)).length;
  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconTemplate size={17} /> ① 选模板
          </h3>
          <p className="v18-panel-sub">TEMPLATE · 选一个 3D 模板开始制片流程</p>
        </div>
        <div className="v18-panel-actions">
          <span className="v18-crumb">{totalHint}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner size={12} /> : <IconRefresh size={13} />} 刷新
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenCinema}>
            <IconFilm size={13} /> 模板与素材
          </button>
        </div>
      </div>
      <div className="v18-panel-body">
        {error && <ErrorBar title="模板数据读取失败" error={error} onRetry={onRefresh} />}
        <div className="v18-filter-bar">
          <div className="tpl-seg" role="group" aria-label="模板分类筛选">
            {([
              { id: "all" as const, label: "全部", count: templates.length },
              { id: "classic" as const, label: "原 16 模板", count: templates.length - realCount },
              { id: "real" as const, label: "写实新模板", count: realCount },
            ]).map((item) => (
              <button
                key={item.id}
                type="button"
                className={filter === item.id ? "is-active" : ""}
                onClick={() => setFilter(item.id)}
                aria-pressed={filter === item.id}
              >
                {item.label}
                <span className="tpl-seg-count">{item.count}</span>
              </button>
            ))}
          </div>
          <span className="v18-filter-count">
            {filtered.length} / {templates.length}
          </span>
        </div>

        {loading && !templates.length ? (
          <div className="v18-empty">
            <Spinner size={16} />
            <span>正在读取 19 个 3D 模板…</span>
          </div>
        ) : !filtered.length ? (
          <EmptyBlock title="该分类下暂无模板" desc="切回「全部」查看所有模板；新模板在 3D 生成前会显示「待生成」。" icon={<IconTemplate size={26} />} />
        ) : (
          <div className="v18-tpl-grid">
            {filtered.map((template) => (
              <button
                key={template.id}
                type="button"
                className={"v18-tpl-card" + (selectedId === template.id ? " is-selected" : "")}
                onClick={() => onSelect(template.id)}
                aria-pressed={selectedId === template.id}
                title={`选择模板：${template.label}`}
              >
                <div className="v18-tpl-poster">
                  {template.poster_url ? (
                    <img src={mediaSrc(template.poster_url)} alt={template.label} loading="lazy" />
                  ) : (
                    <span className="v18-tpl-fallback">{template.label.slice(0, 1)}</span>
                  )}
                  <div className="v18-tpl-badges">
                    <StatusTag template={template} />
                    {isRealTemplate(template.id) && <span className="tpl-real-badge">写实</span>}
                  </div>
                  {selectedId === template.id && (
                    <span className="v18-tpl-check">
                      <IconCheck size={13} />
                    </span>
                  )}
                </div>
                <div className="v18-tpl-body">
                  <div className="v18-tpl-name">
                    <b>{template.label}</b>
                  </div>
                  <div className="v18-tpl-id">{template.id}</div>
                  <div className="v18-tpl-meta">
                    <span>{fmtDuration(template.final.duration || 0)}</span>
                    <span>
                      镜头 {template.clips.done}/{template.clips.total || "—"}
                    </span>
                    <span>{fmtSize(template.final.size)}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
        <StepNav
          onNext={onNext}
          nextDisabled={!selectedId}
          nextLabel="下一步：角色与声音"
          hint={selectedId ? `已选：${selectedId}` : "先选择一个模板"}
        />
      </div>
    </div>
  );
}

/* ------------------------------ Step 2 · 角色与声音 ------------------------------ */
function CastPortrait({ member }: { member: CastingMember }) {
  const sources = useMemo(() => {
    const list = [member.portrait_url, characterArtUrl(member.character), characterPreviewUrl(member.character)].filter(Boolean);
    return list;
  }, [member]);
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [member.character]);
  if (!sources.length || index >= sources.length) return <span>{member.label.slice(0, 1)}</span>;
  return (
    <img
      src={sources[index]}
      alt={member.label}
      onError={() => setIndex((prev) => prev + 1)}
      loading="lazy"
    />
  );
}

export function StepCast({
  templateId,
  casting,
  loading,
  error,
  source,
  onRefresh,
  presets,
  onVoiceSaved,
  onOpenCharacters,
  onNext,
  onPrev,
}: {
  templateId: string | null;
  casting: CastingMember[];
  loading: boolean;
  error: string;
  source: string;
  onRefresh: () => void;
  presets: VoicePreset[];
  onVoiceSaved: (character: string, voiceId: string, label: string) => void;
  onOpenCharacters: () => void;
  onNext: () => void;
  onPrev: () => void;
}) {
  const [picker, setPicker] = useState<CastingMember | null>(null);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [defaults, setDefaults] = useState<Record<string, string>>({});
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const playMember = useCallback(
    async (member: CastingMember) => {
      if (previewing === member.character) {
        if (audioRef.current) {
          audioRef.current.pause();
          audioRef.current = null;
        }
        setPreviewing(null);
        return;
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPreviewError("");
      setPreviewing(member.character);
      try {
        const res = await voiceApi.preview(member.character);
        const audio = new Audio(res.url + (res.url.includes("?") ? "&" : "?") + "t=" + Date.now());
        audioRef.current = audio;
        audio.onended = () => setPreviewing((prev) => (prev === member.character ? null : prev));
        audio.onerror = () => {
          setPreviewing(null);
          setPreviewError("试听 wav 加载失败（该角色可能还没有试听产物）");
        };
        await audio.play();
      } catch (err) {
        setPreviewing(null);
        const message = errorText(err);
        setPreviewError(
          message.includes("404") || message.includes("Not Found")
            ? "试听接口尚未就绪（V18 后端 API 实现中）；可先应用音色保存，生成时生效。"
            : "试听失败：" + message,
        );
      }
    },
    [previewing],
  );

  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconUsers size={17} /> ② 角色与声音
          </h3>
          <p className="v18-panel-sub">CAST &amp; VOICE · 按模板 cast 自动列出角色；可试听 / 换声音</p>
        </div>
        <div className="v18-panel-actions">
          {casting.length > 0 && <span className="v18-crumb">{casting.length} 角色 · {source === "fallback" ? "catalog 兜底" : "casting API"}</span>}
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner size={12} /> : <IconRefresh size={13} />} 刷新
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenCharacters}>
            <IconUsers size={13} /> 角色页
          </button>
        </div>
      </div>
      <div className="v18-panel-body">
        {error && <ErrorBar title="选角数据提示" error={error} onRetry={onRefresh} />}
        {previewError && <ErrorBar title="试听提示" error={previewError} />}
        {!templateId ? (
          <EmptyBlock icon={<IconTemplate size={26} />} title="还没有选择模板" desc="回到第 ① 步选模板后，这里会自动列出该模板的角色与声音。" />
        ) : loading && !casting.length ? (
          <div className="v18-empty">
            <Spinner size={16} />
            <span>正在读取模板 cast…</span>
          </div>
        ) : !casting.length ? (
          <EmptyBlock
            icon={<IconUsers size={26} />}
            title="该模板没有 cast 数据"
            desc="模板合同里没有 cast 字段，或后端 casting 接口尚未就绪；可到角色与声音页统一调整音色。"
            actions={
              <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenCharacters}>
                去角色与声音
              </button>
            }
          />
        ) : (
          <div className="v18-cast-grid">
            {casting.map((member) => (
              <div key={`${member.role}-${member.character}`} className="v18-cast-card">
                <div className="v18-cast-top">
                  <div className="v18-cast-avatar">
                    <CastPortrait member={member} />
                  </div>
                  <div className="v18-cast-head">
                    <div className="v18-cast-name">
                      <b>{member.label}</b>
                      <span className="v18-tag is-gold">{roleLabel(member.role)}</span>
                      {member.era && <span className="v18-tag">{member.era}</span>}
                    </div>
                    <p className="v18-cast-persona">{member.persona || "暂无 persona（可到角色页查看完整资料）"}</p>
                  </div>
                </div>
                <div className="v18-voice-row">
                  <span className="v18-voice-chip" title={member.voice.voice_id}>
                    <IconVolume size={11} />
                    <span>{member.voice.label || member.voice.voice_id || "未绑定音色"}</span>
                    <em>{member.voice.provider}</em>
                  </span>
                </div>
                <div className="v18-cast-actions">
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void playMember(member)}>
                    {previewing === member.character ? <Spinner size={12} /> : <IconPlay size={12} />}
                    {previewing === member.character ? "播放中" : "试听"}
                  </button>
                  <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => setPicker(member)}>
                    <IconVolume size={12} /> 换声音
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <StepNav onPrev={onPrev} onNext={onNext} nextLabel="下一步：剧本预览" hint={casting.length ? `${casting.length} 个角色` : undefined} />
      </div>

      {picker && (
        <VoicePicker
          open
          character={picker.character}
          characterLabel={picker.label}
          presets={presets}
          currentVoiceId={picker.voice.voice_id}
          defaultVoiceId={defaults[picker.character]}
          onClose={() => setPicker(null)}
          onSaved={(voiceId, label) => {
            onVoiceSaved(picker.character, voiceId, label);
            setDefaults((prev) => (prev[picker.character] ? prev : { ...prev, [picker.character]: picker.voice.voice_id }));
            setPicker(null);
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------ Step 3 · 剧本预览 ------------------------------ */
export interface ScriptLine {
  no: number;
  scene: string;
  speaker: string;
  text: string;
  duration: number;
  camera?: string;
}

export function StepScript({
  templateLabel,
  lines,
  shotsInfo,
  loading,
  error,
  onRefresh,
  onNext,
  onPrev,
  onOpenCinema,
}: {
  templateLabel: string;
  lines: ScriptLine[];
  shotsInfo: string;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onNext: () => void;
  onPrev: () => void;
  onOpenCinema: () => void;
}) {
  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconScript size={17} /> ③ 剧本预览
          </h3>
          <p className="v18-panel-sub">SCRIPT · 逐镜台词 / 时长 / 说话人（模板 semantic）</p>
        </div>
        <div className="v18-panel-actions">
          <span className="v18-crumb">{shotsInfo}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner size={12} /> : <IconRefresh size={13} />} 刷新
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenCinema}>
            <IconFilm size={13} /> 镜头台
          </button>
        </div>
      </div>
      <div className="v18-panel-body">
        {error && <ErrorBar title="剧本读取失败" error={error} onRetry={onRefresh} />}
        {loading && !lines.length ? (
          <div className="v18-empty">
            <Spinner size={16} />
            <span>正在读取剧本…</span>
          </div>
        ) : !lines.length ? (
          <EmptyBlock
            icon={<IconScript size={26} />}
            title="暂无剧本数据"
            desc="模板 semantic / 逐镜接口还没有返回台词；可先到镜头台查看模板镜头，或直接进入生成步骤。"
            actions={
              <button type="button" className="cm-btn cm-btn-sm" onClick={onNext}>
                跳过，去生成 3D
              </button>
            }
          />
        ) : (
          <div className="v18-script-list">
            <div className="v18-script-row" style={{ background: "rgba(255,255,255,0.02)", color: "var(--c-mut)", fontFamily: "var(--font-mono)", fontSize: 8.5, letterSpacing: "0.12em" }}>
              <span>NO.</span>
              <span>SCENE</span>
              <span>SPEAKER</span>
              <span>LINE</span>
              <span style={{ textAlign: "right" }}>DUR</span>
            </div>
            {lines.map((line) => (
              <div key={line.no} className="v18-script-row">
                <span className="v18-script-no">{String(line.no).padStart(2, "0")}</span>
                <span className="v18-script-scene" title={line.scene}>{line.scene || "—"}</span>
                <span className="v18-script-speaker" title={line.speaker}>{line.speaker || "—"}</span>
                <span className="v18-script-text">{line.text || "（无台词）"}</span>
                <span className="v18-script-dur">{line.duration ? `${line.duration.toFixed(1)}s` : "—"}</span>
              </div>
            ))}
          </div>
        )}
        <StepNav onPrev={onPrev} onNext={onNext} nextLabel="下一步：生成成片" hint={templateLabel} />
      </div>
    </div>
  );
}

/* ------------------------------ Step 4 · 生成 3D ------------------------------ */
export function StepRun({
  template,
  run,
  starting,
  startError,
  cancelling,
  pollError,
  logOpen,
  onToggleLog,
  onStart,
  onCancel,
  onRetry,
  onNext,
  onPrev,
  finalReady,
  onOpenShots,
  onOpenJobs,
}: {
  template: CinemaTemplate | null;
  run: PipelineRun | null;
  starting: boolean;
  startError: string;
  cancelling: boolean;
  pollError: string;
  logOpen: boolean;
  onToggleLog: () => void;
  onStart: (reuseShots: boolean) => void;
  onCancel: () => void;
  onRetry: () => void;
  onNext: () => void;
  onPrev: () => void;
  finalReady: boolean;
  onOpenShots?: () => void;
  onOpenJobs?: () => void;
}) {
  const active = run && (run.status === "queued" || run.status === "running");
  const renderMeta = renderTypeMeta(template);
  const pct = pipelineRunPercent(run);
  const stageText = run ? PIPELINE_STAGE_LABEL[run.stage] || run.stage || "排队中" : "";
  const statusClass = run
    ? run.status === "done"
      ? "is-done"
      : run.status === "failed"
        ? "is-failed"
        : run.status === "canceled"
          ? "is-failed"
          : "is-running"
    : "";
  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconSpark size={17} /> ④ 生成{renderMeta.short}
          </h3>
          <p className="v18-panel-sub">
            GENERATE · {renderMeta.label} · 预计 {renderMeta.wait} · pipeline 任务（单并发）
          </p>
        </div>
        <div className="v18-panel-actions">
          <span className={"v18-tag v41-type-chip is-" + renderMeta.id}>
            {renderMeta.short} · {renderMeta.wait}
          </span>
          <span className="v18-crumb">{template ? template.id : "未选模板"}</span>
        </div>
      </div>
      <div className="v18-panel-body">
        {(startError || pollError) && (
          <ErrorBar
            title={pollError ? "任务状态读取失败" : "启动生成失败"}
            error={pollError || startError}
            onRetry={onRetry}
          />
        )}

        {!template ? (
          <EmptyBlock
            icon={<IconTemplate size={26} />}
            title="还没有选择模板"
            desc="先回到第 ① 步选择一个 3D 模板，再开始生成。"
            actions={
              <button type="button" className="cm-btn cm-btn-sm" onClick={onPrev}>
                回第 ① 步
              </button>
            }
          />
        ) : (
          <>
            <div className="v18-run-hero">
              <div className="v18-run-hero-main">
                <div className="v18-run-title">{template.label}</div>
                <div className="v18-run-meta">
                  <span>{template.id}</span>
                  <span>已就绪镜头 {template.clips.done}/{template.clips.total || "—"}</span>
                  <span>{template.final.exists ? `已有成片 ${fmtDuration(template.final.duration)}` : "尚无成片"}</span>
                </div>
              </div>
              <div className="v18-run-actions">
                {onOpenShots && (
                  <a className="cm-btn cm-btn-sm" href="#/cinema/shots" onClick={(event) => { event.preventDefault(); onOpenShots(); }} title="进入 3D 制作台 · 镜头台">
                    <IconFilm size={13} /> 镜头台
                  </a>
                )}
                {onOpenJobs && (
                  <a className="cm-btn cm-btn-sm" href="#/cinema/jobs" onClick={(event) => { event.preventDefault(); onOpenJobs(); }} title="进入 3D 制作台 · 生成队列">
                    生成队列
                  </a>
                )}
                <button
                  type="button"
                  className="cm-btn cm-btn-sm"
                  onClick={() => onStart(true)}
                  disabled={starting || Boolean(active)}
                  title="复用 outputs/templates_3d 已有镜头，只跑合成（最快）"
                >
                  {starting ? <Spinner size={12} /> : <IconRefresh size={13} />} 复用已有镜头快速合成
                </button>
                <button
                  type="button"
                  className="cm-btn cm-btn-primary cm-btn-sm"
                  onClick={() => onStart(false)}
                  disabled={starting || Boolean(active)}
                  title="完整流程：生成镜头 → 剪辑 → 海报（耗时较长）"
                >
                  {starting ? <Spinner size={12} /> : <IconSpark size={13} />}
                  {template.final.exists ? "重新生成" : "开始生成"}
                </button>
              </div>
            </div>

            <div className="v18-run-state">
              <div className="v18-run-status-row">
                <span className={"v18-run-status " + statusClass}>
                  <i />
                  {run
                    ? run.status === "queued"
                      ? "排队中"
                      : run.status === "running"
                        ? `${stageText} · 运行中`
                        : run.status === "done"
                          ? "已完成"
                          : run.status === "canceled"
                            ? "已取消"
                            : "失败"
                    : "尚未启动"}
                </span>
                {run && (
                  <span className="v18-tag is-slate">
                    {stageText || "准备"} · 阶段 {Math.max(1, Math.min(5, run.stage_index || 1))}/5
                  </span>
                )}
                {run?.progress.total ? (
                  <span className="v18-tag is-gold">
                    {run.progress.done} / {run.progress.total} 镜
                  </span>
                ) : null}
                <span className="v18-run-elapsed">{run ? `${Math.round(run.elapsed_sec)}s` : "—"}</span>
              </div>
              <div className="v18-progress" title={pct + "%"}>
                <i style={{ width: `${Math.max(active ? 2 : 0, pct)}%` }} />
              </div>
              <div className="v18-run-progress-line">
                <b>{pct}%</b>
                <span>{run ? `job ${run.id}` : "job —"}</span>
                <span style={{ marginLeft: "auto" }}>
                  {run?.status === "done" ? "可进入成片步骤" : run?.error ? run.error : "生成阶段由后台 GPU 串行执行"}
                </span>
              </div>
              {run?.error && run.status === "failed" && (
                <p className="v18-note is-warn">
                  <IconAlert size={13} /> {run.error}
                </p>
              )}
              <div className="v18-actions">
                <button type="button" className="cm-btn cm-btn-sm" onClick={onToggleLog}>
                  {logOpen ? "收起日志尾" : "展开日志尾"}
                </button>
                {active && (
                  <button type="button" className="cm-btn cm-btn-sm" onClick={onCancel} disabled={cancelling}>
                    {cancelling ? <Spinner size={12} /> : null} 取消任务
                  </button>
                )}
                {run?.status === "failed" && (
                  <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={onRetry}>
                    <IconRefresh size={12} /> 重试
                  </button>
                )}
                {finalReady && (
                  <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={onNext}>
                    查看成片 →
                  </button>
                )}
              </div>
              {logOpen && (
                <pre className="v18-log">{(run?.log_tail || []).join("\n") || "（暂无日志；任务启动后这里会显示 pipeline 输出尾）"}</pre>
              )}
            </div>
            {!run && (
              <p className="v18-note">
                <IconInfo size={13} /> {renderMeta.id === "2d"
                  ? "2D 动画走轻量 2D 渲染器（render → qc），单模板约 1–3 分钟；可复用已有镜头快速出片。"
                  : `${renderMeta.label}完整生成（T2VA + 口型）单模板预计 ${renderMeta.wait}；复用已有镜头快速合成通常在数分钟内完成。同一时间只允许一个任务（冲突时后端返回 409）。`}
              </p>
            )}
          </>
        )}
        <StepNav onPrev={onPrev} onNext={onNext} nextLabel="查看成片" nextDisabled={!finalReady} hint="完成/失败后可重复上一步" />
      </div>
    </div>
  );
}

function resolveRenderTypeFallback(id: string): string {
  return id.startsWith("real_") ? "realistic" : "3d";
}

/* ------------------------------ Step 5 · 成片 ------------------------------ */
export function StepFinal({
  template,
  onPrev,
  onNext,
  onRegenerate,
  onOpenCinema,
  busy,
}: {
  template: CinemaTemplate | null;
  onPrev: () => void;
  onNext: () => void;
  onRegenerate: () => void;
  onOpenCinema: () => void;
  busy: boolean;
}) {
  const ready = Boolean(template?.final.exists && template.final.url);
  const renderMeta = renderTypeMeta(template);
  const src = template
    ? mediaSrc(
        template.final.url ||
          (resolveRenderTypeFallback(template.id) === "2d" ? `/media/templates_2d/${template.id}/final.mp4` : `/media/templates_3d/${template.id}/final.mp4`),
      )
    : "";
  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconVideo size={17} /> ⑤ 成片
          </h3>
          <p className="v18-panel-sub">FINAL · {renderMeta.label}成片播放 / 规格 / 重新合成 · 预计 {renderMeta.wait}</p>
        </div>
        <div className="v18-panel-actions">
          {ready ? <span className="v18-tag is-jade"><IconCheck size={10} /> {renderMeta.short} 成片就绪</span> : <span className="v18-tag">尚无成片</span>}
        </div>
      </div>
      <div className="v18-panel-body">
        {!template ? (
          <EmptyBlock icon={<IconTemplate size={26} />} title="还没有选择模板" desc="回到第 ① 步选模板并生成成片。" actions={<button type="button" className="cm-btn cm-btn-sm" onClick={onPrev}>上一步</button>} />
        ) : !ready ? (
          <EmptyBlock
            icon={<IconVideo size={26} />}
            title={`还没有 ${renderMeta.short} 成片`}
            desc={`模板「${template.label}」尚无 final.mp4；回到第 ④ 步生成或复用已有镜头合成。`}
            actions={
              <>
                <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={onRegenerate} disabled={busy}>
                  {busy ? <Spinner size={12} /> : <IconSpark size={12} />} 去生成
                </button>
                <button type="button" className="cm-btn cm-btn-sm" onClick={onPrev}>回第 ④ 步</button>
              </>
            }
          />
        ) : (
          <div className="v18-final">
            <div className="v18-video-wrap">
              <video className="v18-video" src={src} controls playsInline preload="metadata" />
            </div>
            <div className="v18-final-meta">
              <span className="v18-tag is-jade">3D 电影版</span>
              <span>{fmtDuration(template.final.duration)}</span>
              <span>{fmtSize(template.final.size)}</span>
              <span>{template.id}</span>
              <span>镜头 {template.clips.done}/{template.clips.total || "—"}</span>
            </div>
            <div className="v18-actions">
              <button type="button" className="cm-btn cm-btn-sm" onClick={onRegenerate} disabled={busy}>
                <IconRefresh size={12} /> 重新合成
              </button>
              <a className="cm-btn cm-btn-sm" href={src} target="_blank" rel="noreferrer">
                <IconExternal size={12} /> 打开原片
              </a>
              <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenCinema}>
                <IconFilm size={12} /> 模板与素材
              </button>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={onNext} style={{ marginLeft: "auto" }}>
                下一步：发布前检查 →
              </button>
            </div>
          </div>
        )}
        <StepNav onPrev={onPrev} hint={template ? template.id : undefined} />
      </div>
    </div>
  );
}

/* ------------------------------ Step 6 · 发布前检查 ------------------------------ */
export function StepPreflight({
  template,
  settings,
  settingsLoading,
  settingsError,
  onOpenSettings,
  onPrev,
  onDone,
  onExportPackage,
}: {
  template: CinemaTemplate | null;
  settings: Settings | null;
  settingsLoading: boolean;
  settingsError: string;
  onOpenSettings: () => void;
  onPrev: () => void;
  onDone: () => void;
  /** V26：导出当前模板发布包 */
  onExportPackage?: () => void;
}) {
  const ready = Boolean(template?.final.exists);
  const publishConfigured = Boolean((settings as Record<string, unknown> | null)?.upload_post_api_key_configured);
  const publishEnabled = (settings as Record<string, unknown> | null)?.upload_post_enabled !== false;
  const llmConfigured = Boolean((settings as Record<string, unknown> | null)?.llm_api_key_configured);
  const rows = [
    {
      key: "final",
      label: "3D 成片",
      value: ready ? `${template?.label} · ${fmtDuration(template?.final.duration || 0)} · ${fmtSize(template?.final.size || 0)}` : "尚未生成",
      state: ready ? "ok" : "warn",
      action: null,
    },
    {
      key: "publish",
      label: "海外发布账号（Upload-Post）",
      value: settingsLoading
        ? "读取设置中…"
        : publishConfigured
          ? `已配置${publishEnabled ? "" : "（当前关闭）"} · 平台 ${((settings as Record<string, unknown> | null)?.upload_post_platforms as string[] | undefined)?.join(" / ") || "—"}`
          : "未连接（未配置 API Key）",
      state: publishConfigured ? "ok" : "warn",
      action: publishConfigured ? null : "去设置",
    },
    {
      key: "llm",
      label: "AI 模型（台本文案）",
      value: settingsLoading ? "读取设置中…" : llmConfigured ? "已配置 API Key" : "未配置（本地 Ollama / 可选）",
      state: llmConfigured ? "ok" : "warn",
      action: llmConfigured ? null : "去设置",
    },
    {
      key: "master",
      label: "母版规格",
      value: ready ? `1280×720 / 25fps · ${fmtSize(template?.final.size || 0)}` : "—",
      state: ready ? "ok" : "warn",
      action: null,
    },
  ];
  return (
    <div className="v18-panel">
      <div className="v18-panel-head">
        <div className="v18-panel-head-main">
          <h3 className="v18-panel-title">
            <IconGlobe size={17} /> ⑥ 发布前检查
          </h3>
          <p className="v18-panel-sub">PREFLIGHT · 成片 / 发布账号 / 模型 / 母版 自检（如实展示未连接项）</p>
        </div>
        <div className="v18-panel-actions">
          {onExportPackage && (
            <button
              type="button"
              className="cm-btn cm-btn-primary cm-btn-sm"
              onClick={onExportPackage}
              disabled={!ready}
              title={ready ? "导出当前模板发布包 zip（视频/封面/字幕/元数据/README）" : "该模板暂无成片，无法导出发布包"}
            >
              <IconDownload size={13} /> 导出发布包
            </button>
          )}
          <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenSettings}>
            <IconLock size={13} /> 设置
          </button>
        </div>
      </div>
      <div className="v18-panel-body">
        {settingsError && <ErrorBar title="设置读取失败" error={settingsError} />}
        <div className="v18-check-list">
          {rows.map((row) => (
            <div key={row.key} className={"v18-check-row is-" + row.state}>
              <div className="v18-check-label">
                {row.state === "ok" ? <IconCheck size={14} /> : <IconAlert size={14} />} {row.label}
              </div>
              <div className="v18-check-value">{row.value}</div>
              <div className="v18-check-state">
                {row.action ? (
                  <button type="button" className="cm-btn cm-btn-sm" onClick={onOpenSettings}>
                    {row.action}
                  </button>
                ) : (
                  <span className={"v18-tag " + (row.state === "ok" ? "is-jade" : "is-gold")}>{row.state === "ok" ? "OK" : "待处理"}</span>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="v18-note">
          <IconInfo size={13} /> 发布账号/模型未连接不影响本地 3D 成片生成与播放；如需发布，点「去设置」填写 Upload-Post API Key 与 username。
        </p>
        <StepNav onPrev={onPrev} onNext={onDone} nextLabel="完成，返回项目大厅" hint={template ? template.id : undefined} />
      </div>
    </div>
  );
}
