// V5.4 分镜 / 剪辑视图：分镜表 + 剪辑时间线 + 剪辑档案（editorial）
// 数据：GET /api/v5/storyboard/{template}（缺失 404 → 占位/预览）+ GET /api/v5/editorial/{template}
import { useEffect, useMemo, useState } from "react";
import type {
  V5EditorialData,
  V5StoryboardData,
  V5StoryboardShot,
  V5TransitionOut,
} from "../../lib/v5";
import { isV5NotFound, mediaSrc, sceneArtUrl, storyboardHtmlUrl, storyboardPngUrl, v5api } from "../../lib/v5";
import { IconAlert, IconExternal, IconFilm, IconGrid, IconInfo, IconLayout, IconRefresh, IconVideo } from "../icons";
import { Spinner } from "../ui";
import { QcBadge, SmartImage, Tag } from "./shared";

// ---------------------------------------------------------------------------
// 类型 / 常量
// ---------------------------------------------------------------------------
export interface StoryboardAct {
  id?: string;
  name?: string;
  scene?: string;
  camera?: string;
  duration?: number;
  line?: string;
  text?: string;
}

interface Props {
  templateId: string;
  templateLabel?: string;
  acts: StoryboardAct[];
  outputDuration?: number | null;
  qcOk?: boolean | null;
  qcChecks?: number;
  qcTotal?: number;
}

type PreviewShot = V5StoryboardShot & { __preview?: boolean };

const SHOT_SIZE_COLORS: Record<string, string> = {
  EWS: "#5b9dff",
  WS: "#5ac8fa",
  MS: "#38c98b",
  MCU: "#f5c518",
  CU: "#ff9f43",
  ECU: "#ff6060",
  OTS: "#a78bfa",
  DETAIL: "#c89b3c",
  INSERT: "#8b7cf6",
};
const SHOT_SIZE_LABELS: Record<string, string> = {
  EWS: "大远景",
  WS: "远景",
  MS: "中景",
  MCU: "中近景",
  CU: "特写",
  ECU: "大特写",
  OTS: "过肩",
  DETAIL: "细节",
  INSERT: "插入",
};
const TRANSITION_SHORT: Record<string, string> = {
  cut: "硬切",
  dissolve: "溶接",
  fade: "淡入淡出",
  fadeblack: "黑场",
  wipeleft: "左划",
  wiperight: "右划",
  wipeup: "上划",
  wipedown: "下划",
  hblur: "横向模糊",
  vblur: "纵向模糊",
  circleopen: "圆形开",
  radial: "径向",
  pixelize: "像素化",
  zoomin: "推入",
  whip_left: "甩左",
  whip_right: "甩右",
  whip_pan: "甩镜",
};

function shotColor(size?: string): string {
  return (size && SHOT_SIZE_COLORS[size]) || "#78839a";
}

function sizeLabel(size?: string): string {
  return size ? `${size}${SHOT_SIZE_LABELS[size] ? " " + SHOT_SIZE_LABELS[size] : ""}` : "—";
}

function transitionOf(shot: V5StoryboardShot): { mode: string; duration?: number } {
  const raw = shot.transition_out;
  if (typeof raw === "string") return { mode: raw };
  const obj = (raw || {}) as V5TransitionOut;
  return { mode: String(obj.mode || "cut"), duration: typeof obj.duration === "number" ? obj.duration : undefined };
}

function safeNumber(value: unknown, fallback = 0): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

/** 按 act 分组（容忍 storyboard 没有 act 字段：用 scene/顺序兜底） */
function groupByAct(
  shots: PreviewShot[],
  acts: StoryboardAct[]
): { key: string; label: string; shots: PreviewShot[] }[] {
  const groups: { key: string; label: string; shots: PreviewShot[] }[] = [];
  const index = new Map<string, number>();
  let actCursor = 0;
  acts.forEach((act, idx) => {
    const key = String(act.id || `act${idx + 1}`);
    index.set(key, groups.length);
    groups.push({ key, label: act.name || key, shots: [] });
  });
  shots.forEach((shot, shotIndex) => {
    const size = String(shot.shot_size || "");
    const isCard = String(shot.type || "") === "card" || size === "CARD" || !shot.scene;
    if (isCard) {
      const key = "__cards";
      if (!index.has(key)) {
        index.set(key, groups.length);
        groups.push({ key, label: "片头 / 片尾卡片", shots: [] });
      }
      groups[index.get(key) as number].shots.push(shot);
      return;
    }
    let key = String(shot.act || shot.act_id || "");
    if (!key && acts.length) {
      // 优先按 scene 在同一顺序内匹配 act
      for (let i = actCursor; i < acts.length; i += 1) {
        if (acts[i].scene && shot.scene && acts[i].scene === shot.scene) {
          key = String(acts[i].id || `act${i + 1}`);
          actCursor = i;
          break;
        }
      }
      if (!key) {
        const act = acts[Math.min(shotIndex, acts.length - 1)];
        key = String(act.id || `act${Math.min(shotIndex, acts.length - 1) + 1}`);
      }
    }
    if (!key) key = "全片";
    if (!index.has(key)) {
      index.set(key, groups.length);
      groups.push({ key, label: key, shots: [] });
    }
    groups[index.get(key) as number].shots.push(shot);
  });
  return groups.filter((group) => group.shots.length > 0);
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
export function Storyboard({ templateId, templateLabel, acts, outputDuration, qcOk, qcChecks, qcTotal }: Props) {
  const [storyboard, setStoryboard] = useState<V5StoryboardData | null>(null);
  const [editorial, setEditorial] = useState<V5EditorialData | null>(null);
  const [loading, setLoading] = useState(true);
  const [sbMissing, setSbMissing] = useState(false);
  const [edMissing, setEdMissing] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number>(0);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    setStoryboard(null);
    setEditorial(null);
    setSbMissing(false);
    setEdMissing(false);
    (async () => {
      const [sb, ed] = await Promise.allSettled([
        v5api.storyboard(templateId),
        v5api.editorial(templateId),
      ]);
      if (!alive) return;
      if (sb.status === "fulfilled") {
        setStoryboard(sb.value.storyboard);
      } else if (isV5NotFound(sb.reason)) {
        setSbMissing(true);
      } else {
        setError(sb.reason instanceof Error ? sb.reason.message : String(sb.reason));
      }
      if (ed.status === "fulfilled") {
        setEditorial(ed.value.editorial);
      } else if (isV5NotFound(ed.reason)) {
        setEdMissing(true);
      }
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, [templateId, reloadKey]);

  const realShots = useMemo(
    () => ((storyboard?.shots || []) as PreviewShot[]).filter((shot) => shot && typeof shot === "object"),
    [storyboard]
  );

  // 分镜缺失 → 用模板 acts 生成"预览"数据（明确标注非最终分镜）
  const previewShots = useMemo<PreviewShot[]>(
    () =>
      acts.map((act, index) => ({
        id: act.id || `act${index + 1}`,
        act: act.id,
        scene: act.scene,
        duration: act.duration,
        camera: act.camera,
        line: act.line,
        text: act.text,
        shot_size: undefined,
        thumbnail_url: act.scene ? sceneArtUrl(act.scene) : undefined,
        __preview: true,
      })),
    [acts]
  );

  const isPreview = sbMissing && previewShots.length > 0 && !storyboard;
  const shots: PreviewShot[] = storyboard ? realShots : isPreview ? previewShots : [];
  const grouping = useMemo(() => groupByAct(shots, acts), [shots, acts]);

  // 剪辑档案：优先独立 editorial API；缺失时用 storyboard.json 内嵌的 edit.profile
  const effectiveEditorial = useMemo<V5EditorialData | null>(
    () => editorial || (storyboard?.edit?.profile as V5EditorialData | undefined) || null,
    [editorial, storyboard]
  );
  const archiveSource = editorial
    ? "editorial.json"
    : storyboard?.edit?.profile
      ? "storyboard.edit.profile（内嵌）"
      : "";
  const editInfo = storyboard?.edit;
  const signatureText = editInfo?.signature || "";

  const total = useMemo(() => {
    if (typeof storyboard?.total === "number" && storyboard.total > 0) return storyboard.total;
    if (storyboard && typeof storyboard.duration === "number" && storyboard.duration > 0) {
      return storyboard.duration;
    }
    return shots.reduce((sum, shot) => sum + safeNumber(shot.duration), 0);
  }, [storyboard, shots]);

  const stats = useMemo(() => {
    const sizes = new Set<string>();
    const cameras = new Set<string>();
    const transitions = new Set<string>();
    let cuts = 0;
    let boundaries = 0;
    shots.forEach((shot) => {
      const size = String(shot.shot_size || "");
      if (size && size !== "CARD") sizes.add(size);
      if (shot.camera) cameras.add(String(shot.camera));
      const tr = transitionOf(shot);
      if (tr.mode) transitions.add(tr.mode);
      if (storyboard) {
        boundaries += 1;
        if (tr.mode === "cut") cuts += 1;
      }
    });
    return {
      count: typeof editInfo?.shots === "number" ? editInfo.shots : shots.length,
      sizes: editInfo?.shot_sizes?.length ?? sizes.size,
      cameras: editInfo?.camera_types?.length ?? cameras.size,
      transitions: editInfo?.transitions?.length ?? transitions.size,
      hardCutRatio:
        typeof editInfo?.hard_cut_ratio === "number"
          ? editInfo.hard_cut_ratio
          : boundaries
            ? cuts / boundaries
            : null,
    };
  }, [shots, storyboard, editInfo]);

  // 时间线：start/duration（缺 start 时累计）
  const timeline = useMemo(() => {
    let cursor = 0;
    const rows = shots.map((shot, index) => {
      const duration = Math.max(0.001, safeNumber(shot.duration, 1));
      const start = typeof shot.start === "number" ? shot.start : cursor;
      cursor = start + duration;
      return { shot, index, start, duration, transition: transitionOf(shot) };
    });
    const span = Math.max(total, cursor, 0.001);
    return { rows, span };
  }, [shots, total]);

  const selectedShot = timeline.rows[selected]?.shot;

  const signature = [
    { label: "镜", value: stats.count },
    { label: "景别", value: stats.sizes },
    { label: "运镜", value: stats.cameras },
    { label: "转场", value: stats.transitions },
  ];

  return (
    <div className="v5-sb">
      {/* ---------------- 顶部统计 ---------------- */}
      <div className="v5-sb-stats">
        <div className="v5-sb-stats-main">
          {signature.map((item) => (
            <span key={item.label} className="v5-sb-stat">
              <b>{item.value}</b> {item.label}
            </span>
          ))}
          {signatureText && <span className="v5-muted v5-sb-signature-src" title="storyboard.json edit.signature">{signatureText}</span>}
          {stats.hardCutRatio !== null && (
            <span className="v5-sb-stat">
              硬切 <b>{Math.round(stats.hardCutRatio * 100)}%</b>
            </span>
          )}
          {qcOk !== undefined && <QcBadge ok={qcOk ?? null} checks={qcChecks} total={qcTotal} />}
          {storyboard?.storyboard_png_url && (
            <a className="v5-link-like" href={mediaSrc(storyboard.storyboard_png_url)} target="_blank" rel="noreferrer">
              <IconExternal size={12} /> 分镜大图
            </a>
          )}
          {(storyboard?.storyboard_html_url || !sbMissing) && (
            <a className="v5-link-like" href={mediaSrc(storyboardHtmlUrl(templateId))} target="_blank" rel="noreferrer">
              <IconExternal size={12} /> storyboard.html
            </a>
          )}
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setReloadKey((key) => key + 1)}>
          {loading ? <Spinner size={12} /> : <IconRefresh size={12} />} 刷新
        </button>
      </div>

      {loading && (
        <div className="v5-loading">
          <Spinner size={16} /> 正在读取分镜 / 剪辑档案…
        </div>
      )}
      {error && <div className="v5-banner v5-banner-error">分镜数据读取失败：{error}</div>}

      {/* ---------------- 缺失提示 ---------------- */}
      {!loading && sbMissing && (
        <div className="v5-banner v5-sb-banner">
          <IconInfo size={14} />
          <span>
            「{templateLabel || templateId}」<b>尚未生成分镜数据，重渲后可见</b>
            {isPreview ? "（下方为基于模板 acts / 场景 plate 的预览，最终分镜以重渲后 storyboard.json 为准）" : ""}
          </span>
        </div>
      )}

      {/* ---------------- ① 分镜表 ---------------- */}
      <section className="v5-sb-block">
        <div className="v5-sb-block-head">
          <IconGrid size={15} />
          <b>分镜表</b>
          <span className="v5-muted">
            {shots.length} 镜 · 总时长 {total ? total.toFixed(1) : "—"}s
            {isPreview ? " · 预览" : ""}
          </span>
          {storyboard && <Tag>storyboard.json</Tag>}
        </div>
        {shots.length === 0 ? (
          <div className="v5-img-fallback v5-sb-empty">
            <IconAlert size={18} />
            {edMissing ? "分镜与剪辑档案都尚未生成" : "尚未生成分镜数据"}
          </div>
        ) : (
          <div className="v5-sb-groups">
            {grouping.map((group) => {
              const groupDuration = group.shots.reduce((sum, shot) => sum + safeNumber(shot.duration), 0);
              return (
                <div key={group.key} className="v5-sb-group">
                  <div className="v5-sb-group-head">
                    {!group.key.startsWith("__") && <span className="v5-sb-group-key">{group.key}</span>}
                    <span className="v5-sb-group-label">{group.label}</span>
                    <span className="v5-muted">
                      {group.shots.length} 镜 · {groupDuration.toFixed(1)}s
                    </span>
                  </div>
                  <div className="v5-sb-cards">
                    {group.shots.map((shot) => {
                      const globalIndex = shots.indexOf(shot);
                      const tr = transitionOf(shot);
                      return (
                        <button
                          key={String(shot.id || globalIndex)}
                          type="button"
                          className={
                            "v5-sb-card" +
                            (globalIndex === selected ? " active" : "") +
                            (shot.__preview ? " is-preview" : "")
                          }
                          onClick={() => setSelected(globalIndex)}
                          title={String(shot.text || shot.line || "")}
                        >
                          <span className="v5-sb-card-no">{String(globalIndex + 1).padStart(2, "0")}</span>
                          <span className="v5-sb-thumb-wrap">
                            <SmartImage
                              className="v5-sb-thumb"
                              sources={[
                                shot.thumbnail_url ? mediaSrc(String(shot.thumbnail_url)) : null,
                                shot.scene ? sceneArtUrl(shot.scene) : null,
                              ]}
                              alt={String(shot.id || "")}
                              fallback={<span className="v5-sb-thumb-empty"><IconVideo size={16} /></span>}
                            />
                            <span className="v5-sb-dur">{safeNumber(shot.duration).toFixed(1)}s</span>
                          </span>
                          <span className="v5-sb-card-body">
                            <span className="v5-sb-card-row">
                              <i className="v5-sb-size" style={{ background: shotColor(shot.shot_size) }}>
                                {shot.shot_size || "—"}
                              </i>
                              <span className="v5-muted v5-ellipsis">{sizeLabel(shot.shot_size)}</span>
                            </span>
                            <span className="v5-sb-card-row">
                              <span className="v5-mono v5-sb-cam">{shot.camera || "—"}</span>
                            </span>
                            <span className="v5-sb-card-row v5-sb-tr">
                              <span className={"v5-sb-tr-badge" + (tr.mode === "cut" ? " is-cut" : "")}>
                                {tr.mode === "cut" ? "硬切 →" : `${TRANSITION_SHORT[tr.mode] || tr.mode} →`}
                              </span>
                              {shot.j_cut ? <span className="v5-sb-jl" title="J cut">J{safeNumber(shot.j_cut).toFixed(2)}</span> : null}
                              {shot.l_cut ? <span className="v5-sb-jl" title="L cut">L{safeNumber(shot.l_cut).toFixed(2)}</span> : null}
                            </span>
                            <span className="v5-muted v5-sb-line v5-ellipsis">
                              {shot.line ? `台词 ${shot.line}` : ""}
                              {shot.text ? ` “${String(shot.text).slice(0, 18)}…”` : ""}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ---------------- ② 剪辑时间线 ---------------- */}
      {shots.length > 0 && (
        <section className="v5-sb-block">
          <div className="v5-sb-block-head">
            <IconFilm size={15} />
            <b>剪辑时间线</b>
            <span className="v5-muted">
              宽度=时长 · 颜色=景别 · 转场标记在剪辑点 · J/L cut 标注
            </span>
          </div>
          <div className="v5-tl-scroll">
            <div className="v5-tl" style={{ minWidth: 880 }}>
              {/* 刻度 */}
              <div className="v5-tl-ruler">
                {rulerTicks(timeline.span).map((tick) => (
                  <span key={tick} className="v5-tl-tick" style={{ left: (tick / timeline.span) * 100 + "%" }}>
                    {tick.toFixed(1)}s
                  </span>
                ))}
              </div>
              {/* 时间条 */}
              <div className="v5-tl-track">
                {timeline.rows.map((row) => {
                  const isCut = row.transition.mode === "cut";
                  return (
                    <button
                      key={String(row.shot.id || row.index)}
                      type="button"
                      className={"v5-tl-bar" + (row.index === selected ? " active" : "")}
                      style={{
                        left: (row.start / timeline.span) * 100 + "%",
                        width: (row.duration / timeline.span) * 100 + "%",
                        background: shotColor(row.shot.shot_size),
                      }}
                      onClick={() => setSelected(row.index)}
                      title={`#${row.index + 1} ${row.shot.shot_size || ""} ${row.shot.camera || ""} ${row.duration.toFixed(2)}s`}
                    >
                      <span className="v5-tl-no">{row.index + 1}</span>
                      {row.shot.shot_size && <span className="v5-tl-size">{row.shot.shot_size}</span>}
                      {/* 剪辑点：转场 / J-L */}
                      {!isCut && (
                        <span
                          className="v5-tl-trans"
                          style={{ left: "100%" }}
                          title={`转场 ${row.transition.mode}${row.transition.duration ? " " + row.transition.duration + "s" : ""}`}
                        >
                          {TRANSITION_SHORT[row.transition.mode] || row.transition.mode}
                        </span>
                      )}
                      {row.shot.j_cut ? <span className="v5-tl-jl j">J</span> : null}
                      {row.shot.l_cut ? <span className="v5-tl-jl l">L</span> : null}
                    </button>
                  );
                })}
              </div>
              {/* 选中镜头信息 */}
              {selectedShot && (
                <div className="v5-tl-selected">
                  <span className="v5-sb-size" style={{ background: shotColor(selectedShot.shot_size) }}>
                    {selectedShot.shot_size || "—"}
                  </span>
                  <span className="v5-mono">#{selected + 1}</span>
                  <span className="v5-muted">
                    {safeNumber(selectedShot.duration).toFixed(2)}s · camera {selectedShot.camera || "—"} ·
                    转场 {transitionOf(selectedShot).mode}
                    {transitionOf(selectedShot).duration ? ` ${transitionOf(selectedShot).duration}s` : ""}
                  </span>
                  {selectedShot.line && <Tag>台词 {selectedShot.line}</Tag>}
                  {selectedShot.j_cut ? <Tag>J cut {safeNumber(selectedShot.j_cut).toFixed(2)}s</Tag> : null}
                  {selectedShot.l_cut ? <Tag>L cut {safeNumber(selectedShot.l_cut).toFixed(2)}s</Tag> : null}
                  {selectedShot.text && <span className="v5-muted v5-ellipsis">{String(selectedShot.text)}</span>}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ---------------- ③ 剪辑档案 ---------------- */}
      <section className="v5-sb-block">
        <div className="v5-sb-block-head">
          <IconLayout size={15} />
          <b>剪辑档案</b>
          <span className="v5-muted">
            {effectiveEditorial?.label ? effectiveEditorial.label : "contract/v5/editorial"}
          </span>
          {archiveSource && <Tag>{archiveSource}</Tag>}
        </div>
        {!effectiveEditorial ? (
          <div className="v5-img-fallback v5-sb-empty">
            {loading ? "读取中…" : "该模板尚未生成剪辑档案（contract/v5/editorial/<id>.json）"}
          </div>
        ) : (
          <div className="v5-ed">
            <div className="v5-ed-block">
              <span className="v5-detail-label">pacing 节奏曲线（时长倍率）</span>
              <PacingChart pacing={effectiveEditorial.pacing} />
              <div className="v5-chip-row">
                {Object.entries(effectiveEditorial.pacing || {}).map(([key, value]) => (
                  <Tag key={key}>
                    {key} × {safeNumber(value).toFixed(2)}
                  </Tag>
                ))}
                {typeof editInfo?.j_cuts === "number" && <Tag>J cut × {editInfo.j_cuts}</Tag>}
                {typeof editInfo?.l_cuts === "number" && <Tag>L cut × {editInfo.l_cuts}</Tag>}
                {typeof editInfo?.inserts === "number" && <Tag>insert × {editInfo.inserts}</Tag>}
              </div>
            </div>
            <div className="v5-ed-grid">
              <div className="v5-ed-block">
                <span className="v5-detail-label">
                  camera 调色板（{effectiveEditorial.camera_palette?.length || 0}）
                </span>
                <div className="v5-chip-row">
                  {(effectiveEditorial.camera_palette || []).map((camera) => (
                    <Tag key={camera}>{camera}</Tag>
                  ))}
                  {!(effectiveEditorial.camera_palette || []).length && <span className="v5-muted">—</span>}
                </div>
              </div>
              <div className="v5-ed-block">
                <span className="v5-detail-label">
                  transition 调色板（{effectiveEditorial.transition_palette?.length || 0}）
                </span>
                <div className="v5-chip-row">
                  {(effectiveEditorial.transition_palette || []).map((mode, index) => (
                    <Tag key={`${mode}-${index}`}>{TRANSITION_SHORT[mode] || mode}</Tag>
                  ))}
                  {!(effectiveEditorial.transition_palette || []).length && <span className="v5-muted">—</span>}
                </div>
              </div>
            </div>
            <div className="v5-ed-grid">
              <div className="v5-ed-block">
                <span className="v5-detail-label">J / L cut · insert / detail</span>
                <div className="v5-ed-ratios">
                  <RatioBar label="J cut" value={effectiveEditorial.j_cut_ratio} color="#5ac8fa" />
                  <RatioBar label="L cut" value={effectiveEditorial.l_cut_ratio} color="#5b9dff" />
                  <RatioBar label="insert" value={effectiveEditorial.insert_ratio} color="#a78bfa" />
                  <RatioBar label="detail" value={effectiveEditorial.detail_ratio} color="#c89b3c" />
                </div>
              </div>
              <div className="v5-ed-block">
                <span className="v5-detail-label">opening / closing</span>
                <div className="v5-chip-row">
                  <Tag>开 {String((effectiveEditorial.opening || {}).shot || "—")}</Tag>
                  <Tag>{String((effectiveEditorial.opening || {}).camera || "—")}</Tag>
                  <Tag>hold {safeNumber((effectiveEditorial.opening || {}).hold).toFixed(1)}s</Tag>
                  <Tag>收 {String((effectiveEditorial.closing || {}).shot || "—")}</Tag>
                  <Tag>{String((effectiveEditorial.closing || {}).camera || "—")}</Tag>
                  <Tag>hold {safeNumber((effectiveEditorial.closing || {}).hold).toFixed(1)}s</Tag>
                </div>
                {effectiveEditorial.transition_policy && (
                  <div className="v5-muted v5-ed-policy">
                    转场策略：默认 {String(effectiveEditorial.transition_policy.default || "—")} · 硬切比例{" "}
                    {Math.round(safeNumber(effectiveEditorial.transition_policy.hard_cut_ratio) * 100)}% · 场景切换溶接{" "}
                    {String(effectiveEditorial.transition_policy.dissolve_on_scene_change ?? "—")} · 最长{" "}
                    {safeNumber(effectiveEditorial.transition_policy.max_transition_sec).toFixed(2)}s
                  </div>
                )}
              </div>
            </div>
            <div className="v5-ed-block">
              <span className="v5-detail-label">
                shot_sequence（{effectiveEditorial.shot_sequence?.length || 0}）
              </span>
              <div className="v5-chip-row">
                {(effectiveEditorial.shot_sequence || []).map((size, index) => (
                  <span
                    key={`${size}-${index}`}
                    className="v5-ed-size"
                    style={{ borderColor: shotColor(size), color: shotColor(size) }}
                  >
                    {size}
                  </span>
                ))}
                {!(effectiveEditorial.shot_sequence || []).length && <span className="v5-muted">—</span>}
              </div>
            </div>
            {effectiveEditorial.notes && <p className="v5-ed-notes">{effectiveEditorial.notes}</p>}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 小部件
// ---------------------------------------------------------------------------
function PacingChart({ pacing }: { pacing?: Record<string, number> }) {
  const keys = ["lead_in", "mid", "climax", "coda"];
  const values = keys.map((key) => safeNumber((pacing || {})[key], 1));
  const max = Math.max(...values, 1.3);
  const min = Math.min(...values, 0.7);
  const width = 260;
  const height = 76;
  const pad = 8;
  const points = values.map((value, index) => {
    const x = pad + (index * (width - pad * 2)) / Math.max(1, values.length - 1);
    const y = height - pad - ((value - min) / Math.max(0.001, max - min)) * (height - pad * 2);
    return [x, y] as const;
  });
  const polyline = points.map(([x, y]) => `${x},${y}`).join(" ");
  return (
    <svg className="v5-ed-pacing" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="pacing 曲线">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="#2e394c" strokeWidth="1" />
      <line x1={pad} y1={height - pad} x2={pad} y2={pad} stroke="#2e394c" strokeWidth="1" />
      <polyline points={polyline} fill="none" stroke="#f5c518" strokeWidth="2" />
      {points.map(([x, y], index) => (
        <g key={index}>
          <circle cx={x} cy={y} r="3" fill="#f5c518" />
          <text x={x} y={y - 7} fill="#a8b2c4" fontSize="8" textAnchor="middle">
            {values[index].toFixed(2)}
          </text>
          <text x={x} y={height - 1} fill="#525d72" fontSize="8" textAnchor="middle">
            {keys[index]}
          </text>
        </g>
      ))}
    </svg>
  );
}

function RatioBar({ label, value, color }: { label: string; value?: number; color: string }) {
  const pct = Math.max(0, Math.min(1, safeNumber(value)));
  return (
    <span className="v5-ed-ratio">
      <span className="v5-ed-ratio-label">{label}</span>
      <span className="v5-ed-ratio-track">
        <span className="v5-ed-ratio-fill" style={{ width: pct * 100 + "%", background: color }} />
      </span>
      <span className="v5-mono">{Math.round(pct * 100)}%</span>
    </span>
  );
}

function rulerTicks(span: number): number[] {
  if (!span || span <= 0) return [0];
  const target = 8;
  const step = Math.max(0.5, Math.round((span / target) * 2) / 2);
  const ticks: number[] = [];
  for (let t = 0; t <= span + 0.001; t += step) ticks.push(Math.round(t * 10) / 10);
  return ticks;
}
