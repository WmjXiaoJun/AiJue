import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorText, mediaUrl } from "../lib/api";
import type { Character, ConceptFiles, Job } from "../types";
import {
  CONCEPT_STYLE_OPTIONS,
  EXPRESSION_LABEL,
  THREE_VIEW_LABEL,
  conceptImages,
  fmtTime,
  isActiveJob,
  type ConceptImageItem,
} from "../lib/format";
import { useToast } from "./Toast";
import { EmptyState, Notice, ProgressBar, Spinner } from "./ui";
import { IconAlert, IconImage, IconPlay, IconRefresh, IconSpark, IconVideo } from "./icons";

interface Props {
  character: Character;
  concept?: ConceptFiles | null;
  supported: boolean;
  error: string | null;
  onRefreshConcepts: () => Promise<void> | void;
  onCharacterChanged: () => void;
}

/** 一致性产物探测缓存（避免每次打开抽屉都发 404 HEAD） */
const consistentProbeCache = new Map<string, "available" | "missing">();

const CONSISTENT_THREE_VIEW_KEYS = ["front", "side", "three_quarter"];
const CONSISTENT_EXPRESSION_KEYS = ["neutral", "happy", "surprised", "thinking"];

function buildConsistentItems(
  cid: string,
  source:
    | { three_view?: Record<string, string> | string[] | null; expressions?: Record<string, string> | null }
    | null
    | undefined,
  fixedPath: boolean
): ConceptImageItem[] {
  const items: ConceptImageItem[] = [];
  const three: Record<string, string> = {};
  if (source?.three_view) {
    if (Array.isArray(source.three_view)) {
      source.three_view.forEach((url, index) => {
        const key = CONSISTENT_THREE_VIEW_KEYS[index] || String(index);
        if (url) three[key] = url;
      });
    } else {
      Object.assign(three, source.three_view);
    }
  }
  CONSISTENT_THREE_VIEW_KEYS.forEach((key) => {
    const url =
      three[key] || (fixedPath ? "/media/concepts/" + cid + "/consistent/three_view/" + key + ".png" : "");
    if (url) {
      items.push({
        key: "consistent_three_" + key,
        label: "一致性三视图 · " + (THREE_VIEW_LABEL[key] || key),
        url,
        group: "three_view",
      });
    }
  });
  const expr: Record<string, string> = {};
  if (source?.expressions) Object.assign(expr, source.expressions);
  CONSISTENT_EXPRESSION_KEYS.forEach((key) => {
    const url =
      expr[key] || (fixedPath ? "/media/concepts/" + cid + "/consistent/expressions/" + key + ".png" : "");
    if (url) {
      items.push({
        key: "consistent_expr_" + key,
        label: "一致性表情 · " + (EXPRESSION_LABEL[key] || key),
        url,
        group: "expressions",
      });
    }
  });
  return items;
}

/**
 * 概念图 path 解析：优先 outputs 相对路径（后端 _resolve_output_file 接受仓库相对路径），
 * 回退 /media URL（后端同样兼容），兼容不同实现。
 */
function conceptTargetPath(url: string, cid: string): { path: string; fallback: string } {
  const marker = "/media/concepts/" + cid + "/";
  const index = url.indexOf(marker);
  const media = url.startsWith("http") ? url : url.startsWith("/") ? url : "/" + url;
  if (index >= 0) {
    const rest = url.slice(index + marker.length);
    return { path: "outputs/concepts/" + cid + "/" + rest, fallback: media };
  }
  if (url.startsWith("/media/")) {
    return { path: "outputs/" + url.slice("/media/".length), fallback: media };
  }
  return { path: url, fallback: media };
}

export function ConceptStudio({ character, concept, supported, error, onRefreshConcepts, onCharacterChanged }: Props) {
  const notify = useToast();
  const [style, setStyle] = useState("anime");
  const [promptExtra, setPromptExtra] = useState("");
  const [seed, setSeed] = useState("");
  const [generating, setGenerating] = useState(false);
  const [conceptJob, setConceptJob] = useState<Job | null>(null);
  const [h3Job, setH3Job] = useState<Job | null>(null);
  const [h3Url, setH3Url] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [consistentMode, setConsistentMode] = useState<"checking" | "api" | "fixed" | "none">("checking");
  const [h3Version, setH3Version] = useState(() => Date.now());

  const images = useMemo(() => conceptImages(concept), [concept]);
  const groups = useMemo(
    () => ({
      portrait: images.filter((item) => item.group === "portrait"),
      three_view: images.filter((item) => item.group === "three_view"),
      expressions: images.filter((item) => item.group === "expressions"),
      sheet: images.filter((item) => item.group === "sheet"),
    }),
    [images]
  );
  const hasConcept = images.length > 0;

  // 一致性产物（contract/CONSISTENCY.md）：优先 /concepts.consistent，否则按固定路径探测
  const consistentApi = useMemo(() => {
    if (!concept?.consistent) return null;
    const items = buildConsistentItems(character.id, concept.consistent, false);
    return items.length ? items : null;
  }, [concept, character.id]);
  const hasConsistent = consistentMode === "api" ? !!consistentApi : consistentMode === "fixed";
  const consistentItems = useMemo(() => {
    if (!hasConsistent) return [] as ConceptImageItem[];
    return consistentApi || buildConsistentItems(character.id, null, true);
  }, [hasConsistent, consistentApi, character.id]);
  const consistentThree = consistentItems.filter((item) => item.group === "three_view");
  const consistentExpressions = consistentItems.filter((item) => item.group === "expressions");

  useEffect(() => {
    setH3Version(Date.now());
    if (consistentApi) {
      setConsistentMode("api");
      return;
    }
    const cached = consistentProbeCache.get(character.id);
    if (cached) {
      setConsistentMode(cached === "available" ? "fixed" : "none");
      return;
    }
    let alive = true;
    setConsistentMode("checking");
    fetch(mediaUrl("/media/concepts/" + character.id + "/consistent/meta.json"), { method: "HEAD" })
      .then((res) => {
        if (!alive) return;
        consistentProbeCache.set(character.id, res.ok ? "available" : "missing");
        setConsistentMode(res.ok ? "fixed" : "none");
      })
      .catch(() => {
        if (!alive) return;
        consistentProbeCache.set(character.id, "missing");
        setConsistentMode("none");
      });
    return () => {
      alive = false;
    };
  }, [character.id, consistentApi]);

  // 概念图任务轮询（1.6s）
  useEffect(() => {
    if (!conceptJob || conceptJob.status === "done" || conceptJob.status === "failed" || conceptJob.status === "canceled") {
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const job = await api.job(conceptJob.id);
        if (!alive) return;
        setConceptJob(job);
        if (job.status === "done") {
          notify("概念图生成完成", "success");
          setH3Version(Date.now());
          await onRefreshConcepts();
          onCharacterChanged();
        } else if (job.status === "failed" || job.status === "canceled") {
          notify("概念图生成失败：" + (job.error || job.message || "未知原因"), "error");
        }
      } catch {
        /* 下一轮重试 */
      }
    };
    const timer = window.setInterval(() => void tick(), 1600);
    void tick();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [conceptJob?.id, conceptJob?.status, notify, onRefreshConcepts, onCharacterChanged]);

  // H3 动画任务轮询（1.6s）
  useEffect(() => {
    if (!h3Job || h3Job.status === "done" || h3Job.status === "failed" || h3Job.status === "canceled") {
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const job = await api.job(h3Job.id);
        if (!alive) return;
        setH3Job(job);
        if (job.status === "done") {
          setH3Url(mediaUrl(job.output_url || "/media/h3/" + character.id + "_h3.mp4"));
          setH3Version(Date.now());
          notify("H3 角色动画生成完成", "success");
          onCharacterChanged();
        } else if (job.status === "failed" || job.status === "canceled") {
          notify("H3 动画生成失败：" + (job.error || job.message || "未知原因"), "error");
        }
      } catch {
        /* 下一轮重试 */
      }
    };
    const timer = window.setInterval(() => void tick(), 1600);
    void tick();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [h3Job?.id, h3Job?.status, notify, character.id, onCharacterChanged]);

  // 角色上已回填 H3 视频时直接展示
  useEffect(() => {
    if (character.h3_video_url && !h3Url) setH3Url(mediaUrl(character.h3_video_url));
  }, [character.h3_video_url, h3Url]);

  const handleGenerate = async () => {
    setGenerating(true);
    setActionError(null);
    try {
      const body: { style: string; prompt_extra?: string; seed?: number } = { style };
      if (promptExtra.trim()) body.prompt_extra = promptExtra.trim();
      const seedValue = Number(seed);
      if (seed.trim() && Number.isFinite(seedValue)) body.seed = Math.floor(seedValue);
      const job = await api.generateConcept(character.id, body);
      setConceptJob(job);
      notify("概念图任务已提交（" + (CONCEPT_STYLE_OPTIONS.find((item) => item.value === style)?.label || style) + "）", "success");
    } catch (err) {
      const message = errorText(err);
      setActionError(message);
      notify("概念图生成提交失败：" + message, "error");
    } finally {
      setGenerating(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    consistentProbeCache.delete(character.id);
    setH3Version(Date.now());
    try {
      await onRefreshConcepts();
    } finally {
      setRefreshing(false);
    }
  };

  const handleSetPortrait = async (image: ConceptImageItem) => {
    setActionBusy("portrait:" + image.key);
    setActionError(null);
    try {
      const target = conceptTargetPath(image.url, character.id);
      await api.setPortraitFromConcept(character.id, target.path, target.fallback);
      notify("已将「" + image.label + "」设为角色立绘", "success");
      await onRefreshConcepts();
      onCharacterChanged();
    } catch (err) {
      const message = errorText(err);
      setActionError("设置立绘失败：" + message);
      notify("设置立绘失败：" + message, "error");
    } finally {
      setActionBusy(null);
    }
  };

  const handleH3 = async (image?: ConceptImageItem) => {
    setActionBusy("h3:" + (image ? image.key : "portrait"));
    setActionError(null);
    try {
      const body: { image?: string; prompt?: string } = {};
      let fallback: string | undefined;
      if (image) {
        const target = conceptTargetPath(image.url, character.id);
        body.image = target.path;
        fallback = target.fallback;
      }
      if (promptExtra.trim()) body.prompt = promptExtra.trim();
      const job = await api.h3Animate(character.id, body, fallback);
      setH3Job(job);
      notify("H3 动画任务已提交", "success");
    } catch (err) {
      const message = errorText(err);
      setActionError("H3 动画提交失败：" + message);
      notify("H3 动画提交失败：" + message, "error");
    } finally {
      setActionBusy(null);
    }
  };

  const conceptRunning = !!conceptJob && isActiveJob(conceptJob);
  const h3Running = !!h3Job && isActiveJob(h3Job);

  const renderImage = (image: ConceptImageItem, size: "lg" | "md") => (
    <figure key={image.key} className={"concept-image concept-image-" + size}>
      <div className="concept-image-frame">
        <img src={mediaUrl(image.url)} alt={character.name + " " + image.label} loading="lazy" />
        <div className="concept-image-actions">
          <button
            type="button"
            className="concept-action"
            disabled={!!actionBusy}
            onClick={() => void handleSetPortrait(image)}
            title="把这张图设为角色立绘"
          >
            {actionBusy === "portrait:" + image.key ? <Spinner size={12} /> : null}
            设为立绘
          </button>
          <button
            type="button"
            className="concept-action concept-action-h3"
            disabled={!!actionBusy}
            onClick={() => void handleH3(image)}
            title="用这张图生成 H3 动画"
          >
            {actionBusy === "h3:" + image.key ? <Spinner size={12} /> : null}
            H3 动画
          </button>
        </div>
      </div>
      <figcaption>{image.label}</figcaption>
    </figure>
  );

  return (
    <section className="concept-studio">
      <div className="concept-head">
        <div>
          <h4>
            AI 概念图 <span className="concept-flag">CONCEPT STUDIO</span>
            {hasConsistent && <span className="concept-flag concept-flag-consistent">IP-ADAPTER 一致性</span>}
          </h4>
          <p>ComfyUI + SDXL 动漫模型 · 由角色 spec 生成原创概念立绘 / 三视图 / 表情，可交给 H3 做动画。</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleRefresh()} disabled={refreshing}>
          {refreshing ? <Spinner size={12} /> : <IconRefresh size={13} />} 刷新
        </button>
      </div>

      {!supported && !hasConcept ? (
        <Notice tone="warn">
          概念图接口暂未就绪（GET /api/characters/{"{cid}"}/concepts{error ? " · " + error : ""}）。后端上线后点击「刷新」即可加载；生成按钮会先尝试提交任务。
        </Notice>
      ) : null}
      {actionError && <Notice tone="danger">{actionError}</Notice>}

      <div className="concept-controls">
        <select className="select" value={style} onChange={(event) => setStyle(event.target.value)} title="概念图风格">
          {CONCEPT_STYLE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label} · {option.desc}
            </option>
          ))}
        </select>
        <input
          className="input concept-prompt"
          value={promptExtra}
          onChange={(event) => setPromptExtra(event.target.value)}
          placeholder="额外提示词（可选）：silver hair, stage spotlight…"
          maxLength={120}
        />
        <input
          className="input concept-seed"
          value={seed}
          onChange={(event) => setSeed(event.target.value.replace(/[^0-9]/g, ""))}
          placeholder="seed"
          inputMode="numeric"
          title="固定 seed 可复现同一角色"
        />
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void handleGenerate()} disabled={generating || conceptRunning}>
          {generating || conceptRunning ? <Spinner size={13} /> : <IconSpark size={14} />}
          {conceptRunning ? "生成中…" : "生成概念图"}
        </button>
      </div>

      {hasConcept && (
        <p className="concept-tip">
          悬停图片可出现「设为立绘 / H3 动画」操作；立绘与设定总览的大图按钮常显。
          {hasConsistent ? " 三视图/表情已使用 IP-Adapter 一致性版本。" : ""}
        </p>
      )}

      {conceptJob && (
        <div className={"concept-job concept-job-" + conceptJob.status}>
          <div className="concept-job-row">
            <span className="concept-job-id">{conceptJob.id}</span>
            <span className="concept-job-status">
              {conceptJob.status === "done" ? "已完成" : conceptJob.status === "failed" ? "失败" : "生成中"}
            </span>
            <span className="muted">{fmtTime(conceptJob.updated_at || conceptJob.created_at)}</span>
          </div>
          {(conceptRunning || conceptJob.status === "done") && (
            <ProgressBar
              value={conceptJob.status === "done" ? 100 : conceptJob.progress || 0}
              label={Math.round(conceptJob.status === "done" ? 100 : conceptJob.progress || 0) + "%"}
            />
          )}
          {conceptJob.message && <div className="concept-job-msg">{conceptJob.message}</div>}
          {conceptJob.status === "failed" && conceptJob.error && (
            <div className="concept-job-error">
              <IconAlert size={13} /> {conceptJob.error}
            </div>
          )}
        </div>
      )}

      {!hasConcept ? (
        <EmptyState
          icon={<IconImage size={22} />}
          title={conceptRunning ? "概念图生成中" : "还没有 AI 概念图"}
          hint={
            conceptRunning
              ? "SDXL 正在按角色 spec 绘制立绘 / 三视图 / 表情，完成后自动展示。"
              : "点击「生成概念图」：输出概念立绘、三视图 ×3、四表情与设定总览，可设为本集立绘或交给 H3 做动画。"
          }
        />
      ) : (
        <div className="concept-gallery">
          {groups.portrait.map((image) => renderImage(image, "lg"))}
          {consistentThree.length > 0 ? (
            <div className="concept-group concept-group-consistent">
              <div className="concept-group-title">
                一致性三视图 <span className="concept-consistent-tag">IP-Adapter</span>
              </div>
              <div className="concept-grid concept-grid-3">
                {consistentThree.map((image) => renderImage(image, "md"))}
              </div>
            </div>
          ) : groups.three_view.length > 0 ? (
            <div className="concept-group">
              <div className="concept-group-title">三视图</div>
              <div className="concept-grid concept-grid-3">{groups.three_view.map((image) => renderImage(image, "md"))}</div>
            </div>
          ) : null}
          {consistentExpressions.length > 0 ? (
            <div className="concept-group concept-group-consistent">
              <div className="concept-group-title">
                一致性表情 <span className="concept-consistent-tag">IP-Adapter</span>
              </div>
              <div className="concept-grid concept-grid-4">
                {consistentExpressions.map((image) => renderImage(image, "md"))}
              </div>
            </div>
          ) : groups.expressions.length > 0 ? (
            <div className="concept-group">
              <div className="concept-group-title">表情</div>
              <div className="concept-grid concept-grid-4">{groups.expressions.map((image) => renderImage(image, "md"))}</div>
            </div>
          ) : null}
          {groups.sheet.length > 0 && (
            <div className="concept-group">
              <div className="concept-group-title">设定总览</div>
              {groups.sheet.map((image) => renderImage(image, "lg"))}
            </div>
          )}
        </div>
      )}

      <div className="concept-h3">
        <div className="concept-h3-head">
          <h5>
            <IconVideo size={14} /> H3 角色动画
          </h5>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void handleH3()} disabled={!!actionBusy || h3Running}>
            {h3Running ? <Spinner size={12} /> : <IconPlay size={12} />}
            {h3Running ? "生成中…" : "用当前立绘生成"}
          </button>
        </div>
        {h3Job && (h3Running || h3Job.status === "failed") && (
          <div className="concept-h3-status">
            <ProgressBar
              value={h3Running ? h3Job.progress || 0 : 0}
              tone={h3Job.status === "failed" ? "danger" : "accent"}
              label={Math.round(h3Job.progress || 0) + "%"}
            />
            <span>{h3Job.status === "failed" ? h3Job.error || h3Job.message || "生成失败" : h3Job.message || "H3 正在生成角色动画…"}</span>
          </div>
        )}
        {h3Url ? (
          <video
            className="concept-h3-video"
            src={h3Url + (h3Url.includes("?") ? "&" : "?") + "v=" + h3Version}
            controls
            playsInline
            preload="metadata"
          />
        ) : (
          !h3Job && <p className="concept-h3-hint">选择上方的概念图点击「H3 动画」，或直接用当前立绘生成 5 秒角色动态。</p>
        )}
      </div>
    </section>
  );
}
