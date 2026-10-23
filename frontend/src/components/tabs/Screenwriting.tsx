import { useMemo, useState } from "react";
import { api, errorText } from "../../lib/api";
import {
  SHOT_TYPE_LABEL,
  fmtDuration,
  fmtMoney,
  fmtShotDuration,
  segmentName,
} from "../../lib/format";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { Avatar, EmptyState, SectionHead, Spinner } from "../ui";
import { IconChevronDown, IconScript, IconSpark, IconUser } from "../icons";

interface Props extends TabBaseProps {
  onChanged: () => void;
}

export function Screenwriting({ project, episode, assets, shots, onChanged, onOpenShot }: Props) {
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const assetMap = useMemo(() => new Map(assets.map((asset) => [asset.id, asset])), [assets]);
  const segments = useMemo(() => {
    const order: string[] = [];
    const groups = new Map<string, typeof shots>();
    shots.forEach((shot) => {
      const key = shot.segment_id || shot.segment || "other";
      if (!groups.has(key)) {
        groups.set(key, []);
        order.push(key);
      }
      groups.get(key)!.push(shot);
    });
    return order.map((id, index) => ({
      id,
      index,
      name: segmentName(id),
      shots: (groups.get(id) || []).slice().sort((a, b) => a.no - b.no),
    }));
  }, [shots]);

  const lineCount = shots.filter((shot) => (shot.line || "").trim().length > 0).length;
  const totalDuration = shots.reduce((sum, shot) => sum + (shot.duration || 0), 0);

  const handleGenerate = async () => {
    setBusy(true);
    try {
      const result = await api.generateStoryboard(episode.id);
      if ((result.created ?? 0) > 0) {
        notify("分镜已拆解：新增 " + result.created + " 个镜头", "success");
      } else {
        notify("分镜已是最新，本次没有新增镜头", "info");
      }
      onChanged();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="script-layout">
      <aside className="panel overview-panel">
        <div className="overview-brand">
          <span className="overview-kicker">EPISODE BRIEF</span>
          <h3>{episode.title}</h3>
          <p>{project.name} · 第 {String(episode.no || 1).padStart(3, "0")} 期</p>
        </div>

        <div className="overview-grid">
          <div className="overview-cell">
            <span>节目模式</span>
            <b>{project.format_id === "roundtable_5min" ? "AI 圆桌 · 5 分钟" : project.format_id}</b>
          </div>
          <div className="overview-cell">
            <span>目标时长</span>
            <b>{fmtDuration(episode.target_duration)}</b>
          </div>
          <div className="overview-cell">
            <span>画幅</span>
            <b>{project.aspect || "16:9"} · 1280×720</b>
          </div>
          <div className="overview-cell">
            <span>段落 / 镜头</span>
            <b>{segments.length} 段 · {shots.length} 镜</b>
          </div>
          <div className="overview-cell">
            <span>台词镜头</span>
            <b>{lineCount} 条</b>
          </div>
          <div className="overview-cell">
            <span>预计总时长</span>
            <b>{fmtDuration(totalDuration)}</b>
          </div>
        </div>

        <div className="overview-cost">
          <span>本集费用</span>
          <b>{fmtMoney(episode.cost)}</b>
        </div>

        <button type="button" className="btn btn-primary btn-block" onClick={() => void handleGenerate()} disabled={busy}>
          {busy ? <Spinner size={15} /> : <IconSpark size={16} />}
          {busy ? "正在拆解分镜…" : "生成分镜"}
        </button>
        <p className="overview-hint">按当前台本重新拆解镜头，写入分镜台；已有镜头会保留生成结果。</p>

        <div className="overview-cast">
          <span className="overview-cast-title">本期阵容</span>
          {assets
            .filter((asset) => asset.type === "character")
            .map((asset) => (
              <button key={asset.id} type="button" className="cast-chip" onClick={() => undefined}>
                <Avatar name={asset.name} color={asset.accent_color} size={26} />
                <span>
                  <b>{asset.name}</b>
                  <em>{asset.title}</em>
                </span>
              </button>
            ))}
        </div>
      </aside>

      <section className="script-main">
        <SectionHead
          title="台本 · 分镜台词"
          en="SCRIPT"
          extra={<span className="muted">按段落分组 · 点击镜头查看详情</span>}
        />

        {segments.length === 0 ? (
          <EmptyState
            icon={<IconScript size={26} />}
            title="还没有分镜数据"
            hint="点击左侧「生成分镜」，从台本拆解出镜头。"
          />
        ) : (
          <div className="segment-list">
            {segments.map((segment) => {
              const open = !collapsed[segment.id];
              const segDuration = segment.shots.reduce((sum, shot) => sum + (shot.duration || 0), 0);
              const segLines = segment.shots.filter((shot) => (shot.line || "").trim()).length;
              return (
                <section key={segment.id} className="segment">
                  <button
                    type="button"
                    className="segment-head"
                    onClick={() => setCollapsed((prev) => ({ ...prev, [segment.id]: !prev[segment.id] }))}
                  >
                    <span className="segment-no">{String(segment.index + 1).padStart(2, "0")}</span>
                    <span className="segment-name">{segment.name}</span>
                    <span className="segment-stat">{segment.shots.length} 镜</span>
                    <span className="segment-stat">{fmtDuration(segDuration)}</span>
                    <span className="segment-stat">{segLines} 条台词</span>
                    <IconChevronDown size={16} className={"segment-chevron" + (open ? " open" : "")} />
                  </button>

                  {open && (
                    <div className="segment-body">
                      {segment.shots.map((shot) => {
                        const asset = shot.speaker ? assetMap.get(shot.speaker) : undefined;
                        const color = asset?.accent_color || "#8A94A6";
                        const hasLine = (shot.line || "").trim().length > 0;
                        return (
                          <button
                            key={shot.id}
                            type="button"
                            className={"line-row" + (hasLine ? " has-line" : "")}
                            onClick={() => onOpenShot(shot)}
                            title="查看镜头详情"
                          >
                            <span className="line-no">#{String(shot.no).padStart(3, "0")}</span>
                            <span className="line-type" style={{ color }}>
                              {SHOT_TYPE_LABEL[shot.type] || shot.type}
                            </span>
                            <span className="line-speaker">
                              {shot.speaker_name ? (
                                <>
                                  <Avatar name={shot.speaker_name} color={color} size={24} />
                                  <span>{shot.speaker_name}</span>
                                </>
                              ) : (
                                <span className="line-speaker-none">
                                  <IconUser size={13} /> 画外
                                </span>
                              )}
                            </span>
                            <span className="line-text">
                              {hasLine ? shot.line : shot.visual || shot.subtitle || "—"}
                              {shot.punchline && hasLine && <em className="line-punch">花字：{shot.punchline}</em>}
                            </span>
                            <span className="line-duration">{fmtShotDuration(shot.duration)}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
