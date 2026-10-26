// V5 模板库：每个模板的风格 / 工作流 / 角色 / 场景「可见可改」
// - 列表：16+1 模板卡（风格/时长/tags/description/工作流缩略/角色头像/场景缩略/剪辑签名）
// - 详情弹层：子 tab「工作流」7 步 /「分镜·剪辑」（V5.4 分镜表+剪辑时间线+剪辑档案）
// - 编辑：GET /api/v5/templates/{id} → 改 JSON → PUT（dry_run 校验 / 保存 custom 覆盖）
// - 用此模板渲染：跳渲染 tab 并预选模板
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type {
  V5CatalogResponse,
  V5EditorialData,
  V5Output,
  V5Palette,
  V5StyleSummary,
  V5VoiceInfo,
} from "../../lib/v5";import {
  characterArtUrl,
  characterPreviewUrl,
  defaultStyleForCharacter,
  isV5NotFound,
  mediaSrc,
  sceneArtUrl,
  scenePreviewUrl,
  v5api,
} from "../../lib/v5";
import { useToast } from "../Toast";
import {
  IconArrowRight,
  IconBoard,
  IconCheck,
  IconClock,
  IconClose,
  IconEdit,
  IconExternal,
  IconFilm,
  IconGrid,
  IconImage,
  IconLayout,
  IconPlay,
  IconRefresh,
  IconSpark,
  IconUser,
  IconVolume,
} from "../icons";
import { EmptyState, Spinner } from "../ui";
import { Palette, QcBadge, SmartImage, StyleBadge, Tag, VideoModal } from "./shared";
import { Storyboard } from "./Storyboard";

// ---------------------------------------------------------------------------
// 类型（模板 / 风格完整 JSON 的子集）
// ---------------------------------------------------------------------------
interface TemplateAct {
  id?: string;
  name?: string;
  scene?: string;
  camera?: string;
  camera_strength?: number;
  duration?: number;
  line?: string;
  text?: string;
  characters?: string[];
  broll?: boolean;
}

interface TemplateCast {
  role?: string;
  character?: string;
  name?: string;
}

interface TemplateFull {
  id: string;
  label?: string;
  description?: string;
  tags?: string[];
  style?: string;
  target_duration?: number;
  aspect?: number[];
  cast?: TemplateCast[];
  acts?: TemplateAct[];
  packaging?: Record<string, unknown>;
  audio?: Record<string, unknown>;
  lipsync?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

interface StyleFull {
  id?: string;
  label?: string;
  palette?: V5Palette;
  technique?: string[];
  character_treatment?: string;
  transition?: string;
  typography?: Record<string, unknown>;
  packaging?: Record<string, unknown>;
  audio?: Record<string, unknown>;
}

interface Props {
  catalog: V5CatalogResponse | null;
  outputs: V5Output[];
  loading: boolean;
  onOpenRender: (templateId: string) => void;
  onOpenCharacters: () => void;
}

// ---------------------------------------------------------------------------
// hash 深链：#/v5/templates[/<id>[/storyboard]][?edit=1]
// ---------------------------------------------------------------------------
type DetailView = "workflow" | "storyboard";

interface TemplateRoute {
  id?: string;
  edit: boolean;
  view: DetailView;
}

function parseRoute(): TemplateRoute {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] !== "v5" || parts[1] !== "templates") return { edit: false, view: "workflow" };
  return {
    id: parts[2],
    edit: /(^|&)edit=1/.test(query),
    view: parts[3] === "storyboard" ? "storyboard" : "workflow",
  };
}

function writeRoute(id?: string, edit = false, view: DetailView = "workflow"): void {
  const next =
    "#/v5/templates" +
    (id ? "/" + id : "") +
    (id && view === "storyboard" ? "/storyboard" : "") +
    (edit ? "?edit=1" : "");
  if (window.location.hash !== next) window.history.replaceState(null, "", next);
}

function fmtDuration(value?: number | null): string {
  return typeof value === "number" ? `${Math.round(value * 10) / 10}s` : "—";
}

const clockValue = (contract: number) => contract / 60;

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
export function Templates({ catalog, outputs, loading, onOpenRender, onOpenCharacters }: Props) {
  const notify = useToast();
  const [details, setDetails] = useState<Record<string, TemplateFull>>({});
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [signatures, setSignatures] = useState<Record<string, string | null>>({});
  const [route, setRoute] = useState<TemplateRoute>(() => parseRoute());
  const [voices, setVoices] = useState<Record<string, V5VoiceInfo | null>>({});
  const [styleDetails, setStyleDetails] = useState<Record<string, StyleFull>>({});
  const [playing, setPlaying] = useState<V5Output | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  const [editError, setEditError] = useState("");
  const [editNotice, setEditNotice] = useState("");
  const [editBusy, setEditBusy] = useState(false);

  // hash → state（外部前进/后退/直接打开 URL）
  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // 加载 17 个模板完整 JSON（列表要 description / acts / packaging）
  const loadDetails = useCallback(async (templates: { id: string }[]) => {
    setDetailsLoading(true);
    try {
      const entries = await Promise.all(
        templates.map(async (item) => {
          try {
            const res = await v5api.item("templates", item.id);
            return [item.id, res.item as unknown as TemplateFull] as const;
          } catch {
            return [item.id, null] as const;
          }
        })
      );
      const next: Record<string, TemplateFull> = {};
      entries.forEach(([id, item]) => {
        if (item) next[id] = item;
      });
      setDetails(next);
    } finally {
      setDetailsLoading(false);
    }
  }, []);

  // 加载剪辑签名（优先独立剪辑档案；缺失时用 storyboard.json 的 edit.signature / 统计）
  const loadSignatures = useCallback(async (templates: { id: string }[]) => {
    const entries = await Promise.all(
      templates.map(async (item) => {
        try {
          const res = await v5api.editorial(item.id);
          const editorial: V5EditorialData = res.editorial;
          const seq = editorial.shot_sequence || [];
          const sizes = new Set(seq.map(String));
          const cameras = editorial.camera_palette || [];
          const transitions = new Set((editorial.transition_palette || []).map(String));
          return [
            item.id,
            `${seq.length} 镜 · ${sizes.size} 景别 · ${cameras.length} 运镜 · ${transitions.size} 转场`,
          ] as const;
        } catch {
          try {
            const res = await v5api.storyboard(item.id);
            const edit = res.storyboard.edit;
            if (edit?.signature) return [item.id, String(edit.signature)] as const;
            if (edit) {
              return [
                item.id,
                `${edit.shots ?? 0} 镜 · ${(edit.shot_sizes || []).length} 景别 · ${(edit.camera_types || []).length} 运镜 · ${(edit.transitions || []).length} 转场`,
              ] as const;
            }
            return [item.id, null] as const;
          } catch {
            return [item.id, null] as const;
          }
        }
      })
    );
    const next: Record<string, string | null> = {};
    entries.forEach(([id, signature]) => {
      next[id] = signature;
    });
    setSignatures(next);
  }, []);

  useEffect(() => {
    if (!catalog) return;
    void loadDetails(catalog.templates);
    void loadSignatures(catalog.templates);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog?.templates.length]);

  const selectedId = route.id && catalog?.templates.some((item) => item.id === route.id) ? route.id : undefined;
  const selected = selectedId ? details[selectedId] : undefined;
  const selectedOutput = selectedId ? outputs.find((item) => item.id === selectedId) : undefined;
  const styleSummary =
    catalog && selected?.style
      ? catalog.styles.find((item) => item.id === selected.style)
      : undefined;

  // 详情打开时补：cast 声音、style 详情
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const cast = (selected.cast || []).filter((member) => member.character);
    void Promise.all(
      cast.map(async (member) => {
        const characterId = String(member.character);
        if (voices[characterId] !== undefined) return;
        try {
          const info = await v5api.characterVoice(characterId);
          if (alive) setVoices((prev) => ({ ...prev, [characterId]: info }));
        } catch {
          if (alive) setVoices((prev) => ({ ...prev, [characterId]: null }));
        }
      })
    );
    if (selected.style && styleDetails[selected.style] === undefined) {
      v5api
        .item("styles", selected.style)
        .then((res) => {
          if (alive) {
            setStyleDetails((prev) => ({ ...prev, [selected.style as string]: res.item as unknown as StyleFull }));
          }
        })
        .catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.style]);

  // 打开编辑弹层时填充 JSON（深链 ?edit=1 也能直接打开）
  useEffect(() => {
    if (!route.id) {
      setEditOpen(false);
      return;
    }
    if (route.edit) {
      setEditOpen(true);
    }
  }, [route.id, route.edit]);

  useEffect(() => {
    if (!editOpen || !selected) return;
    setEditText(JSON.stringify(selected, null, 2));
    setEditError("");
    setEditNotice("");
  }, [editOpen, selected?.id]);

  const openDetail = useCallback((id: string) => {
    writeRoute(id, false, "workflow");
    setRoute({ id, edit: false, view: "workflow" });
  }, []);

  const openStoryboard = useCallback((id: string) => {
    writeRoute(id, false, "storyboard");
    setRoute({ id, edit: false, view: "storyboard" });
  }, []);

  const switchDetailView = useCallback(
    (view: DetailView) => {
      if (!route.id) return;
      writeRoute(route.id, false, view);
      setRoute({ id: route.id, edit: false, view });
    },
    [route.id]
  );

  const closeDetail = useCallback(() => {
    writeRoute();
    setRoute({ edit: false, view: "workflow" });
    setEditOpen(false);
  }, []);

  const openEdit = useCallback(
    (id: string) => {
      writeRoute(id, true, route.view);
      setRoute({ id, edit: true, view: route.view });
      setEditOpen(true);
    },
    [route.view]
  );

  const closeEdit = useCallback(() => {
    setEditOpen(false);
    if (route.id) writeRoute(route.id, false, route.view);
    setRoute({ id: route.id, edit: false, view: route.view });
  }, [route.id, route.view]);

  const doSave = useCallback(
    async (dryRun: boolean) => {
      if (!selectedId) return;
      setEditError("");
      setEditNotice("");
      let parsed: Record<string, unknown>;
      try {
        const data = JSON.parse(editText);
        if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("顶层必须是 JSON object");
        parsed = data as Record<string, unknown>;
        parsed.id = selectedId;
      } catch (err) {
        setEditError("JSON 解析失败：" + (err instanceof Error ? err.message : String(err)));
        return;
      }
      setEditBusy(true);
      try {
        const res = await v5api.putItem("templates", selectedId, parsed, dryRun);
        setEditNotice(
          dryRun
            ? `校验通过（dry_run，未写盘；write_mode=${res.write_mode || "?"}）`
            : `已保存 custom 覆盖：${res.path || ""}（write_mode=${res.write_mode || "?"}）`
        );
        if (!dryRun) {
          notify("模板已保存为 custom 覆盖（重渲后生效）", "success");
          await loadDetails(catalog?.templates || []);
          const fresh = await v5api.item("templates", selectedId);
          setDetails((prev) => ({ ...prev, [selectedId]: fresh.item as unknown as TemplateFull }));
        } else {
          notify("模板 JSON 校验通过", "success");
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setEditError("保存失败：" + message);
      } finally {
        setEditBusy(false);
      }
    },
    [selectedId, editText, notify, loadDetails, catalog]);

  const styleOf = useCallback(
    (templateId: string): V5StyleSummary | undefined => {
      const styleId = details[templateId]?.style ||
        (catalog?.templates || []).find((item) => item.id === templateId)?.style;
      return styleId ? catalog?.styles.find((item) => item.id === styleId) : undefined;
    },
    [catalog, details]
  );

  const sceneLabel = useCallback(
    (sceneId?: string) => catalog?.scenes.find((item) => item.id === sceneId)?.label || sceneId || "—",
    [catalog]
  );

  if (!catalog) {
    return (
      <div className="v5-loading">
        {loading ? <Spinner size={18} /> : null} 正在读取模板库…
      </div>
    );
  }

  const templates = catalog.templates;
  const selectedLipsync = selected ? String((selected.lipsync || {}).mode || "viseme") : "viseme";

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconLayout size={16} />
          <b>模板库</b>
          <span className="v5-muted">
            {templates.length} 个模板 · 每个模板自己的风格/场景/角色/工作流 · 可编辑 JSON（保存为 custom 覆盖）
          </span>
        </div>
        <div className="v5-toolbar-right">
          {detailsLoading && <Spinner size={14} />}
          <button type="button" className="btn btn-ghost" onClick={() => void loadDetails(templates)}>
            <IconRefresh size={14} /> 刷新
          </button>
        </div>
      </div>

      {detailsLoading && Object.keys(details).length === 0 && (
        <div className="v5-loading">
          <Spinner size={16} /> 正在读取 17 个模板的完整工作流…
        </div>
      )}

      <div className="v5-tpl-grid">
        {templates.map((summary) => {
          const full = details[summary.id];
          const style = styleOf(summary.id);
          const output = outputs.find((item) => item.id === summary.id);
          const rawCast = full?.cast || summary.cast || [];
          const cast: TemplateCast[] = rawCast.map((member) => ({
            role: member.role,
            character: member.character,
            name: (member as TemplateCast).name,
          }));
          const scenes = uniqueList(full ? (full.acts || []).map((act) => act.scene) : summary.scene_route || []);
          const actCount = full?.acts?.length ?? summary.act_count ?? 0;
          const custom = summary.custom;
          const signature = signatures[summary.id] || "";
          return (
            <article key={summary.id} className="v5-tpl-card">
              <button type="button" className="v5-tpl-main" onClick={() => openDetail(summary.id)}>
                <div className="v5-tpl-head">
                  <div className="v5-tpl-title">
                    <b>{summary.label}</b>
                    <span className="v5-muted v5-mono">{summary.id}</span>
                  </div>
                  <div className="v5-tpl-head-right">
                    {custom && <span className="v5-custom-flag">custom</span>}
                    <StyleBadge label={style?.label || summary.style || "?"} />
                  </div>
                </div>
                <p className="v5-tpl-desc">{full?.description || "（模板 JSON 无 description）"}</p>
                <div className="v5-tpl-meta">
                  <span>
                    <IconClock size={12} /> {fmtDuration(full?.target_duration ?? summary.target_duration)}
                  </span>
                  <span>
                    <IconBoard size={12} /> {actCount} acts
                  </span>
                  <span>
                    <IconUser size={12} /> {cast.length} 角色
                  </span>
                  <span>
                    <IconGrid size={12} /> {scenes.length} 场景
                  </span>
                  {output?.duration ? <span>成片 {fmtDuration(output.duration)}</span> : <span className="v5-muted">未渲染</span>}
                </div>
                <div className={"v5-tpl-signature" + (signature ? "" : " is-empty")}>
                  <IconFilm size={12} />
                  {signature ? (
                    <>
                      剪辑签名：<b>{signature}</b>
                    </>
                  ) : (
                    <span>尚未生成分镜数据</span>
                  )}
                </div>
                <div className="v5-tpl-tags">
                  {(summary.tags || []).slice(0, 4).map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                  {style && <Palette palette={style.palette} size={14} />}
                </div>
                {/* 工作流缩略条：①..⑦ */}
                <div className="v5-tpl-flow">
                  {FLOW.map((step, index) => (
                    <span key={step.key} className="v5-tpl-flow-step" title={step.title}>
                      <i>{index + 1}</i>
                      {step.short}
                    </span>
                  ))}
                </div>
                <div className="v5-tpl-previews">
                  <div className="v5-tpl-avatars">
                    {cast.slice(0, 4).map((member) => (
                      <SmartImage
                        key={String(member.role)}
                        className="v5-tpl-avatar"
                        sources={[
                          characterArtUrl(String(member.character)),
                          characterPreviewUrl(String(member.character),
                            defaultStyleForCharacter(catalog,
                              { id: String(member.character), label: String(member.name || member.character) } as never)),
                        ]}
                        alt={String(member.name || member.character)}
                        fallback={<span>{String(member.name || member.character).slice(0, 1)}</span>}
                      />
                    ))}
                  </div>
                  <div className="v5-tpl-scene-thumbs">
                    {scenes.slice(0, 3).map((sceneId) => (
                      <SmartImage
                        key={sceneId}
                        className="v5-tpl-scene-thumb"
                        sources={[sceneArtUrl(sceneId), scenePreviewUrl(sceneId, full?.style, "320x180")]}
                        alt={sceneLabel(sceneId)}
                        fallback={<span className="v5-muted">无图</span>}
                      />
                    ))}
                  </div>
                </div>
              </button>
              <div className="v5-tpl-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openDetail(summary.id)}>
                  工作流
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openStoryboard(summary.id)}>
                  <IconFilm size={12} /> 分镜/剪辑
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openEdit(summary.id)}>
                  <IconEdit size={12} /> 编辑 JSON
                </button>
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => onOpenRender(summary.id)}
                >
                  <IconSpark size={12} /> 用此模板渲染
                </button>
                {output?.final_url && (
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => setPlaying(output)}
                  >
                    <IconPlay size={12} /> 播放
                  </button>
                )}
                {output?.timeline_url && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() =>
                      window.open(mediaSrc(output.timeline_url), "_blank", "noopener,noreferrer")
                    }
                  >
                    时间线
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {/* ---------------------------- 模板详情 / 工作流 ---------------------------- */}
      {selectedId && selected && (
        <div className="v5-overlay" onMouseDown={closeDetail}>
          <div className="v5-modal v5-modal-xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="v5-modal-head">
              <div>
                <b>{selected.label || selectedId}</b>
                <span className="v5-muted v5-mono">
                  {selectedId} · style {selected.style || "?"} · target{" "}
                  {fmtDuration(selected.target_duration)} · {(selected.acts || []).length} acts
                </span>
              </div>
              <div className="v5-tpl-head-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => openEdit(selectedId)}>
                  <IconEdit size={13} /> 编辑 JSON
                </button>
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => {
                    onOpenRender(selectedId);
                    closeDetail();
                  }}
                >
                  <IconSpark size={13} /> 用此模板渲染
                </button>
                <button type="button" className="icon-btn" onClick={closeDetail} aria-label="关闭">
                  <IconClose size={16} />
                </button>
              </div>
            </div>
            <div className="v5-subtabs">
              <button
                type="button"
                className={"v5-subtab" + (route.view === "workflow" ? " active" : "")}
                onClick={() => switchDetailView("workflow")}
              >
                <IconSpark size={13} /> 工作流
              </button>
              <button
                type="button"
                className={"v5-subtab" + (route.view === "storyboard" ? " active" : "")}
                onClick={() => switchDetailView("storyboard")}
              >
                <IconFilm size={13} /> 分镜 / 剪辑
              </button>
              <span className="v5-muted v5-subtabs-hint">
                {route.view === "storyboard"
                  ? "分镜表 · 剪辑时间线 · 转场 · 剪辑档案（V5.4）"
                  : "7 步 V5 工作流（V5.3）"}
              </span>
            </div>
            {route.view === "storyboard" ? (
              <div className="v5-modal-body v5-tpl-detail v5-tpl-detail-sb">
                <Storyboard
                  templateId={selectedId}
                  templateLabel={selected.label}
                  acts={selected.acts || []}
                  outputDuration={selectedOutput?.duration ?? null}
                  qcOk={selectedOutput?.qc_ok ?? null}
                  qcChecks={selectedOutput?.qc_checks}
                  qcTotal={selectedOutput?.qc_total}
                />
              </div>
            ) : (
              <div className="v5-modal-body v5-tpl-detail">
                <div className="v5-tpl-detail-top">
                  <div className="v5-tpl-detail-meta">
                  <StyleBadge label={styleSummary?.label || selected.style || "?"} />
                  <Tag>{fmtDuration(selected.target_duration)}</Tag>
                  <Tag>
                    {(selected.aspect || [16, 9]).join(":")}
                  </Tag>
                  <Tag>{(selected.tags || []).slice(0, 3).join(" / ") || "—"}</Tag>
                  {selectedOutput && (
                    <QcBadge
                      ok={selectedOutput.qc_ok}
                      checks={selectedOutput.qc_checks}
                      total={selectedOutput.qc_total}
                    />
                  )}
                </div>
                {selectedOutput?.final_url && (
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => setPlaying(selectedOutput)}>
                    <IconPlay size={13} /> 播放成片
                  </button>
                )}
                </div>

              <ol className="v5-flow-steps">
                {/* ① 分镜 acts → shots */}
                <FlowStep index={1} title="分镜：acts → shots" icon={<IconBoard size={15} />}
                  hint={`${(selected.acts || []).length} acts → ${selectedOutput?.shots ?? (selected.acts || []).length} shots`}>
                  <div className="v5-act-list">
                    {(selected.acts || []).map((act) => (
                      <div key={String(act.id)} className="v5-act-row">
                        <span className="v5-act-id">{act.id || "—"}</span>
                        <span className="v5-act-scene">{sceneLabel(act.scene)}</span>
                        <span className="v5-tag">{act.camera || "—"}</span>
                        <span className="v5-muted">{fmtDuration(act.duration)}</span>
                        <span className="v5-muted v5-act-line">
                          {act.line ? `台词 ${act.line}` : act.broll ? "B-roll" : "—"}
                        </span>
                        <span className="v5-muted v5-ellipsis" title={act.text}>
                          {act.text ? `“${act.text.slice(0, 26)}${act.text.length > 26 ? "…" : ""}”` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </FlowStep>

                {/* ② 固定声音 TTS */}
                <FlowStep index={2} title="固定声音 TTS：cast → voice_id" icon={<IconVolume size={15} />}
                  hint={`${(selected.cast || []).length} 个角色 · 跨镜/跨集音色固定`}>
                  <div className="v5-flow-cast">
                    {(selected.cast || []).map((member) => {
                      const characterId = String(member.character);
                      const info = voices[characterId];
                      return (
                        <button
                          key={String(member.role)}
                          type="button"
                          className="v5-flow-cast-card"
                          onClick={onOpenCharacters}
                          title="到角色库查看/改声音"
                        >
                          <SmartImage
                            className="v5-flow-avatar"
                            sources={[characterArtUrl(characterId), characterPreviewUrl(characterId)]}
                            alt={String(member.name || characterId)}
                            fallback={<span>{String(member.name || characterId).slice(0, 1)}</span>}
                          />
                          <span className="v5-flow-cast-main">
                            <b>{member.name || characterId}</b>
                            <span className="v5-muted">
                              {member.role} · {characterId}
                            </span>
                          </span>
                          <span className="v5-flow-cast-voice">
                            {info ? (
                              <>
                                <span className="v5-mono">{info.voice_id}</span>
                                <span className="v5-muted">seed {info.seed}</span>
                              </>
                            ) : (
                              <span className="v5-muted">读取声音…</span>
                            )}
                            {info?.lock?.exists && <span className="v5-qc v5-qc-ok">已锁声</span>}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </FlowStep>

                {/* ③ 场景 AI plate + style finish */}
                <FlowStep index={3} title="场景 AI plate + style finish" icon={<IconImage size={15} />}
                  hint={`${uniqueList((selected.acts || []).map((act) => act.scene)).length} 个场景 · style ${selected.style || "?"}`}>
                  <div className="v5-flow-scenes">
                    {uniqueList((selected.acts || []).map((act) => act.scene)).map((sceneId) => (
                      <div key={sceneId} className="v5-flow-scene">
                        <SmartImage
                          className="v5-flow-scene-img"
                          sources={[sceneArtUrl(sceneId), scenePreviewUrl(sceneId, selected.style, "480x270")]}
                          alt={sceneLabel(sceneId)}
                          fallback={<span className="v5-muted">无图</span>}
                        />
                        <span className="v5-flow-scene-label">{sceneLabel(sceneId)}</span>
                      </div>
                    ))}
                  </div>
                </FlowStep>

                {/* ④ 角色 AI 立绘 + 口型 */}
                <FlowStep index={4} title="角色 AI 立绘 + 口型" icon={<IconUser size={15} />}
                  hint={`lipsync ${selectedLipsync} · 立绘 + style finish`}>
                  <div className="v5-flow-avatars">
                    {(selected.cast || []).map((member) => (
                      <div key={String(member.role)} className="v5-flow-portrait">
                        <SmartImage
                          className="v5-flow-portrait-img"
                          sources={[
                            characterArtUrl(String(member.character)),
                            characterPreviewUrl(String(member.character), defaultStyleForCharacter(catalog,
                              { id: String(member.character), label: String(member.name || member.character) } as never)),
                          ]}
                          alt={String(member.name || member.character)}
                          fallback={<span>{String(member.name || member.character).slice(0, 1)}</span>}
                        />
                        <span>{member.name || member.character}</span>
                        <span className="v5-muted v5-mono">{String(member.character)}</span>
                      </div>
                    ))}
                  </div>
                </FlowStep>

                {/* ⑤ motion2d 镜头动画 */}
                <FlowStep index={5} title="motion2d 镜头动画" icon={<IconFilm size={15} />}
                  hint={cameraHint(selected.acts)}>
                  <div className="v5-chip-row">
                    {cameraCounts(selected.acts).map(([camera, count]) => (
                      <Tag key={camera}>
                        {camera} × {count}
                      </Tag>
                    ))}
                  </div>
                </FlowStep>

                {/* ⑥ 字幕 / 包装 */}
                <FlowStep index={6} title="字幕 / 包装" icon={<IconLayout size={15} />}
                  hint="style typography + template packaging（标题/角标/结束卡/转场）">
                  <div className="v5-flow-packaging">
                    <div>
                      <span className="v5-detail-label">style typography</span>
                      <div className="v5-muted">
                        {Object.entries(styleDetails[selected.style || ""]?.typography || {})
                          .filter(([, value]) => typeof value !== "object")
                          .slice(0, 6)
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(" · ") || "—"}
                      </div>
                    </div>
                    <div>
                      <span className="v5-detail-label">template packaging</span>
                      <div className="v5-muted">
                        {Object.entries(selected.packaging || {})
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(" · ") || "—"}
                      </div>
                    </div>
                  </div>
                </FlowStep>

                {/* ⑦ 混音 / QC */}
                <FlowStep index={7} title="混音 / QC" icon={<IconCheck size={15} />}
                  hint={selectedOutput ? `QC ${selectedOutput.qc_ok ? "通过" : "未通过"} · ${selectedOutput.qc_checks}/${selectedOutput.qc_total}` : "尚未渲染"}>
                  <div className="v5-flow-mix">
                    <div className="v5-muted">
                      audio：{Object.entries({ ...(styleDetails[selected.style || ""]?.audio || {}), ...(selected.audio || {}) })
                        .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join("/") : String(value)}`)
                        .join(" · ") || "—"}
                    </div>
                    {selectedOutput ? (
                      <div className="v5-flow-qc">
                        <span className="v5-muted">
                          {fmtDuration(selectedOutput.duration)} · {selectedOutput.resolution} · {selectedOutput.fps}fps · {selectedOutput.shots} 镜
                        </span>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() =>
                            selectedOutput.timeline_url &&
                            window.open(mediaSrc(selectedOutput.timeline_url), "_blank", "noopener,noreferrer")
                          }
                        >
                          <IconExternal size={12} /> 时间线
                        </button>
                      </div>
                    ) : (
                      <span className="v5-muted">点右上「用此模板渲染」生成成片</span>
                    )}
                  </div>
                </FlowStep>
              </ol>
            </div>
            )}
          </div>
        </div>
      )}

      {/* ---------------------------- 编辑模板 JSON ---------------------------- */}
      {selectedId && editOpen && selected && (
        <div className="v5-overlay" onMouseDown={closeEdit}>
          <div className="v5-modal v5-modal-xl" onMouseDown={(event) => event.stopPropagation()}>
            <div className="v5-modal-head">
              <div>
                <b>编辑模板 JSON · {selected.label || selectedId}</b>
                <span className="v5-muted">
                  GET /api/v5/templates/{selectedId} → PUT 保存为 custom 覆盖（保存前校验；重渲后生效）
                </span>
              </div>
              <button type="button" className="icon-btn" onClick={closeEdit} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </div>
            <div className="v5-modal-body v5-tpl-edit">
              <textarea
                className="textarea v5-json-editor"
                spellCheck={false}
                value={editText}
                onChange={(event) => setEditText(event.target.value)}
              />
              {editError && <div className="v5-error v5-json-error">{editError}</div>}
              {editNotice && <div className="v5-json-ok"><IconCheck size={13} /> {editNotice}</div>}
              <div className="v5-form-actions">
                <span className="v5-muted">
                  写入 contract/v5/custom/templates/{selectedId}.json；字段规范见 contract/v5/schema/template.schema.json
                </span>
                <div className="v5-spacer" />
                <button type="button" className="btn btn-ghost" onClick={() => void doSave(true)} disabled={editBusy}>
                  {editBusy ? <Spinner size={13} /> : null} 只校验（dry_run）
                </button>
                <button type="button" className="btn btn-primary" onClick={() => void doSave(false)} disabled={editBusy}>
                  {editBusy ? <Spinner size={13} /> : <IconCheck size={13} />} 保存为 custom 覆盖
                </button>
                <button type="button" className="btn btn-ghost" onClick={closeEdit}>
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {templates.length === 0 && <EmptyState title="模板库为空" hint="检查 contract/v5/templates" />}

      <VideoModal
        output={playing}
        onClose={() => setPlaying(null)}
        onOpenTimeline={(output) =>
          output.timeline_url && window.open(mediaSrc(output.timeline_url), "_blank", "noopener,noreferrer")
        }
        onOpenQc={(output) =>
          output.qc_url && window.open(mediaSrc(output.qc_url), "_blank", "noopener,noreferrer")
        }
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// 工作流步骤小部件
// ---------------------------------------------------------------------------
const FLOW = [
  { key: "acts", short: "分镜", title: "① 分镜 acts→shots" },
  { key: "tts", short: "TTS", title: "② 固定声音 TTS" },
  { key: "scenes", short: "场景", title: "③ 场景 AI plate + style finish" },
  { key: "chars", short: "立绘", title: "④ 角色 AI 立绘 + 口型" },
  { key: "motion", short: "镜头", title: "⑤ motion2d 镜头动画" },
  { key: "pack", short: "包装", title: "⑥ 字幕/包装" },
  { key: "qc", short: "QC", title: "⑦ 混音/QC" },
];

function FlowStep({
  index,
  title,
  icon,
  hint,
  children,
}: {
  index: number;
  title: string;
  icon: ReactNode;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <li className="v5-flow-step">
      <div className="v5-flow-step-head">
        <span className="v5-flow-step-index">{index}</span>
        <span className="v5-flow-step-icon">{icon}</span>
        <b>{title}</b>
        {hint && <span className="v5-muted">{hint}</span>}
      </div>
      {children && <div className="v5-flow-step-body">{children}</div>}
    </li>
  );
}

function uniqueList(values: (string | undefined)[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    if (!value || seen.has(value)) return;
    seen.add(value);
    result.push(value);
  });
  return result;
}

function cameraCounts(acts?: TemplateAct[]): [string, number][] {
  const counts = new Map<string, number>();
  (acts || []).forEach((act) => {
    if (!act.camera) return;
    counts.set(act.camera, (counts.get(act.camera) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function cameraHint(acts?: TemplateAct[]): string {
  const strengths = (acts || []).map((act) => act.camera_strength).filter(
    (value): value is number => typeof value === "number"
  );
  const avg = strengths.length ? strengths.reduce((sum, value) => sum + value, 0) / strengths.length : 0;
  return `${cameraCounts(acts).length} 种机位 · 平均强度 ${(Math.round(avg * 100) / 100).toFixed(2)}`;
}

// 避免未使用告警：clockValue / IconArrowRight 等保留给后续扩展
void clockValue;
void IconArrowRight;
