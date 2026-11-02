// V18 · 音色库弹层（两页共用）：24 preset 列表 / 试听 / 应用 / 恢复默认音色。
// 后端契约：GET /api/v5/voices、GET/PUT /api/v5/voice/{character}、POST /api/v5/voice/{character}/preview。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { VoicePreset } from "../../lib/voice";
import { voiceApi } from "../../lib/voice";
import { errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconCheck, IconClose, IconPlay, IconRefresh, IconSearch, IconVolume } from "../icons";
import "../../styles/v18.css";

interface Props {
  open: boolean;
  character: string;
  characterLabel: string;
  presets: VoicePreset[];
  currentVoiceId?: string;
  /** 内建默认音色（用于「恢复默认」；来自角色合同，不含自定义覆盖时用） */
  defaultVoiceId?: string;
  onClose: () => void;
  onSaved: (voiceId: string, label: string) => void;
}

export function VoicePicker({
  open,
  character,
  characterLabel,
  presets,
  currentVoiceId,
  defaultVoiceId,
  onClose,
  onSaved,
}: Props) {
  const notify = useToast();
  const [list, setList] = useState<VoicePreset[]>(presets);
  const [loadingPresets, setLoadingPresets] = useState(false);
  const [voiceInfo, setVoiceInfo] = useState<{ voice_id: string; needs_relock: boolean; lockExists: boolean } | null>(null);
  const [infoError, setInfoError] = useState("");
  const [selected, setSelected] = useState(currentVoiceId || "");
  const [search, setSearch] = useState("");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [savedVoice, setSavedVoice] = useState<string | null>(null);
  const [error, setError] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    setPreviewing(null);
  }, []);

  // 打开时：重置选择、拉当前音色 + 音色库
  useEffect(() => {
    if (!open) {
      stopAudio();
      return;
    }
    setSelected(currentVoiceId || "");
    setSavedVoice(null);
    setError("");
    setInfoError("");
    let alive = true;
    void (async () => {
      try {
        const info = await voiceApi.voiceInfo(character);
        if (alive) setVoiceInfo({ voice_id: info.voice_id, needs_relock: info.needs_relock, lockExists: info.lockExists });
        if (alive && !currentVoiceId) setSelected(info.voice_id);
      } catch (err) {
        if (alive) setInfoError(errorText(err));
      }
    })();
    if (!presets.length) {
      setLoadingPresets(true);
      void (async () => {
        try {
          const loaded = await voiceApi.voices(true);
          if (alive) setList(loaded);
        } catch (err) {
          if (alive) setError("音色库读取失败：" + errorText(err));
        } finally {
          if (alive) setLoadingPresets(false);
        }
      })();
    }
    return () => {
      alive = false;
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, character, currentVoiceId, stopAudio]);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return list;
    return list.filter((preset) =>
      [preset.voice_id, preset.label, preset.description, ...(preset.tags || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(keyword),
    );
  }, [list, search]);

  const playPreview = useCallback(
    async (preset: VoicePreset) => {
      if (previewing === preset.voice_id) {
        stopAudio();
        return;
      }
      stopAudio();
      setPreviewing(preset.voice_id);
      setError("");
      try {
        let url = preset.preview_url ? mediaSrc(preset.preview_url) : "";
        try {
          const res = await voiceApi.preview(character, { voiceId: preset.voice_id });
          url = res.url;
        } catch (err) {
          if (preset.preview_url) url = mediaSrc(preset.preview_url);
          else throw err;
        }
        const audio = new Audio(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now());
        audioRef.current = audio;
        audio.onended = () => setPreviewing((prev) => (prev === preset.voice_id ? null : prev));
        audio.onerror = () => {
          setPreviewing(null);
          setError("试听音频加载失败：该音色可能还没有试听 wav（后端生成中）");
        };
        await audio.play();
      } catch (err) {
        setPreviewing(null);
        const message = errorText(err);
        if (message.includes("404") || message.includes("Not Found")) {
          setError("试听接口尚未就绪（V18 后端 API 实现中）；音色仍可「应用」保存，保存后由生成任务使用。");
        } else {
          setError("试听失败：" + message);
        }
      }
    },
    [character, previewing, stopAudio],
  );

  const apply = useCallback(
    async (preset: VoicePreset) => {
      setApplying(preset.voice_id);
      setError("");
      try {
        const res = (await voiceApi.setVoice(character, {
          voice_id: preset.voice_id,
          seed: preset.seed,
          cfg_scale: preset.cfg_scale,
        })) as { needs_relock?: boolean; hint?: string };
        setSelected(preset.voice_id);
        setSavedVoice(preset.voice_id);
        setVoiceInfo((prev) => (prev ? { ...prev, voice_id: preset.voice_id, needs_relock: Boolean(res?.needs_relock) } : prev));
        onSaved(preset.voice_id, preset.label || preset.voice_id);
        notify(`已为「${characterLabel}」应用音色：${preset.label || preset.voice_id}`, "success");
        if (res?.needs_relock) {
          notify("音色已保存；下次生成时会按新音色重新生成锁定 wav", "info");
        }
      } catch (err) {
        setError("保存音色失败：" + errorText(err));
      } finally {
        setApplying(null);
      }
    },
    [character, characterLabel, notify, onSaved],
  );

  const restoreDefault = useCallback(async () => {
    if (!defaultVoiceId) return;
    const preset =
      list.find((item) => item.voice_id === defaultVoiceId) ||
      ({ voice_id: defaultVoiceId, label: defaultVoiceId } as VoicePreset);
    await apply(preset);
    setSavedVoice(defaultVoiceId);
  }, [apply, defaultVoiceId, list]);

  if (!open) return null;

  return (
    <div className="v18-voice-mask" onMouseDown={onClose} role="presentation">
      <div className="v18-voice-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`${characterLabel} 换声音`}>
        <header className="v18-voice-head">
          <div className="v18-voice-head-main">
            <span className="v18-voice-kicker">VOICE LIBRARY · 音色库</span>
            <h3 className="v18-voice-title">
              {characterLabel}
              <small>{character}</small>
            </h3>
            <div className="v18-voice-current">
              {voiceInfo ? (
                <>
                  <span className="v18-tag is-gold">
                    <IconVolume size={10} /> {list.find((item) => item.voice_id === voiceInfo.voice_id)?.label || voiceInfo.voice_id}
                  </span>
                  <span className="v18-tag">{voiceInfo.needs_relock ? "待重新锁定" : "已锁定"}</span>
                  <span className="v18-crumb">{voiceInfo.lockExists ? "试听 wav 已缓存" : "尚无试听 wav"}</span>
                </>
              ) : infoError ? (
                <span className="v18-crumb">当前音色读取失败：{infoError}</span>
              ) : (
                <span className="v18-crumb">读取当前音色…</span>
              )}
            </div>
          </div>
          <button type="button" className="v18-voice-close" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </header>

        <div className="v18-voice-body">
          <div className="v18-voice-toolbar">
            <label className="st-search">
              <IconSearch size={14} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索音色 / 标签"
                aria-label="搜索音色"
              />
            </label>
            <span className="v18-crumb">
              {filtered.length} / {list.length} presets
            </span>
          </div>

          {error && (
            <div className="v18-error">
              <IconAlert size={15} />
              <div className="v18-error-main">
                <b>试听 / 保存提示</b>
                <p className="v18-error-text">{error}</p>
              </div>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => setError("")}>
                知道了
              </button>
            </div>
          )}

          {loadingPresets && !list.length ? (
            <div className="v18-empty">
              <Spinner size={16} />
              <span>正在读取音色库…</span>
            </div>
          ) : (
            <div className="v18-preset-list">
              {filtered.map((preset) => {
                const active = preset.voice_id === selected;
                return (
                  <div key={preset.voice_id} className={"v18-preset-row" + (active ? " is-active" : "")}>
                    <div className="v18-preset-main">
                      <div className="v18-preset-label">
                        <b>{preset.label || preset.voice_id}</b>
                        <code>{preset.voice_id}</code>
                        {preset.seed !== undefined && <span className="v18-tag">seed {preset.seed}</span>}
                      </div>
                      <div className="v18-preset-desc">
                        {preset.description || (preset.tags || []).join(" · ") || "breeze Voice Design preset"}
                      </div>
                    </div>
                    <div className="v18-preset-actions">
                      <button
                        type="button"
                        className="cm-btn cm-btn-sm"
                        onClick={() => void playPreview(preset)}
                        title="试听该音色"
                      >
                        {previewing === preset.voice_id ? <Spinner size={12} /> : <IconPlay size={12} />}
                        {previewing === preset.voice_id ? "播放中" : "试听"}
                      </button>
                      <button
                        type="button"
                        className={"cm-btn cm-btn-sm " + (active ? "" : "cm-btn-primary")}
                        onClick={() => void apply(preset)}
                        disabled={applying === preset.voice_id}
                        title="应用该音色（保存到角色 custom 覆盖）"
                      >
                        {applying === preset.voice_id ? <Spinner size={12} /> : active ? <IconCheck size={12} /> : null}
                        {applying === preset.voice_id ? "保存中…" : active ? "使用中" : "应用"}
                      </button>
                    </div>
                  </div>
                );
              })}
              {!filtered.length && <div className="v18-empty"><span>没有匹配的音色</span></div>}
            </div>
          )}
        </div>

        <footer className="v18-voice-foot">
          <span className="v18-voice-foot-note">
            {savedVoice
              ? `已保存：${savedVoice}（下次生成按新音色锁定；已缓存试听 wav 会按需重建）`
              : "应用后写入角色 custom 覆盖；试听使用 V18 preview 接口（404 时给出提示，不影响保存）。"}
          </span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onClose}>
            关闭
          </button>
          <button
            type="button"
            className="cm-btn cm-btn-sm cm-btn-ghost"
            onClick={() => void restoreDefault()}
            disabled={!defaultVoiceId || applying !== null}
            title={defaultVoiceId ? `恢复内建默认音色：${defaultVoiceId}` : "后端未提供内建默认音色"}
          >
            <IconRefresh size={12} /> 恢复默认
          </button>
        </footer>
      </div>
    </div>
  );
}
