// V9 Project Studio · 01 编剧间（SCRIPT ROOM）
// 台本编辑器（episode.script）+ AI 生成台本（/projects/{pid}/generate-script）
// + 从台本生成分镜（/episodes/{eid}/storyboard/generate）+ 台本结构速览。
// 设计与共享元件见 styles/studio.css；本页专属样式在 styles/studio-a.css（st-script-*）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GenerateScriptResult } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import {
  EPISODE_STATUS_TEXT,
  countWords,
  errorText,
  fmtClock,
  scriptParagraphs,
  studioApi,
  toneOfStatus,
} from "../../lib/studio";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconBoard, IconCheck, IconRefresh, IconScript, IconSpark } from "../icons";
import "../../styles/studio-a.css";

/** 语气选项（后端 tone 为自由文本） */
const TONES: { value: string; label: string }[] = [
  { value: "轻松幽默", label: "轻松" },
  { value: "犀利", label: "犀利" },
  { value: "深情", label: "深情" },
  { value: "悬疑", label: "悬疑" },
];

/** 台本书写格式提示（由后端台本解析规则得出） */
const SCRIPT_TIPS = [
  "[段落] 单独一行做分段（分段不单独成镜）",
  "名字：台词 —— 对白，名字会匹配本期角色",
  "（动作）整行括号 → 生成反应镜头",
  "没有冒号的行按旁白处理",
];

const EDITOR_PLACEHOLDER = [
  "在这里写台本，例如：",
  "",
  "[开场]",
  "老周：今天这期，我们聊一个得罪人的话题。",
  "阿栗：AI 现在能写脚本、能剪片，还能自己找梗。",
  "大魏：（冷笑）机器先学会让人笑，再谈取代。",
  "",
  "[正片]",
  "老周：所以第一个问题——你上一次被 AI 逗笑是什么时候？",
].join("\n");

/** 段落分隔符（与 scriptParagraphs 一致）的位置 → 每个非空段落的起始下标 */
function blockStarts(text: string): number[] {
  const starts: number[] = [];
  const re = /\n{2,}/g;
  let cursor = 0;
  for (;;) {
    re.lastIndex = cursor;
    const match = re.exec(text);
    const end = match ? match.index : text.length;
    const block = text.slice(cursor, end);
    if (block.trim()) starts.push(cursor + (block.length - block.trimStart().length));
    if (!match) break;
    cursor = end + match[0].length;
  }
  return starts;
}

/** 状态 → 共享标签色调（.st-tag.is-*） */
function stTone(tone: "gold" | "jade" | "red" | "slate"): string {
  return "is-" + tone;
}

interface GenInfo {
  title: string;
  provider: string;
  model: string;
  elapsed: number;
  warning?: string | null;
}

export function StudioScripts({ project, episode, onEpisode, onNavigate }: StudioTabProps) {
  const notify = useToast();

  // ---- 台本草稿 / 保存 ----
  const [draft, setDraft] = useState<string>(episode.script || "");
  const [saved, setSaved] = useState<string>(episode.script || "");
  const dirty = draft !== saved;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const saveLock = useRef(false);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  // ---- AI 生成台本 ----
  const [topic, setTopic] = useState(project.name || episode.title || "");
  const [duration, setDuration] = useState<number>(Math.max(15, Math.min(600, Number(episode.target_duration) || 60)));
  const [tone, setTone] = useState(TONES[0].value);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genInfo, setGenInfo] = useState<GenInfo | null>(null);

  // ---- 生成分镜 ----
  const [boardBusy, setBoardBusy] = useState(false);
  const [boardError, setBoardError] = useState<string | null>(null);

  // 外部刷新 / 切期后 episode.script 变化：没有未保存修改时同步编辑器
  useEffect(() => {
    if (dirtyRef.current) return;
    setDraft(episode.script || "");
    setSaved(episode.script || "");
  }, [episode.script]);

  const paragraphs = useMemo(() => scriptParagraphs(draft), [draft]);
  const words = useMemo(() => countWords(draft), [draft]);
  const lines = useMemo(() => (draft.trim() ? draft.split(/\r?\n/).length : 0), [draft]);
  const savedWords = useMemo(() => countWords(saved), [saved]);

  // ---- 保存台本（PATCH /episodes/{eid}） ----
  const save = useCallback(
    async (silent = false): Promise<boolean> => {
      if (saveLock.current) return false;
      if (!dirtyRef.current) {
        if (!silent) notify("没有需要保存的修改", "info");
        return true;
      }
      saveLock.current = true;
      setSaving(true);
      setSaveError(null);
      try {
        const updated = await studioApi.patchEpisode(episode.id, { script: draftRef.current });
        const text = typeof updated.script === "string" ? updated.script : draftRef.current;
        setDraft(text);
        setSaved(text);
        onEpisode(updated);
        if (!silent) notify("台本已保存", "success");
        return true;
      } catch (err) {
        const message = "保存台本失败：" + errorText(err);
        setSaveError(message);
        notify(message, "error");
        return false;
      } finally {
        saveLock.current = false;
        setSaving(false);
      }
    },
    [episode.id, notify, onEpisode],
  );

  // ---- 重新加载（GET /episodes/{eid}） ----
  const reload = useCallback(async () => {
    if (dirty && !window.confirm("台本有未保存的修改，重新加载会丢弃这些修改。是否继续？")) return;
    setReloading(true);
    setSaveError(null);
    try {
      const fresh = await studioApi.episode(episode.id);
      const text = fresh.script || "";
      setDraft(text);
      setSaved(text);
      onEpisode(fresh);
      notify("已重新加载台本", "success");
    } catch (err) {
      notify("重新加载失败：" + errorText(err), "error");
    } finally {
      setReloading(false);
    }
  }, [dirty, episode.id, notify, onEpisode]);

  // ---- AI 生成台本（POST /projects/{pid}/generate-script，同步返回 script） ----
  const generate = useCallback(async () => {
    const cover = topic.trim();
    if (!cover) {
      notify("请先填写本期主题", "error");
      return;
    }
    const seconds = Math.round(Number(duration) || 0);
    if (seconds < 15 || seconds > 600) {
      notify("时长需在 15–600 秒之间", "error");
      return;
    }
    setGenerating(true);
    setGenError(null);
    setGenInfo(null);
    const started = typeof performance !== "undefined" ? performance.now() : Date.now();
    try {
      const result: GenerateScriptResult = await studioApi.generateScript(project.id, {
        topic: cover,
        template_id: project.format_id,
        duration: seconds,
        tone,
      });
      const elapsed = (typeof performance !== "undefined" ? performance.now() : Date.now()) - started;
      setGenInfo({
        title: result.title || "",
        provider: result.provider || "—",
        model: result.model || "",
        elapsed: typeof result.elapsed_sec === "number" && result.elapsed_sec > 0 ? result.elapsed_sec : elapsed / 1000,
        warning: result.warning,
      });
      if (typeof result.script === "string" && result.script.trim()) {
        setDraft(result.script);
        notify("AI 台本已生成，记得保存", "success");
      } else {
        setGenError("后端返回了空台本（script 为空）");
      }
    } catch (err) {
      const message = "生成台本失败：" + errorText(err);
      setGenError(message);
      notify(message, "error");
    } finally {
      setGenerating(false);
    }
  }, [duration, notify, project.format_id, project.id, tone, topic]);

  // ---- 生成分镜（脏台本先保存 → storyboard/generate force → 刷新 episode → 跳分镜台） ----
  const generateBoard = useCallback(async () => {
    const count = episode.shots_total || 0;
    const dirtyNote = dirtyRef.current ? "\n\n当前台本有未保存修改，生成前会先保存台本。" : "";
    const confirmText =
      (count > 0 ? `将按台本重建分镜，现有 ${count} 个镜头会被替换。` : "将按台本拆解出片头 / 各段对白 / 反应 / 片尾镜头。") +
      "是否继续？" +
      dirtyNote;
    if (!window.confirm(confirmText)) return;

    setBoardBusy(true);
    setBoardError(null);
    try {
      if (dirtyRef.current) {
        const savedOk = await save(true);
        if (!savedOk) {
          setBoardError("台本保存失败，已取消生成分镜");
          return;
        }
      }
      const result = await studioApi.generateStoryboard(episode.id, true);
      const fresh = await studioApi.episode(episode.id);
      onEpisode(fresh);
      const created = Number(result?.created) || fresh.shots_total || 0;
      notify(`已生成 ${created} 个镜头`, "success");
      onNavigate("storyboard");
    } catch (err) {
      const message = "生成分镜失败：" + errorText(err);
      setBoardError(message);
      notify(message, "error");
    } finally {
      setBoardBusy(false);
    }
  }, [episode.id, episode.shots_total, notify, onEpisode, onNavigate, save]);

  // Ctrl/Cmd + S 保存
  const onEditorKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((event.ctrlKey || event.metaKey) && (event.key === "s" || event.key === "S")) {
        event.preventDefault();
        void save();
      }
    },
    [save],
  );

  // 点击「台本结构」里的段落：光标落到段落开头，编辑器滚动到大致位置
  const jumpToParagraph = useCallback(
    (index: number) => {
      const el = editorRef.current;
      if (!el) return;
      const starts = blockStarts(draftRef.current);
      const offset = starts[index] ?? 0;
      el.focus();
      try {
        el.setSelectionRange(offset, offset);
      } catch {
        /* 某些浏览器在 textarea 隐藏时不允许设置选区，忽略 */
      }
      const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
      const ratio = draftRef.current.length > 0 ? offset / draftRef.current.length : 0;
      el.scrollTop = Math.max(0, maxScroll * ratio);
    },
    [],
  );

  const statusTone = toneOfStatus(episode.status);

  return (
    <div className="st-page">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">SCRIPT ROOM · 台本</span>
          <h2 className="st-h1">编剧间</h2>
          <p className="st-sub">
            第 {episode.no} 期 · 目标 <span className="cm-mono">{fmtClock(episode.target_duration)}</span> · 镜头{" "}
            <span className="cm-mono">{episode.shots_total}</span>
          </p>
        </div>
        <div className="st-head-actions">
          <button type="button" className="cm-btn" onClick={() => void reload()} disabled={reloading} title="重新读取已保存的台本">
            {reloading ? <Spinner size={13} /> : <IconRefresh size={14} />}
            {reloading ? "加载中…" : "重新加载"}
          </button>
          <button
            type="button"
            className={"cm-btn cm-btn-primary st-script-save" + (dirty ? " is-dirty" : "")}
            onClick={() => void save()}
            disabled={saving || !dirty}
            title={dirty ? "保存台本（Ctrl/⌘ + S）" : "台本已是最新"}
          >
            {saving ? <Spinner size={13} /> : <IconCheck size={14} />}
            {saving ? "保存中…" : "保存台本"}
          </button>
        </div>
      </header>

      <div className="st-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">台本字数</span>
          <div className="st-kpi-value">
            {words.toLocaleString("zh-CN")}
            <small>字</small>
          </div>
          <div className="st-kpi-hint">已保存 {savedWords.toLocaleString("zh-CN")} 字</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">段落</span>
          <div className="st-kpi-value">
            {paragraphs.length}
            <small>段</small>
          </div>
          <div className="st-kpi-hint">空行分段 · {lines} 行</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">镜头</span>
          <div className="st-kpi-value">
            {episode.shots_total}
            <small>镜</small>
          </div>
          <div className="st-kpi-hint">已完成 {episode.shots_done}</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">目标时长</span>
          <div className="st-kpi-value">{fmtClock(episode.target_duration)}</div>
          <div className="st-kpi-hint">
            {episode.resolution || "—"} · {episode.aspect || project.aspect || "16:9"}
          </div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">状态</span>
          <div className="st-kpi-value st-script-kpi-tag">
            <span className={"st-tag " + stTone(statusTone)}>{EPISODE_STATUS_TEXT[episode.status] || episode.status}</span>
          </div>
          <div className="st-kpi-hint">{dirty ? "台本有未保存修改" : "台本已保存"}</div>
        </div>
      </div>

      <div className="st-split">
        {/* ---------------- 左：台本编辑器 ---------------- */}
        <section className="st-panel">
          <div className="st-panel-head">
            <div className="st-script-panel-title">
              <h3 className="st-panel-title">台本编辑器</h3>
              <p className="st-panel-sub">SCRIPT · 保存后可用于生成分镜</p>
            </div>
            <div className="st-panel-right">
              <span className={"st-tag " + (dirty ? "is-gold" : "is-jade")}>
                <i className="cm-status-dot" style={{ background: "currentColor" }} />
                {dirty ? "未保存" : "已保存"}
              </span>
              <span className="cm-chip is-mono" title="保存快捷键">
                Ctrl / ⌘ + S
              </span>
            </div>
          </div>
          <div className="st-panel-body">
            <textarea
              ref={editorRef}
              className="st-script-editor"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onEditorKeyDown}
              placeholder={EDITOR_PLACEHOLDER}
              spellCheck={false}
              maxLength={40000}
              aria-label="台本内容"
            />

            <div className="st-script-editor-foot">
              <span className="st-script-stats">
                <b className="cm-mono">{words.toLocaleString("zh-CN")}</b> 字 · <b className="cm-mono">{lines}</b> 行 ·{" "}
                <b className="cm-mono">{paragraphs.length}</b> 段
              </span>
              <span className="st-hint">
                {dirty ? "有未保存修改：Ctrl/⌘ + S 或点右上「保存台本」" : "生成分镜使用已保存的台本"}
              </span>
            </div>

            {saveError && (
              <div className="st-error st-script-error">
                <IconAlert size={15} />
                <span>{saveError}</span>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void save()}>
                  重试
                </button>
              </div>
            )}

            {!draft.trim() && (
              <div className="st-script-empty-tip">
                <div className="st-script-empty-head">
                  <IconScript size={15} /> 还没有台本
                </div>
                <ul className="st-script-rules">
                  {SCRIPT_TIPS.map((tip) => (
                    <li key={tip}>{tip}</li>
                  ))}
                </ul>
                <p className="st-hint">右侧可用「AI 生成台本」直接产出初稿，或直接把已有台本粘进编辑器。</p>
              </div>
            )}
          </div>
        </section>

        {/* ---------------- 右：AI / 分镜 / 结构 ---------------- */}
        <div className="st-split-side">
          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">AI 生成台本</h3>
                <p className="st-panel-sub">LLM · 回填后需手动保存</p>
              </div>
              <div className="st-panel-right st-script-gold">
                <IconSpark size={16} />
              </div>
            </div>
            <div className="st-panel-body">
              <div className="st-field">
                <span className="st-label">本期主题</span>
                <input
                  className="cm-input"
                  value={topic}
                  maxLength={500}
                  placeholder="例如：AI 会取代综艺导演吗？"
                  onChange={(event) => setTopic(event.target.value)}
                />
              </div>
              <div className="st-script-gen-row">
                <div className="st-field">
                  <span className="st-label">时长（秒）</span>
                  <input
                    className="cm-input"
                    type="number"
                    min={15}
                    max={600}
                    step={15}
                    value={Number.isFinite(duration) ? duration : 0}
                    onChange={(event) => setDuration(Number(event.target.value) || 0)}
                  />
                </div>
                <div className="st-field">
                  <span className="st-label">语气</span>
                  <select className="cm-select" value={tone} onChange={(event) => setTone(event.target.value)}>
                    {TONES.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <button
                type="button"
                className="cm-btn cm-btn-primary st-script-block"
                onClick={() => void generate()}
                disabled={generating || !topic.trim()}
              >
                {generating ? <Spinner size={14} /> : <IconSpark size={15} />}
                {generating ? "正在生成台本…" : "生成台本"}
              </button>

              {genError && (
                <div className="st-error st-script-error">
                  <IconAlert size={15} />
                  <span>{genError}</span>
                </div>
              )}

              {genInfo && (
                <div className="st-script-gen-info">
                  <div className="st-script-gen-chips">
                    {genInfo.title && <span className="st-tag is-gold">{genInfo.title}</span>}
                    <span className="st-tag is-slate cm-mono">{genInfo.provider}</span>
                    {genInfo.model && <span className="st-tag is-slate cm-mono">{genInfo.model}</span>}
                    <span className="st-tag is-slate cm-mono">耗时 {genInfo.elapsed.toFixed(1)}s</span>
                  </div>
                  {genInfo.warning && <p className="st-script-warn">{genInfo.warning}</p>}
                  <p className="st-hint">台本已回填编辑器（未保存），记得点「保存台本」。</p>
                </div>
              )}

              <p className="st-hint st-script-panel-hint">
                使用项目模板 <span className="cm-mono">{project.format_id}</span> 作为格式约束；生成不会自动覆盖已保存台本。
              </p>
            </div>
          </section>

          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">生成分镜</h3>
                <p className="st-panel-sub">台本 → 镜头拆解</p>
              </div>
              <div className="st-panel-right">
                <IconBoard size={16} />
              </div>
            </div>
            <div className="st-panel-body">
              <p className="st-hint st-script-panel-hint">
                读取已保存台本，拆出片头 / 各段对白 / 反应 / 片尾镜头。
                {episode.shots_total > 0 ? (
                  <>
                    {" "}
                    当前已有 <b className="cm-mono">{episode.shots_total}</b> 个镜头，重建会替换现有分镜。
                  </>
                ) : (
                  " 当前还没有分镜。"
                )}
              </p>
              <button type="button" className="cm-btn st-script-block" onClick={() => void generateBoard()} disabled={boardBusy}>
                {boardBusy ? <Spinner size={14} /> : <IconBoard size={15} />}
                {boardBusy ? "正在生成分镜…" : "生成分镜"}
              </button>
              {dirty && <p className="st-script-warn">台本有未保存修改：生成前会先自动保存台本。</p>}
              {boardError && (
                <div className="st-error st-script-error">
                  <IconAlert size={15} />
                  <span>{boardError}</span>
                </div>
              )}
            </div>
          </section>

          <section className="st-panel">
            <div className="st-panel-head">
              <div>
                <h3 className="st-panel-title">台本结构</h3>
                <p className="st-panel-sub">{paragraphs.length} 个段落 · 点击定位</p>
              </div>
              <div className="st-panel-right">
                <span className="cm-chip is-mono">{paragraphs.length}</span>
              </div>
            </div>
            <div className="st-panel-body is-flush">
              {paragraphs.length === 0 ? (
                <div className="st-script-structure-empty">
                  <div className="st-empty">
                    <span>还没有分段</span>
                    <span className="st-hint">用空行分隔段落，或先用 AI 生成一版台本。</span>
                  </div>
                </div>
              ) : (
                <ol className="st-script-structure">
                  {paragraphs.map((paragraph, index) => (
                    <li key={index}>
                      <button
                        type="button"
                        className="st-script-structure-item"
                        onClick={() => jumpToParagraph(index)}
                        title="定位到编辑器"
                      >
                        <span className="st-script-structure-no cm-mono">{String(index + 1).padStart(2, "0")}</span>
                        <span className="st-script-structure-title">{paragraph.title || "（无标题段落）"}</span>
                        <span className="st-script-structure-count cm-mono">{countWords(paragraph.body || paragraph.title)}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              )}
              <div className="st-script-structure-foot">
                <span className="st-hint">点击段落可定位到编辑器</span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
