import { useEffect, useState, type CSSProperties } from "react";
import { api, errorText } from "../lib/api";
import type { Character, CharacterSpec, Job } from "../types";
import {
  CHARACTER_STYLE_DESC,
  CHARACTER_STYLE_KEYS,
  CHARACTER_STYLE_LABEL,
  GENDER_LABEL,
  HAIR_STYLE_LABEL,
  HAIR_SWATCHES,
  OUTFIT_LABEL,
  ACCESSORY_LABEL,
  STYLE_SWATCHES,
  VOICE_OPTIONS,
  characterStyles,
  firstChar,
  styleFamily,
} from "../lib/format";
import { useToast } from "./Toast";
import { Field, Modal, Notice, Spinner } from "./ui";
import { IconCheck, IconSpark } from "./icons";

interface Props {
  open: boolean;
  projectId: string;
  onClose: () => void;
  onSubmitted: (character: Character, job: Job | null) => void;
}

const HAIR_STYLE_OPTIONS = ["short_swept", "spiky", "bob", "twin_tail", "ponytail", "long_straight", "bald", "bun"];

interface FormState {
  name: string;
  title: string;
  persona: string;
  voice: string;
  accent: string;
  styles: string[];
  gender: string;
  age: string;
  chibi: number;
  hairColor: string;
  hairStyle: string;
  outfit: string;
  accessory: string;
}

const DEFAULT_FORM: FormState = {
  name: "",
  title: "嘉宾",
  persona: "",
  voice: "zh-CN-YunxiNeural",
  accent: "#F5C518",
  styles: ["3d_toon"],
  gender: "male",
  age: "30",
  chibi: 0.2,
  hairColor: "#2B2B33",
  hairStyle: "short_swept",
  outfit: "jacket",
  accessory: "none",
};

export function NewCharacterModal({ open, projectId, onClose, onSubmitted }: Props) {
  const notify = useToast();
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm(DEFAULT_FORM);
      setBusy(false);
      setError(null);
    }
  }, [open]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const toggleStyle = (style: string) => {
    setForm((prev) => {
      const exists = prev.styles.includes(style);
      if (exists && prev.styles.length === 1) return prev;
      const styles = exists ? prev.styles.filter((item) => item !== style) : [...prev.styles, style];
      return { ...prev, styles };
    });
  };

  const buildSpec = (): CharacterSpec => ({
    name: form.name.trim(),
    title: form.title.trim() || "嘉宾",
    persona: form.persona.trim(),
    voice: form.voice,
    accent_color: form.accent,
    style: { primary: form.styles[0], variants: form.styles },
    appearance: {
      gender: form.gender,
      age: Number(form.age) || 30,
      height_ratio: 1,
      head_scale: 1.12,
      chibi: form.chibi,
      skin: "#F2D6C3",
      hair_color: form.hairColor,
      hair_style: form.hairStyle,
      eye_color: "#2B2B33",
      eye_size: 1,
      brow: form.gender === "female" ? "soft" : "thick",
      facial_hair: "none",
      outfit: { type: form.outfit, main: form.accent, accent: "#F4F1E8", shirt: "#FFFFFF" },
      accessories: form.accessory === "none" ? [] : [form.accessory],
      marks: [],
    },
  });

  const submit = async () => {
    if (!form.name.trim()) {
      setError("请先给角色起个名字");
      return;
    }
    if (form.styles.length === 0) {
      setError("至少选择一种角色风格");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const spec = buildSpec();
      const character = await api.createCharacter(projectId, spec);
      let job: Job | null = null;
      try {
        job = await api.generateCharacter(character.id, form.styles);
        notify("角色「" + character.name + "」已创建，生成任务已提交", "success");
      } catch (err) {
        notify("角色已创建，但生成任务提交失败：" + errorText(err), "error");
      }
      onSubmitted(character, job);
    } catch (err) {
      const message = errorText(err);
      setError("创建失败：" + message);
      notify("创建角色失败：" + message, "error");
    } finally {
      setBusy(false);
    }
  };

  const previewStyles = characterStyles({ primary: form.styles[0], variants: form.styles });
  const accent = form.accent;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建角色"
      subtitle="平台原创动漫角色 · 创建后自动触发 Blender 生成：半身像 / 三视图 / 四表情 / 转面视频"
      size="lg"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? <Spinner size={14} /> : <IconSpark size={16} />}
            {busy ? "创建中…" : "创建并生成角色"}
          </button>
        </>
      }
    >
      {error && <Notice tone="danger">{error}</Notice>}

      <div className="new-char-layout">
        <div className="new-char-form">
          <div className="form-grid">
            <Field label="角色名字">
              <input
                className="input"
                value={form.name}
                maxLength={20}
                placeholder="例如：小满"
                onChange={(event) => update("name", event.target.value)}
                autoFocus
              />
            </Field>
            <Field label="身份 / 定位">
              <input
                className="input"
                value={form.title}
                maxLength={20}
                placeholder="例如：飞行嘉宾"
                onChange={(event) => update("title", event.target.value)}
              />
            </Field>
          </div>

          <Field label="人设">
            <textarea
              className="textarea"
              rows={2}
              value={form.persona}
              maxLength={80}
              placeholder="例如：嘴硬心软、爱拆台、金句制造机"
              onChange={(event) => update("persona", event.target.value)}
            />
          </Field>

          <div className="form-grid">
            <Field label="声音（edge-tts）">
              <select className="select select-wide" value={form.voice} onChange={(event) => update("voice", event.target.value)}>
                {VOICE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="强调色">
              <div className="color-field">
                <input type="color" className="color-input" value={form.accent} onChange={(event) => update("accent", event.target.value)} />
                <div className="swatch-row">
                  {STYLE_SWATCHES.map((color) => (
                    <button
                      key={color}
                      type="button"
                      className={"swatch" + (form.accent === color ? " active" : "")}
                      style={{ background: color }}
                      onClick={() => update("accent", color)}
                      aria-label={color}
                    />
                  ))}
                </div>
              </div>
            </Field>
          </div>

          <Field label="角色风格（多选，第一个为主风格）">
            <div className="style-pick-grid">
              {CHARACTER_STYLE_KEYS.map((style) => {
                const index = form.styles.indexOf(style);
                const active = index >= 0;
                return (
                  <button
                    key={style}
                    type="button"
                    className={"style-pick" + (active ? " active" : "")}
                    onClick={() => toggleStyle(style)}
                    title={CHARACTER_STYLE_DESC[style]}
                  >
                    <span className={"style-family-tag family-" + styleFamily(style).toLowerCase()}>{styleFamily(style)}</span>
                    <span className="style-pick-label">{CHARACTER_STYLE_LABEL[style]}</span>
                    <span className="style-pick-desc">{CHARACTER_STYLE_DESC[style]}</span>
                    {active && (
                      <span className="style-pick-check">
                        {index === 0 ? "主" : <IconCheck size={12} />}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </Field>

          <div className="form-note">
            <IconSpark size={13} /> 外观预设会写入角色 spec，Blender 生成器按它程序化建模；后续可在角色详情中继续迭代。
          </div>

          <div className="form-subhead">预设外观</div>
          <div className="form-grid">
            <Field label="性别">
              <div className="segmented">
                {["male", "female", "neutral"].map((gender) => (
                  <button
                    key={gender}
                    type="button"
                    className={"segmented-item" + (form.gender === gender ? " active" : "")}
                    onClick={() => update("gender", gender)}
                  >
                    {GENDER_LABEL[gender]}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="年龄">
              <input
                className="input"
                type="number"
                min={16}
                max={70}
                value={form.age}
                onChange={(event) => update("age", event.target.value)}
              />
            </Field>
            <Field label="发色">
              <div className="swatch-row">
                {HAIR_SWATCHES.map((color) => (
                  <button
                    key={color}
                    type="button"
                    className={"swatch" + (form.hairColor === color ? " active" : "")}
                    style={{ background: color }}
                    onClick={() => update("hairColor", color)}
                    aria-label={color}
                  />
                ))}
              </div>
            </Field>
            <Field label="发型">
              <select className="select select-wide" value={form.hairStyle} onChange={(event) => update("hairStyle", event.target.value)}>
                {HAIR_STYLE_OPTIONS.map((style) => (
                  <option key={style} value={style}>
                    {HAIR_STYLE_LABEL[style]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="服装">
              <select className="select select-wide" value={form.outfit} onChange={(event) => update("outfit", event.target.value)}>
                {Object.entries(OUTFIT_LABEL).map(([key, text]) => (
                  <option key={key} value={key}>
                    {text}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="配饰">
              <select className="select select-wide" value={form.accessory} onChange={(event) => update("accessory", event.target.value)}>
                {Object.entries(ACCESSORY_LABEL).map(([key, text]) => (
                  <option key={key} value={key}>
                    {text}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label={"Q 版度 " + Math.round(form.chibi * 100) + "%"}>
            <input
              type="range"
              className="range"
              min={0}
              max={0.4}
              step={0.05}
              value={form.chibi}
              onChange={(event) => update("chibi", Number(event.target.value))}
            />
          </Field>
        </div>

        <aside className="new-char-preview">
          <div className="new-char-preview-title">角色预览</div>
          <div
            className="new-char-avatar"
            style={{ "--accent": accent, "--hair": form.hairColor } as CSSProperties}
          >
            <span className="new-char-avatar-initial">{firstChar(form.name || "新")}</span>
            <span className="new-char-avatar-glow" />
          </div>
          <div className="new-char-preview-name">{form.name || "未命名角色"}</div>
          <div className="new-char-preview-title">{form.title || "嘉宾"}</div>
          <div className="char-style-row">
            {previewStyles.map((style) => (
              <span key={style} className="char-style-chip">
                {styleFamily(style)} · {CHARACTER_STYLE_LABEL[style]}
              </span>
            ))}
          </div>
          <p className="new-char-preview-persona">{form.persona || "写一句人设，生成后角色会更有辨识度。"}</p>
          <div className="new-char-preview-meta">
            <span>声线</span>
            <b>{VOICE_OPTIONS.find((item) => item.value === form.voice)?.label.split(" · ")[0] || form.voice}</b>
            <span>年龄</span>
            <b>{form.age} 岁</b>
            <span>发色</span>
            <b className="new-char-color">
              <i style={{ background: form.hairColor }} /> {form.hairColor}
            </b>
          </div>
          <div className="new-char-preview-hint">生成完成后，半身像会替换此占位预览。</div>
        </aside>
      </div>
    </Modal>
  );
}
