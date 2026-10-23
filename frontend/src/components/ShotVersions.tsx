import { useCallback, useEffect, useState } from "react";
import { api, errorText, mediaUrl } from "../lib/api";
import type { Shot, ShotVersion } from "../types";
import { VERSION_STATUS_LABEL, fmtMoney, fmtTime } from "../lib/format";
import { usePolling } from "../hooks/usePolling";
import { useToast } from "./Toast";
import { EmptyState, Notice, Spinner } from "./ui";
import { IconCheck, IconDownload, IconPlay, IconRefresh, IconSpark, IconVideo } from "./icons";

interface Props {
  shot: Shot;
  onChanged: () => void;
  showGenerate?: boolean;
}

export function ShotVersions({ shot, onChanged, showGenerate = true }: Props) {
  const notify = useToast();
  const [versions, setVersions] = useState<ShotVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.versions(shot.id);
      setVersions(list);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    }
  }, [shot.id]);

  useEffect(() => {
    setVersions(null);
    void load();
  }, [load]);

  usePolling(load, 2500, true);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      await api.generateShot(shot.id);
      notify("已提交镜头 #" + String(shot.no).padStart(3, "0") + " 的生成任务", "success");
      onChanged();
      window.setTimeout(() => void load(), 1200);
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setGenerating(false);
    }
  };

  const handleSelect = async (version: ShotVersion) => {
    setSelecting(version.id);
    try {
      await api.selectVersion(shot.id, version.id);
      notify("已选用 V" + version.version + " 作为镜头成片", "success");
      onChanged();
      await load();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setSelecting(null);
    }
  };

  return (
    <div className="versions">
      <div className="versions-head">
        <div>
          <h4>版本</h4>
          <span className="muted">{versions ? versions.length + " 个版本" : "加载中…"}</span>
        </div>
        <div className="versions-actions">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()} title="刷新版本">
            <IconRefresh size={14} />
          </button>
          {showGenerate && (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void handleGenerate()} disabled={generating}>
              {generating ? <Spinner size={13} /> : <IconSpark size={14} />}
              {generating ? "提交中…" : "生成新版本"}
            </button>
          )}
        </div>
      </div>

      {error && (
        <Notice tone="danger">
          版本加载失败：{error}
          <button type="button" className="link-btn" onClick={() => void load()}>
            重试
          </button>
        </Notice>
      )}

      {versions === null && !error ? (
        <div className="versions-loading">
          <Spinner size={16} /> 正在读取版本…
        </div>
      ) : versions && versions.length === 0 ? (
        <EmptyState
          icon={<IconVideo size={22} />}
          title="还没有生成版本"
          hint={showGenerate ? "点击「生成新版本」开始渲染这个镜头。" : "生成完成后会出现在这里。"}
        />
      ) : (
        <div className="version-list">
          {(versions || []).map((version) => {
            const selected = shot.selected_version_id === version.id;
            return (
              <article key={version.id} className={"version-card" + (selected ? " is-selected" : "")}>
                <div className="version-card-head">
                  <span className="version-no">V{version.version}</span>
                  <span className={"version-status version-status-" + (version.status || "ready")}>
                    {VERSION_STATUS_LABEL[version.status] || version.status || "可用"}
                  </span>
                  {selected && (
                    <span className="version-selected">
                      <IconCheck size={12} /> 已选用
                    </span>
                  )}
                  <span className="version-time">
                    {fmtTime(version.created_at)} · {fmtMoney(version.cost)}
                  </span>
                </div>

                {version.video_url ? (
                  <video
                    className="version-video"
                    src={mediaUrl(version.video_url)}
                    controls
                    preload="metadata"
                    playsInline
                  />
                ) : (
                  <div className="version-placeholder">
                    <IconPlay size={20} />
                    <span>暂无视频文件</span>
                  </div>
                )}

                <div className="version-foot">
                  <span className="muted">#{version.id}</span>
                  <div className="version-foot-actions">
                    {version.video_url && (
                      <a className="btn btn-ghost btn-sm" href={mediaUrl(version.video_url)} target="_blank" rel="noreferrer">
                        <IconDownload size={13} /> 打开
                      </a>
                    )}
                    <button
                      type="button"
                      className={"btn btn-sm " + (selected ? "btn-ghost" : "btn-outline")}
                      disabled={selected || selecting === version.id}
                      onClick={() => void handleSelect(version)}
                    >
                      {selecting === version.id ? <Spinner size={13} /> : <IconCheck size={14} />}
                      {selected ? "已选用" : "选用此版本"}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
