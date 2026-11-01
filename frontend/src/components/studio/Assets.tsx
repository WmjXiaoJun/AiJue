// V9 Project Studio · 02 资产库（ASSETS ROOM）
// 角色工坊（角色卡 / 概念图生成与轮询 / 锁定）+ 固定资产（assets 分组表格）
// + 场景卡片 + 新建角色抽屉 + 图片 lightbox。
// 设计与共享元件见 styles/studio.css；本页专属样式在 styles/studio-a.css（st-assets-*）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Asset, Character, CharacterSpec, ConceptFiles, Job } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import { ASSET_ROLE_TEXT, errorText, fmtDateTime, isActiveJob, mediaUrl, studioApi, toneOfStatus, waitJob } from "../../lib/studio";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconClose,
  IconImage,
  IconLock,
  IconPlus,
  IconRefresh,
  IconSpark,
  IconUnlock,
} from "../icons";
import "../../styles/studio-a.css";

// ---------------------------------------------------------------------------
// 常量 / 纯函数
// ---------------------------------------------------------------------------
const TYPE_ORDER = ["character", "scene", "prop", "style"];

const TYPE_TEXT: Record<string, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  style: "风格",
};

const CHARACTER_STATUS_TEXT: Record<string, string> = {
  pending: "待生成",
  draft: "待生成",
  queued: "排队中",
  generating: "生成中",
  running: "生成中",
  ready: "已就绪",
  done: "已就绪",
  failed: "生成失败",
};

const STYLE_TEXT: Record<string, string> = {
  "3d_toon": "3D 卡通",
  "3d_cel": "3D 赛璐璐",
  "2d_cel": "2D 赛璐璐",
  "2d_paint": "2D 厚涂",
  anime: "日漫",
  guoman: "国漫",
};

const VOICE_OPTIONS: { value: string; label: string }[] = [
  { value: "zh-CN-YunxiNeural", label: "云希 · 男声 · 沉稳" },
  { value: "zh-CN-YunjianNeural", label: "云健 · 男声 · 浑厚" },
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓 · 女声 · 明亮" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊 · 女声 · 少女" },
];

const ACCENT_SWATCHES = ["#F5C518", "#5AC8FA", "#FF6B6B", "#C89B3C", "#8B7CF6", "#35C08A"];

const CONCEPT_MODES: { value: string; label: string }[] = [
  { value: "portrait", label: "立绘" },
  { value: "three_view", label: "三视图" },
  { value: "expressions", label: "表情" },
  { value: "sheet", label: "设定总览" },
];

const CONCEPT_STYLES: { value: string; label: string }[] = [
  { value: "anime", label: "Anime 日系（animagine）" },
  { value: "illustrious", label: "Illustrious 二次元插画" },
  { value: "guoman", label: "Guoman 国漫" },
];

const DEFAULT_CONCEPT_MODES = ["portrait", "three_view", "expressions"];

const THREE_VIEW_TEXT: Record<string, string> = { front: "正面", side: "侧面", three_quarter: "四分之三" };
const EXPRESSION_TEXT: Record<string, string> = { neutral: "平静", happy: "开心", surprised: "惊讶", thinking: "思考" };

function firstChar(name?: string | null): string {
  return (name || "").trim().slice(0, 1) || "?";
}

function styleList(style?: Character["style"] | CharacterSpec["style"] | null): string[] {
  if (!style) return [];
  if (typeof style === "string") return [style];
  const list = [style.primary, ...(style.variants || [])].filter((item): item is string => !!item);
  return Array.from(new Set(list));
}

function styleText(style?: Character["style"] | null): string {
  const list = styleList(style);
  if (!list.length) return "未设定风格";
  return list.map((key) => STYLE_TEXT[key] || key).join(" / ");
}

function toneClass(tone: "gold" | "jade" | "red" | "slate"): string {
  return "is-" + tone;
}

function characterPortrait(character: Character): string | null {
  return character.portrait_url || character.concept_portrait_url || null;
}

function conceptFromCharacter(character: Character): ConceptFiles {
  return {
    portrait: character.concept_portrait_url || null,
    three_view: character.concept_three_view || {},
    expressions: character.concept_expressions || {},
    sheet: character.concept_sheet_url || null,
    meta: null,
    consistent: null,
  };
}

function hasConcept(character: Character): boolean {
  return !!(
    character.concept_portrait_url ||
    character.concept_sheet_url ||
    (character.concept_three_view && Object.keys(character.concept_three_view).length > 0) ||
    (character.concept_expressions && Object.keys(character.concept_expressions).length > 0)
  );
}

interface ConceptTile {
  key: string;
  label: string;
  url: string;
  group: "portrait" | "three_view" | "expressions" | "sheet";
}

/** /characters/{cid}/concepts 响应 → 图片瓦片（兼容 three_view 对象 / 数组） */
function conceptTiles(concept?: ConceptFiles | null): ConceptTile[] {
  if (!concept) return [];
  const tiles: ConceptTile[] = [];
  if (concept.portrait) tiles.push({ key: "portrait", label: "概念立绘", url: concept.portrait, group: "portrait" });
  const three = concept.three_view;
  if (three) {
    const entries: [string, string][] = Array.isArray(three)
      ? three.map((url, index) => [String(index), url])
      : Object.entries(three);
    entries.forEach(([key, url]) => {
      if (!url) return;
      tiles.push({ key: "three_" + key, label: "三视图 · " + (THREE_VIEW_TEXT[key] || key), url, group: "three_view" });
    });
  }
  if (concept.expressions) {
    Object.entries(concept.expressions).forEach(([key, url]) => {
      if (!url) return;
      tiles.push({ key: "expr_" + key, label: EXPRESSION_TEXT[key] || key, url, group: "expressions" });
    });
  }
  if (concept.sheet) tiles.push({ key: "sheet", label: "设定总览", url: concept.sheet, group: "sheet" });
  return tiles;
}

function assetImage(asset: Asset): string | null {
  const images = asset.images;
  if (!images || !images.length) return null;
  const first = images.find((item) => typeof item === "string" && item.trim().length > 0);
  return first || null;
}

// ---------------------------------------------------------------------------
// 角色卡
// ---------------------------------------------------------------------------
function CharacterCard({
  character,
  portrait,
  asset,
  job,
  locking,
  onOpen,
  onGenerate,
  onToggleLock,
}: {
  character: Character;
  portrait: string | null;
  asset: Asset | null;
  job?: Job;
  locking: boolean;
  onOpen: () => void;
  onGenerate: () => void;
  onToggleLock: () => void;
}) {
  const busy = isActiveJob(job);
  const progress = job ? Math.max(0, Math.min(100, Number(job.progress) || 0)) : 0;
  const locked = !!asset?.locked;
  return (
    <article className={"st-assets-card" + (locked ? " is-locked" : "")}>
      <button type="button" className="st-assets-card-media" onClick={onOpen} title="打开角色抽屉 · 概念图">
        {portrait ? (
          <img src={mediaUrl(portrait)} alt={character.name} loading="lazy" />
        ) : (
          <span className="st-avatar-fallback">{firstChar(character.name)}</span>
        )}
        <span className={"st-assets-card-status st-tag " + toneClass(toneOfStatus(character.status))}>
          {CHARACTER_STATUS_TEXT[character.status] || character.status}
        </span>
      </button>

      <div className="st-assets-card-body">
        <div className="st-assets-card-head">
          <h4 className="st-assets-card-name">{character.name}</h4>
          {locked && <IconLock size={13} className="st-assets-lock-icon" />}
        </div>
        <div className="st-assets-card-title">{character.title || "未设定定位"}</div>
        <p className="st-assets-card-persona">{character.persona || "还没有填写人设。"}</p>
        <div className="st-assets-card-tags">
          <span className="st-tag is-slate cm-mono">{styleText(character.style)}</span>
          <span className="st-tag is-slate cm-mono">{character.voice || "未分配声线"}</span>
        </div>
        {busy && (
          <div className="st-assets-card-job">
            <div className="cm-progress is-running">
              <i style={{ width: progress + "%" }} />
            </div>
            <span className="st-hint">
              {job?.message || "概念图生成中"} <span className="cm-mono">{Math.round(progress)}%</span>
            </span>
          </div>
        )}
      </div>

      <div className="st-assets-card-actions">
        <button type="button" className="cm-btn cm-btn-sm" onClick={onOpen}>
          <IconImage size={13} />
          概念图
        </button>
        <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={onGenerate} disabled={busy}>
          {busy ? <Spinner size={12} /> : <IconSpark size={13} />}
          生成概念
        </button>
        <button
          type="button"
          className="cm-btn cm-btn-sm"
          onClick={onToggleLock}
          disabled={locking || !asset}
          title={asset ? (locked ? "解锁资产" : "锁定资产（生成时保持形象）") : "本期资产里没有该角色的对应记录"}
        >
          {locking ? <Spinner size={12} /> : locked ? <IconUnlock size={13} /> : <IconLock size={13} />}
          {locked ? "解锁" : "锁定"}
        </button>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// 角色抽屉（概念图 / 生成控制 / lightbox 入口）
// ---------------------------------------------------------------------------
function CharacterDrawer({
  character,
  concept,
  conceptLoading,
  conceptError,
  job,
  modes,
  style,
  onToggleMode,
  onStyleChange,
  onGenerate,
  onClose,
  onLightbox,
}: {
  character: Character;
  concept: ConceptFiles;
  conceptLoading: boolean;
  conceptError: string | null;
  job?: Job;
  modes: string[];
  style: string;
  onToggleMode: (mode: string) => void;
  onStyleChange: (style: string) => void;
  onGenerate: () => void;
  onClose: () => void;
  onLightbox: (url: string) => void;
}) {
  const busy = isActiveJob(job);
  const progress = job ? Math.max(0, Math.min(100, Number(job.progress) || 0)) : 0;
  const portrait = characterPortrait(character) || concept.portrait || null;
  const tiles = conceptTiles(concept);
  const statusTone = toneOfStatus(character.status);

  return (
    <>
      <div className="st-drawer-mask" onClick={onClose} />
      <aside className="st-drawer st-assets-drawer" role="dialog" aria-modal="true" aria-label={"角色 · " + character.name}>
        <div className="st-drawer-head">
          <div className="st-assets-drawer-head-main">
            <h3 className="st-drawer-title">{character.name}</h3>
            <div className="st-drawer-sub">
              {character.title || "角色"} · <span className="cm-mono">{character.id}</span>
            </div>
          </div>
          <span className={"st-tag " + toneClass(statusTone)}>
            <i className="cm-status-dot" style={{ background: "currentColor" }} />
            {CHARACTER_STATUS_TEXT[character.status] || character.status}
          </span>
          <button type="button" className="st-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={15} />
          </button>
        </div>

        <div className="st-drawer-body">
          <div className="st-assets-hero">
            <div className="st-assets-hero-avatar">
              {portrait ? (
                <img src={mediaUrl(portrait)} alt={character.name} />
              ) : (
                <span className="st-avatar-fallback">{firstChar(character.name)}</span>
              )}
            </div>
            <div className="st-assets-hero-main">
              <div className="st-assets-hero-name">{character.name}</div>
              <div className="st-assets-hero-title">{character.title || "未设定定位"}</div>
              <p className="st-assets-hero-persona">{character.persona || "还没有填写人设。"}</p>
              <div className="st-assets-hero-meta">
                <span className="st-tag is-slate cm-mono">声线 {character.voice || "未分配"}</span>
                <span className="st-tag is-slate cm-mono">风格 {styleText(character.style)}</span>
                <span className="st-tag is-slate cm-mono">创建 {fmtDateTime(character.created_at)}</span>
              </div>
            </div>
          </div>

          {job && isActiveJob(job) && (
            <div className="st-assets-job">
              <div className="cm-progress is-running">
                <i style={{ width: progress + "%" }} />
              </div>
              <div className="st-assets-job-meta">
                <span className="cm-mono">{Math.round(progress)}%</span>
                <span className="st-hint">{job.message || "AI 正在生成概念图…"}</span>
              </div>
            </div>
          )}

          <section className="st-assets-section">
            <div className="st-assets-section-head">
              <h4 className="st-assets-section-title">概念图</h4>
              <span className="st-hint">{conceptLoading ? "读取中…" : tiles.length ? tiles.length + " 张" : "暂无"}</span>
            </div>
            {conceptLoading && tiles.length === 0 ? (
              <div className="st-loading">
                <Spinner size={14} /> 读取概念图…
              </div>
            ) : tiles.length === 0 ? (
              conceptError ? (
                <div className="st-error">
                  <IconAlert size={15} />
                  <span>{conceptError}</span>
                </div>
              ) : (
                <div className="st-empty st-assets-empty-sm">还没有概念图：选好模式后点下方「生成概念图」。</div>
              )
            ) : (
              <>
                {conceptError && (
                  <div className="st-assets-warn-inline">
                    <IconAlert size={14} />
                    <span>{conceptError}（展示角色卡上已回填的概念图）</span>
                  </div>
                )}
                <div className="st-assets-concept-grid">
                  {tiles.map((tile) => (
                    <button
                      key={tile.key}
                      type="button"
                      className="st-assets-concept-tile"
                      onClick={() => onLightbox(tile.url)}
                      title={"点击放大 · " + tile.label}
                    >
                      <img src={mediaUrl(tile.url)} alt={tile.label} loading="lazy" />
                      <span className="st-assets-concept-label">{tile.label}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </section>

          <section className="st-assets-section">
            <div className="st-assets-section-head">
              <h4 className="st-assets-section-title">生成概念图</h4>
              <span className="st-hint">SDXL · 本地</span>
            </div>
            <div className="st-assets-modes">
              {CONCEPT_MODES.map((item) => (
                <label key={item.value} className={"st-assets-check" + (modes.includes(item.value) ? " is-on" : "")}>
                  <input
                    type="checkbox"
                    checked={modes.includes(item.value)}
                    onChange={() => onToggleMode(item.value)}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </div>
            <div className="st-field">
              <span className="st-label">风格 style</span>
              <input
                className="cm-input"
                value={style}
                list="st-assets-concept-styles"
                placeholder="留空用后端默认（anime）"
                onChange={(event) => onStyleChange(event.target.value)}
              />
              <datalist id="st-assets-concept-styles">
                {CONCEPT_STYLES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </datalist>
              <span className="st-hint">
                可选 {CONCEPT_STYLES.map((item) => item.value).join(" / ")}；留空由后端使用默认风格。
              </span>
            </div>
            <button type="button" className="cm-btn cm-btn-primary st-assets-block" onClick={onGenerate} disabled={busy || !modes.length}>
              {busy ? <Spinner size={14} /> : <IconSpark size={15} />}
              {busy ? "生成中…" : "生成概念图"}
            </button>
            {busy && job && (
              <p className="st-hint st-assets-job-hint">
                任务 <span className="cm-mono">{job.id}</span> 进行中，完成后会自动刷新概念图。
              </p>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// 新建角色抽屉
// ---------------------------------------------------------------------------
function NewCharacterDrawer({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: (character: Character) => void;
}) {
  const notify = useToast();
  const [name, setName] = useState("");
  const [title, setTitle] = useState("嘉宾");
  const [persona, setPersona] = useState("");
  const [voice, setVoice] = useState(VOICE_OPTIONS[0].value);
  const [accent, setAccent] = useState(ACCENT_SWATCHES[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) {
      setError("请先填写角色名字");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // 默认现代人物外观参数（写入角色 spec，AI 概念图按它绘制）
      const spec: CharacterSpec = {
        name: name.trim(),
        title: title.trim() || "嘉宾",
        persona: persona.trim(),
        voice,
        accent_color: accent,
        style: { primary: "2d_cel", variants: ["2d_cel", "3d_toon"] },
        appearance: {
          gender: "male",
          age: 30,
          height_ratio: 1,
          head_scale: 1.12,
          chibi: 0.2,
          skin: "#F2D6C3",
          hair_color: "#2B2B33",
          hair_style: "short_swept",
          eye_color: "#2B2B33",
          eye_size: 1,
          brow: "thick",
          facial_hair: "none",
          outfit: { type: "suit", main: "#1F2733", accent: "#C9A227", shirt: "#FFFFFF" },
          accessories: [],
          marks: [],
        },
      };
      const created = await studioApi.createCharacter(projectId, spec);
      notify(`角色「${created.name}」已创建，去角色卡点「生成概念」出图`, "success");
      onCreated(created);
    } catch (err) {
      const message = "创建角色失败：" + errorText(err);
      setError(message);
      notify(message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="st-drawer-mask" onClick={onClose} />
      <aside className="st-drawer st-assets-drawer" role="dialog" aria-modal="true" aria-label="新建角色">
        <div className="st-drawer-head">
          <div className="st-assets-drawer-head-main">
            <h3 className="st-drawer-title">新建角色</h3>
            <div className="st-drawer-sub">角色工坊 · 现代人物默认外观</div>
          </div>
          <button type="button" className="st-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={15} />
          </button>
        </div>

        <div className="st-drawer-body">
          {error && (
            <div className="st-error st-assets-form-error">
              <IconAlert size={15} />
              <span>{error}</span>
            </div>
          )}

          <div className="st-field">
            <span className="st-label">名字</span>
            <input
              className="cm-input"
              value={name}
              maxLength={20}
              placeholder="例如：小满"
              autoFocus
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
            />
          </div>
          <div className="st-field">
            <span className="st-label">身份 / 定位</span>
            <input
              className="cm-input"
              value={title}
              maxLength={20}
              placeholder="例如：飞行嘉宾"
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="st-field">
            <span className="st-label">人设</span>
            <textarea
              className="cm-textarea"
              rows={3}
              value={persona}
              maxLength={120}
              placeholder="例如：嘴硬心软、爱拆台、金句制造机"
              onChange={(event) => setPersona(event.target.value)}
            />
          </div>
          <div className="st-field">
            <span className="st-label">声线（edge-tts）</span>
            <select className="cm-select" value={voice} onChange={(event) => setVoice(event.target.value)}>
              {VOICE_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="st-field">
            <span className="st-label">强调色</span>
            <div className="st-assets-accent">
              <input type="color" className="st-assets-color" value={accent} onChange={(event) => setAccent(event.target.value)} aria-label="强调色" />
              <div className="st-assets-swatches">
                {ACCENT_SWATCHES.map((color) => (
                  <button
                    key={color}
                    type="button"
                    className={"st-assets-swatch" + (accent.toLowerCase() === color.toLowerCase() ? " is-on" : "")}
                    style={{ background: color }}
                    onClick={() => setAccent(color)}
                    aria-label={"强调色 " + color}
                  />
                ))}
              </div>
            </div>
          </div>

          <p className="st-hint">
            角色创建后挂到项目第 1 期资产，默认外观为现代男性（30 岁 / 短碎盖 / 西装）；可在角色卡里生成概念图后再调整。
          </p>
        </div>

        <div className="st-assets-drawer-foot">
          <button type="button" className="cm-btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="cm-btn cm-btn-primary" onClick={() => void submit()} disabled={busy || !name.trim()}>
            {busy ? <Spinner size={14} /> : <IconPlus size={15} />}
            {busy ? "创建中…" : "创建角色"}
          </button>
        </div>
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------
export function StudioAssets({ project, episode }: StudioTabProps) {
  const notify = useToast();

  const [assets, setAssets] = useState<Asset[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [charactersLoading, setCharactersLoading] = useState(true);
  const [assetsError, setAssetsError] = useState<string | null>(null);
  const [charactersError, setCharactersError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [lockingId, setLockingId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [lightbox, setLightbox] = useState<string | null>(null);

  const [concepts, setConcepts] = useState<Record<string, ConceptFiles>>({});
  const [conceptLoading, setConceptLoading] = useState(false);
  const [conceptError, setConceptError] = useState<string | null>(null);
  const [charJobs, setCharJobs] = useState<Record<string, Job>>({});
  const charJobsRef = useRef(charJobs);
  charJobsRef.current = charJobs;

  const [genModes, setGenModes] = useState<string[]>(DEFAULT_CONCEPT_MODES);
  const [genStyle, setGenStyle] = useState("anime");

  // ---- 数据加载（固定资产 / 角色工坊 分别降级） ----
  const loadAssets = useCallback(async (): Promise<boolean> => {
    setAssetsLoading(true);
    setAssetsError(null);
    try {
      const list = await studioApi.assets(episode.id);
      setAssets(Array.isArray(list) ? list : []);
      return true;
    } catch (err) {
      setAssetsError(errorText(err));
      return false;
    } finally {
      setAssetsLoading(false);
    }
  }, [episode.id]);

  const loadCharacters = useCallback(async (): Promise<boolean> => {
    setCharactersLoading(true);
    setCharactersError(null);
    try {
      const list = await studioApi.characters(project.id);
      setCharacters(Array.isArray(list) ? list : []);
      return true;
    } catch (err) {
      setCharactersError(errorText(err));
      return false;
    } finally {
      setCharactersLoading(false);
    }
  }, [project.id]);

  useEffect(() => {
    void loadAssets();
    void loadCharacters();
  }, [loadAssets, loadCharacters]);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      const [assetsOk, charactersOk] = await Promise.all([loadAssets(), loadCharacters()]);
      if (assetsOk && charactersOk) notify("已刷新资产与角色", "success");
      else notify("刷新完成，但部分接口失败（见页面提示）", "info");
    } finally {
      setRefreshing(false);
    }
  }, [loadAssets, loadCharacters, notify]);

  // ---- 锁定 / 解锁（PATCH /assets/{aid}） ----
  const toggleLock = useCallback(
    async (asset: Asset) => {
      if (lockingId) return;
      setLockingId(asset.id);
      try {
        const updated = await studioApi.patchAsset(asset.id, { locked: !asset.locked });
        setAssets((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
        notify(updated.locked ? `已锁定「${updated.name}」` : `已解锁「${updated.name}」`, "success");
      } catch (err) {
        notify("更新锁定失败：" + errorText(err), "error");
      } finally {
        setLockingId(null);
      }
    },
    [lockingId, notify],
  );

  // ---- 概念图生成（job + waitJob 轮询） ----
  const refreshConcepts = useCallback(async (cid: string): Promise<ConceptFiles | null> => {
    try {
      const data = await studioApi.characterConcepts(cid);
      setConcepts((prev) => ({ ...prev, [cid]: data }));
      return data;
    } catch {
      return null;
    }
  }, []);

  const runConcept = useCallback(
    async (character: Character, modes: string[], style: string) => {
      if (isActiveJob(charJobsRef.current[character.id])) {
        notify(`「${character.name}」的概念图任务还在进行中`, "info");
        return;
      }
      if (!modes.length) {
        notify("至少选择一种生成模式", "error");
        return;
      }
      try {
        const job = await studioApi.generateConcept(character.id, {
          modes,
          style: style.trim() ? style.trim() : undefined,
        });
        setCharJobs((prev) => ({ ...prev, [character.id]: job }));
        notify(`已提交「${character.name}」概念图任务`, "success");
        const final = await waitJob(job.id, (tick) => {
          setCharJobs((prev) => ({ ...prev, [character.id]: tick }));
        });
        if (final.status === "done") {
          notify(`「${character.name}」概念图已生成`, "success");
          await refreshConcepts(character.id);
          await loadCharacters();
        } else if (final.status === "failed" || final.status === "canceled") {
          notify("概念图生成失败：" + (final.error || final.message || "未知原因"), "error");
          await loadCharacters();
        } else {
          notify("概念图任务仍未结束，可稍后在「生成台」查看进度", "info");
        }
      } catch (err) {
        notify("生成概念图失败：" + errorText(err), "error");
      } finally {
        setCharJobs((prev) => {
          const next = { ...prev };
          delete next[character.id];
          return next;
        });
      }
    },
    [loadCharacters, notify, refreshConcepts],
  );

  // ---- 抽屉：打开时读取概念图 ----
  useEffect(() => {
    if (!openId) {
      setConceptLoading(false);
      setConceptError(null);
      return;
    }
    let alive = true;
    setConceptLoading(true);
    setConceptError(null);
    studioApi
      .characterConcepts(openId)
      .then((data) => {
        if (alive) setConcepts((prev) => ({ ...prev, [openId]: data }));
      })
      .catch((err) => {
        if (alive) setConceptError("概念图读取失败：" + errorText(err));
      })
      .finally(() => {
        if (alive) setConceptLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [openId]);

  // Esc 关闭：lightbox → 新建抽屉 → 角色抽屉
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (lightbox) {
        setLightbox(null);
        return;
      }
      if (newOpen) {
        setNewOpen(false);
        return;
      }
      if (openId) setOpenId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox, newOpen, openId]);

  const toggleMode = useCallback((mode: string) => {
    setGenModes((prev) => {
      if (prev.includes(mode)) {
        if (prev.length <= 1) return prev;
        return prev.filter((item) => item !== mode);
      }
      return [...prev, mode];
    });
  }, []);

  // ---- 派生数据 ----
  const openCharacter = useMemo(() => characters.find((item) => item.id === openId) || null, [characters, openId]);
  const openConcept = useMemo<ConceptFiles | null>(() => {
    if (!openCharacter) return null;
    return concepts[openCharacter.id] || conceptFromCharacter(openCharacter);
  }, [concepts, openCharacter]);

  const characterAssets = useMemo(() => assets.filter((item) => item.type === "character"), [assets]);
  const scenes = useMemo(() => assets.filter((item) => item.type === "scene"), [assets]);
  const lockedCount = useMemo(() => assets.filter((item) => item.locked).length, [assets]);
  const voices = useMemo(() => {
    const set = new Set<string>();
    assets.forEach((item) => {
      if (item.voice && item.voice.trim()) set.add(item.voice.trim());
    });
    characters.forEach((item) => {
      if (item.voice && item.voice.trim()) set.add(item.voice.trim());
    });
    return Array.from(set);
  }, [assets, characters]);
  const conceptCount = useMemo(() => characters.filter(hasConcept).length, [characters]);

  const groups = useMemo(() => {
    const known = new Set(TYPE_ORDER);
    const list = TYPE_ORDER.map((type) => ({
      type,
      label: TYPE_TEXT[type] || type,
      items: assets.filter((item) => item.type === type),
    })).filter((group) => group.items.length > 0);
    const others = assets.filter((item) => !known.has(item.type));
    if (others.length) list.push({ type: "other", label: "其他", items: others });
    return list;
  }, [assets]);

  const assetForCharacter = useCallback(
    (character: Character): Asset | null => {
      const byId = character.asset_id ? assets.find((item) => item.id === character.asset_id) : undefined;
      if (byId) return byId;
      return assets.find((item) => item.type === "character" && item.name === character.name) || null;
    },
    [assets],
  );

  const handleCharacterLock = useCallback(
    (character: Character) => {
      const asset = assetForCharacter(character);
      if (!asset) {
        notify(`本期资产里没有找到「${character.name}」的对应记录，无法锁定（角色可能属于其他期）`, "error");
        return;
      }
      void toggleLock(asset);
    },
    [assetForCharacter, notify, toggleLock],
  );

  const handleCharacterCreated = useCallback(async () => {
    setNewOpen(false);
    await Promise.all([loadCharacters(), loadAssets()]);
  }, [loadAssets, loadCharacters]);

  return (
    <div className="st-page">
      <header className="st-page-head">
        <div className="st-head-main">
          <span className="st-kicker">ASSETS ROOM · 资产</span>
          <h2 className="st-h1">资产库</h2>
          <p className="st-sub">
            第 {episode.no} 期 · 角色 <span className="cm-mono">{characters.length}</span> · 场景{" "}
            <span className="cm-mono">{scenes.length}</span> · 锁定{" "}
            <span className="cm-mono">
              {lockedCount}/{assets.length}
            </span>
          </p>
        </div>
        <div className="st-head-actions">
          <button type="button" className="cm-btn" onClick={() => void refreshAll()} disabled={refreshing}>
            {refreshing ? <Spinner size={13} /> : <IconRefresh size={14} />}
            {refreshing ? "刷新中…" : "刷新"}
          </button>
          <button type="button" className="cm-btn cm-btn-primary" onClick={() => setNewOpen(true)}>
            <IconPlus size={14} />
            新建角色
          </button>
        </div>
      </header>

      <div className="st-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">角色</span>
          <div className="st-kpi-value">
            {characters.length}
            <small>个</small>
          </div>
          <div className="st-kpi-hint">角色资产 {characterAssets.length} 条</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">场景</span>
          <div className="st-kpi-value">
            {scenes.length}
            <small>个</small>
          </div>
          <div className="st-kpi-hint">类型 type=scene</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">声线</span>
          <div className="st-kpi-value">
            {voices.length}
            <small>条</small>
          </div>
          <div className="st-kpi-hint">资产 / 角色 voice 去重</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">锁定 / 资产</span>
          <div className="st-kpi-value">
            {lockedCount}
            <small>/ {assets.length}</small>
          </div>
          <div className="st-kpi-hint">锁定后生成时保持形象一致</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">已出概念图</span>
          <div className="st-kpi-value">
            {conceptCount}
            <small>/ {characters.length}</small>
          </div>
          <div className="st-kpi-hint">概念立绘 / 三视图 / 表情</div>
        </div>
      </div>

      {/* ---------------- 角色工坊 ---------------- */}
      <section className="st-panel">
        <div className="st-panel-head">
          <div>
            <h3 className="st-panel-title">角色工坊</h3>
            <p className="st-panel-sub">角色卡 / 概念图 / 锁定</p>
          </div>
          <div className="st-panel-right">
            {charactersLoading && <Spinner size={13} />}
            <span className="cm-chip is-mono">{characters.length} 角色</span>
          </div>
        </div>
        <div className="st-panel-body">
          {charactersError ? (
            <div className="st-error">
              <IconAlert size={15} />
              <span>角色接口失败：{charactersError}</span>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void loadCharacters()}>
                重试
              </button>
            </div>
          ) : charactersLoading ? (
            <div className="st-loading">
              <Spinner size={14} /> 加载角色工坊…
            </div>
          ) : characters.length === 0 ? (
            <div className="st-empty">
              <span>还没有角色</span>
              <span className="st-hint">点右上「新建角色」创建第一个角色，再生成概念图。</span>
            </div>
          ) : (
            <div className="st-assets-grid">
              {characters.map((character) => (
                <CharacterCard
                  key={character.id}
                  character={character}
                  portrait={characterPortrait(character)}
                  asset={assetForCharacter(character)}
                  job={charJobs[character.id]}
                  locking={lockingId !== null && assetForCharacter(character)?.id === lockingId}
                  onOpen={() => setOpenId(character.id)}
                  onGenerate={() => void runConcept(character, DEFAULT_CONCEPT_MODES, "anime")}
                  onToggleLock={() => handleCharacterLock(character)}
                />
              ))}
              <button type="button" className="st-assets-add-card" onClick={() => setNewOpen(true)} title="新建角色">
                <IconPlus size={22} />
                <span className="st-assets-add-card-title">新建角色</span>
                <span className="st-hint">创建后可在卡片上生成概念图</span>
              </button>
            </div>
          )}
        </div>
      </section>

      {/* ---------------- 固定资产 ---------------- */}
      <section className="st-panel">
        <div className="st-panel-head">
          <div>
            <h3 className="st-panel-title">固定资产</h3>
            <p className="st-panel-sub">按类型分组 · 缩略图可放大</p>
          </div>
          <div className="st-panel-right">
            <span className="cm-chip is-mono">
              {assets.length} 资产 · {lockedCount} 锁定
            </span>
          </div>
        </div>
        {assetsError ? (
          <div className="st-panel-body">
            <div className="st-error">
              <IconAlert size={15} />
              <span>资产接口失败：{assetsError}</span>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void loadAssets()}>
                重试
              </button>
            </div>
          </div>
        ) : assetsLoading ? (
          <div className="st-panel-body">
            <div className="st-loading">
              <Spinner size={14} /> 加载固定资产…
            </div>
          </div>
        ) : groups.length === 0 ? (
          <div className="st-panel-body">
            <div className="st-empty">
              <span>本期还没有固定资产</span>
              <span className="st-hint">生成分镜后角色 / 场景会自动写入资产；也可以新建角色。</span>
            </div>
          </div>
        ) : (
          <div className="st-panel-body is-flush">
            <table className="st-table st-assets-table">
              <thead>
                <tr>
                  <th>资产</th>
                  <th>角色</th>
                  <th>类型</th>
                  <th>声线</th>
                  <th className="st-cell-actions">锁定</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <AssetGroupRows
                    key={group.type}
                    label={group.label}
                    items={group.items}
                    lockingId={lockingId}
                    onToggleLock={(asset) => void toggleLock(asset)}
                    onLightbox={(url) => setLightbox(url)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ---------------- 场景 ---------------- */}
      <section className="st-panel">
        <div className="st-panel-head">
          <div>
            <h3 className="st-panel-title">场景</h3>
            <p className="st-panel-sub">assets · type=scene</p>
          </div>
          <div className="st-panel-right">
            <span className="cm-chip is-mono">{scenes.length} 场景</span>
          </div>
        </div>
        <div className="st-panel-body">
          {assetsError ? (
            <div className="st-hint">资产接口不可用，场景列表暂时无法显示。</div>
          ) : assetsLoading ? (
            <div className="st-loading">
              <Spinner size={14} /> 加载场景…
            </div>
          ) : scenes.length === 0 ? (
            <div className="st-empty">
              <span>暂无场景资产</span>
              <span className="st-hint">场景会随分镜生成写入资产，并支持锁定。</span>
            </div>
          ) : (
            <div className="st-assets-scene-grid">
              {scenes.map((asset) => {
                const image = assetImage(asset);
                return (
                  <article key={asset.id} className={"st-assets-scene" + (asset.locked ? " is-locked" : "")}>
                    {image ? (
                      <button type="button" className="st-assets-scene-img" onClick={() => setLightbox(image)} title="点击放大">
                        <img src={mediaUrl(image)} alt={asset.name} loading="lazy" />
                      </button>
                    ) : (
                      <div className="st-assets-scene-img is-empty">
                        <IconImage size={20} />
                        <span>暂无场景图</span>
                      </div>
                    )}
                    <div className="st-assets-scene-body">
                      <div className="st-assets-scene-name">
                        {asset.name}
                        {asset.locked && <IconLock size={12} />}
                      </div>
                      <div className="st-assets-scene-sub">
                        {asset.title || "场景"} · <span className="cm-mono">{asset.id}</span>
                      </div>
                      <div className="st-assets-scene-actions">
                        <span className="st-tag is-slate">{ASSET_ROLE_TEXT[asset.role] || asset.role}</span>
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm"
                          onClick={() => void toggleLock(asset)}
                          disabled={lockingId === asset.id}
                        >
                          {lockingId === asset.id ? <Spinner size={12} /> : asset.locked ? <IconUnlock size={12} /> : <IconLock size={12} />}
                          {asset.locked ? "解锁" : "锁定"}
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </section>

      {openCharacter && openConcept && (
        <CharacterDrawer
          character={openCharacter}
          concept={openConcept}
          conceptLoading={conceptLoading}
          conceptError={conceptError}
          job={charJobs[openCharacter.id]}
          modes={genModes}
          style={genStyle}
          onToggleMode={toggleMode}
          onStyleChange={setGenStyle}
          onGenerate={() => void runConcept(openCharacter, genModes, genStyle)}
          onClose={() => setOpenId(null)}
          onLightbox={(url) => setLightbox(url)}
        />
      )}

      {newOpen && (
        <NewCharacterDrawer projectId={project.id} onClose={() => setNewOpen(false)} onCreated={() => void handleCharacterCreated()} />
      )}

      {lightbox && (
        <div className="st-lightbox" onClick={() => setLightbox(null)} role="dialog" aria-modal="true" aria-label="图片预览">
          <img src={mediaUrl(lightbox)} alt="图片预览" onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 固定资产表格分组（分组行 + 资产行）
// ---------------------------------------------------------------------------
function AssetGroupRows({
  label,
  items,
  lockingId,
  onToggleLock,
  onLightbox,
}: {
  label: string;
  items: Asset[];
  lockingId: string | null;
  onToggleLock: (asset: Asset) => void;
  onLightbox: (url: string) => void;
}) {
  return (
    <>
      <tr className="st-assets-group-row">
        <td colSpan={5}>
          <span className="st-assets-group-label">{label}</span>
          <span className="st-assets-group-count cm-mono">{items.length}</span>
        </td>
      </tr>
      {items.map((asset) => {
        const image = assetImage(asset);
        const busy = lockingId === asset.id;
        return (
          <tr key={asset.id} className={asset.locked ? "is-locked" : undefined}>
            <td>
              <div className="st-assets-asset-cell">
                {image ? (
                  <button type="button" className="st-assets-thumb" onClick={() => onLightbox(image)} title="查看图片">
                    <img src={mediaUrl(image)} alt={asset.name} loading="lazy" />
                  </button>
                ) : (
                  <span className="st-assets-thumb is-empty" aria-hidden="true">
                    <IconImage size={14} />
                  </span>
                )}
                <span className="st-assets-asset-name">
                  <b>{asset.name}</b>
                  <small>{asset.title || "—"}</small>
                </span>
              </div>
            </td>
            <td>{ASSET_ROLE_TEXT[asset.role] || asset.role}</td>
            <td>
              <span className="st-tag is-slate">{TYPE_TEXT[asset.type] || asset.type}</span>
            </td>
            <td className="cm-mono">{asset.voice || "—"}</td>
            <td className="st-cell-actions">
              <button
                type="button"
                className={"cm-btn cm-btn-sm st-assets-lock-btn" + (asset.locked ? " is-locked" : "")}
                onClick={() => onToggleLock(asset)}
                disabled={busy}
              >
                {busy ? <Spinner size={12} /> : asset.locked ? <IconUnlock size={12} /> : <IconLock size={12} />}
                {asset.locked ? "解锁" : "锁定"}
              </button>
            </td>
          </tr>
        );
      })}
    </>
  );
}
