// V5 角色库：立绘卡（AI 立绘 → 程序化预览 → 首字占位）、固定声音显示、试听、改声音
import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  V5CatalogResponse,
  V5CharacterSummary,
  V5VoiceInfo,
  V5VoicePreset,
} from "../../lib/v5";
import {
  characterArtUrl,
  characterPreviewUrl,
  defaultStyleForCharacter,
  v5api,
  voiceWavUrl,
} from "../../lib/v5";
import { Avatar, EmptyState, Spinner } from "../ui";
import { useToast } from "../Toast";
import { IconLock, IconUnlock, IconUser, IconVolume } from "../icons";
import { SmartImage, Tag } from "./shared";
import { VoiceEditor } from "./VoiceEditor";

interface Props {
  catalog: V5CatalogResponse | null;
  loading: boolean;
}

export function CharacterLibrary({ catalog, loading }: Props) {
  const notify = useToast();
  const [presets, setPresets] = useState<V5VoicePreset[]>([]);
  const [voiceMap, setVoiceMap] = useState<Record<string, V5VoiceInfo | null>>({});
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [playing, setPlaying] = useState<string>("");
  const [editorChar, setEditorChar] = useState<V5CharacterSummary | null>(null);

  const loadVoices = useCallback(async (characters: V5CharacterSummary[]) => {
    setVoicesLoading(true);
    try {
      const lib = await v5api.voices();
      setPresets(lib.presets || []);
    } catch {
      setPresets([]);
    }
    const entries = await Promise.all(
      characters.map(async (character) => {
        try {
          return [character.id, await v5api.characterVoice(character.id)] as const;
        } catch {
          return [character.id, null] as const;
        }
      })
    );
    setVoiceMap((previous) => {
      const next = { ...previous };
      entries.forEach(([id, info]) => {
        next[id] = info;
      });
      return next;
    });
    setVoicesLoading(false);
  }, []);

  useEffect(() => {
    if (catalog) void loadVoices(catalog.characters);
  }, [catalog, loadVoices]);

  const tryListen = useCallback(
    async (character: V5CharacterSummary) => {
      setPlaying(character.id);
      try {
        const res = await v5api.previewVoice(character.id);
        const audio = new Audio(voiceWavUrl(character.id));
        await audio.play();
        notify(
          `正在试听「${character.label}」${res.duration ? `（${res.duration.toFixed(1)}s）` : ""}`,
          "success"
        );
        window.setTimeout(() => setPlaying(""), 1500);
      } catch (err) {
        notify("试听失败：" + (err instanceof Error ? err.message : String(err)), "error");
        setPlaying("");
      }
    },
    [notify]
  );

  const handleSaved = useCallback(
    (characterId: string, info: V5VoiceInfo) => {
      setVoiceMap((previous) => ({ ...previous, [characterId]: info }));
      notify("声音已保存（写 contract/v5/custom/characters/" + characterId + ".json）", "success");
    },
    [notify]
  );

  const lockCharacter = useCallback(
    async (character: V5CharacterSummary) => {
      try {
        await v5api.lockVoice(character.id, true);
        notify("声音锁已重建（下次渲染生效）", "success");
        const info = await v5api.characterVoice(character.id);
        setVoiceMap((previous) => ({ ...previous, [character.id]: info }));
      } catch (err) {
        notify("重建声音锁失败：" + (err instanceof Error ? err.message : String(err)), "error");
      }
    },
    [notify]
  );

  const stats = useMemo(() => {
    const infos = Object.values(voiceMap).filter((info): info is V5VoiceInfo => Boolean(info));
    return {
      total: catalog?.characters.length || 0,
      locked: infos.filter((info) => info.lock?.exists).length,
      needsRelock: infos.filter((info) => info.needs_relock).length,
    };
  }, [catalog, voiceMap]);

  if (!catalog && loading) {
    return (
      <div className="v5-loading">
        <Spinner size={18} /> 正在读取角色库…
      </div>
    );
  }
  if (!catalog) return <EmptyState title="角色库不可用" hint="检查后端 /api/v5/catalog" />;

  return (
    <section className="v5-section">
      <div className="v5-toolbar">
        <div className="v5-toolbar-main">
          <IconUser size={16} />
          <b>角色库</b>
          <span className="v5-muted">
            {stats.total} 个角色 · 声音库 {presets.length} 个预设 · 声音锁 {stats.locked}/
            {stats.total}
            {stats.needsRelock > 0 ? ` · ${stats.needsRelock} 个待重建` : ""}
          </span>
        </div>
        <div className="v5-toolbar-right">
          {voicesLoading && <Spinner size={14} />}
        </div>
      </div>

      <div className="v5-char-grid">
        {catalog.characters.map((character) => {
          const info = voiceMap[character.id];
          const styleId = defaultStyleForCharacter(catalog, character);
          const locked = Boolean(info?.lock?.exists);
          return (
            <article key={character.id} className="v5-char-card">
              <div className="v5-char-portrait">
                <SmartImage
                  sources={[
                    characterArtUrl(character.id, "portrait_25d.png"),
                    characterArtUrl(character.id, "portrait.png"),
                    characterPreviewUrl(character.id, styleId),
                  ]}
                  alt={character.label}
                  className="v5-char-img"
                  fallback={
                    <div className="v5-char-avatar">
                      <Avatar name={character.label} size={72} square />
                    </div>
                  }
                />
                <span className={"v5-lock " + (locked ? "v5-lock-on" : "v5-lock-off")}>
                  {locked ? <IconLock size={12} /> : <IconUnlock size={12} />}
                  {locked ? "声音已锁" : "未锁声"}
                </span>
                {character.custom && <span className="v5-custom-flag v5-char-custom">custom</span>}
              </div>
              <div className="v5-char-body">
                <div className="v5-char-title">
                  <b>{character.label}</b>
                  <span className="v5-muted">{character.id}</span>
                </div>
                <div className="v5-muted v5-char-meta">
                  {character.gender || "?"} · {character.age ?? "?"} 岁 ·{" "}
                  {(character.role_pool || []).slice(0, 3).join(" / ")}
                </div>
                <div className="v5-char-voice">
                  <span className="v5-detail-label">固定声音</span>
                  <div className="v5-muted v5-ellipsis">
                    {info ? (
                      <>
                        {info.voice_id} · seed {info.seed}
                      </>
                    ) : (
                      "读取中/不可用"
                    )}
                  </div>
                </div>
                <div className="v5-chip-row">
                  {(character.tags || []).slice(0, 3).map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </div>
                <div className="v5-film-actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => void tryListen(character)}
                    disabled={playing === character.id || !info}
                  >
                    {playing === character.id ? <Spinner size={12} /> : <IconVolume size={13} />}
                    试听
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setEditorChar(character)}
                    disabled={!info}
                  >
                    改声音
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => void lockCharacter(character)}
                    title="用当前声音参数重建参考音频（Breeze TTS）"
                  >
                    重建锁
                  </button>
                </div>
              </div>
            </article>
          );
        })}
      </div>
      {catalog.characters.length === 0 && <EmptyState title="角色库为空" />}

      {editorChar && voiceMap[editorChar.id] && (
        <VoiceEditor
          character={editorChar}
          info={voiceMap[editorChar.id] as V5VoiceInfo}
          presets={presets}
          onClose={() => setEditorChar(null)}
          onSaved={handleSaved}
        />
      )}
    </section>
  );
}
