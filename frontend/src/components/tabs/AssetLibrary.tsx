import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Asset, Character, Job } from "../../types";
import { ASSET_ROLE_LABEL, ASSET_TYPE_LABEL, characterStyles } from "../../lib/format";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { Avatar, EmptyState, SectionHead, Spinner } from "../ui";
import { CharacterWorkshop } from "../CharacterWorkshop";
import { CharacterDrawer } from "../CharacterDrawer";
import { CharacterAvatar } from "../CharacterAvatar";
import { NewCharacterModal } from "../NewCharacterModal";
import { IconBox, IconImage, IconLock, IconPalette, IconUnlock, IconUser, IconUsers } from "../icons";

interface Props extends TabBaseProps {
  onAssetUpdated: (asset: Asset) => void;
  characters: Character[] | null;
  charactersSupported: boolean;
  charactersError: string | null;
  onRefreshCharacters: () => Promise<Character[] | null>;
  onRefreshAssets: () => Promise<void>;
}

const TYPE_FILTERS: { key: string; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { key: "all", label: "全部", icon: IconUsers },
  { key: "character", label: "角色", icon: IconUser },
  { key: "scene", label: "场景", icon: IconImage },
  { key: "prop", label: "道具", icon: IconBox },
  { key: "style", label: "风格", icon: IconPalette },
];

interface CharacterJobState {
  characterId: string;
  job: Job;
}

export function AssetLibrary({
  assets,
  shots,
  project,
  characters,
  charactersSupported,
  charactersError,
  onAssetUpdated,
  onRefreshCharacters,
  onRefreshAssets,
}: Props) {
  const notify = useToast();
  const [filter, setFilter] = useState("all");
  const [lockingId, setLockingId] = useState<string | null>(null);
  const [showNewCharacter, setShowNewCharacter] = useState(false);
  const [activeCharacterId, setActiveCharacterId] = useState<string | null>(null);
  const [charJob, setCharJob] = useState<CharacterJobState | null>(null);

  const activeCharacter = (characters || []).find((item) => item.id === activeCharacterId) || null;
  const activeJob = charJob && activeCharacter && charJob.characterId === activeCharacter.id ? charJob.job : null;

  // 角色生成任务轮询：完成/失败后刷新卡片与素材
  useEffect(() => {
    if (!charJob) return;
    let alive = true;
    const tick = async () => {
      try {
        const job = await api.job(charJob.job.id);
        if (!alive) return;
        setCharJob((prev) => (prev && prev.job.id === job.id ? { ...prev, job } : prev));
        if (job.status === "done") {
          setCharJob(null);
          notify("角色素材生成完成", "success");
          await onRefreshCharacters();
          await onRefreshAssets();
        } else if (job.status === "failed" || job.status === "canceled") {
          setCharJob(null);
          notify("角色生成" + (job.status === "failed" ? "失败" : "已取消") + "：" + (job.error || job.message || "未知原因"), "error");
          await onRefreshCharacters();
        } else {
          void onRefreshCharacters();
        }
      } catch {
        /* 轮询暂时失败，保留任务状态等下一轮重试 */
      }
    };
    const timer = window.setInterval(() => {
      void tick();
    }, 1600);
    void tick();
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [charJob?.job.id, notify, onRefreshCharacters, onRefreshAssets]);

  const handleRegenerate = useCallback(
    async (character: Character) => {
      try {
        const job = await api.generateCharacter(character.id, characterStyles(character.style));
        setCharJob({ characterId: character.id, job });
        notify("已提交「" + character.name + "」重新生成任务", "success");
        void onRefreshCharacters();
      } catch (err) {
        notify(errorText(err), "error");
      }
    },
    [notify, onRefreshCharacters]
  );

  const handleCharacterSubmitted = useCallback(
    async (character: Character, job: Job | null) => {
      setShowNewCharacter(false);
      setActiveCharacterId(character.id);
      await onRefreshCharacters();
      await onRefreshAssets();
      if (job) setCharJob({ characterId: character.id, job });
    },
    [onRefreshCharacters, onRefreshAssets]
  );

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: assets.length };
    assets.forEach((asset) => {
      map[asset.type] = (map[asset.type] || 0) + 1;
    });
    return map;
  }, [assets]);

  const filtered = filter === "all" ? assets : assets.filter((asset) => asset.type === filter);
  const characterAssets = assets.filter((asset) => asset.type === "character");
  const characterByAssetId = useMemo(() => {
    const map = new Map<string, Character>();
    (characters || []).forEach((character) => {
      if (character.asset_id) map.set(character.asset_id, character);
      map.set(character.id, character);
    });
    return map;
  }, [characters]);

  const shotCountBySpeaker = useMemo(() => {
    const map = new Map<string, number>();
    shots.forEach((shot) => {
      if (shot.speaker) map.set(shot.speaker, (map.get(shot.speaker) || 0) + 1);
    });
    return map;
  }, [shots]);

  const toggleLock = async (asset: Asset) => {
    setLockingId(asset.id);
    try {
      const updated = await api.patchAsset(asset.id, { locked: !asset.locked });
      onAssetUpdated(updated);
      notify(updated.locked ? "已锁定「" + updated.name + "」，生成时保持形象一致" : "已解锁「" + updated.name + "」", "success");
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setLockingId(null);
    }
  };

  return (
    <div className="assets-view">
      <CharacterWorkshop
        characters={characters}
        supported={charactersSupported}
        error={charactersError}
        onRefresh={() => void onRefreshCharacters()}
        onNew={() => setShowNewCharacter(true)}
        onOpen={(character) => setActiveCharacterId(character.id)}
      />

      <div className="asset-section-divider" />

      <SectionHead
        title="固定资产"
        en="PRODUCTION ASSETS"
        extra={
          <div className="filter-tabs">
            {TYPE_FILTERS.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.key}
                  type="button"
                  className={"filter-tab" + (filter === item.key ? " active" : "")}
                  onClick={() => setFilter(item.key)}
                >
                  <Icon size={14} />
                  {item.label}
                  <span className="filter-count">{counts[item.key] || 0}</span>
                </button>
              );
            })}
          </div>
        }
      />

      <div className="asset-overview">
        <div className="asset-overview-item">
          <span>角色</span>
          <b>{characterAssets.length}</b>
          <em>{characterAssets.filter((asset) => asset.locked).length} 个已锁定</em>
        </div>
        <div className="asset-overview-item">
          <span>场景</span>
          <b>{assets.filter((asset) => asset.type === "scene").length}</b>
          <em>固定虚拟棚</em>
        </div>
        <div className="asset-overview-item">
          <span>声线</span>
          <b>{characterAssets.filter((asset) => asset.voice).length}</b>
          <em>edge-tts 中文</em>
        </div>
        <div className="asset-overview-item">
          <span>锁定策略</span>
          <b>{characterAssets.filter((asset) => asset.locked).length}/{characterAssets.length}</b>
          <em>锁定后保持人设与形象</em>
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={<IconUsers size={26} />} title="该分类下暂无资产" hint="切换分类查看主持、嘉宾与场景。" />
      ) : (
        <div className="asset-grid">
          {filtered.map((asset) => (
            <AssetCard
              key={asset.id}
              asset={asset}
              character={characterByAssetId.get(asset.id)}
              shotCount={shotCountBySpeaker.get(asset.id) || 0}
              busy={lockingId === asset.id}
              onToggleLock={() => void toggleLock(asset)}
            />
          ))}
        </div>
      )}

      <CharacterDrawer
        character={activeCharacter}
        job={activeJob}
        onClose={() => setActiveCharacterId(null)}
        onRegenerate={(character) => void handleRegenerate(character)}
      />

      <NewCharacterModal
        open={showNewCharacter}
        projectId={project.id}
        onClose={() => setShowNewCharacter(false)}
        onSubmitted={(character, job) => void handleCharacterSubmitted(character, job)}
      />
    </div>
  );
}

function AssetCard({
  asset,
  character,
  shotCount,
  busy,
  onToggleLock,
}: {
  asset: Asset;
  character?: Character;
  shotCount: number;
  busy: boolean;
  onToggleLock: () => void;
}) {
  const accent = asset.accent_color || "#F5C518";
  const isCharacter = asset.type === "character";
  return (
    <article className={"asset-card" + (asset.locked ? " locked" : "")} style={{ ["--accent" as string]: accent }}>
      <div className="asset-accent" />
      <div className="asset-head">
        {isCharacter ? (
          <CharacterAvatar character={character} name={asset.name} color={accent} size={52} square={false} />
        ) : (
          <Avatar name={asset.name} color={accent} size={52} square />
        )}
        <div className="asset-title">
          <div className="asset-name">
            {asset.name}
            {asset.locked && <IconLock size={13} className="asset-lock-icon" />}
          </div>
          <div className="asset-sub">{asset.title || ASSET_TYPE_LABEL[asset.type] || asset.type}</div>
        </div>
        <span className={"asset-role" + (asset.role === "host" ? " is-host" : "")}>
          {ASSET_ROLE_LABEL[asset.role] || asset.role}
        </span>
      </div>

      <div className="asset-tags">
        <span className="tag">{ASSET_TYPE_LABEL[asset.type] || asset.type}</span>
        <span className="tag">#{asset.id}</span>
        {isCharacter && shotCount > 0 && <span className="tag tag-hl">{shotCount} 镜出镜</span>}
        {asset.locked && <span className="tag tag-lock">已锁定</span>}
      </div>

      {isCharacter ? (
        <div className="asset-rows">
          <div className="asset-row">
            <span>声音</span>
            <b className="asset-voice">{asset.voice || "未分配"}</b>
          </div>
          <div className="asset-row asset-row-persona">
            <span>人设</span>
            <p>{asset.persona || "—"}</p>
          </div>
        </div>
      ) : (
        <div className="asset-rows">
          <div className="asset-row asset-row-persona">
            <span>{asset.type === "scene" ? "场景设定" : "描述"}</span>
            <p>{asset.persona || "—"}</p>
          </div>
        </div>
      )}

      <div className="asset-foot">
        <span className="asset-foot-hint">
          {asset.locked ? "生成时锁定形象与声线" : "未锁定：可在生成时调整"}
        </span>
        <button type="button" className={"btn btn-ghost btn-sm" + (asset.locked ? " is-locked" : "")} onClick={onToggleLock} disabled={busy}>
          {busy ? <Spinner size={13} /> : asset.locked ? <IconUnlock size={14} /> : <IconLock size={14} />}
          {asset.locked ? "解锁" : "锁定"}
        </button>
      </div>
    </article>
  );
}
