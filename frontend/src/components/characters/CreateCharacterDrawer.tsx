// V21 · 新建角色抽屉：基础资料 / 外观 / 音色（24 preset 试听）
// 提交 POST /api/v5/characters（只写角色数据，不触发 GPU）。
import { useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import type { VoicePreset } from "../../lib/voice";
import { voiceApi } from "../../lib/voice";
import { creationApi, creationErrorMessage, isCreationNotReady, type CharacterCreateBody, type CharacterCreateResponse } from "../../lib/creation";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconClose, IconPlay, IconRefresh, IconSpark, IconVolume } from "../icons";
import "../../styles/creation.css";

const ERA_OPTIONS = [
  { id: "modern", label: "现代" },
  { id: "history", label: "历史" },
];

const GENDER_OPTIONS = [
  { id: "female", label: "女" },
  { id: "male", label: "男" },
  { id: "other", label: "其他" },
];

const HAIR_STYLES = ["twin_tail", "ponytail", "short", "long", "bun", "wave", "buzz", "bald"];

const OUTFIT_TYPES = ["hoodie", "robe", "suit", "dress", "coat", "uniform", "casual", "armor"];

const DEFAULT_VOICE_SAMPLE = "你好，我是新角色。这是我的试听句。";

interface Props {
  open: boolean;
  presets: VoicePreset[];
  /** 试听用的已存在角色（新建角色尚无 id，借已存在角色 + voice_id 扩展试听） */
  auditionCharacter?: string;
  onClose: () => void;
  onCreated: (id: string, res: CharacterCreateResponse) => void;
}

export function CreateCharacterDrawer({ open, presets, auditionCharacter, onClose, onCreated }: Props) {
  const notify = useToast();
  const [label, setLabel] = useState("");
  const [era, setEra] = useState("modern");
  const [persona, setPersona] = useState("");
  const [gender, setGender] = useState("female");
  const [age, setAge] = useState("28");
  const [hairStyle, setHairStyle] = useState("short");
  const [hairColor, setHairColor] = useState("#6B4A8F");
  const [outfitType, setOutfitType] = useState("hoodie");
  const [outfitMain, setOutfitMain] = useState("#8B6BB5");
  const [outfitAccent, setOutfitAccent] = useState("#5AC8FA");
  const [accessories, setAccessories] = useState("");
  const [rolePool, setRolePool] = useState("host, guest_a");
  const [voiceId, setVoiceId] = useState(presets[0]?.voice_id || "");
  const [autoArt, setAutoArt] = useState(true);
  const [autoArtModes, setAutoArtModes] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [auditioning, setAuditioning] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (!open) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      return;
    }
    setErrors([]);
    setSaving(false);
    setAutoArt(true);
    setAutoArtModes(false);
    if (!voiceId && presets[0]) setVoiceId(presets[0].voice_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, open, saving]);

  const voiceLabel = useMemo(() => {
    const map = new Map<string, string>();
    presets.forEach((preset) => map.set(preset.voice_id, preset.label || preset.voice_id));
    return map;
  }, [presets]);

  const audition = async (preset: VoicePreset) => {
    if (!auditionCharacter) {
      notify("试听需要已有角色作为音色参考，稍后可在角色详情继续试听", "info");
      return;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setAuditioning(preset.voice_id);
    try {
      const res = await voiceApi.preview(auditionCharacter, { voiceId: preset.voice_id, text: DEFAULT_VOICE_SAMPLE });
      const audio = new Audio(mediaSrc(res.url) + (res.url.includes("?") ? "&" : "?") + "t=" + Date.now());
      audioRef.current = audio;
      audio.onended = () => setAuditioning((prev) => (prev === preset.voice_id ? null : prev));
      audio.onerror = () => setAuditioning(null);
      await audio.play();
    } catch (err) {
      setAuditioning(null);
      const message = errorText(err);
      setErrors([message.includes("404") ? "试听接口暂不可用（可先创建角色，随后在详情里试听/换声音）" : "试听失败：" + message]);
    }
  };

  const submit = async () => {
    if (!label.trim()) {
      setErrors(["请填写角色名字"]);
      return;
    }
    setSaving(true);
    setErrors([]);
    const body: CharacterCreateBody = {
      label: label.trim(),
      era,
      persona: persona.trim(),
      voice_id: voiceId || undefined,
      role_pool: rolePool
        .split(/[,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
      tags: era === "history" ? ["history"] : [],
      auto_art: autoArt,
      auto_art_modes: autoArt && autoArtModes ? ["all"] : undefined,
      appearance: {
        gender,
        age: Math.max(1, Math.min(120, Number(age) || 28)),
        hair: hairColor,
        hair_style: hairStyle,
        outfit: { type: outfitType, main: outfitMain, accent: outfitAccent },
        accessories: accessories
          .split(/[,，\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      },
    };
    try {
      const res = await creationApi.createCharacter(body);
      const art = res?.art || null;
      if (autoArt && art?.generated) {
        notify(`角色「${label.trim()}」已创建，概念图已生成`, "success");
      } else if (autoArt && art && art.generated === false) {
        notify(`角色「${label.trim()}」已创建；概念图生成失败${art.error ? "（" + art.error + "）" : ""}，可在详情重新生成`, "info");
      } else {
        notify(`角色「${label.trim()}」已创建`, "success");
      }
      onCreated(res.id, res);
    } catch (err) {
      const items = creationErrorMessage(err);
      setErrors(items);
      if (isCreationNotReady(err)) {
        notify("创建接口尚未就绪（后端 V21 API 实现中），可稍后重试", "info");
      }
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div className="v21-mask is-right" onMouseDown={() => !saving && onClose()} role="presentation">
      <aside className="v21-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="新建角色">
        <header className="v21-drawer-head">
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            <span className="v21-drawer-kicker">NEW CHARACTER · 新建角色</span>
            <h3 className="v21-drawer-title">新建角色</h3>
            <p className="v21-drawer-sub">
              写入角色合同（custom 覆盖）；创建后可继续换声音、生成概念图、加入模板 cast。
            </p>
          </div>
          <button type="button" className="v21-close" onClick={onClose} aria-label="关闭" disabled={saving}>
            <IconClose size={16} />
          </button>
        </header>

        <div className="v21-body">
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

          <div className="v21-grid">
            <label className="v21-field is-wide">
              <span className="v21-label"><b>名字</b> NAME</span>
              <input className="v21-input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="例如：阿梨" maxLength={20} autoFocus />
            </label>

            <label className="v21-field">
              <span className="v21-label">时代 ERA</span>
              <select className="v21-select" value={era} onChange={(event) => setEra(event.target.value)}>
                {ERA_OPTIONS.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
            <label className="v21-field">
              <span className="v21-label">性别 GENDER</span>
              <select className="v21-select" value={gender} onChange={(event) => setGender(event.target.value)}>
                {GENDER_OPTIONS.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>

            <label className="v21-field is-wide">
              <span className="v21-label">人格 PERSONA</span>
              <textarea
                className="v21-textarea"
                value={persona}
                onChange={(event) => setPersona(event.target.value)}
                placeholder="一句话描述角色性格 / 说话风格，例如：语速快、脑洞大、乐观到有点欠揍"
                maxLength={200}
              />
            </label>

            <label className="v21-field">
              <span className="v21-label">年龄 AGE</span>
              <input className="v21-input" type="number" min={1} max={120} value={age} onChange={(event) => setAge(event.target.value)} />
            </label>
            <label className="v21-field">
              <span className="v21-label">发型 HAIR STYLE</span>
              <select className="v21-select" value={hairStyle} onChange={(event) => setHairStyle(event.target.value)}>
                {HAIR_STYLES.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label className="v21-field">
              <span className="v21-label">主色 HAIR</span>
              <div className="v21-chips">
                <input type="color" className="v21-input" style={{ width: 58, padding: 4 }} value={hairColor} onChange={(event) => setHairColor(event.target.value)} />
                <input className="v21-input" style={{ flex: "1 1 auto" }} value={hairColor} onChange={(event) => setHairColor(event.target.value)} />
              </div>
            </label>
            <label className="v21-field">
              <span className="v21-label">服装类型 OUTFIT</span>
              <select className="v21-select" value={outfitType} onChange={(event) => setOutfitType(event.target.value)}>
                {OUTFIT_TYPES.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>

            <label className="v21-field">
              <span className="v21-label">服装主色</span>
              <div className="v21-chips">
                <input type="color" className="v21-input" style={{ width: 58, padding: 4 }} value={outfitMain} onChange={(event) => setOutfitMain(event.target.value)} />
                <input className="v21-input" style={{ flex: "1 1 auto" }} value={outfitMain} onChange={(event) => setOutfitMain(event.target.value)} />
              </div>
            </label>
            <label className="v21-field">
              <span className="v21-label">服装辅色 / 配饰</span>
              <input className="v21-input" value={outfitAccent} onChange={(event) => setOutfitAccent(event.target.value)} placeholder="#5AC8FA" />
            </label>

            <label className="v21-field is-wide">
              <span className="v21-label">配饰 ACCESSORIES（逗号分隔）</span>
              <input className="v21-input" value={accessories} onChange={(event) => setAccessories(event.target.value)} placeholder="headphones, glasses, ring" />
            </label>

            <label className="v21-field is-wide">
              <span className="v21-label">角色池 ROLE POOL（逗号分隔）</span>
              <input className="v21-input" value={rolePool} onChange={(event) => setRolePool(event.target.value)} placeholder="host, guest_a, expert" />
            </label>
          </div>

          <div className="v21-field is-wide">
            <span className="v21-label"><IconVolume size={11} /> 音色 VOICE（{presets.length} preset）</span>
            <div className="v21-voice-grid">
              {presets.map((preset) => (
                <div
                  key={preset.voice_id}
                  className={"v21-voice-option" + (voiceId === preset.voice_id ? " is-active" : "")}
                  onClick={() => setVoiceId(preset.voice_id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setVoiceId(preset.voice_id);
                    }
                  }}
                >
                  <span className="v21-voice-name">{preset.label || preset.voice_id}</span>
                  <span className="v21-voice-id">{preset.voice_id}</span>
                  <button
                    type="button"
                    className="v21-voice-audition"
                    onClick={(event) => {
                      event.stopPropagation();
                      void audition(preset);
                    }}
                    title={`试听 ${preset.label || preset.voice_id}`}
                    aria-label={`试听 ${preset.label || preset.voice_id}`}
                  >
                    {auditioning === preset.voice_id ? <Spinner size={11} /> : <IconPlay size={11} />}
                  </button>
                </div>
              ))}
            </div>
            <span className="v21-hint">
              已选：{voiceLabel.get(voiceId) || voiceId || "—"} · 试听借已存在角色 + 指定 preset（audition），创建后可在角色详情继续换声音。
            </span>
          </div>
        </div>

        <footer className="v21-foot" style={{ flexDirection: "column", alignItems: "stretch", gap: 10 }}>
          <label className={"v21-autogen" + (autoArt ? " is-on" : "")} style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={autoArt}
              disabled={saving}
              onChange={(event) => {
                setAutoArt(event.target.checked);
                if (!event.target.checked) setAutoArtModes(false);
              }}
            />
            <span className="v21-autogen-text">
              <b>创建后生成概念图</b>
              <em>约 20–60 秒 · SDXL 立绘写入角色美术目录</em>
            </span>
          </label>
          <label className={"v21-autogen" + (autoArtModes ? " is-on" : "")} style={{ margin: 0, opacity: autoArt ? 1 : 0.55 }}>
            <input
              type="checkbox"
              checked={autoArtModes}
              disabled={saving || !autoArt}
              onChange={(event) => setAutoArtModes(event.target.checked)}
            />
            <span className="v21-autogen-text">
              <b>同时生成三视图与表情包</b>
              <em>约 1–2 分钟 · 三视图 3 张 + 表情 4 张 + 设定图 1 张</em>
            </span>
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="v21-foot-note">创建只写角色数据；勾选时会调用本地 imagegen 生成 portrait.png（不触发 3D 渲染），失败不阻塞创建。</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void submit()} disabled={saving}>
            {saving ? <Spinner size={13} /> : <IconSpark size={13} />}
            {saving
              ? autoArtModes
                ? "创建并生成素材中…（约 1–2 分钟）"
                : autoArt
                  ? "创建并生成中…"
                  : "创建中…"
              : autoArtModes
                ? "创建并生成三视图/表情"
                : autoArt
                  ? "创建并生成概念图"
                  : "创建角色"}
          </button>
          </div>
        </footer>
      </aside>
    </div>
  );
}
