import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorText, mediaUrl } from "../../lib/api";
import type { Character, CostReport, Job } from "../../types";
import {
  CHARACTER_STYLE_LABEL,
  characterStyles,
  fmtDuration,
  fmtMoney,
  fmtTime,
  styleFamily,
} from "../../lib/format";
import { usePolling } from "../../hooks/usePolling";
import { useToast } from "../Toast";
import type { TabBaseProps } from "./types";
import { Notice, ProgressBar, SectionHead, Spinner, StatusBadge, EmptyState } from "../ui";
import {
  IconAlert,
  IconDownload,
  IconExternal,
  IconFilm,
  IconPlay,
  IconRefresh,
  IconSpark,
  IconWallet,
} from "../icons";
import { TEMPLATES } from "../../lib/templates";
import { CharacterAvatar } from "../CharacterAvatar";

interface Props extends TabBaseProps {
  jobs: Job[];
  characters: Character[];
}

const BY_TYPE_LABEL: Record<string, string> = {
  shot: "镜头生成",
  render: "成片渲染",
  tts: "配音 TTS",
  llm: "文本生成",
  other: "其他",
};

export function Finishing({ episode, jobs, characters, onChanged }: Props) {
  const notify = useToast();
  const [rendering, setRendering] = useState(false);

  const renderJobs = useMemo(
    () =>
      jobs
        .filter((job) => job.type === "render")
        .slice()
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))),
    [jobs]
  );
  const latestRender = renderJobs[0];
  const isRendering = !!latestRender && (latestRender.status === "queued" || latestRender.status === "running");
  const doneRender = renderJobs.find((job) => job.status === "done");
  const finalUrl = mediaUrl(episode.render_url || doneRender?.output_url || null);
  const renderProgress = isRendering ? latestRender.progress || 0 : episode.render_url ? 100 : 0;
  const packaging = TEMPLATES[0].packaging;

  const handleRender = async () => {
    setRendering(true);
    try {
      await api.render(episode.id);
      notify("成片渲染任务已提交，可在任务中心查看进度", "success");
      onChanged();
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setRendering(false);
    }
  };

  return (
    <div className="finish-view">
      <div className="finish-main">
        <section className="panel render-panel">
          <div className="render-head">
            <div>
              <SectionHead title="成片渲染" en="FINISHING" />
              <p className="render-desc">
                串联 {episode.shots_total || "全部"} 个镜头 · 合成音轨 / 字幕 / 花字 / 片头片尾 · 目标时长{" "}
                {fmtDuration(episode.target_duration)}
              </p>
            </div>
            <button type="button" className="btn btn-primary btn-lg" onClick={() => void handleRender()} disabled={rendering || isRendering}>
              {rendering ? <Spinner size={16} /> : <IconSpark size={17} />}
              {isRendering ? "渲染中…" : episode.render_url ? "重新渲染成片" : "渲染成片"}
            </button>
          </div>

          {latestRender && (
            <div className="render-job">
              <div className="render-job-head">
                <span className="render-job-id">{latestRender.id}</span>
                <StatusBadge status={latestRender.status} kind="job" />
                <span className="muted">{fmtTime(latestRender.updated_at || latestRender.created_at)}</span>
                <span className="muted">{fmtMoney(latestRender.cost)}</span>
              </div>
              <ProgressBar
                value={renderProgress}
                tone={latestRender.status === "failed" ? "danger" : "accent"}
                label={Math.round(renderProgress) + "%"}
              />
              {latestRender.message && <div className="render-message">{latestRender.message}</div>}
              {latestRender.status === "failed" && latestRender.error && (
                <div className="render-error">
                  <IconAlert size={14} /> {latestRender.error}
                </div>
              )}
            </div>
          )}

          <div className="final-stage">
            {finalUrl ? (
              <video className="final-video" src={finalUrl} controls playsInline preload="metadata" />
            ) : (
              <div className="final-placeholder">
                <div className="final-placeholder-icon">
                  <IconFilm size={34} />
                </div>
                <h4>{isRendering ? "正在渲染成片…" : "成片尚未渲染"}</h4>
                <p>
                  {isRendering
                    ? "流水线正在拼接镜头、混音与包装，完成后自动出现在这里。"
                    : "点击右上角「渲染成片」，流水线会将已就绪镜头合成为 final.mp4。"}
                </p>
                {isRendering && <ProgressBar value={renderProgress} label={Math.round(renderProgress) + "%"} />}
              </div>
            )}
          </div>

          {finalUrl && (
            <div className="final-actions">
              <a className="btn btn-outline btn-sm" href={finalUrl} target="_blank" rel="noreferrer">
                <IconExternal size={14} /> 新窗口打开
              </a>
              <a className="btn btn-ghost btn-sm" href={finalUrl} download>
                <IconDownload size={14} /> 下载
              </a>
              <span className="muted">输出路径 /media/episodes/{episode.id}/final.mp4</span>
            </div>
          )}
        </section>

        <section className="panel packaging-panel">
          <SectionHead title="包装配置" en="PACKAGING" extra={<span className="muted">来自节目模式 roundtable_5min</span>} />
          <div className="packaging-grid">
            {packaging.map(([key, value]) => (
              <div key={key} className="packaging-item">
                <span>{key}</span>
                <b>{value}</b>
              </div>
            ))}
          </div>
          <div className="packaging-note">
            <IconPlay size={14} />
            成片规格：1280×720 · 25fps · 横屏 16:9 · 硬字幕 · hard_cut 转场
          </div>
        </section>

        {characters.length > 0 && (
          <section className="panel packaging-panel cast-style-panel">
            <SectionHead
              title="角色画风"
              en="CAST STYLE"
              extra={<span className="muted">全部为平台自主生成的原创动漫角色 · 成片不使用真人素材</span>}
            />
            <div className="cast-style-grid">
              {characters.map((character) => {
                const styles = characterStyles(character.style);
                return (
                  <div key={character.id} className="cast-style-item">
                    <CharacterAvatar character={character} size={44} />
                    <div className="cast-style-main">
                      <b>{character.name}</b>
                      <span>{character.title}</span>
                    </div>
                    <div className="char-style-row">
                      {styles.slice(0, 2).map((style) => (
                        <span key={style} className="char-style-chip">
                          {styleFamily(style)} · {CHARACTER_STYLE_LABEL[style] || style}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>

      <CostPanel episodeId={episode.id} fallbackTotal={episode.cost || 0} />
    </div>
  );
}

function CostPanel({ episodeId, fallbackTotal }: { episodeId: string; fallbackTotal: number }) {
  const [report, setReport] = useState<CostReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.costs(episodeId);
      setReport(data);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    }
  }, [episodeId]);

  useEffect(() => {
    void load();
  }, [load]);

  usePolling(load, 3000, true);

  const total = report?.total ?? fallbackTotal;
  const byType = report?.by_type || {};
  const items = report?.items || [];
  const visibleItems = items.slice().sort((a, b) => b.amount - a.amount).slice(0, 8);
  const maxItem = Math.max(1, ...visibleItems.map((item) => item.amount));

  return (
    <aside className="panel cost-panel">
      <SectionHead title="费用汇总" en="COST" extra={<IconWallet size={16} />} />
      <div className="cost-total">
        <span>本集累计</span>
        <b>{fmtMoney(total)}</b>
        <em>{report ? report.currency || "CNY" : "等待费用接口"}</em>
      </div>

      {error && (
        <Notice tone="warn">
          费用接口不可用：{error}
          <button type="button" className="link-btn" onClick={() => void load()}>
            重试
          </button>
        </Notice>
      )}

      <div className="cost-types">
        {Object.keys(byType).length === 0 && <div className="muted cost-empty">暂无费用明细</div>}
        {Object.entries(byType).map(([key, amount]) => (
          <div key={key} className="cost-type">
            <div className="cost-type-row">
              <span>{BY_TYPE_LABEL[key] || key}</span>
              <b>{fmtMoney(amount)}</b>
            </div>
            <ProgressBar value={total > 0 ? (amount / total) * 100 : 0} size="sm" />
          </div>
        ))}
      </div>

      {visibleItems.length > 0 && (
        <div className="cost-items">
          {visibleItems.map((item, index) => (
            <div key={item.label + index} className="cost-item">
              <div className="cost-item-row">
                <span title={item.label}>{item.label}</span>
                <b>{fmtMoney(item.amount)}</b>
              </div>
              <ProgressBar value={(item.amount / maxItem) * 100} size="sm" tone="success" />
            </div>
          ))}
          {items.length > visibleItems.length && (
            <div className="cost-more">共 {items.length} 项明细 · 仅展示前 {visibleItems.length} 项</div>
          )}
        </div>
      )}

      <div className="cost-foot">
        <span>计费口径</span>
        <span>H3 768P ¥0.09/秒 · mock 记 0 元</span>
      </div>
    </aside>
  );
}
