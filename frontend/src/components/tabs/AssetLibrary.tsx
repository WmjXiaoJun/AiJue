import { useMemo, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Asset } from "../../types";
import { ASSET_ROLE_LABEL, ASSET_TYPE_LABEL } from "../../lib/format";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { Avatar, EmptyState, SectionHead, Spinner } from "../ui";
import { IconBox, IconImage, IconLock, IconPalette, IconUnlock, IconUser, IconUsers } from "../icons";

interface Props extends TabBaseProps {
  onAssetUpdated: (asset: Asset) => void;
}

const TYPE_FILTERS: { key: string; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { key: "all", label: "全部", icon: IconUsers },
  { key: "character", label: "角色", icon: IconUser },
  { key: "scene", label: "场景", icon: IconImage },
  { key: "prop", label: "道具", icon: IconBox },
  { key: "style", label: "风格", icon: IconPalette },
];

export function AssetLibrary({ assets, shots, onAssetUpdated }: Props) {
  const notify = useToast();
  const [filter, setFilter] = useState("all");
  const [lockingId, setLockingId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: assets.length };
    assets.forEach((asset) => {
      map[asset.type] = (map[asset.type] || 0) + 1;
    });
    return map;
  }, [assets]);

  const filtered = filter === "all" ? assets : assets.filter((asset) => asset.type === filter);
  const characters = assets.filter((asset) => asset.type === "character");
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
      <SectionHead
        title="资产库"
        en="ASSETS"
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
          <b>{characters.length}</b>
          <em>{characters.filter((asset) => asset.locked).length} 个已锁定</em>
        </div>
        <div className="asset-overview-item">
          <span>场景</span>
          <b>{assets.filter((asset) => asset.type === "scene").length}</b>
          <em>固定虚拟棚</em>
        </div>
        <div className="asset-overview-item">
          <span>声线</span>
          <b>{characters.filter((asset) => asset.voice).length}</b>
          <em>edge-tts 中文</em>
        </div>
        <div className="asset-overview-item">
          <span>锁定策略</span>
          <b>{characters.filter((asset) => asset.locked).length}/{characters.length}</b>
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
              shotCount={shotCountBySpeaker.get(asset.id) || 0}
              busy={lockingId === asset.id}
              onToggleLock={() => void toggleLock(asset)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AssetCard({
  asset,
  shotCount,
  busy,
  onToggleLock,
}: {
  asset: Asset;
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
        <Avatar name={asset.name} color={accent} size={52} square={!isCharacter} />
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
