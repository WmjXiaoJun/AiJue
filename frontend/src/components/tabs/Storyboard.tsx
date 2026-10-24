import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { api, errorText, mediaUrl } from "../../lib/api";
import type { Character, Job, NewShot, Shot } from "../../types";
import {
  CHARACTER_STYLE_LABEL,
  SHOT_STATUS_LABEL,
  SHOT_TYPE_LABEL,
  SHOT_TYPE_SHORT,
  TYPE_ACCENT,
  characterStyles,
  fmtShotDuration,
  segmentName,
  styleFamily,
} from "../../lib/format";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { EmptyState, ProgressBar, SectionHead, Spinner, StatusBadge } from "../ui";
import { CharacterAvatar } from "../CharacterAvatar";
import { ShotFormModal } from "../ShotFormModal";
import { IconBoard, IconCheck, IconEdit, IconGrid, IconPlus, IconSpark, IconTable, IconTrash } from "../icons";

interface Props extends TabBaseProps {
  jobByShot: Map<string, Job>;
  characters: Character[];
}

type ViewMode = "table" | "grid";

const STATUS_FILTERS = [
  { key: "all", label: "全部" },
  { key: "pending", label: "待生成" },
  { key: "active", label: "生成中" },
  { key: "ready", label: "已就绪" },
  { key: "failed", label: "失败" },
];

export function Storyboard({ episode, assets, shots, jobByShot, characters, onChanged, onOpenShot }: Props) {
  const notify = useToast();
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState({ done: 0, total: 0 });
  const [showCreate, setShowCreate] = useState(false);
  const [editingShot, setEditingShot] = useState<Shot | null>(null);
  const [formBusy, setFormBusy] = useState(false);

  const assetMap = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);
  const types = useMemo(() => Array.from(new Set(shots.map((shot) => shot.type))), [shots]);

  // 角色工坊角色：按 asset_id / 角色名 / speaker 匹配（后端可能同步生成资产）
  const characterMap = useMemo(() => {
    const byAsset = new Map<string, Character>();
    const byName = new Map<string, Character>();
    characters.forEach((character) => {
      if (character.asset_id) byAsset.set(character.asset_id, character);
      byAsset.set(character.id, character);
      byName.set(character.name, character);
    });
    return { byAsset, byName };
  }, [characters]);
  const characterForShot = (shot: Shot): Character | undefined => {
    if (shot.speaker && characterMap.byAsset.has(shot.speaker)) return characterMap.byAsset.get(shot.speaker);
    if (shot.speaker_name && characterMap.byName.has(shot.speaker_name)) return characterMap.byName.get(shot.speaker_name);
    return undefined;
  };

  useEffect(() => {
    // 镜头集合变化后，剔除已不存在的选择
    setSelected((prev) => {
      const next = new Set<string>();
      prev.forEach((id) => {
        if (shots.some((shot) => shot.id === id)) next.add(id);
      });
      return next;
    });
  }, [shots]);

  const filtered = useMemo(
    () =>
      shots.filter((shot) => {
        if (statusFilter === "active" && shot.status !== "queued" && shot.status !== "generating") return false;
        if (statusFilter !== "all" && statusFilter !== "active" && shot.status !== statusFilter) return false;
        if (typeFilter !== "all" && shot.type !== typeFilter) return false;
        return true;
      }),
    [shots, statusFilter, typeFilter]
  );

  const allChecked = filtered.length > 0 && filtered.every((shot) => selected.has(shot.id));
  const characterStyleLabel = (character: Character): string => {
    const styles = characterStyles(character.style);
    if (!styles.length) return "";
    return styleFamily(styles[0]) + " · " + (CHARACTER_STYLE_LABEL[styles[0]] || styles[0]);
  };

  const toggleOne = (shot: Shot) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(shot.id)) next.delete(shot.id);
      else next.add(shot.id);
      return next;
    });
  };

  const toggleAll = () => {
    if (allChecked) {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((shot) => next.delete(shot.id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((shot) => next.add(shot.id));
        return next;
      });
    }
  };

  const batchGenerate = async () => {
    const targets = shots.filter(
      (shot) => selected.has(shot.id) && shot.status !== "generating" && shot.status !== "queued"
    );
    if (targets.length === 0) {
      notify("所选镜头都在排队或生成中，请稍后再试", "info");
      return;
    }
    setBatchBusy(true);
    setBatchProgress({ done: 0, total: targets.length });
    let ok = 0;
    let fail = 0;
    for (let index = 0; index < targets.length; index += 1) {
      try {
        await api.generateShot(targets[index].id);
        ok += 1;
      } catch {
        fail += 1;
      }
      setBatchProgress({ done: index + 1, total: targets.length });
    }
    setBatchBusy(false);
    setSelected(new Set());
    notify(
      fail === 0 ? "已提交 " + ok + " 个镜头生成任务" : "已提交 " + ok + " 个，失败 " + fail + " 个",
      fail === 0 ? "success" : "error"
    );
    onChanged();
  };

  const selectedCount = selected.size;

  const submitCreate = async (body: NewShot) => {
    setFormBusy(true);
    try {
      await api.createShot(episode.id, body);
      notify("已添加镜头", "success");
      setShowCreate(false);
      onChanged();
    } finally {
      setFormBusy(false);
    }
  };

  const submitEdit = async (body: NewShot) => {
    if (!editingShot) return;
    setFormBusy(true);
    try {
      await api.patchShot(editingShot.id, {
        line: body.line,
        duration: body.duration,
        visual: body.visual,
        punchline: body.punchline,
      });
      notify("镜头已更新", "success");
      setEditingShot(null);
      onChanged();
    } finally {
      setFormBusy(false);
    }
  };

  const handleDelete = async (shot: Shot) => {
    if (!window.confirm("删除镜头 #" + String(shot.no).padStart(3, "0") + "？删除后序号会自动重排。")) return;
    try {
      await api.deleteShot(shot.id);
      notify("已删除镜头", "success");
      onChanged();
    } catch (err) {
      notify("删除失败：" + errorText(err), "error");
    }
  };

  return (
    <div className="storyboard-view">
      <div className="sb-toolbar">
        <div className="sb-toolbar-left">
          <SectionHead title="分镜台" en="STORYBOARD" />
          <span className="sb-count">
            共 <b>{filtered.length}</b> / {shots.length} 镜
          </span>
        </div>
        <div className="sb-toolbar-right">
          <div className="filter-tabs">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={"filter-tab" + (statusFilter === item.key ? " active" : "")}
                onClick={() => setStatusFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <select className="select" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">全部镜头类型</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {SHOT_TYPE_LABEL[type] || type}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowCreate(true)} title="手动添加镜头">
            <IconPlus size={14} /> 添加镜头
          </button>
          <div className="view-toggle">
            <button
              type="button"
              className={viewMode === "table" ? "active" : ""}
              onClick={() => setViewMode("table")}
              title="表格视图"
            >
              <IconTable size={15} />
            </button>
            <button
              type="button"
              className={viewMode === "grid" ? "active" : ""}
              onClick={() => setViewMode("grid")}
              title="宫格视图"
            >
              <IconGrid size={15} />
            </button>
          </div>
        </div>
      </div>

      <div className="sb-actionbar">
        <label className="check">
          <input type="checkbox" checked={allChecked} onChange={toggleAll} />
          <span className="check-box">{allChecked && <IconCheck size={12} />}</span>
          <span>全选当前筛选</span>
        </label>
        <span className="sb-selected">已选 <b>{selectedCount}</b> 镜</span>
        <div className="sb-actionbar-spacer" />
        <button
          type="button"
          className="btn btn-primary"
          disabled={selectedCount === 0 || batchBusy}
          onClick={() => void batchGenerate()}
        >
          {batchBusy ? <Spinner size={14} /> : <IconSpark size={16} />}
          {batchBusy ? "批量提交中 " + batchProgress.done + "/" + batchProgress.total : "批量生成" + (selectedCount ? "（" + selectedCount + "）" : "")}
        </button>
      </div>

      {shots.length === 0 ? (
        <EmptyState
          icon={<IconBoard size={26} />}
          title="分镜台为空"
          hint="到编剧间点击「从台本生成分镜」，或手动添加一个镜头。"
          action={
            <button type="button" className="btn btn-outline" onClick={() => setShowCreate(true)}>
              <IconPlus size={15} /> 添加镜头
            </button>
          }
        />
      ) : viewMode === "table" ? (
        <div className="sb-table-wrap panel">
          <table className="sb-table">
            <thead>
              <tr>
                <th className="col-check" />
                <th className="col-no">镜号</th>
                <th className="col-type">类型</th>
                <th className="col-dur">时长</th>
                <th className="col-speaker">说话人</th>
                <th className="col-line">台词 / 画面</th>
                <th className="col-status">状态</th>
                <th className="col-actions" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((shot) => {
                const asset = shot.speaker ? assetMap.get(shot.speaker) : undefined;
                const character = characterForShot(shot);
                const accent = character?.accent_color || asset?.accent_color || TYPE_ACCENT[shot.type] || "#8A94A6";
                const job = jobByShot.get(shot.id);
                const checked = selected.has(shot.id);
                return (
                  <tr key={shot.id} className={"sb-row" + (checked ? " is-checked" : "")} onClick={() => onOpenShot(shot)}>
                    <td className="col-check" onClick={(event) => event.stopPropagation()}>
                      <label className="check check-only">
                        <input type="checkbox" checked={checked} onChange={() => toggleOne(shot)} />
                        <span className="check-box">{checked && <IconCheck size={12} />}</span>
                      </label>
                    </td>
                    <td className="col-no">
                      <span className="sb-no">#{String(shot.no).padStart(3, "0")}</span>
                      <span className="sb-seg">{segmentName(shot.segment_id || shot.segment)}</span>
                    </td>
                    <td className="col-type">
                      <span className="type-chip" style={{ "--chip": accent } as CSSProperties}>
                        {SHOT_TYPE_SHORT[shot.type] || SHOT_TYPE_LABEL[shot.type] || shot.type}
                      </span>
                    </td>
                    <td className="col-dur">{fmtShotDuration(shot.duration)}</td>
                    <td className="col-speaker">
                      {shot.speaker_name ? (
                        <span className="speaker-mini">
                          <CharacterAvatar character={character} name={shot.speaker_name} color={accent} size={22} />
                          {shot.speaker_name}
                        </span>
                      ) : (
                        <span className="muted">画外</span>
                      )}
                    </td>
                    <td className="col-line">
                      <span className="line-ellipsis">{shot.line || shot.visual || shot.subtitle || "—"}</span>
                    </td>
                    <td className="col-status">
                      <StatusBadge status={shot.status} />
                      {job && (job.status === "running" || job.status === "queued") && (
                        <ProgressBar value={job.progress} size="sm" label={Math.round(job.progress || 0) + "%"} />
                      )}
                    </td>
                    <td className="col-actions" onClick={(event) => event.stopPropagation()}>
                      <span className="row-actions">
                        <button
                          type="button"
                          className="row-action"
                          title="编辑台词 / 时长"
                          onClick={() => setEditingShot(shot)}
                        >
                          <IconEdit size={13} />
                        </button>
                        <button
                          type="button"
                          className="row-action row-action-danger"
                          title="删除镜头"
                          onClick={() => void handleDelete(shot)}
                        >
                          <IconTrash size={13} />
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length === 0 && <div className="sb-table-empty">当前筛选没有镜头</div>}
        </div>
      ) : (
        <div className="sb-grid">
          {filtered.map((shot) => {
            const asset = shot.speaker ? assetMap.get(shot.speaker) : undefined;
            const character = characterForShot(shot);
            const accent = character?.accent_color || asset?.accent_color || TYPE_ACCENT[shot.type] || "#8A94A6";
            const checked = selected.has(shot.id);
            const job = jobByShot.get(shot.id);
            return (
              <article
                key={shot.id}
                className={"shot-card" + (checked ? " is-checked" : "")}
                style={{ "--accent": accent } as CSSProperties}
                onClick={() => onOpenShot(shot)}
              >
                <div className="shot-thumb">
                  <div className="shot-thumb-bg" />
                  {shot.video_url && (
                    <video
                      className="shot-thumb-video"
                      src={mediaUrl(shot.video_url)}
                      muted
                      loop
                      playsInline
                      preload="none"
                      onMouseEnter={(event) => {
                        void event.currentTarget.play().catch(() => undefined);
                      }}
                      onMouseLeave={(event) => {
                        const video = event.currentTarget;
                        video.pause();
                        video.currentTime = 0;
                      }}
                    />
                  )}
                  <span className="shot-thumb-type">{SHOT_TYPE_LABEL[shot.type] || shot.type}</span>
                  <span className="shot-thumb-no">#{String(shot.no).padStart(3, "0")}</span>
                  <label className="check check-only shot-check" onClick={(event) => event.stopPropagation()}>
                    <input type="checkbox" checked={checked} onChange={() => toggleOne(shot)} />
                    <span className="check-box">{checked && <IconCheck size={12} />}</span>
                  </label>
                  {job && (job.status === "running" || job.status === "queued") && (
                    <div className="shot-thumb-progress">
                      <ProgressBar value={job.progress} size="sm" label={Math.round(job.progress || 0) + "%"} />
                    </div>
                  )}
                  {shot.status === "ready" && <span className="shot-thumb-ready">已就绪</span>}
                </div>
                <div className="shot-body">
                  <div className="shot-body-top">
                    {shot.speaker_name ? (
                      <span className="speaker-mini">
                        <CharacterAvatar character={character} name={shot.speaker_name} color={accent} size={24} />
                        {shot.speaker_name}
                        {character && <span className="speaker-char-tag">{characterStyleLabel(character)}</span>}
                      </span>
                    ) : (
                      <span className="muted">画外 · {fmtShotDuration(shot.duration)}</span>
                    )}
                    <StatusBadge status={shot.status} />
                  </div>
                  <p className="shot-line">{shot.line || shot.visual || shot.subtitle || "—"}</p>
                  <div className="shot-meta">
                    <span>{segmentName(shot.segment_id || shot.segment)}</span>
                    <i />
                    <span>{fmtShotDuration(shot.duration)}</span>
                    <i />
                    <span>{SHOT_TYPE_SHORT[shot.type] || shot.type}</span>
                  </div>
                  <div className="shot-card-actions" onClick={(event) => event.stopPropagation()}>
                    <button type="button" className="row-action" title="编辑台词 / 时长" onClick={() => setEditingShot(shot)}>
                      <IconEdit size={12} /> 编辑
                    </button>
                    <button type="button" className="row-action row-action-danger" title="删除镜头" onClick={() => void handleDelete(shot)}>
                      <IconTrash size={12} /> 删除
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <ShotFormModal
        open={showCreate}
        mode="create"
        assets={assets}
        busy={formBusy}
        onClose={() => setShowCreate(false)}
        onSubmit={submitCreate}
      />
      <ShotFormModal
        open={!!editingShot}
        mode="edit"
        shot={editingShot}
        assets={assets}
        busy={formBusy}
        onClose={() => setEditingShot(null)}
        onSubmit={submitEdit}
      />
    </div>
  );
}
