import type { Asset, Shot } from "../types";
import {
  SHOT_TYPE_LABEL,
  fmtShotDuration,
  segmentName,
} from "../lib/format";
import { Drawer, Avatar, Meta, StatusBadge } from "./ui";
import { ShotVersions } from "./ShotVersions";
import { ShotDynamicSection } from "./ShotDynamicSection";
import { IconUser } from "./icons";

interface Props {
  shot: Shot | null;
  assets: Asset[];
  onClose: () => void;
  onChanged: () => void;
}

export function ShotDrawer({ shot, assets, onClose, onChanged }: Props) {
  const asset = shot?.speaker ? assets.find((item) => item.id === shot.speaker) : undefined;
  const accent = asset?.accent_color || "#F5C518";

  return (
    <Drawer
      open={!!shot}
      onClose={onClose}
      width={460}
      title={
        shot ? (
          <span className="drawer-title-row">
            <span className="drawer-shot-no">#{String(shot.no).padStart(3, "0")}</span>
            <span>{SHOT_TYPE_LABEL[shot.type] || shot.type}</span>
            <StatusBadge status={shot.status} />
          </span>
        ) : (
          "镜头详情"
        )
      }
      subtitle={
        shot
          ? segmentName(shot.segment_id || shot.segment) + " · " + fmtShotDuration(shot.duration) + " · " + shot.id
          : undefined
      }
    >
      {shot && (
        <div className="shot-detail">
          <div className="shot-detail-meta">
            <Meta label="镜头类型" value={SHOT_TYPE_LABEL[shot.type] || shot.type} />
            <Meta label="时长" value={fmtShotDuration(shot.duration)} />
            <Meta
              label="说话人"
              value={
                shot.speaker_name ? (
                  <span className="speaker-mini">
                    <Avatar name={shot.speaker_name} color={accent} size={20} />
                    {shot.speaker_name}
                  </span>
                ) : (
                  <span className="muted">画外 / 无台词</span>
                )
              }
            />
            <Meta label="镜头 ID" value={<code>{shot.id}</code>} />
          </div>

          <section className="detail-block">
            <h4>台词</h4>
            {shot.line ? <blockquote className="detail-line">{shot.line}</blockquote> : <p className="muted">无台词镜头</p>}
            {shot.punchline && (
              <div className="detail-punch">
                <span>花字</span>
                {shot.punchline}
              </div>
            )}
          </section>

          <section className="detail-block">
            <h4>画面描述</h4>
            <p className="detail-visual">{shot.visual || "—"}</p>
            {shot.subtitle && <p className="detail-sub">字幕：{shot.subtitle}</p>}
          </section>

          <section className="detail-block">
            <h4>后期信息</h4>
            <div className="detail-tags">
              {shot.sfx && <span className="tag">音效 {shot.sfx}</span>}
              {shot.mood && <span className="tag">情绪 {shot.mood}</span>}
              {shot.lower_third && (
                <span className="tag tag-hl">
                  人名条 {shot.lower_third.name || ""} {shot.lower_third.title ? "· " + shot.lower_third.title : ""}
                </span>
              )}
              {shot.speaker && (
                <span className="tag">
                  <IconUser size={12} /> {asset ? asset.name + " · " + asset.voice : shot.speaker}
                </span>
              )}
            </div>
          </section>

          <ShotDynamicSection shot={shot} onChanged={onChanged} />

          <section className="detail-block detail-block-last">
            <ShotVersions shot={shot} onChanged={onChanged} />
          </section>
        </div>
      )}
    </Drawer>
  );
}
