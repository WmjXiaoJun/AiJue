import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, errorText, mediaUrl } from "../lib/api";
import type { Job, Localization, LocalizationMeta } from "../types";
import { fmtTime, isActiveJob } from "../lib/format";
import { useToast } from "./Toast";
import { EmptyState, Notice, ProgressBar, SectionHead, Spinner } from "./ui";
import {
  IconAlert,
  IconCheck,
  IconCopy,
  IconDownload,
  IconExternal,
  IconGlobe,
  IconRefresh,
  IconSpark,
} from "./icons";

interface Props {
  episodeId: string;
  jobs: Job[];
  hasFinal: boolean;
  onChanged: () => void;
}

const LANGS = [
  { code: "en", label: "英语", native: "English", flag: "🇺🇸" },
  { code: "es", label: "西语", native: "Español", flag: "🇪🇸" },
  { code: "ja", label: "日语", native: "日本語", flag: "🇯🇵" },
  { code: "ko", label: "韩语", native: "한국어", flag: "🇰🇷" },
  { code: "pt", label: "葡语", native: "Português", flag: "🇧🇷" },
];

const LANG_MAP = new Map(LANGS.map((lang) => [lang.code, lang]));

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  queued: "排队中",
  rendering: "生成中",
  ready: "已就绪",
  failed: "生成失败",
};

function langOf(code: string): { code: string; label: string; native: string; flag: string } {
  return LANG_MAP.get(code) || { code, label: code.toUpperCase(), native: "", flag: "🌐" };
}

function jobLang(job: Job): string {
  const params = (job.params || {}) as Record<string, unknown>;
  for (const key of ["lang", "language", "languages", "langs"]) {
    const value = params[key];
    if (typeof value === "string") return value.toLowerCase();
    if (Array.isArray(value) && typeof value[0] === "string") return value[0].toLowerCase();
  }
  const message = String(job.message || "").toLowerCase();
  const hit = LANGS.find((lang) => message.includes(lang.code));
  return hit ? hit.code : "";
}

function publishRecord(meta: LocalizationMeta | null | undefined): Record<string, unknown> | null {
  const publish = meta?.publish;
  if (publish && typeof publish === "object" && !Array.isArray(publish)) return publish as Record<string, unknown>;
  return null;
}

function metaString(meta: LocalizationMeta | null | undefined, key: string): string {
  const fromPublish = publishRecord(meta)?.[key];
  if (typeof fromPublish === "string") return fromPublish;
  const value = meta?.[key];
  return typeof value === "string" ? value : "";
}

function metaHashtags(meta: LocalizationMeta | null | undefined): string[] {
  const value = publishRecord(meta)?.hashtags ?? meta?.hashtags;
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  if (typeof value === "string") return value.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
  return [];
}

function metaTranslator(meta: LocalizationMeta | null | undefined): string {
  const value = meta?.translator;
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    return String((value as { model?: string; provider?: string }).model || (value as { provider?: string }).provider || "");
  }
  return "";
}

function metaDuration(meta: LocalizationMeta | null | undefined): string {
  return formatSeconds(meta?.duration ?? meta?.duration_s);
}

function formatSeconds(value: unknown): string {
  const seconds = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const total = Math.round(seconds);
  return String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

function MetaCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <button type="button" className="btn btn-ghost btn-sm loc-copy" onClick={() => void handleCopy()} title="复制">
      {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

interface CardEntry {
  lang: string;
  localization?: Localization;
  job?: Job;
}

export function LocalizationPanel({ episodeId, jobs, hasFinal, onChanged }: Props) {
  const notify = useToast();
  const [selected, setSelected] = useState<string[]>(["en", "es"]);
  const [localizations, setLocalizations] = useState<Localization[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const startedRef = useRef(false);

  const localizeJobs = useMemo(
    () =>
      jobs
        .filter((job) => job.type === "localize")
        .slice()
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))),
    [jobs]
  );
  const activeJobs = useMemo(() => localizeJobs.filter((job) => isActiveJob(job)), [localizeJobs]);

  const load = useCallback(async () => {
    try {
      const list = await api.localizations(episodeId);
      setLocalizations(list);
      setListError(null);
    } catch (err) {
      setLocalizations((prev) => prev ?? []);
      if (err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501)) {
        setListError("多语种接口未就绪（等待后端 task-26 POST /localize / GET /localizations）：" + errorText(err));
      } else {
        setListError("多语种列表加载失败：" + errorText(err));
      }
    }
  }, [episodeId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 有本地化任务进行中时 2.5s 轮询列表
  useEffect(() => {
    if (!activeJobs.length) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [activeJobs.length, load]);

  // 任务完成/失败时刷新一次列表
  const doneKey = localizeJobs.map((job) => job.id + ":" + job.status).join(",");
  const prevDoneRef = useRef(doneKey);
  useEffect(() => {
    if (prevDoneRef.current === doneKey) return;
    prevDoneRef.current = doneKey;
    if (localizeJobs.some((job) => job.status === "done" || job.status === "failed")) void load();
  }, [doneKey, localizeJobs, load]);

  const latestByLang = useMemo(() => {
    const map = new Map<string, Localization>();
    (localizations || []).forEach((item) => {
      const prev = map.get(item.lang);
      if (!prev || String(item.created_at) > String(prev.created_at)) map.set(item.lang, item);
    });
    return map;
  }, [localizations]);

  const latestJobByLang = useMemo(() => {
    const map = new Map<string, Job>();
    localizeJobs.forEach((job) => {
      const lang = jobLang(job);
      if (lang && !map.has(lang)) map.set(lang, job);
    });
    return map;
  }, [localizeJobs]);

  const cards = useMemo<CardEntry[]>(() => {
    const langs = new Set<string>();
    latestByLang.forEach((_item, lang) => langs.add(lang));
    latestJobByLang.forEach((_job, lang) => langs.add(lang));
    return LANGS.map((lang) => lang.code)
      .filter((code) => langs.has(code))
      .map((code) => ({ lang: code, localization: latestByLang.get(code), job: latestJobByLang.get(code) }));
  }, [latestByLang, latestJobByLang]);

  const toggleLang = (code: string) => {
    setSelected((prev) => (prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code]));
  };

  const handleGenerate = async (langs: string[] = selected) => {
    if (!langs.length) {
      notify("请至少选择一种语言", "info");
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    try {
      const created = await api.localizeEpisode(episodeId, langs);
      const names = langs.map((code) => langOf(code).label).join(" / ");
      notify("已提交多语种生成：" + names + (created.length ? "（" + created.length + " 个任务）" : ""), "success");
      onChanged();
      window.setTimeout(() => void load(), 900);
    } catch (err) {
      const message = errorText(err);
      setGenerateError("生成失败：" + message + "（POST /api/episodes/" + episodeId + "/localize）");
      notify("多语种生成失败：" + message, "error");
    } finally {
      setGenerating(false);
    }
  };

  const toggleMeta = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderProgress = (job: Job) => {
    const progress = job.status === "done" ? 100 : Math.max(0, Math.min(100, job.progress || 0));
    return (
      <div className="loc-job">
        <div className="loc-job-head">
          <span className="loc-job-id">{job.id}</span>
          <span className={"loc-status loc-status-" + job.status}>{STATUS_LABEL[job.status] || job.status}</span>
          <span className="muted">{fmtTime(job.updated_at || job.created_at)}</span>
        </div>
        <ProgressBar value={progress} tone={job.status === "failed" ? "danger" : "accent"} label={Math.round(progress) + "%"} />
        {job.message && <div className="loc-job-msg">{job.message}</div>}
        {job.status === "failed" && job.error && (
          <div className="loc-error">
            <IconAlert size={13} /> {job.error}
          </div>
        )}
      </div>
    );
  };

  return (
    <section className="panel loc-panel">
      <SectionHead
        title="多语种出海版本"
        en="LOCALIZATION"
        extra={<span className="muted">中文原版保留在上方 · 同画面 + 目标语配音 / 字幕</span>}
      />
      <p className="loc-desc">
        {hasFinal
          ? "选择目标语言，一键产出口播配音、软字幕与发布元数据（YouTube / TikTok 出海）；产物为 localized/{lang}/final.mp4 + subtitles.srt。"
          : "建议先渲染中文成片；多语版本复用原画面，只替换配音与字幕。"}
      </p>

      {listError && (
        <Notice tone="warn">
          {listError}
          <button type="button" className="link-btn" onClick={() => void load()}>
            重试
          </button>
        </Notice>
      )}
      {generateError && <Notice tone="warn">{generateError}</Notice>}

      <div className="loc-picker">
        <div className="loc-lang-chips">
          {LANGS.map((lang) => {
            const active = selected.includes(lang.code);
            return (
              <button
                key={lang.code}
                type="button"
                className={"lang-chip" + (active ? " active" : "")}
                onClick={() => toggleLang(lang.code)}
              >
                <span className="lang-flag">{lang.flag}</span>
                <span className="lang-main">
                  <b>{lang.label}</b>
                  <em>{lang.native}</em>
                </span>
                <span className="lang-check">{active ? <IconCheck size={12} /> : null}</span>
              </button>
            );
          })}
        </div>
        <div className="loc-picker-actions">
          <span className="loc-selected">已选 {selected.length} 种语言</span>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => void handleGenerate()}
            disabled={generating || !selected.length}
          >
            {generating ? <Spinner size={13} /> : <IconGlobe size={14} />}
            {generating ? "提交中…" : "生成多语种版本"}
          </button>
        </div>
      </div>

      {activeJobs.length > 0 && (
        <div className="loc-active">
          <div className="loc-active-title">
            <Spinner size={13} /> 正在生成 {activeJobs.length} 个语种版本（2.5s 轮询）
          </div>
          {activeJobs.map((job) => (
            <div key={job.id} className="loc-active-item">
              <span className="loc-job-lang">{langOf(jobLang(job)).label || "多语种"}</span>
              {renderProgress(job)}
            </div>
          ))}
        </div>
      )}

      {cards.length === 0 ? (
        <EmptyState
          icon={<IconGlobe size={28} />}
          title={localizations === null ? "正在加载多语种版本…" : "还没有多语种版本"}
          hint="默认勾选英语 + 西语（出海主发）；生成后每语言卡片支持播放、下载 mp4/srt 与发布元数据复制。"
        />
      ) : (
        <div className="loc-grid">
          {cards.map(({ lang, localization, job }) => {
            const info = langOf(lang);
            const meta = localization?.meta || null;
            const status = localization?.status || job?.status || "queued";
            const title = metaString(meta, "title");
            const description = metaString(meta, "description");
            const hashtags = metaHashtags(meta);
            const duration = metaDuration(meta);
            const translatorLabel = metaTranslator(meta);
            const errorTextValue =
              metaString(meta, "error") || (job?.status === "failed" ? job.error || job.message || "" : "");
            const isExpanded = expanded.has(lang);
            const videoUrl = localization?.video_url ? mediaUrl(localization.video_url) : "";
            const srtUrl = localization?.srt_url ? mediaUrl(localization.srt_url) : "";
            return (
              <article key={lang} className={"loc-card loc-card-" + status}>
                <div className="loc-card-head">
                  <span className="loc-flag">{info.flag}</span>
                  <div className="loc-lang-name">
                    <b>{info.label}</b>
                    <em>
                      {info.native} · {lang.toUpperCase()}
                    </em>
                  </div>
                  <span className={"loc-status loc-status-" + status}>{STATUS_LABEL[status] || status}</span>
                </div>

                <div className="loc-meta-row">
                  <span>时长 {duration}</span>
                  <i />
                  <span>生成时间 {localization ? fmtTime(localization.created_at) : job ? fmtTime(job.created_at) : "—"}</span>
                  {translatorLabel && (
                    <>
                      <i />
                      <span>{translatorLabel}</span>
                    </>
                  )}
                </div>

                {status === "failed" || errorTextValue ? (
                  <div className="loc-failed">
                    <div className="loc-error">
                      <IconAlert size={14} /> {errorTextValue || "生成失败，可重试"}
                    </div>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => void handleGenerate([lang])}
                      disabled={generating}
                    >
                      <IconRefresh size={13} /> 重试 {info.label}
                    </button>
                  </div>
                ) : videoUrl ? (
                  <video className="loc-video" src={videoUrl} controls playsInline preload="metadata" />
                ) : (
                  <div className="loc-video loc-video-pending">
                    {isActiveStatus(status) ? <Spinner size={16} /> : <IconGlobe size={22} />}
                    <span>{STATUS_LABEL[status] === "生成中" ? "多语版本渲染中…" : "等待渲染"}</span>
                  </div>
                )}

                {videoUrl && (
                  <div className="loc-actions">
                    <a className="btn btn-outline btn-sm" href={videoUrl} download={episodeId + "_" + lang + ".mp4"}>
                      <IconDownload size={13} /> 下载 mp4
                    </a>
                    {srtUrl && (
                      <a className="btn btn-ghost btn-sm" href={srtUrl} download={episodeId + "_" + lang + ".srt"}>
                        <IconDownload size={13} /> 下载 srt
                      </a>
                    )}
                    <a className="btn btn-ghost btn-sm" href={videoUrl} target="_blank" rel="noreferrer">
                      <IconExternal size={13} /> 新窗口
                    </a>
                    {(title || description || hashtags.length > 0) && (
                      <button type="button" className="btn btn-ghost btn-sm loc-meta-toggle" onClick={() => toggleMeta(lang)}>
                        {isExpanded ? "收起元数据" : "发布元数据"}
                      </button>
                    )}
                  </div>
                )}

                {isExpanded && (
                  <div className="loc-metadata">
                    <div className="loc-meta-field">
                      <div className="loc-meta-label">
                        <span>标题</span>
                        <MetaCopyButton text={title} />
                      </div>
                      <p className="loc-meta-title">{title || "—"}</p>
                    </div>
                    <div className="loc-meta-field">
                      <div className="loc-meta-label">
                        <span>描述</span>
                        <MetaCopyButton text={description} />
                      </div>
                      <p className="loc-meta-desc">{description || "—"}</p>
                    </div>
                    <div className="loc-meta-field">
                      <div className="loc-meta-label">
                        <span>Hashtags</span>
                        {hashtags.length > 0 && <MetaCopyButton text={hashtags.join(" ")} />}
                      </div>
                      <div className="loc-hashtags">
                        {hashtags.length ? hashtags.map((tag) => <span key={tag}>{tag.startsWith("#") ? tag : "#" + tag}</span>) : <span className="muted">—</span>}
                      </div>
                    </div>
                    <div className="loc-meta-foot">
                      产物路径 /media/episodes/{episodeId}/localized/{lang}/final.mp4 · subtitles.srt
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      <div className="loc-note">
        <IconGlobe size={13} /> 多语种为 MVP：EN 主发 / ES 次发，JA·KO·PT 同步支持；只替换配音与字幕，不改变画面与时长。
      </div>
    </section>
  );
}

function isActiveStatus(status: string): boolean {
  return status === "queued" || status === "running" || status === "rendering";
}
