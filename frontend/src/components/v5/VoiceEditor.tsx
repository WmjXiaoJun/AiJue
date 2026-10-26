// V5 声音编辑器：voice_id（声音库预设）+ seed/cfg_scale/instruction → PUT 保存 → 重建声音锁
import { useState } from "react";
import type { V5CharacterSummary, V5VoiceInfo, V5VoicePreset } from "../../lib/v5";
import { v5api, voiceWavUrl } from "../../lib/v5";
import { useToast } from "../Toast";
import { IconClose, IconLock, IconVolume } from "../icons";
import { Field, Notice, Spinner } from "../ui";

interface Props {
  character: V5CharacterSummary;
  info: V5VoiceInfo;
  presets: V5VoicePreset[];
  onClose: () => void;
  onSaved: (characterId: string, info: V5VoiceInfo) => void;
}

export function VoiceEditor({ character, info, presets, onClose, onSaved }: Props) {
  const notify = useToast();
  const [voiceId, setVoiceId] = useState(info.voice_id);
  const [seed, setSeed] = useState(String(info.seed ?? ""));
  const [cfgScale, setCfgScale] = useState(String(info.cfg_scale ?? ""));
  const [instruction, setInstruction] = useState(info.instruction || "");
  const [saving, setSaving] = useState(false);
  const [locking, setLocking] = useState(false);
  const [saved, setSaved] = useState<{ needsRelock: boolean; key: string } | null>(null);
  const [error, setError] = useState("");

  const currentPreset = presets.find((preset) => preset.voice_id === voiceId);
  const dirty =
    voiceId !== info.voice_id ||
    seed !== String(info.seed ?? "") ||
    cfgScale !== String(info.cfg_scale ?? "") ||
    instruction !== (info.instruction || "");

  const refreshInfo = async (): Promise<V5VoiceInfo> => {
    const next = await v5api.characterVoice(character.id);
    onSaved(character.id, next);
    return next;
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      const body: Record<string, unknown> = { voice_id: voiceId };
      const seedNumber = Number(seed);
      if (seed !== "" && Number.isFinite(seedNumber)) body.seed = Math.round(seedNumber);
      const cfgNumber = Number(cfgScale);
      if (cfgScale !== "" && Number.isFinite(cfgNumber)) body.cfg_scale = cfgNumber;
      if (instruction.trim()) body.instruction = instruction.trim();
      const res = await v5api.putVoice(character.id, body);
      setSaved({ needsRelock: res.needs_relock, key: res.voice.key });
      await refreshInfo();
      notify("声音参数已保存（custom 覆盖）", "success");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleLock = async () => {
    setLocking(true);
    setError("");
    try {
      await v5api.lockVoice(character.id, true);
      const next = await refreshInfo();
      setSaved({ needsRelock: next.needs_relock, key: next.key });
      notify("声音锁已重建：之后渲染用新音色", "success");
    } catch (err) {
      setError("重建声音锁失败：" + (err instanceof Error ? err.message : String(err)));
    } finally {
      setLocking(false);
    }
  };

  const handlePreview = async () => {
    try {
      await v5api.previewVoice(character.id);
      const audio = new Audio(voiceWavUrl(character.id));
      await audio.play();
      notify("正在试听（基于当前已保存参数）", "info");
    } catch (err) {
      setError("试听失败：" + (err instanceof Error ? err.message : String(err)));
    }
  };

  const lockNow = saved ? saved.needsRelock : info.needs_relock;

  return (
    <div className="v5-overlay" onMouseDown={onClose}>
      <div className="v5-modal v5-voice-editor" onMouseDown={(event) => event.stopPropagation()}>
        <div className="v5-modal-head">
          <div>
            <b>改声音 · {character.label}</b>
            <span className="v5-muted">
              {character.id} · 当前 {info.voice_id} · seed {info.seed}
            </span>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </div>
        <div className="v5-modal-body v5-voice-body">
          <div className="v5-voice-form">
            <Field label="声音预设 voice_id" hint={`声音库 ${presets.length} 个预设；改 voice_id 会换音色`}>
              <select
                className="select select-wide"
                value={voiceId}
                onChange={(event) => setVoiceId(event.target.value)}
              >
                {presets.map((preset) => (
                  <option key={preset.voice_id} value={preset.voice_id}>
                    {preset.label || preset.voice_id}
                    {preset.seed ? ` · seed ${preset.seed}` : ""}
                  </option>
                ))}
                {!currentPreset && <option value={voiceId}>{voiceId}（当前）</option>}
              </select>
            </Field>
            <div className="v5-form-row">
              <Field label="seed" hint="固定种子 → 音色可复现">
                <input
                  className="input"
                  type="number"
                  value={seed}
                  onChange={(event) => setSeed(event.target.value)}
                />
              </Field>
              <Field label="cfg_scale" hint="1-10，越大越贴指令">
                <input
                  className="input"
                  type="number"
                  step="0.1"
                  value={cfgScale}
                  onChange={(event) => setCfgScale(event.target.value)}
                />
              </Field>
            </div>
            <Field
              label="音色指令 instruction"
              hint="Breeze Voice Design 中文指令：年龄/性别/音色/气质/语速"
            >
              <textarea
                className="textarea v5-textarea"
                rows={4}
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="例如：28岁女性，清亮温和，语速偏慢，带一点书卷气"
              />
            </Field>
            {error && <Notice tone="danger">{error}</Notice>}
            {saved && (
              <Notice tone={saved.needsRelock ? "warn" : "info"}>
                {saved.needsRelock
                  ? "参数已保存，但声音锁还是旧参数：请点「重建声音锁」后重渲，成片音色才会更新。"
                  : `参数已保存，声音锁已匹配（key ${saved.key}）。`}
              </Notice>
            )}
            <div className="v5-form-actions">
              <button type="button" className="btn btn-ghost" onClick={onClose}>
                关闭
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void handlePreview()}
                title="试听当前已保存的声音（改参数需先保存）"
              >
                <IconVolume size={14} /> 试听
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void handleSave()}
                disabled={saving || !dirty}
              >
                {saving ? <Spinner size={13} /> : null} 保存声音
              </button>
              <button
                type="button"
                className={"btn " + (lockNow ? "btn-outline" : "btn-ghost")}
                onClick={() => void handleLock()}
                disabled={locking}
                title="用当前参数生成角色参考音频（Breeze TTS，首次较慢）"
              >
                {locking ? <Spinner size={13} /> : <IconLock size={14} />} 重建声音锁
              </button>
            </div>
            <div className="v5-muted v5-voice-foot">
              声音锁状态：{info.lock?.exists ? `已锁（key ${info.key}）` : "未锁"}
              {info.needs_relock ? " · 待重建" : ""} · 保存位置 contract/v5/custom/characters/
              {character.id}.json
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
