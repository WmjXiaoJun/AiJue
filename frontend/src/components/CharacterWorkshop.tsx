import { useState, type CSSProperties } from "react";
import { mediaUrl } from "../lib/api";
import type { Character } from "../types";
import {
  CHARACTER_STYLE_LABEL,
  characterStyles,
  fmtDateTime,
  firstChar,
  label,
  CHARACTER_STATUS_LABEL,
  styleFamily,
} from "../lib/format";
import { EmptyState, SectionHead, Spinner, StatusBadge } from "./ui";
import { IconArrowRight, IconRefresh, IconSpark, IconUsers } from "./icons";

interface Props {
  characters: Character[] | null;
  supported: boolean;
  error: string | null;
  onRefresh: () => void;
  onNew: () => void;
  onOpen: (character: Character) => void;
}

export function CharacterWorkshop({ characters, supported, error, onRefresh, onNew, onOpen }: Props) {
  const [refreshing, setRefreshing] = useState(false);
  const list = characters || [];
  const ready = list.filter((item) => item.status === "ready" || item.status === "done").length;

  const handleRefresh = () => {
    setRefreshing(true);
    window.setTimeout(() => setRefreshing(false), 900);
    onRefresh();
  };

  return (
    <section className="char-workshop">
      <SectionHead
        title="角色工坊"
        en="CHARACTER STUDIO"
        extra={
          <div className="char-workshop-actions">
            {supported && (
              <span className="char-workshop-count">
                原创角色 <b>{list.length}</b> · 已就绪 <b>{ready}</b>
              </span>
            )}
            <button type="button" className="btn btn-ghost btn-sm" onClick={handleRefresh} title="刷新角色列表">
              {refreshing ? <Spinner size={13} /> : <IconRefresh size={14} />} 刷新
            </button>
            <button type="button" className="btn btn-primary" onClick={onNew}>
              <IconSpark size={15} /> 新建角色
            </button>
          </div>
        }
      />
      <p className="char-workshop-sub">
        平台自主生成的原创动漫人物 · 支持 3D / 2D 多风格（Blender 程序化建模 + 卡通渲染），生成后可直接用于分镜与成片。
      </p>

      {!supported ? (
        <div className="char-unsupported">
          <EmptyState
            icon={<IconUsers size={26} />}
            title="角色工坊接口尚未就绪"
            hint={
              "GET /api/projects/{pid}/characters 暂未返回数据" +
              (error ? "（" + error + "）" : "") +
              "。后端角色 API 上线后，点击「刷新」即可加载，无需刷新页面。"
            }
            action={
              <div className="char-unsupported-actions">
                <button type="button" className="btn btn-primary" onClick={onNew}>
                  <IconSpark size={15} /> 新建角色
                </button>
                <button type="button" className="btn btn-ghost" onClick={handleRefresh}>
                  <IconRefresh size={14} /> 重试
                </button>
              </div>
            }
          />
        </div>
      ) : characters === null ? (
        <div className="char-grid">
          {[0, 1, 2].map((index) => (
            <div key={index} className="char-card char-card-skeleton" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          icon={<IconUsers size={26} />}
          title="还没有原创角色"
          hint="创建角色时会自动触发 Blender 生成：半身像、三视图、四表情与转面视频。"
          action={
            <button type="button" className="btn btn-primary" onClick={onNew}>
              <IconSpark size={15} /> 新建角色
            </button>
          }
        />
      ) : (
        <div className="char-grid">
          {list.map((character) => (
            <CharacterCard key={character.id} character={character} onOpen={() => onOpen(character)} />
          ))}
        </div>
      )}
    </section>
  );
}

function CharacterCard({ character, onOpen }: { character: Character; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const styles = characterStyles(character.style);
  const primary = styles[0] || "3d_toon";
  const family = styleFamily(primary);
  const accent = character.accent_color || "#F5C518";
  const portrait = character.portrait_url;
  const generating = character.status === "generating" || character.status === "queued";

  return (
    <button
      type="button"
      className="char-card"
      onClick={onOpen}
      style={{ "--accent": accent } as CSSProperties}
      title={"查看「" + character.name + "」角色设定"}
    >
      <div
        className="char-portrait"
        style={{ background: "linear-gradient(150deg, " + accent + "33, #0a0d13 62%), radial-gradient(circle at 78% 14%, " + accent + "33, transparent 52%)" }}
      >
        {portrait && !failed ? (
          <img src={mediaUrl(portrait)} alt={character.name} loading="lazy" onError={() => setFailed(true)} />
        ) : (
          <span className="char-initial">{firstChar(character.name)}</span>
        )}
        <span className={"char-family family-" + family.toLowerCase()}>{family}</span>
        <span className="char-status">
          <StatusBadge status={character.status} kind="character" labelText={label(CHARACTER_STATUS_LABEL, character.status)} />
        </span>
        {generating && (
          <span className="char-generating">
            <Spinner size={13} /> 生成中
          </span>
        )}
      </div>
      <div className="char-card-body">
        <div className="char-name-row">
          <span className="char-name">{character.name}</span>
          <span className="char-title">{character.title}</span>
        </div>
        <p className="char-persona">{character.persona || "—"}</p>
        <div className="char-style-row">
          {styles.slice(0, 3).map((style) => (
            <span key={style} className="char-style-chip">
              {CHARACTER_STYLE_LABEL[style] || style}
            </span>
          ))}
          {styles.length > 3 && <span className="char-style-more">+{styles.length - 3}</span>}
        </div>
        <div className="char-meta-row">
          <span className="char-voice">{character.voice || "未分配声线"}</span>
          <span className="char-created">{character.created_at ? fmtDateTime(character.created_at).slice(0, 10) : ""}</span>
        </div>
      </div>
      <div className="char-card-foot">
        <span>查看三视图 / 表情 / 转面</span>
        <IconArrowRight size={14} />
      </div>
    </button>
  );
}
