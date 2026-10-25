import { useEffect, useMemo, useState } from "react";
import { api, errorText, mediaUrl } from "../lib/api";
import type { Character, CharacterFiles, ConceptFiles, CharacterSpec, Job } from "../types";
import {
  ACCESSORY_LABEL,
  BROW_LABEL,
  CHARACTER_STATUS_LABEL,
  CHARACTER_STYLE_LABEL,
  FACIAL_HAIR_LABEL,
  GENDER_LABEL,
  HAIR_STYLE_LABEL,
  OUTFIT_LABEL,
  characterStyles,
  fmtDateTime,
  firstChar,
  isActiveJob,
  label,
  styleFamily,
} from "../lib/format";
import { Drawer, Meta, Notice, ProgressBar, StatusBadge } from "./ui";
import { ConceptStudio } from "./ConceptStudio";
import { IconRefresh, IconSpark } from "./icons";

interface Props {
  character: Character | null;
  job: Job | null;
  onClose: () => void;
  onRegenerate: (character: Character) => void;
  concept?: ConceptFiles | null;
  conceptSupported: boolean;
  conceptError: string | null;
  onRefreshConcepts: () => Promise<void> | void;
  /** 刷新角色列表（概念图设为立绘 / H3 回填后卡片同步） */
  onCharacterChanged: () => void;
}

export function CharacterDrawer({
  character,
  job,
  onClose,
  onRegenerate,
  concept,
  conceptSupported,
  conceptError,
  onRefreshConcepts,
  onCharacterChanged,
}: Props) {
  const [files, setFiles] = useState<CharacterFiles | null>(null);
  const [filesError, setFilesError] = useState<string | null>(null);

  const characterId = character?.id || "";
  const characterStatus = character?.status || "";

  useEffect(() => {
    setFiles(null);
    setFilesError(null);
    if (!characterId) return;
    let alive = true;
    api
      .characterFiles(characterId)
      .then((data) => {
        if (alive) setFiles(data);
      })
      .catch((err) => {
        if (alive) setFilesError(errorText(err));
      });
    return () => {
      alive = false;
    };
  }, [characterId, characterStatus]);

  const appearance = useMemo(() => (character?.spec as CharacterSpec | null | undefined)?.appearance || {}, [character]);
  const styles = characterStyles(character?.style);
  const primary = styles[0] || "";
  const accent = character?.accent_color || "#F5C518";
  const busy = !!job && isActiveJob(job);
  const progress = job ? Math.max(0, Math.min(100, job.progress || 0)) : 0;

  // 立绘优先用 Character.portrait_url（SDXL 概念立绘），再回落角色立绘文件
  const portrait = character?.portrait_url || files?.portrait;
  const outfit = appearance.outfit || {};

  return (
    <Drawer
      open={!!character}
      onClose={onClose}
      width={520}
      title={
        <span className="drawer-title-row">
          <span className="drawer-shot-no">角色工坊</span>
          <span>{character?.name || ""}</span>
          {character && <StatusBadge status={character.status} kind="character" labelText={label(CHARACTER_STATUS_LABEL, character.status)} />}
        </span>
      }
      subtitle={character ? character.title + " · " + (styles.map((style) => CHARACTER_STYLE_LABEL[style] || style).join(" / ") || "未设定风格") : undefined}
    >
      {character && (
        <div className="char-detail">
          <div className="char-hero">
            <div
              className="char-hero-portrait"
              style={{ borderColor: accent + "55", background: "linear-gradient(150deg, " + accent + "33, #0a0d13 70%)" }}
            >
              {portrait ? <img src={mediaUrl(portrait)} alt={character.name} /> : <span className="char-initial">{firstChar(character.name)}</span>}
            </div>
            <div className="char-hero-main">
              <div className="char-hero-name">{character.name}</div>
              <div className="char-hero-title">{character.title}</div>
              <div className="char-style-row">
                {styles.map((style) => (
                  <span key={style} className={"char-style-chip" + (style === primary ? " is-primary" : "")}>
                    {styleFamily(style)} · {CHARACTER_STYLE_LABEL[style] || style}
                  </span>
                ))}
                {concept?.portrait && (
                  <span className="char-style-chip concept-chip" title="当前角色卡优先使用 AI 概念图">
                    AI 概念图
                  </span>
                )}
              </div>
              <div className="char-hero-voice">声线 {character.voice || "未分配"}</div>
            </div>
          </div>

          <div className="char-regen-row">
            <button type="button" className="btn btn-primary btn-block" onClick={() => onRegenerate(character)} disabled={busy}>
              {busy ? <IconRefresh size={15} /> : <IconSpark size={15} />}
              {busy ? "正在重新生成…" : "重新生成 AI 概念图"}
            </button>
            {busy && (
              <div className="char-regen-progress">
                <ProgressBar value={progress} label={Math.round(progress) + "%"} />
                <span>{job?.message || "AI 正在生成角色概念图（立绘 / 三视图 / 表情）"}</span>
              </div>
            )}
          </div>

          {(filesError && !portrait && !concept?.portrait) && (
            <Notice tone="warn">角色素材接口暂不可用：{filesError}（生成完成后会自动出现）</Notice>
          )}

          <div className="detail-block">
            <ConceptStudio
              character={character}
              concept={concept}
              supported={conceptSupported}
              error={conceptError}
              onRefreshConcepts={onRefreshConcepts}
              onCharacterChanged={onCharacterChanged}
            />
          </div>

          <section className="detail-block">
            <h4>基本设定</h4>
            <div className="char-meta-grid">
              <Meta label="声音" value={character.voice || "—"} />
              <Meta label="主风格" value={primary ? CHARACTER_STYLE_LABEL[primary] || primary : "—"} />
              <Meta label="变体" value={styles.slice(1).map((style) => CHARACTER_STYLE_LABEL[style] || style).join(" / ") || "—"} />
              <Meta label="资产 ID" value={<code>{character.asset_id || "—"}</code>} />
              <Meta label="角色 ID" value={<code>{character.id}</code>} />
              <Meta label="创建时间" value={character.created_at ? fmtDateTime(character.created_at) : "—"} />
            </div>
            <p className="char-persona-detail">{character.persona || "—"}</p>
          </section>

          <section className="detail-block detail-block-last">
            <h4>外观参数</h4>
            <div className="detail-tags">
              <span className="tag">性别 {label(GENDER_LABEL, appearance.gender)}</span>
              <span className="tag">年龄 {appearance.age ?? "—"}</span>
              <span className="tag">Q 版度 {appearance.chibi ?? "—"}</span>
              <span className="tag">肤色 {appearance.skin || "—"}</span>
              <span className="tag">发型 {label(HAIR_STYLE_LABEL, appearance.hair_style)}</span>
              <span className="tag">发色 {appearance.hair_color || "—"}</span>
              <span className="tag">眼型 {label(BROW_LABEL, appearance.brow)}</span>
              <span className="tag">胡型 {label(FACIAL_HAIR_LABEL, appearance.facial_hair)}</span>
              <span className="tag">服装 {label(OUTFIT_LABEL, outfit.type)}</span>
              <span className="tag">服色 {outfit.main || "—"}</span>
              <span className="tag">
                配饰 {(appearance.accessories || []).map((item) => label(ACCESSORY_LABEL, item)).join(" / ") || "无"}
              </span>
              {appearance.marks && appearance.marks.length > 0 && <span className="tag">标记 {appearance.marks.join(" / ")}</span>}
            </div>
          </section>
        </div>
      )}
    </Drawer>
  );
}
