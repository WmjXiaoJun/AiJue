import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Shot } from "../../types";
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

const SCRIPT_RULES = [
  "段落：单独一行用 [开场] / 【正片】 标记，只做分段，不单独成镜",
  "对白：一行「名字：台词」（中英文冒号均可），名字会匹配资产/角色",
  "旁白：没有冒号的行按主持人旁白处理",
  "动作/反应：整行用（）或 () 包裹，生成反应镜头",
  "片头片尾会根据本集标题自动生成",
];

/** 台本示例：优先使用本期实际角色名，保证一键填入后可直接生成分镜 */
function buildExample(names: string[]): string {
  const host = names[0] || "老周";
  const guestA = names[1] || "阿栗";
  const guestB = names[2] || "大魏";
  return [
    "[开场]",
    host + "：今天这期，我们聊一个得罪人的话题。",
    guestA + "：AI 现在能写脚本、能剪片，还能自己找梗。",
    guestB + "：（冷笑）机器先学会让人笑，再谈取代。",
    "",
    "[正片]",
    host + "：所以第一个问题——你上一次被 AI 逗笑是什么时候？",
    guestA + "：上周三，它把我的口头禅重复了七遍。",
    guestB + "：这不是好笑，这是故障。",
    "",
    "[结尾]",
    host + "：好，今天先聊到这儿，下期我们继续拆。",
    "",
  ].join("\n");
}

interface ScriptStats {
  lines: number;
  chars: number;
  segments: number;
  dialogues: number;
  actions: number;
}

function analyzeScript(text: string): ScriptStats {
  const lines = text.split(/\r?\n/);
  let segments = 0;
  let dialogues = 0;
  let actions = 0;
  lines.forEach((line) => {
    const value = line.trim();
    if (!value) return;
    if (/^[\[【].+[\]】]$/.test(value)) {
      segments += 1;
      return;
    }
    if (/^[（(].+[）)]$/.test(value)) {
      actions += 1;
      return;
    }
    if (/^[^：:]{1,12}[：:]/.test(value)) dialogues += 1;
  });
  return { lines: lines.length, chars: text.length, segments, dialogues, actions };
}

interface SegmentSummary {
  key: string;
  name: string;
  count: number;
  duration: number;
}

function summarizeShots(shots: Shot[]): { list: SegmentSummary[]; total: number } {
  const order: string[] = [];
  const map = new Map<string, SegmentSummary>();
  shots.forEach((shot) => {
    const key = shot.segment_id || shot.segment || "other";
    if (!map.has(key)) {
      map.set(key, { key, name: segmentName(key), count: 0, duration: 0 });
      order.push(key);
    }
    const entry = map.get(key)!;
    entry.count += 1;
    entry.duration += shot.duration || 0;
  });
  const list = order.map((key) => map.get(key)!);
  // 后端的 segment_id 可能是中文段落名（如「开场」），与 closing 等英文映射同名时合并展示
  const merged: SegmentSummary[] = [];
  const byName = new Map<string, SegmentSummary>();
  list.forEach((segment) => {
    const existing = byName.get(segment.name);
    if (existing) {
      existing.count += segment.count;
      existing.duration += segment.duration;
    } else {
      const copy = { ...segment };
      byName.set(segment.name, copy);
      merged.push(copy);
    }
  });
  return { list: merged, total: shots.reduce((sum, shot) => sum + (shot.duration || 0), 0) };
}

export function Screenwriting({ project, episode, assets, shots, onChanged, onOpenShot }: Props) {
  const notify = useToast();
  const [draft, setDraft] = useState(episode.script || "");
  const [saved, setSaved] = useState(episode.script || "");
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<{ created: number; summary: SegmentSummary[]; total: number; fallback: boolean } | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const dirty = draft !== saved;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // 切换剧集时重置草稿
  useEffect(() => {
    setDraft(episode.script || "");
    setSaved(episode.script || "");
    setResult(null);
  }, [episode.id]);

  // 外部刷新 episode 时，未编辑状态下同步最新台本
  useEffect(() => {
    if (dirtyRef.current) return;
    setDraft(episode.script || "");
    setSaved(episode.script || "");
  }, [episode.script]);

  // 未保存提示：关闭/刷新页面时拦截
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const characterNames = useMemo(
    () => assets.filter((asset) => asset.type === "character").map((asset) => asset.name),
    [assets]
  );
  const example = useMemo(() => buildExample(characterNames), [characterNames]);
  const stats = useMemo(() => analyzeScript(draft), [draft]);

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

  const saveScript = async (showToast = true): Promise<boolean> => {
    if (!dirty) return true;
    setSaving(true);
    try {
      const updated = await api.patchEpisode(episode.id, { script: draft });
      if (typeof updated.script !== "string") {
        notify("后端尚未支持台本存储（PATCH /episodes/{id}.script 未生效），请等后端更新后再保存", "error");
        return false;
      }
      setDraft(updated.script);
      setSaved(updated.script);
      if (showToast) notify("台本已保存", "success");
      onChanged();
      return true;
    } catch (err) {
      notify("保存台本失败：" + errorText(err), "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      if (dirty && !(await saveScript(false))) return;
      const force = shots.length > 0;
      if (force) {
        const confirmed = window.confirm(
          "当前已有 " + shots.length + " 个镜头。\n从台本重新生成会替换原有分镜（force），是否继续？"
        );
        if (!confirmed) return;
      }
      const response = await api.generateStoryboard(episode.id, force);
      const fresh = await api.shots(episode.id).catch(() => shots);
      const summary = summarizeShots(fresh);
      setResult({
        created: response.created ?? 0,
        summary: summary.list,
        total: summary.total,
        fallback: !draft.trim(),
      });
      if ((response.created ?? 0) > 0) {
        notify("已从台本生成 " + response.created + " 个镜头", "success");
      } else {
        notify(force ? "本次没有新增镜头（台本为空时会回退模板骨架）" : "已有镜头，未重新生成", "info");
      }
      onChanged();
    } catch (err) {
      notify("生成分镜失败：" + errorText(err), "error");
    } finally {
      setGenerating(false);
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

        <div className={"overview-script-status" + (dirty ? " is-dirty" : "")}>
          <span>{dirty ? "台本有未保存修改" : "台本已保存"}</span>
          <b>{stats.lines} 行 · {stats.chars} 字</b>
          <em>{stats.segments} 段落 · {stats.dialogues} 句对白 · {stats.actions} 个动作</em>
        </div>

        <div className="overview-cost">
          <span>本集费用</span>
          <b>{fmtMoney(episode.cost)}</b>
        </div>

        <button
          type="button"
          className="btn btn-primary btn-block"
          onClick={() => void handleGenerate()}
          disabled={generating}
        >
          {generating ? <Spinner size={15} /> : <IconSpark size={16} />}
          {generating ? "正在生成分镜…" : "从台本生成分镜"}
        </button>
        <p className="overview-hint">
          {shots.length > 0
            ? "已有 " + shots.length + " 个镜头：再次生成会二次确认，并用 force 覆盖原分镜。"
            : "按台本解析生成片头 / 各段镜头 / 片尾；台本为空时回退模板骨架。"}
        </p>

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
        <div className="panel script-editor">
          <div className="script-editor-head">
            <SectionHead
              title="台本编辑"
              en="SCREENPLAY"
              extra={
                <span className={"script-save-flag" + (dirty ? " is-dirty" : "")}>
                  {dirty ? "● 有未保存修改" : "已保存"}
                </span>
              }
            />
            <p className="script-editor-tip">
              按「[段落] / 名字：台词 / （动作）」格式书写；保存后即可从台本生成分镜。
            </p>
          </div>

          <textarea
            className="script-textarea"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={"在这里粘贴或编写台本，例如：\n\n[开场]\n老周：今天这期，我们聊一个得罪人的话题。\n阿栗：AI 现在能写脚本、能剪片。\n大魏：（冷笑）机器先学会让人笑，再谈取代。"}
            spellCheck={false}
            maxLength={20000}
          />

          {!draft.trim() && (
            <div className="script-example">
              <div className="script-example-head">
                <b>还没有台本 · 示例格式</b>
                <button type="button" className="btn btn-outline btn-sm" onClick={() => setDraft(example)}>
                  一键填入示例
                </button>
              </div>
              <pre className="script-example-pre">{example}</pre>
              <ul className="script-example-rules">
                {SCRIPT_RULES.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="script-editor-foot">
            <span className="script-editor-stats">
              {stats.lines} 行 · {stats.chars} 字 · {stats.segments} 段落 · {stats.dialogues} 句对白
            </span>
            <div className="script-editor-actions">
              {draft.trim() && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(example)} title="用示例覆盖当前内容">
                  填入示例
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => void saveScript()}
                disabled={!dirty || saving}
                title="PATCH /api/episodes/{id}"
              >
                {saving ? <Spinner size={13} /> : null}
                {saving ? "保存中…" : "保存台本"}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => void handleGenerate()}
                disabled={generating}
              >
                {generating ? <Spinner size={13} /> : <IconSpark size={14} />}
                {generating ? "生成中…" : "从台本生成分镜"}
              </button>
            </div>
          </div>
          {dirty && <div className="script-unsaved-hint">有未保存修改：直接生成分镜会先自动保存台本。</div>}
        </div>

        {result && (
          <div className="panel script-result">
            <div className="script-result-head">
              <b>生成结果</b>
              <span>
                新增 <b>{result.created}</b> 个镜头 · 总时长 {fmtDuration(result.total)}
                {result.fallback ? " · 台本为空，已回退模板骨架" : ""}
              </span>
            </div>
            {result.summary.length > 0 && (
              <div className="script-result-chips">
                {result.summary.map((segment) => (
                  <span key={segment.key} className="script-result-chip">
                    {segment.name} <b>{segment.count}</b> 镜 · {fmtShotDuration(segment.duration)}
                  </span>
                ))}
              </div>
            )}
            <div className="script-result-actions">
              <button type="button" className="link-btn" onClick={() => setResult(null)}>
                收起
              </button>
            </div>
          </div>
        )}

        <SectionHead
          title="分镜台词预览"
          en="SHOTS"
          extra={<span className="muted">按段落分组 · 点击镜头查看详情</span>}
        />

        {segments.length === 0 ? (
          <EmptyState
            icon={<IconScript size={26} />}
            title="还没有分镜数据"
            hint="写好台本后点击「从台本生成分镜」，即可拆解出片头、对白、反应与片尾镜头。"
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
