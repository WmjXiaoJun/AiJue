// V21 · 新建模板向导（4 步）：基本信息 → 角色 cast → 分幕 → 预览创建
// 提交 POST /api/v5/templates（后端会合成 TTS/episode 并生成 semantic brief；不触发 GPU）。
import { useEffect, useMemo, useRef, useState } from "react";
import type { V5CatalogResponse, V5TemplateSummary } from "../../lib/v5";
import { RENDER_STYLE_META, RENDER_STYLE_ORDER, RENDER_TYPE_META, type RenderStyle, type RenderType } from "../../lib/cinema";
import { creationApi, creationErrorMessage, isCreationNotReady, makeCustomId, type TemplateActInput, type TemplateCastInput, type TemplateCreateResponse } from "../../lib/creation";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconCheck, IconClose, IconPlus, IconSpark, IconTrash } from "../icons";
import "../../styles/creation.css";

const ROLE_OPTIONS = ["host", "figure_a", "figure_b", "guest_a", "guest_b", "narrator", "expert", "judge"];
const STEP_META = [
  { no: 1, label: "基本信息", en: "BASIC" },
  { no: 2, label: "角色 cast", en: "CAST" },
  { no: 3, label: "分幕", en: "ACTS" },
  { no: 4, label: "预览创建", en: "PREVIEW" },
];
const ASPECT_OPTIONS = ["16:9", "9:16", "1:1", "4:3"];

interface CastRow {
  key: string;
  character: string;
  role: string;
  name: string;
}

interface ActRow {
  key: string;
  scene: string;
  speaker: string;
  text: string;
  broll: boolean;
}

interface Props {
  open: boolean;
  catalog: V5CatalogResponse | null;
  existingTemplates: V5TemplateSummary[];
  onClose: () => void;
  onCreated: (id: string, res: TemplateCreateResponse) => void;
}

function rowKey(): string {
  return Math.random().toString(36).slice(2, 9);
}

function estimateSpeechDuration(text: string): number {
  const chars = text.replace(/\s+/g, "").length;
  return Math.round((chars / 4.2 + 0.6) * 10) / 10;
}

export function CreateTemplateWizard({ open, catalog, existingTemplates, onClose, onCreated }: Props) {
  const notify = useToast();
  const [step, setStep] = useState(1);
  const [id, setId] = useState(() => makeCustomId("custom_tpl"));
  const [label, setLabel] = useState("");
  const [style, setStyle] = useState("");
  const [renderType, setRenderType] = useState<RenderType>("3d");
  const [renderStyle, setRenderStyle] = useState<RenderStyle>("jp_anime");
  const [aspect, setAspect] = useState("16:9");
  const [targetDuration, setTargetDuration] = useState("40");
  const [description, setDescription] = useState("");
  const [cast, setCast] = useState<CastRow[]>([]);
  const [acts, setActs] = useState<ActRow[]>([]);
  const [castPick, setCastPick] = useState("");
  const [sceneQueries, setSceneQueries] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [autoGenerate, setAutoGenerate] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);

  const styles = catalog?.styles || [];
  const scenes = catalog?.scenes || [];
  const characters = catalog?.characters || [];

  const reset = () => {
    setStep(1);
    setId(makeCustomId("custom_tpl"));
    setLabel("");
    setStyle(styles[0]?.id || "");
    setRenderType("3d");
    setRenderStyle("jp_anime");
    setAspect("16:9");
    setTargetDuration("40");
    setDescription("");
    setCast([]);
    setActs([]);
    setCastPick("");
    setSceneQueries({});
    setErrors([]);
    setCreating(false);
    setAutoGenerate(true);
  };

  useEffect(() => {
    if (!open) return;
    reset();
    // 默认 4 幕
    setActs(Array.from({ length: 4 }, () => ({ key: rowKey(), scene: "", speaker: "", text: "", broll: false })));
    if (!style && styles[0]) setStyle(styles[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 目录异步加载完成后补默认风格（后端慢/重连场景）
  useEffect(() => {
    if (open && !style && styles.length) setStyle(styles[0].id);
  }, [open, style, styles]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !creating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [creating, onClose, open]);

  const idValid = /^[a-z][a-z0-9_]{1,60}$/.test(id);
  const idTaken = existingTemplates.some((item) => item.id === id) || templatesHasId(id);

  const castValid = cast.length >= 3 && cast.length <= 5 && cast.every((row) => row.character && row.role);
  const actsValid =
    acts.length >= 4 &&
    acts.length <= 8 &&
    acts.every((row) => row.scene && (row.broll || (row.speaker && row.text.trim())));
  const step1Valid = Boolean(label.trim()) && idValid && !idTaken && Boolean(style);
  const canNext = step === 1 ? step1Valid : step === 2 ? castValid : step === 3 ? actsValid : true;

  const addCast = () => {
    if (!castPick) return;
    const character = characters.find((item) => item.id === castPick);
    if (!character) return;
    if (cast.some((row) => row.character === castPick)) {
      notify("该角色已在 cast 中", "info");
      return;
    }
    if (cast.length >= 5) {
      notify("cast 最多 5 个角色", "info");
      return;
    }
    setCast((prev) => [
      ...prev,
      { key: rowKey(), character: castPick, role: ROLE_OPTIONS[prev.length] || "guest_a", name: character.label },
    ]);
    setCastPick("");
  };

  const removeCast = (key: string) => setCast((prev) => prev.filter((row) => row.key !== key));

  const addAct = () => {
    if (acts.length >= 8) {
      notify("最多 8 幕", "info");
      return;
    }
    setActs((prev) => [...prev, { key: rowKey(), scene: "", speaker: cast[0]?.role || "", text: "", broll: false }]);
  };

  const removeAct = (key: string) => {
    if (acts.length <= 4) {
      notify("至少 4 幕", "info");
      return;
    }
    setActs((prev) => prev.filter((row) => row.key !== key));
  };

  const submit = async () => {
    setCreating(true);
    setErrors([]);
    const castBody: TemplateCastInput[] = cast.map((row) => ({ role: row.role, character: row.character, name: row.name.trim() || undefined }));
    const actBody: TemplateActInput[] = acts.map((row) => ({
      scene: row.scene,
      speaker: row.broll ? undefined : row.speaker,
      text: row.broll ? undefined : row.text.trim(),
    }));
    try {
      const res = await creationApi.createTemplate({
        id,
        label: label.trim(),
        style,
        render_type: renderType,
        render_style: renderType === "2d" ? renderStyle : undefined,
        aspect,
        description: description.trim() || undefined,
        cast: castBody,
        acts: actBody,
        auto_brief: true,
        auto_generate: autoGenerate,
      });
      const pipeline = res?.pipeline || null;
      const renderMeta = RENDER_TYPE_META[renderType];
      if (pipeline?.started) {
        notify(`模板已创建，${renderMeta.label}生成已开始（${renderMeta.wait}）`, "success");
      } else if (autoGenerate && pipeline && !pipeline.started) {
        notify(
          pipeline.reason === "busy"
            ? "已有任务运行，模板已创建；可在生成队列或一键成片稍后生成"
            : `模板已创建；${renderMeta.label}生成未能启动${pipeline.error ? "（" + pipeline.error + "）" : ""}，可稍后手动生成`,
          "info",
        );
      } else {
        notify(
          `模板「${label.trim()}」已创建${res?.shots ? ` · ${res.shots} 镜` : ""}${res?.total_duration ? ` · ${Math.round(Number(res.total_duration))}s` : ""}；可去一键成片生成${RENDER_TYPE_META[renderType].label}`,
          "success",
        );
      }
      if (Array.isArray(res?.warnings) && res.warnings.length) {
        notify("创建完成，但有提示：" + res.warnings.join("；"), "info");
      }
      onCreated(res.id, res);
    } catch (err) {
      setErrors(creationErrorMessage(err));
      if (isCreationNotReady(err)) toastNotReady();
    } finally {
      setCreating(false);
    }
  };

  const toastNotReady = () => notify("创建接口尚未就绪（后端 V21 API 实现中），可稍后重试", "info");

  if (!open) return null;

  return (
    <div className="v21-mask is-center" onMouseDown={() => !creating && onClose()} role="presentation">
      <div className="v21-wizard" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="新建模板">
        <header className="v21-wizard-head">
          <div className="v21-wizard-head-main">
            <span className="v21-drawer-kicker">NEW TEMPLATE · 新建模板</span>
            <h3 className="v21-drawer-title">新建模板</h3>
            <p className="v21-drawer-sub">
              创建模板合同 + TTS 分镜 + semantic brief；选择节目类型（2D 快 / 3D 与写实慢），创建后可在「一键成片」生成。
            </p>
          </div>
          <button type="button" className="v21-close" onClick={onClose} aria-label="关闭" disabled={creating}>
            <IconClose size={16} />
          </button>
        </header>

        <div className="v21-steps" role="tablist" aria-label="新建模板步骤">
          {STEP_META.map((item) => (
            <button
              key={item.no}
              type="button"
              role="tab"
              aria-selected={step === item.no}
              className={"v21-step" + (step === item.no ? " is-active" : "") + (step > item.no ? " is-done" : "")}
              onClick={() => item.no < step && setStep(item.no)}
              disabled={item.no > step}
            >
              {step > item.no ? <IconCheck size={10} /> : `0${item.no}`} {item.label}
            </button>
          ))}
        </div>

        <div className="v21-wizard-body">
          {errors.length > 0 && (
            <div className="v21-error">
              <b>创建失败（{errors.length} 项）</b>
              <ul>
                {errors.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {step === 1 && (
            <div className="v21-grid">
              <label className="v21-field">
                <span className="v21-label">模板 ID（英文小写）</span>
                <input className="v21-input" value={id} onChange={(event) => setId(event.target.value.trim().toLowerCase())} placeholder="custom_tpl_xxx" />
                <span className={"v21-hint" + (id && (!idValid || idTaken) ? " is-warn" : "")}>
                  {idTaken ? "id 已存在，请更换" : idValid ? "符合 ^[a-z][a-z0-9_]{1,60}$" : "需以小写字母开头，仅 a-z0-9_"}
                </span>
              </label>
              <label className="v21-field">
                <span className="v21-label">中文名</span>
                <input className="v21-input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如：深夜茶馆" maxLength={24} autoFocus />
              </label>
              <label className="v21-field">
                <span className="v21-label">风格（{styles.length}）</span>
                <select className="v21-select" value={style} onChange={(event) => setStyle(event.target.value)} disabled={!styles.length}>
                  {!styles.length && <option value="">目录未加载（后端未连接）</option>}
                  {styles.map((item) => (
                    <option key={item.id} value={item.id}>{item.label || item.id} · {item.id}</option>
                  ))}
                </select>
                {!styles.length && <span className="v21-hint is-warn">风格目录读取失败，请确认后端 8090 已启动后重试</span>}
              </label>
              <label className="v21-field">
                <span className="v21-label">画幅</span>
                <select className="v21-select" value={aspect} onChange={(event) => setAspect(event.target.value)}>
                  {ASPECT_OPTIONS.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </label>
              <div className="v21-field is-wide v41-render-field">
                <span className="v21-label">节目类型（渲染路径与耗时）</span>
                <div className="v41-render-grid" role="radiogroup" aria-label="节目类型">
                  {(Object.values(RENDER_TYPE_META) as typeof RENDER_TYPE_META[RenderType][]).map((meta) => (
                    <label key={meta.id} className={"v41-render-option is-" + meta.tone + (renderType === meta.id ? " is-active" : "")}>
                      <input
                        type="radio"
                        name="v41_render_type"
                        value={meta.id}
                        checked={renderType === meta.id}
                        onChange={() => setRenderType(meta.id)}
                      />
                      <b>{meta.label}</b>
                      <span className="v41-render-wait">{meta.wait}</span>
                      <em>{meta.desc}</em>
                    </label>
                  ))}
                </div>
                <span className="v21-hint">
                  {renderType === "2d"
                    ? "AI 生成 2D 动漫画面（cel-shading / 平涂），与 3D 同档约 15–40 分钟（镜头生成 → 口型 → 剪辑）"
                    : renderType === "3d"
                      ? "3D 电影走 3D pipeline（T2VA + 口型），单模板约 15–40 分钟"
                      : "写实实拍复用写实立绘/镜头并走 3D 生成路径，约 15–40 分钟"}
                </span>
              </div>
              {renderType === "2d" && (
                <div className="v21-field is-wide v44-style-field">
                  <span className="v21-label">2D 风格（影响角色气质与背景）</span>
                  <div className="v44-style-grid" role="radiogroup" aria-label="2D 风格">
                    {RENDER_STYLE_ORDER.map((id) => {
                      const meta = RENDER_STYLE_META[id];
                      return (
                        <label key={id} className={"v44-style-option" + (renderStyle === id ? " is-active" : "")}>
                          <input
                            type="radio"
                            name="v44_render_style"
                            value={id}
                            checked={renderStyle === id}
                            onChange={() => setRenderStyle(id)}
                          />
                          <b>{meta.label}{id === "jp_anime" ? "（默认）" : ""}</b>
                          <em>{meta.desc}</em>
                        </label>
                      );
                    })}
                  </div>
                  <span className="v21-hint">仅 2D 动画生效；不同风格使用不同 checkpoint/提示词，会改变角色气质、关键帧与背景画面（IP 权重更低、背景更完整）</span>
                </div>
              )}
              <label className="v21-field">
                <span className="v21-label">目标时长（参考，秒）</span>
                <input className="v21-input" type="number" min={10} max={180} value={targetDuration} onChange={(event) => setTargetDuration(event.target.value)} />
                <span className="v21-hint">实际时长以后端 TTS 逐幕合成为准（创建时会计算并写回）</span>
              </label>
              <label className="v21-field is-wide">
                <span className="v21-label">简介（可选）</span>
                <textarea className="v21-textarea" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="一句话描述这个模板的节目形态" maxLength={160} />
              </label>
            </div>
          )}

          {step === 2 && (
            <>
              <div className="v21-actions" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select className="v21-select" style={{ width: 280 }} value={castPick} onChange={(event) => setCastPick(event.target.value)}>
                  <option value="">从 {characters.length} 个角色中选择…</option>
                  {characters.map((item) => (
                    <option key={item.id} value={item.id} disabled={cast.some((row) => row.character === item.id)}>
                      {item.label} · {item.id}
                    </option>
                  ))}
                </select>
                <button type="button" className="cm-btn cm-btn-sm" onClick={addCast}>
                  <IconPlus size={12} /> 加入 cast
                </button>
                <span className="v21-hint">已选 {cast.length}/5（需 3–5 个）</span>
              </div>
              {cast.length === 0 ? (
                <div className="v18-empty">
                  <IconSpark size={24} />
                  <b>还没有 cast</b>
                  <span>从角色库选 3–5 个角色；每行可改 role 与显示名。</span>
                </div>
              ) : (
                cast.map((row, index) => {
                  const character = characters.find((item) => item.id === row.character);
                  return (
                    <div key={row.key} className="v21-cast-row">
                      <span className="v21-cast-no">{String(index + 1).padStart(2, "0")}</span>
                      <div className="v21-cast-select">
                        <select className="v21-select" value={row.character} onChange={(event) => setCast((prev) => prev.map((item) => (item.key === row.key ? { ...item, character: event.target.value } : item)))}>
                          {characters.map((item) => (
                            <option key={item.id} value={item.id}>{item.label} · {item.id}</option>
                          ))}
                        </select>
                        {character?.voice_provider && <span className="v21-hint">{character.voice_provider}</span>}
                      </div>
                      <select className="v21-select" value={row.role} onChange={(event) => setCast((prev) => prev.map((item) => (item.key === row.key ? { ...item, role: event.target.value } : item)))}>
                        {ROLE_OPTIONS.map((item) => (
                          <option key={item} value={item}>{item}</option>
                        ))}
                      </select>
                      <input className="v21-input" value={row.name} onChange={(event) => setCast((prev) => prev.map((item) => (item.key === row.key ? { ...item, name: event.target.value } : item)))} placeholder="显示名" />
                      <button type="button" className="v21-close" onClick={() => removeCast(row.key)} aria-label="移除角色" title="移除">
                        <IconTrash size={13} />
                      </button>
                    </div>
                  );
                })
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="v21-actions" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="v21-hint">分幕 {acts.length}/8（需 4–8 幕）；说话角色来自 cast，B-roll 无台词。</span>
                <button type="button" className="cm-btn cm-btn-sm" onClick={addAct} style={{ marginLeft: "auto" }}>
                  <IconPlus size={12} /> 添加一幕
                </button>
              </div>
              {acts.map((row, index) => {
                const sceneQuery = sceneQueries[row.key] || "";
                const sceneOptions = scenes.filter((scene) =>
                  !sceneQuery ? true : [scene.id, scene.label].filter(Boolean).join(" ").toLowerCase().includes(sceneQuery.toLowerCase()),
                );
                return (
                  <div key={row.key} className="v21-act-row">
                    <span className="v21-act-no">{String(index + 1).padStart(2, "0")}</span>
                    <div className="v21-cast-select">
                      <select className="v21-select" value={row.scene} onChange={(event) => setActs((prev) => prev.map((item) => (item.key === row.key ? { ...item, scene: event.target.value } : item)))}>
                        <option value="">选择场景（{scenes.length}）</option>
                        {sceneOptions.map((scene) => (
                          <option key={scene.id} value={scene.id}>{scene.label || scene.id} · {scene.id}</option>
                        ))}
                      </select>
                      <input
                        className="v21-input"
                        value={sceneQuery}
                        onChange={(event) => setSceneQueries((prev) => ({ ...prev, [row.key]: event.target.value }))}
                        placeholder="搜索场景…"
                      />
                    </div>
                    <select
                      className="v21-select"
                      value={row.broll ? "__broll__" : row.speaker}
                      onChange={(event) =>
                        setActs((prev) =>
                          prev.map((item) =>
                            item.key === row.key
                              ? event.target.value === "__broll__"
                                ? { ...item, broll: true, speaker: "", text: "" }
                                : { ...item, broll: false, speaker: event.target.value }
                              : item,
                          ),
                        )
                      }
                    >
                      <option value="__broll__">B-roll（无台词）</option>
                      {cast.map((castRow) => (
                        <option key={castRow.key} value={castRow.role}>{castRow.role} · {castRow.name || castRow.character}</option>
                      ))}
                    </select>
                    <div className="v21-act-text">
                      <textarea
                        className="v21-textarea"
                        value={row.text}
                        disabled={row.broll}
                        onChange={(event) => setActs((prev) => prev.map((item) => (item.key === row.key ? { ...item, text: event.target.value } : item)))}
                        placeholder={row.broll ? "B-roll 无台词" : "台词（中文 1–2 句）"}
                      />
                      {!row.broll && <span className="v21-hint">预计 {estimateSpeechDuration(row.text)}s</span>}
                    </div>
                    <button type="button" className="v21-close" onClick={() => removeAct(row.key)} aria-label="删除此幕" title="删除此幕">
                      <IconTrash size={13} />
                    </button>
                  </div>
                );
              })}
            </>
          )}

          {step === 4 && (
            <>
              <div className="v21-summary">
                <div className="v21-summary-item"><span>Template</span><b>{id}</b></div>
                <div className="v21-summary-item"><span>画面</span><b>{label || "—"} / {aspect}</b></div>
                <div className="v21-summary-item"><span>Cast</span><b>{cast.length} 角色</b></div>
                <div className="v21-summary-item"><span>分幕</span><b>{acts.length} 幕</b></div>
                <div className="v21-summary-item"><span>将生成</span><b>{acts.length} 镜</b></div>
                <div className="v21-summary-item"><span>节目类型</span><b>{RENDER_TYPE_META[renderType].label}{renderType === "2d" ? ` · ${RENDER_STYLE_META[renderStyle].label}` : ""}</b></div>
                <div className="v21-summary-item"><span>预计生成</span><b>{RENDER_TYPE_META[renderType].wait}</b></div>
              </div>

              <label className={"v21-autogen" + (autoGenerate ? " is-on" : "")}>
                <input
                  type="checkbox"
                  checked={autoGenerate}
                  disabled={creating}
                  onChange={(event) => setAutoGenerate(event.target.checked)}
                />
                <span className="v21-autogen-text">
                  <b>创建后立即生成 3D</b>
                  <em>约 10–45 分钟 · 后台任务，不阻塞页面</em>
                </span>
                <span className="v21-autogen-switch" aria-hidden="true" />
              </label>
              <div className="v21-hint">
                生成期间可离开页面，进度在「一键成片」可见；创建完成后会自动跳到「一键成片 · 第 ④ 步」。
              </div>
              <div className="v21-preview-list">
                {acts.map((row, index) => (
                  <div key={row.key} className="v21-preview-row">
                    <span>0{index + 1}</span>
                    <b>{row.scene || "—"}</b>
                    <span>{row.broll ? "B-roll（无台词）" : `${row.speaker || "—"}：${row.text || "—"}`}</span>
                    <em>{row.broll ? "—" : `${estimateSpeechDuration(row.text)}s`}</em>
                  </div>
                ))}
              </div>
              <div className="v21-hint">
                创建会：写入模板合同、按 cast 合成 24k TTS 试听 wav、写 episode 分镜、生成 semantic brief；耗时约 20–60s。
                {autoGenerate
                  ? "随后自动提交 3D 生成任务（后台约 10–45 分钟，可离开页面）。"
                  : "不自动生成；创建后可去「一键成片」手动开始 3D 生成。"}
              </div>
            </>
          )}
        </div>

        <footer className="v21-wizard-foot">
          <span className="v21-foot-note">
            {step === 1
              ? "填写 id / 中文名 / 风格后继续"
              : step === 2
                ? "选择 3–5 个角色并指定 role"
                : step === 3
                  ? "至少 4 幕，逐幕选场景与说话角色（或 B-roll）"
                  : autoGenerate
                    ? "确认后创建并自动提交 3D 生成任务（后台运行，可离开页面）"
                    : "确认后仅创建模板（不生成 3D）"}
          </span>
          <div className="v18-actions-right" style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
            <button type="button" className="cm-btn cm-btn-sm" onClick={onClose} disabled={creating}>
              取消
            </button>
            {step > 1 && (
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => setStep((prev) => prev - 1)} disabled={creating}>
                上一步
              </button>
            )}
            {step < 4 ? (
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => setStep((prev) => prev + 1)} disabled={!canNext}>
                下一步 →
              </button>
            ) : (
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void submit()} disabled={creating || !castValid || !actsValid}>
                {creating ? <Spinner size={13} /> : <IconSpark size={13} />}
                {creating
                  ? autoGenerate
                    ? "创建并生成中…"
                    : "创建中（TTS 合成，约 20–60s）…"
                  : autoGenerate
                    ? "创建并生成 3D"
                    : "仅创建模板"}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

/** 判断是否与内建/已存在模板 id 冲突（仅用于前端提示，最终以后端校验为准） */
function templatesHasId(id: string): boolean {
  return ["tonight_show", "classics", "history_talk", "masked_singer", "roundtable", "national_treasure"].includes(id);
}
