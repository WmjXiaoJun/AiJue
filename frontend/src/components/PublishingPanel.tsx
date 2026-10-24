import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, errorText } from "../lib/api";
import type { Episode, Job, Localization, PublishConfig, PublishRecord } from "../types";
import { fmtTime, isActiveJob } from "../lib/format";
import { useToast } from "./Toast";
import { EmptyState, Notice, ProgressBar, SectionHead, Spinner } from "./ui";
import { IconAlert, IconCheck, IconCopy, IconGlobe, IconInfo, IconRefresh, IconSend } from "./icons";
import {
  PUBLISH_BRAND_TITLE,
  PUBLISH_LANGS,
  PUBLISH_PLATFORMS,
  PUBLISH_PRIVACY_OPTIONS,
  PUBLISH_BASE_HASHTAGS,
  UPLOAD_POST_URL,
  defaultPublishCopy,
  formatHashtags,
  langLabel,
  parseHashtags,
  platformLabel,
} from "../lib/publishing";

interface Props {
  episode: Episode;
  jobs: Job[];
  onChanged: () => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "发布中",
  pending: "处理中",
  processing: "处理中",
  completed: "已发布",
  done: "已完成",
  failed: "失败",
  dry_run: "预检",
  prechecked: "预检通过",
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] || status;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleCopy()} title={label}>
      {copied ? <IconCheck size={12} /> : <IconCopy size={12} />}
      {copied ? "已复制" : label}
    </button>
  );
}

function isUnsupported(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}

export function PublishingPanel({ episode, jobs, onChanged }: Props) {
  const notify = useToast();
  const [config, setConfig] = useState<PublishConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [records, setRecords] = useState<PublishRecord[] | null>(null);
  const [recordsError, setRecordsError] = useState<string | null>(null);
  const [localizations, setLocalizations] = useState<Localization[]>([]);

  const defaultCopy = useMemo(() => defaultPublishCopy(episode.title, episode.no || 1), [episode.title, episode.no]);

  const [platforms, setPlatforms] = useState<string[]>(["youtube", "tiktok", "instagram"]);
  const [lang, setLang] = useState("zh");
  const [version, setVersion] = useState<"final" | "dynamic">("final");
  const [privacy, setPrivacy] = useState("unlisted");
  const [title, setTitle] = useState(defaultCopy.title);
  const [description, setDescription] = useState(defaultCopy.description);
  const [hashtagsText, setHashtagsText] = useState(formatHashtags(defaultCopy.hashtags));
  const [metaEdited, setMetaEdited] = useState(false);

  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState<null | "dry" | "publish">(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastDryRun, setLastDryRun] = useState(false);
  const platformsInitRef = useRef(false);

  const loadConfig = useCallback(async () => {
    try {
      const data = await api.publishConfig();
      setConfig(data);
      setConfigError(null);
      if (!platformsInitRef.current && Array.isArray(data.platforms) && data.platforms.length) {
        setPlatforms(data.platforms);
        platformsInitRef.current = true;
      }
      if (data.youtube_privacy) setPrivacy(data.youtube_privacy);
    } catch (err) {
      setConfig(null);
      setConfigError(
        isUnsupported(err)
          ? "发布接口未就绪（等待后端 task-33：GET /publish/config）：" + errorText(err)
          : "发布配置读取失败：" + errorText(err)
      );
    }
  }, []);

  const loadRecords = useCallback(async () => {
    try {
      const list = await api.publishes(episode.id);
      setRecords(list);
      setRecordsError(null);
    } catch (err) {
      setRecords((prev) => prev ?? []);
      setRecordsError(isUnsupported(err) ? "发布记录接口未就绪（等待后端 task-33）" : "发布记录加载失败：" + errorText(err));
    }
  }, [episode.id]);

  const loadLocalizations = useCallback(async () => {
    try {
      setLocalizations(await api.localizations(episode.id));
    } catch {
      /* 未就绪时忽略：仅影响多语元数据预填 */
    }
  }, [episode.id]);

  useEffect(() => {
    void loadConfig();
    void loadRecords();
    void loadLocalizations();
  }, [loadConfig, loadRecords, loadLocalizations]);

  const readyLocalization = useMemo(
    () => localizations.find((item) => item.lang === lang && item.status === "ready"),
    [localizations, lang]
  );

  // 按语言预填发布文案（中文用剧集默认；其它语言优先 localized meta.publish）
  useEffect(() => {
    if (metaEdited) return;
    if (lang === "zh") {
      setTitle(defaultCopy.title);
      setDescription(defaultCopy.description);
      setHashtagsText(formatHashtags(defaultCopy.hashtags));
      return;
    }
    const publish = (readyLocalization?.meta?.publish || null) as
      | { title?: string; description?: string; hashtags?: string[] | string }
      | null;
    const localizedTitle = typeof publish?.title === "string" ? publish.title : defaultCopy.title;
    const localizedDesc = typeof publish?.description === "string" ? publish.description : defaultCopy.description;
    const rawTags = publish?.hashtags;
    const tags = Array.isArray(rawTags)
      ? rawTags
      : typeof rawTags === "string"
        ? parseHashtags(rawTags)
        : defaultCopy.hashtags;
    setTitle(localizedTitle);
    setDescription(localizedDesc);
    setHashtagsText(formatHashtags(tags));
  }, [lang, readyLocalization?.id, metaEdited, defaultCopy]);

  const activeJob = useMemo(() => {
    if (job) return job;
    const latest = jobs
      .filter((item) => item.type === "publish")
      .slice()
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
    return latest || null;
  }, [job, jobs]);

  // 发布任务进度轮询
  useEffect(() => {
    if (!job || !isActiveJob(job)) return;
    let alive = true;
    const tick = async () => {
      try {
        const next = await api.job(job.id);
        if (!alive) return;
        setJob(next);
        if (next.status === "done") {
          notify(lastDryRun ? "预检完成（未联网上传）" : "发布任务完成", "success");
          void loadRecords();
          onChanged();
        } else if (next.status === "failed") {
          setSubmitError((next.error || next.message || "发布失败") + "（不会自动重试，避免重复发布）");
          notify("发布失败：" + (next.error || next.message || "未知原因"), "error");
          void loadRecords();
        }
      } catch {
        /* 网络抖动保留上次状态 */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1800);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.status, lastDryRun, loadRecords, notify, onChanged]);

  const togglePlatform = (id: string) => {
    setPlatforms((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const appendHashtag = (tag: string) => {
    const current = parseHashtags(hashtagsText);
    if (current.includes(tag)) return;
    setHashtagsText(formatHashtags([...current, tag]));
    setMetaEdited(true);
  };

  const submit = async (dryRun: boolean) => {
    if (!platforms.length) {
      notify("请至少选择一个发布平台", "info");
      return;
    }
    if (dryRun) {
      /* 预检不联网 */
    } else {
      const confirmed = window.confirm(
        "将把「" +
          title.trim().slice(0, 40) +
          "」真实上传到 " +
          platforms.map(platformLabel).join(" / ") +
          "，可能立即公开。\n\n请确认已获得平台账号授权并同意平台条款后再继续。"
      );
      if (!confirmed) return;
    }
    setBusy(dryRun ? "dry" : "publish");
    setSubmitError(null);
    setLastDryRun(dryRun);
    setJob(null);
    try {
      const created = await api.publishEpisode(episode.id, {
        platforms,
        lang,
        title: title.trim(),
        description: description.trim(),
        hashtags: parseHashtags(hashtagsText),
        privacy,
        dry_run: dryRun,
        version,
      });
      setJob(created);
      notify(dryRun ? "预检任务已提交（不联网）" : "发布任务已提交", "success");
      onChanged();
    } catch (err) {
      const message = errorText(err);
      setSubmitError((dryRun ? "预检失败：" : "发布失败：") + message);
      notify((dryRun ? "预检失败：" : "发布失败：") + message, "error");
    } finally {
      setBusy(null);
    }
  };

  const unsupported = !!configError && !config;
  const configured = !!config?.configured && !!config?.enabled;
  const jobRunning = !!activeJob && isActiveJob(activeJob);
  const jobDone = activeJob?.status === "done";
  const jobFailed = activeJob?.status === "failed";
  const dryRunJob = lastDryRun && activeJob;

  return (
    <section className="panel publish-panel">
      <SectionHead
        title="一键发布（海外）"
        en="PUBLISH"
        extra={
          <span className="publish-brand">
            {PUBLISH_BRAND_TITLE} · Upload-Post 发布网关
          </span>
        }
      />
      <p className="publish-desc">
        通过 upload-post.com 将成片一键分发到 YouTube / TikTok / Instagram；支持中文原版与多语版本，
        预检只做本地文件 / 时长 / 平台限制 / 配置校验，<b>不会上传</b>。
      </p>

      {configError && (
        <Notice tone="warn">
          {configError}
          <button type="button" className="link-btn" onClick={() => void loadConfig()}>
            重试
          </button>
        </Notice>
      )}

      {!configError && !config && (
        <div className="publish-config-loading">
          <Spinner size={14} /> 正在读取 Upload-Post 配置…
        </div>
      )}

      {config && (
        <div className={"publish-config " + (configured ? "is-ready" : "is-missing")}>
          <div className="publish-config-head">
            <span className={"publish-status-dot " + (configured ? "ok" : "warn")} />
            <b>{configured ? "Upload-Post 已连接" : "Upload-Post 未配置"}</b>
            {config.username_masked && <span className="muted">@{config.username_masked}</span>}
            {config.enabled === false && <span className="muted">· 发布开关已关闭</span>}
          </div>
          {!configured && (
            <div className="publish-guide">
              <IconInfo size={14} />
              <div>
                <p>
                  前往 <a href={UPLOAD_POST_URL} target="_blank" rel="noreferrer">upload-post.com</a> 注册 → 连接
                  YouTube / TikTok / Instagram 账号 → 复制 API Key 与 username → 在「设置 · 海外发布（Upload-Post）」中填入并启用。
                </p>
                <p className="muted">未配置时只能预检；发布按钮保持禁用，不会伪造成功或尝试上传。</p>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="publish-grid">
        <div className="publish-langs">
          <span className="publish-label">语言版本</span>
          <div className="publish-lang-chips">
            {PUBLISH_LANGS.map((item) => {
              const ready = localizations.some((loc) => loc.lang === item.code && loc.status === "ready");
              return (
                <button
                  key={item.code}
                  type="button"
                  className={"publish-lang" + (lang === item.code ? " active" : "")}
                  onClick={() => setLang(item.code)}
                >
                  <b>{item.label}</b>
                  <em>{item.native}</em>
                  {item.code !== "zh" && ready && <span className="publish-lang-ready">已本地化</span>}
                </button>
              );
            })}
          </div>
          <div className="publish-version">
            <span className="publish-label">版本</span>
            <div className="segmented">
              <button
                type="button"
                className={"segmented-item" + (version === "final" ? " active" : "")}
                onClick={() => setVersion("final")}
              >
                final · 成片
              </button>
              <button
                type="button"
                className={"segmented-item" + (version === "dynamic" ? " active" : "")}
                onClick={() => setVersion("dynamic")}
                title="优先 final_dynamic.mp4（需已渲染动态版）"
              >
                dynamic · 动态版
              </button>
            </div>
          </div>
        </div>

        <div className="publish-platforms">
          <span className="publish-label">发布平台（多选）</span>
          <div className="publish-platform-list">
            {PUBLISH_PLATFORMS.map((platform) => {
              const active = platforms.includes(platform.id);
              return (
                <button
                  key={platform.id}
                  type="button"
                  className={"publish-platform" + (active ? " active" : "")}
                  onClick={() => togglePlatform(platform.id)}
                >
                  <span className="publish-platform-head">
                    <span className={"check-box" + (active ? " checked" : "")}>{active && <IconCheck size={11} />}</span>
                    <b>{platform.label}</b>
                  </span>
                  <span className="publish-platform-limits">
                    {platform.limits.map((limit) => (
                      <em key={limit}>{limit}</em>
                    ))}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="publish-privacy">
            <span className="publish-label">YouTube 可见性</span>
            <select className="select" value={privacy} onChange={(event) => setPrivacy(event.target.value)}>
              {PUBLISH_PRIVACY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="publish-meta">
        <label className="publish-field">
          <span>
            标题 <em>{title.length}/100</em>
          </span>
          <input
            className="input"
            value={title}
            maxLength={100}
            onChange={(event) => {
              setTitle(event.target.value);
              setMetaEdited(true);
            }}
            placeholder="发布会展示的标题"
          />
        </label>
        <label className="publish-field">
          <span>
            描述 <em>{description.length}/5000</em>
          </span>
          <textarea
            className="textarea"
            rows={3}
            value={description}
            maxLength={5000}
            onChange={(event) => {
              setDescription(event.target.value);
              setMetaEdited(true);
            }}
            placeholder="发布描述（可含话题与 AI 生成说明）"
          />
        </label>
        <label className="publish-field">
          <span>
            Hashtags <em>空格分隔</em>
          </span>
          <input
            className="input"
            value={hashtagsText}
            onChange={(event) => {
              setHashtagsText(event.target.value);
              setMetaEdited(true);
            }}
            placeholder="#造片局 #AI微综艺 #AIVariety"
          />
          <div className="publish-tag-suggest">
            {PUBLISH_BASE_HASHTAGS.map((tag) => (
              <button key={tag} type="button" className="publish-tag" onClick={() => appendHashtag(tag)}>
                + {tag}
              </button>
            ))}
          </div>
        </label>
      </div>

      <div className="publish-actions">
        <div className="publish-actions-hint">
          {lang === "zh" ? "中文原版 /media/episodes/" + episode.id + "/final.mp4" : "多语版本 localized/" + lang + "/final.mp4"}
          {version === "dynamic" ? " · dynamic 优先 final_dynamic.mp4" : ""}
          {lang !== "zh" && !readyLocalization ? " · 该语言尚未生成多语版本，预检会提示" : ""}
        </div>
        <button
          type="button"
          className="btn btn-outline"
          onClick={() => void submit(true)}
          disabled={busy !== null || jobRunning}
          title="本地校验：文件/时长/平台限制/配置"
        >
          {busy === "dry" ? <Spinner size={14} /> : <IconGlobe size={15} />}
          {busy === "dry" ? "预检提交中…" : "预检"}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void submit(false)}
          disabled={busy !== null || jobRunning || !configured}
          title={configured ? "真实上传到所选平台" : "需先在设置中配置 Upload-Post"}
        >
          {busy === "publish" || (jobRunning && !lastDryRun) ? <Spinner size={14} /> : <IconSend size={15} />}
          {jobRunning && !lastDryRun ? "发布中…" : "发布"}
        </button>
      </div>

      {submitError && (
        <div className="publish-error">
          <IconAlert size={14} /> {submitError}
        </div>
      )}

      {(activeJob || jobDone || jobFailed) && (
        <div className="publish-job">
          <div className="publish-job-head">
            <span className="publish-job-id">{activeJob?.id}</span>
            <span className={"publish-job-status publish-job-status-" + (activeJob?.status || "queued")}>
              {lastDryRun ? "预检 · " : ""}
              {statusLabel(activeJob?.status || "")}
            </span>
            <span className="muted">{fmtTime(activeJob?.created_at || "")}</span>
          </div>
          <ProgressBar
            value={jobDone ? 100 : activeJob?.progress || 0}
            tone={jobFailed ? "danger" : "accent"}
            label={Math.round(jobDone ? 100 : activeJob?.progress || 0) + "%"}
          />
          {activeJob?.message && <div className="publish-job-msg">{activeJob.message}</div>}
          {dryRunJob && jobDone && (
            <div className="publish-dry-result">
              <IconCheck size={14} /> 预检通过 · 未联网上传。可到「设置」完善配置后再点「发布」。
            </div>
          )}
          {jobFailed && activeJob?.error && (
            <div className="publish-error">
              <IconAlert size={14} /> {activeJob.error}
            </div>
          )}
        </div>
      )}

      <div className="publish-records">
        <div className="publish-records-head">
          <span className="publish-label">发布记录</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void loadRecords()} title="刷新发布记录">
            <IconRefresh size={13} /> 刷新
          </button>
        </div>
        {recordsError && (
          <Notice tone="warn">
            {recordsError}
            <button type="button" className="link-btn" onClick={() => void loadRecords()}>
              重试
            </button>
          </Notice>
        )}
        {records === null ? (
          <div className="publish-config-loading">
            <Spinner size={13} /> 正在读取发布记录…
          </div>
        ) : records.length === 0 ? (
          <EmptyState
            icon={<IconSend size={22} />}
            title="还没有发布记录"
            hint="先点「预检」验证文件与平台限制；配置 Upload-Post 后再发布，记录会在这里显示平台 / 状态 / request_id。"
          />
        ) : (
          <div className="publish-record-list">
            {records.map((record) => {
              const recordMeta = record.meta || {};
              const dryRun = recordMeta.dry_run === true || recordMeta.dry_run === "true";
              const warnings = Array.isArray(recordMeta.warnings)
                ? recordMeta.warnings.filter((item): item is string => typeof item === "string")
                : [];
              const errors = Array.isArray(recordMeta.errors)
                ? recordMeta.errors.filter((item): item is string => typeof item === "string")
                : [];
              const duration = typeof recordMeta.duration === "number" ? recordMeta.duration : null;
              const sizeMb = typeof recordMeta.size_mb === "number" ? recordMeta.size_mb : null;
              return (
                <article key={record.id} className={"publish-record publish-record-" + record.status}>
                  <div className="publish-record-head">
                    <span className="publish-record-platforms">
                      {record.platforms.map((platform) => (
                        <span key={platform} className="publish-platform-tag">
                          {platformLabel(platform)}
                        </span>
                      ))}
                      {dryRun && <span className="publish-dry-tag">预检</span>}
                    </span>
                    <span className={"publish-record-status publish-record-status-" + record.status}>
                      {statusLabel(record.status)}
                    </span>
                    <span className="muted">{fmtTime(record.created_at)}</span>
                  </div>
                  <div className="publish-record-title">{record.title || "—"}</div>
                  <div className="publish-record-meta">
                    <span>{langLabel(record.lang || "zh")}</span>
                    {duration !== null && (
                      <>
                        <i />
                        <span>时长 {Math.round(duration * 10) / 10}s</span>
                      </>
                    )}
                    {sizeMb !== null && (
                      <>
                        <i />
                        <span>{sizeMb.toFixed(1)} MB</span>
                      </>
                    )}
                    {typeof recordMeta.privacy === "string" && (
                      <>
                        <i />
                        <span>{recordMeta.privacy}</span>
                      </>
                    )}
                    {record.request_id && (
                      <>
                        <i />
                        <span className="publish-request-id">
                          request_id <code>{record.request_id}</code>
                        </span>
                        <CopyButton text={record.request_id} label="复制" />
                      </>
                    )}
                  </div>
                  {warnings.length > 0 && (
                    <div className="publish-warnings">
                      {warnings.map((warning) => (
                        <span key={warning} className="publish-warning">
                          <IconAlert size={12} /> {warning}
                        </span>
                      ))}
                    </div>
                  )}
                  {errors.length > 0 && (
                    <div className="publish-warnings">
                      {errors.map((item) => (
                        <span key={item} className="publish-warning is-error">
                          <IconAlert size={12} /> {item}
                        </span>
                      ))}
                    </div>
                  )}
                  {record.error && (
                    <div className="publish-error">
                      <IconAlert size={13} /> {record.error}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
