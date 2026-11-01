// V14 Project Studio · 02 资产库（ASSETS ROOM）
// 角色工坊升级：列表筛选/搜索/排序 + 角色卡信息升级 + 角色工作台抽屉
//（概念图 tabs / 生成控制 / H3 动画 / 锁定一致性 / 资料编辑）+ 新建角色流程。
// 另外保留固定资产（assets 分组表格）+ 场景卡片 + 图片 lightbox。
// 设计与共享元件见 styles/studio.css；本页专属样式在 styles/studio-a.css（st-assets-*）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Asset, Character, CharacterSpec, ConceptFiles, Job } from "../../types";
import type { StudioTabProps } from "../../lib/studio";
import {
  ASSET_ROLE_TEXT,
  errorText,
  fmtDateTime,
  isActiveJob,
  mediaUrl,
  relativeTime,
  studioApi,
  toneOfStatus,
  waitJob,
} from "../../lib/studio";
import { ApiError } from "../../lib/api";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClock,
  IconClose,
  IconEdit,
  IconImage,
  IconLock,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSpark,
  IconUnlock,
  IconUser,
  IconVideo,
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

const STYLE_FAMILY: Record<string, string> = {
  "3d_toon": "3D",
  "3d_cel": "3D",
  "2d_cel": "2D",
  "2d_paint": "2D",
  anime: "ANIME",
  guoman: "GUOMAN",
};

const GENDER_TEXT: Record<string, string> = { male: "男", female: "女", neutral: "中性" };
const HAIR_TEXT: Record<string, string> = {
  short_swept: "短碎盖",
  spiky: "刺猬头",
  bob: "波波头",
  twin_tail: "双马尾",
  ponytail: "马尾",
  long_straight: "黑长直",
  bald: "光头",
  bun: "丸子头",
};
const OUTFIT_TEXT: Record<string, string> = {
  suit: "西装",
  hoodie: "连帽卫衣",
  jacket: "夹克",
  shirt: "衬衫",
  dress: "连衣裙",
  coat: "风衣",
};
const ACCESSORY_TEXT: Record<string, string> = {
  glasses: "眼镜",
  watch: "腕表",
  headphones: "耳机",
  earring: "耳饰",
  none: "无",
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
  { value: "sheet", label: "联系表" },
];

const CONCEPT_STYLES: { value: string; label: string }[] = [
  { value: "anime", label: "Anime 日系（animagine）" },
  { value: "illustrious", label: "Illustrious 二次元插画" },
  { value: "guoman", label: "Guoman 国漫" },
];

const DEFAULT_CONCEPT_MODES = ["portrait", "three_view", "expressions"];

const THREE_VIEW_TEXT: Record<string, string> = { front: "正面", side: "侧面", three_quarter: "四分之三" };
const EXPRESSION_TEXT: Record<string, string> = { neutral: "平静", happy: "开心", surprised: "惊讶", thinking: "思考" };

type CharacterFilterKey = "all" | "locked" | "concept" | "todo";

const CHARACTER_FILTERS: { key: CharacterFilterKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "locked", label: "已锁定" },
  { key: "concept", label: "有概念图" },
  { key: "todo", label: "待生成" },
];

type CharacterSortKey = "recent" | "name";

const SORT_OPTIONS: { value: CharacterSortKey; label: string }[] = [
  { value: "recent", label: "排序：最近创建" },
  { value: "name", label: "排序：名字" },
];

type WorkspaceTabKey = "concept" | "generate" | "h3" | "profile";

const WORKSPACE_TABS: { key: WorkspaceTabKey; label: string }[] = [
  { key: "concept", label: "概念图" },
  { key: "generate", label: "生成" },
  { key: "h3", label: "H3 动画" },
  { key: "profile", label: "角色资料" },
];

type ConceptGroupKey = "portrait" | "three_view" | "expressions" | "sheet";

const CONCEPT_TAB_OPTIONS: { key: ConceptGroupKey; label: string }[] = [
  { key: "portrait", label: "立绘" },
  { key: "three_view", label: "三视图" },
  { key: "expressions", label: "表情" },
  { key: "sheet", label: "联系表" },
];

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

function styleFamily(style?: Character["style"] | null): string {
  const primary = styleList(style)[0] || "";
  return STYLE_FAMILY[primary] || (primary ? primary.toUpperCase() : "—");
}

function toneClass(tone: "gold" | "jade" | "red" | "slate"): string {
  return "is-" + tone;
}

function voiceLabel(voice?: string | null): string {
  if (!voice) return "未分配声线";
  const option = VOICE_OPTIONS.find((item) => item.value === voice);
  return option ? option.label : voice;
}

function normalizeAccent(color?: string | null): string {
  return /^#[0-9a-fA-F]{6}$/.test(String(color || "")) ? String(color) : "#F5C518";
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
  group: ConceptGroupKey;
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
  if (concept.sheet) tiles.push({ key: "sheet", label: "联系表 / 设定总览", url: concept.sheet, group: "sheet" });
  return tiles;
}

interface ConceptCompleteness {
  portrait: boolean;
  threeView: number;
  expressions: number;
  sheet: boolean;
  /** 立绘 / 三视图 / 表情 三类完成数（联系表不计入） */
  score: number;
}

function conceptCompleteness(concept?: ConceptFiles | null): ConceptCompleteness {
  const portrait = !!concept?.portrait;
  const three = concept?.three_view;
  const threeView = Array.isArray(three) ? three.filter(Boolean).length : three ? Object.keys(three).length : 0;
  const expressions = concept?.expressions ? Object.keys(concept.expressions).filter((key) => !!concept.expressions?.[key]).length : 0;
  const sheet = !!concept?.sheet;
  const score = [portrait, threeView > 0, expressions > 0].filter(Boolean).length;
  return { portrait, threeView, expressions, sheet, score };
}

function metaString(meta: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = meta?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** 上次生成时间：优先 /concepts meta.created_at，其次 meta.updated_at */
function conceptUpdatedAt(concept?: ConceptFiles | null): string | null {
  return metaString(concept?.meta, "created_at") || metaString(concept?.meta, "updated_at");
}

function assetImage(asset: Asset): string | null {
  const images = asset.images;
  if (!images || !images.length) return null;
  const first = images.find((item) => typeof item === "string" && item.trim().length > 0);
  return first || null;
}

interface CharacterProfileForm {
  name: string;
  title: string;
  persona: string;
  voice: string;
  accent: string;
}

function profileFormOf(character: Character): CharacterProfileForm {
  return {
    name: character.name || "",
    title: character.title || "",
    persona: character.persona || "",
    voice: character.voice || VOICE_OPTIONS[0].value,
    accent: normalizeAccent(character.accent_color),
  };
}

function specChipsOf(character: Character): string[] {
  const appearance = (character.spec as CharacterSpec | null | undefined)?.appearance;
  const chips: string[] = [];
  const styles = styleList(character.style);
  if (styles.length) chips.push("风格 " + styles.map((key) => STYLE_TEXT[key] || key).join(" / "));
  if (appearance?.gender) chips.push("性别 " + (GENDER_TEXT[appearance.gender] || appearance.gender));
  if (appearance?.age) chips.push("年龄 " + appearance.age);
  if (appearance?.hair_style) chips.push("发型 " + (HAIR_TEXT[appearance.hair_style] || appearance.hair_style));
  if (appearance?.hair_color) chips.push("发色 " + appearance.hair_color);
  if (appearance?.outfit?.type) chips.push("服装 " + (OUTFIT_TEXT[appearance.outfit.type] || appearance.outfit.type));
  if (appearance?.accessories && appearance.accessories.length) {
    chips.push("配饰 " + appearance.accessories.map((key) => ACCESSORY_TEXT[key] || key).join(" / "));
  }
  if (appearance?.marks && appearance.marks.length) chips.push("标记 " + appearance.marks.join(" / "));
  return chips.length ? chips : ["未记录 spec 外观参数"];
}

// ---------------------------------------------------------------------------
// 角色卡（V14：风格 badge / 概念完成度 / 上次生成 / hover 快捷操作）
// ---------------------------------------------------------------------------
function CharacterCard({
  character,
  portrait,
  asset,
  job,
  locking,
  completeness,
  lastGenerated,
  h3Ready,
  onOpen,
  onGenerate,
  onToggleLock,
}: {
  character: Character;
  portrait: string | null;
  asset: Asset | null;
  job?: Job;
  locking: boolean;
  completeness: ConceptCompleteness;
  lastGenerated: string | null;
  h3Ready: boolean;
  onOpen: () => void;
  onGenerate: () => void;
  onToggleLock: () => void;
}) {
  const busy = isActiveJob(job);
  const progress = job ? Math.max(0, Math.min(100, Number(job.progress) || 0)) : 0;
  const locked = !!asset?.locked;
  const accent = normalizeAccent(character.accent_color);
  return (
    <article className={"st-assets-card" + (locked ? " is-locked" : "")} style={{ ["--accent" as string]: accent }}>
      <div className="st-assets-card-media">
        <button type="button" className="st-assets-card-cover" onClick={onOpen} title="打开角色工作台">
          {portrait ? (
            <img src={mediaUrl(portrait)} alt={character.name} loading="lazy" />
          ) : (
            <span className="st-avatar-fallback">{firstChar(character.name)}</span>
          )}
          <span className={"st-assets-card-status st-tag " + toneClass(toneOfStatus(character.status))}>
            {CHARACTER_STATUS_TEXT[character.status] || character.status}
          </span>
          <span className="st-assets-card-style">{styleFamily(character.style)}</span>
          {h3Ready && (
            <span className="st-assets-card-h3">
              <IconVideo size={11} /> H3
            </span>
          )}
          <span className="st-assets-card-complete" title="概念图完成度：立绘 / 三视图 / 表情">
            概念 {completeness.score}/3{completeness.sheet ? " · 联系表" : ""}
          </span>
        </button>
        <div className="st-assets-card-quick">
          <button type="button" className="st-assets-quick-btn" onClick={onOpen} title="打开概念图">
            <IconImage size={14} />
          </button>
          <button type="button" className="st-assets-quick-btn" onClick={onGenerate} disabled={busy} title="生成概念图">
            {busy ? <Spinner size={13} /> : <IconSpark size={14} />}
          </button>
          <button
            type="button"
            className="st-assets-quick-btn"
            onClick={onToggleLock}
            disabled={locking || !asset}
            title={asset ? (locked ? "解锁资产" : "锁定资产（生成时保持形象）") : "本期资产里没有该角色的对应记录"}
          >
            {locking ? <Spinner size={13} /> : locked ? <IconUnlock size={14} /> : <IconLock size={14} />}
          </button>
        </div>
      </div>

      <div className="st-assets-card-body">
        <div className="st-assets-card-head">
          <h4 className="st-assets-card-name">{character.name}</h4>
          {locked && <IconLock size={13} className="st-assets-lock-icon" />}
        </div>
        <div className="st-assets-card-title">{character.title || "未设定定位"}</div>
        <p className="st-assets-card-persona">{character.persona || "还没有填写人设。"}</p>
        <div className="st-assets-card-meta2">
          <span className="st-assets-card-meta-item" title="声线">
            <IconVideo size={11} />
            <span>{voiceLabel(character.voice)}</span>
          </span>
          <span className="st-assets-card-meta-item" title="上次生成概念图">
            <IconClock size={11} />
            <span>{lastGenerated ? relativeTime(lastGenerated) : "未生成"}</span>
          </span>
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
          <IconImage size={12} />
          角色工坊
        </button>
        <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={onGenerate} disabled={busy}>
          {busy ? <Spinner size={12} /> : <IconSpark size={12} />}
          生成概念
        </button>
        <button
          type="button"
          className="cm-btn cm-btn-sm"
          onClick={onToggleLock}
          disabled={locking || !asset}
          title={asset ? (locked ? "解锁资产" : "锁定资产（生成时保持形象）") : "本期资产里没有该角色的对应记录"}
        >
          {locking ? <Spinner size={12} /> : locked ? <IconUnlock size={12} /> : <IconLock size={12} />}
          {locked ? "解锁" : "锁定"}
        </button>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// 角色工作台抽屉（概念图 / 生成 / H3 动画 / 角色资料）
// ---------------------------------------------------------------------------
function CharacterWorkspaceDrawer({
  character,
  concept,
  conceptsLoading,
  conceptsError,
  asset,
  job,
  h3Job,
  h3Error,
  locking,
  settingPortrait,
  onClose,
  onRefreshConcepts,
  onSetPortrait,
  onGenerate,
  onGenerateH3,
  onToggleLock,
  onSaveProfile,
  onLightbox,
}: {
  character: Character;
  concept: ConceptFiles | null;
  conceptsLoading: boolean;
  conceptsError: string | null;
  asset: Asset | null;
  job?: Job;
  h3Job?: Job;
  h3Error: string | null;
  locking: boolean;
  settingPortrait: string | null;
  onClose: () => void;
  onRefreshConcepts: () => void;
  onSetPortrait: (url: string) => void;
  onGenerate: (modes: string[], style: string, promptExtra?: string) => void;
  onGenerateH3: () => void;
  onToggleLock: () => void;
  onSaveProfile: (body: {
    name: string;
    title: string;
    persona: string;
    voice: string;
    accent_color: string;
  }) => Promise<void>;
  onLightbox: (url: string) => void;
}) {
  const [tab, setTab] = useState<WorkspaceTabKey>("concept");
  const [conceptTab, setConceptTab] = useState<ConceptGroupKey>("portrait");
  const [modes, setModes] = useState<string[]>(DEFAULT_CONCEPT_MODES);
  const [genStyleValue, setGenStyleValue] = useState("anime");
  const [promptExtra, setPromptExtra] = useState("");
  const [form, setForm] = useState<CharacterProfileForm>(() => profileFormOf(character));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const tiles = useMemo(() => conceptTiles(concept), [concept]);
  const completeness = useMemo(() => conceptCompleteness(concept), [concept]);
  const lastGenerated = useMemo(() => conceptUpdatedAt(concept), [concept]);
  const portrait = characterPortrait(character) || concept?.portrait || null;
  const spec = character.spec as CharacterSpec | null | undefined;
  const specChips = useMemo(() => specChipsOf(character), [character]);
  const locked = !!asset?.locked;
  const accent = normalizeAccent(character.accent_color);
  const h3LocalUrl = character.h3_video_url || null;
  const h3MetasoUrl = character.h3_metaso_url || null;
  const h3Url = h3LocalUrl || h3MetasoUrl;
  const conceptBusy = !!job && isActiveJob(job);
  const h3Busy = !!h3Job && isActiveJob(h3Job);
  const conceptProgress = job ? Math.max(0, Math.min(100, Number(job.progress) || 0)) : 0;
  const h3Progress = h3Job ? Math.max(0, Math.min(100, Number(h3Job.progress) || 0)) : 0;

  // 切换角色：重置 tab / 生成参数 / 编辑表单；没有概念图时默认去「生成」
  useEffect(() => {
    setTab(conceptTiles(concept).length ? "concept" : "generate");
    setConceptTab("portrait");
    setModes(DEFAULT_CONCEPT_MODES);
    setGenStyleValue("anime");
    setPromptExtra("");
    setSaveError(null);
    setForm(profileFormOf(character));
    // 仅按角色 id 重置（concept/character 的后续刷新不打断用户操作）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character.id]);

  // 角色资料被外部更新（保存成功 / 刷新）时同步表单
  useEffect(() => {
    setForm(profileFormOf(character));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character.id, character.name, character.title, character.persona, character.voice, character.accent_color]);

  // 概念图任务结束（成功 / 失败）后自动切回「概念图」tab 查看结果
  const prevConceptJobIdRef = useRef<string | null>(job?.id ?? null);
  useEffect(() => {
    const currentJobId = job?.id ?? null;
    if (prevConceptJobIdRef.current && !currentJobId) setTab("concept");
    prevConceptJobIdRef.current = currentJobId;
  }, [job?.id]);

  const toggleMode = (mode: string) => {
    setModes((prev) => {
      if (prev.includes(mode)) {
        if (prev.length <= 1) return prev;
        return prev.filter((item) => item !== mode);
      }
      return [...prev, mode];
    });
  };

  const saveProfile = async () => {
    const name = form.name.trim();
    if (!name) {
      setSaveError("角色名字不能为空");
      return;
    }
    if (!/^#[0-9a-fA-F]{6}$/.test(form.accent)) {
      setSaveError("强调色必须是 #RRGGBB 格式");
      return;
    }
    if (!form.voice.trim()) {
      setSaveError("请选择声线");
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onSaveProfile({
        name,
        title: form.title.trim(),
        persona: form.persona.trim(),
        voice: form.voice,
        accent_color: form.accent,
      });
    } catch (err) {
      setSaveError("保存角色资料失败：" + errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const currentTiles = tiles.filter((tile) => tile.group === conceptTab);

  return (
    <>
      <div className="st-drawer-mask" onClick={onClose} />
      <aside
        className="st-drawer st-assets-drawer is-workspace"
        role="dialog"
        aria-modal="true"
        aria-label={"角色工作台 · " + character.name}
      >
        <div className="st-drawer-head">
          <div className="st-assets-drawer-head-main">
            <h3 className="st-drawer-title">{character.name}</h3>
            <div className="st-drawer-sub">
              角色工作台 · <span className="cm-mono">{character.id}</span>
            </div>
          </div>
          <span className={"st-tag " + toneClass(toneOfStatus(character.status))}>
            <i className="cm-status-dot" style={{ background: "currentColor" }} />
            {CHARACTER_STATUS_TEXT[character.status] || character.status}
          </span>
          <button type="button" className="st-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={15} />
          </button>
        </div>

        <div className="st-drawer-body">
          {/* ---- Hero：头像 / 基本信息 / 锁定 ---- */}
          <div className="st-assets-hero st-assets-ws-hero">
            <div className="st-assets-hero-avatar" style={{ borderColor: accent + "66" }}>
              {portrait ? (
                <img src={mediaUrl(portrait)} alt={character.name} />
              ) : (
                <span className="st-avatar-fallback">{firstChar(character.name)}</span>
              )}
            </div>
            <div className="st-assets-ws-info">
              <div className="st-assets-ws-name-row">
                <span className="st-assets-ws-name">{character.name}</span>
                <span className="st-assets-ws-style" title={styleText(character.style)}>
                  {styleFamily(character.style)}
                </span>
                {h3Url && <span className="st-tag is-jade">H3</span>}
                {locked && (
                  <span className="st-tag is-gold">
                    <IconLock size={10} /> 已锁定
                  </span>
                )}
              </div>
              <div className="st-assets-ws-title">{character.title || "未设定定位"}</div>
              <p className="st-assets-ws-persona">{character.persona || "还没有填写人设。"}</p>
              <div className="st-assets-ws-tags">
                <span className="st-tag is-slate">
                  <IconVideo size={11} /> {voiceLabel(character.voice)}
                </span>
                <span className="st-tag is-slate">
                  强调色
                  <i className="st-assets-accent-dot" style={{ background: accent }} />
                  <span className="cm-mono">{character.accent_color || "—"}</span>
                </span>
                <span className="st-tag is-slate">{styleText(character.style)}</span>
                <span className="st-tag is-slate">
                  上次生成
                  <span className="cm-mono" title={lastGenerated ? fmtDateTime(lastGenerated) : "—"}>
                    {lastGenerated ? relativeTime(lastGenerated) : "未生成"}
                  </span>
                </span>
              </div>
              <div className="st-assets-ws-complete">
                <span className={"st-assets-ws-cmp" + (completeness.portrait ? " is-on" : "")}>
                  立绘 {completeness.portrait ? "✓" : "—"}
                </span>
                <span className={"st-assets-ws-cmp" + (completeness.threeView ? " is-on" : "")}>
                  三视图 {completeness.threeView}/3
                </span>
                <span className={"st-assets-ws-cmp" + (completeness.expressions ? " is-on" : "")}>
                  表情 {completeness.expressions}/4
                </span>
                <span className={"st-assets-ws-cmp" + (completeness.sheet ? " is-on" : "")}>
                  联系表 {completeness.sheet ? "✓" : "—"}
                </span>
              </div>
              <div className="st-assets-ws-actions">
                <button
                  type="button"
                  className={"cm-btn cm-btn-sm" + (locked ? " st-assets-lock-btn is-locked" : "")}
                  onClick={onToggleLock}
                  disabled={locking || !asset}
                  title={asset ? (locked ? "解锁资产" : "锁定资产") : "本期资产里没有该角色的对应记录"}
                >
                  {locking ? <Spinner size={12} /> : locked ? <IconUnlock size={13} /> : <IconLock size={13} />}
                  {locked ? "已锁定 · 解锁" : "锁定形象与声线"}
                </button>
                <span className="st-hint st-assets-ws-lock-hint">
                  {asset
                    ? locked
                      ? "已锁定：后续概念图 / 分镜生成会保持该角色形象与声线一致。"
                      : "未锁定：生成时可能被新产物覆盖，建议定稿后锁定。"
                    : "本期资产里没有该角色的对应记录（角色属于其它期），无法锁定。"}
                </span>
              </div>
            </div>
          </div>

          {/* ---- 主 tabs ---- */}
          <div className="st-assets-ws-tabs st-seg">
            {WORKSPACE_TABS.map((item) => (
              <button key={item.key} type="button" className={tab === item.key ? "is-active" : ""} onClick={() => setTab(item.key)}>
                {item.label}
                {item.key === "concept" && <span className="st-assets-ws-tab-count">{tiles.length}</span>}
                {item.key === "h3" && h3Busy && <i className="cm-status-dot is-running" />}
              </button>
            ))}
          </div>

          <div className="st-assets-ws-body">
            {/* ---- 1. 概念图 ---- */}
            {tab === "concept" && (
              <section className="st-assets-ws-section">
                <div className="st-assets-ws-section-head">
                  <h4 className="st-assets-ws-section-title">概念图</h4>
                  <div className="st-assets-ws-section-right">
                    {conceptsLoading && <Spinner size={12} />}
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={onRefreshConcepts} disabled={conceptsLoading}>
                      <IconRefresh size={12} /> 刷新
                    </button>
                  </div>
                </div>

                {conceptsError ? (
                  <div className="st-error">
                    <IconAlert size={15} />
                    <span>{conceptsError}</span>
                    <button type="button" className="cm-btn cm-btn-sm" onClick={onRefreshConcepts}>
                      重试
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="st-assets-ws-subtabs">
                      {CONCEPT_TAB_OPTIONS.map((option) => {
                        const count = tiles.filter((tile) => tile.group === option.key).length;
                        return (
                          <button
                            key={option.key}
                            type="button"
                            className={"st-assets-ws-subtab" + (conceptTab === option.key ? " is-active" : "")}
                            onClick={() => setConceptTab(option.key)}
                          >
                            {option.label}
                            <span className="st-assets-ws-subtab-count">{count}</span>
                          </button>
                        );
                      })}
                    </div>

                    {currentTiles.length === 0 ? (
                      <div className="st-empty st-assets-empty-sm">
                        <span>「{CONCEPT_TAB_OPTIONS.find((item) => item.key === conceptTab)?.label}」还没有图</span>
                        <span className="st-hint">在「生成」tab 勾选该模式后生成。</span>
                        <button type="button" className="cm-btn cm-btn-sm" onClick={() => setTab("generate")}>
                          去生成
                        </button>
                      </div>
                    ) : (
                      <div className="st-assets-ws-grid">
                        {currentTiles.map((tile) => (
                          <article key={tile.key} className="st-assets-ws-tile">
                            <button type="button" className="st-assets-ws-tile-img" onClick={() => onLightbox(tile.url)} title="点击放大">
                              <img src={mediaUrl(tile.url)} alt={tile.label} loading="lazy" />
                            </button>
                            <div className="st-assets-ws-tile-foot">
                              <span className="st-assets-ws-tile-label" title={tile.label}>
                                {tile.label}
                              </span>
                              <button
                                type="button"
                                className="st-assets-ws-tile-btn"
                                onClick={() => onSetPortrait(tile.url)}
                                disabled={settingPortrait !== null}
                                title="用这张图覆盖角色头像（POST /portrait/from-concept）"
                              >
                                {settingPortrait === tile.url ? <Spinner size={11} /> : <IconUser size={11} />}
                                设为头像
                              </button>
                            </div>
                          </article>
                        ))}
                      </div>
                    )}

                    <p className="st-hint st-assets-ws-note">
                      共 {tiles.length} 张（立绘 {completeness.portrait ? 1 : 0} · 三视图 {completeness.threeView} · 表情{" "}
                      {completeness.expressions} · 联系表 {completeness.sheet ? 1 : 0}）；点「设为头像」会把该图复制为角色立绘
                      （portrait_url），资产与分镜同步使用。
                    </p>
                  </>
                )}
              </section>
            )}

            {/* ---- 2. 生成控制 ---- */}
            {tab === "generate" && (
              <section className="st-assets-ws-section">
                <div className="st-assets-ws-section-head">
                  <h4 className="st-assets-ws-section-title">生成概念图</h4>
                  <div className="st-assets-ws-section-right">
                    <span className="st-hint">SDXL · 本地 ComfyUI</span>
                  </div>
                </div>

                <div className="st-field">
                  <span className="st-label">生成模式（多选，至少 1 个）</span>
                  <div className="st-assets-modes">
                    {CONCEPT_MODES.map((item) => (
                      <label key={item.value} className={"st-assets-check" + (modes.includes(item.value) ? " is-on" : "")}>
                        <input type="checkbox" checked={modes.includes(item.value)} onChange={() => toggleMode(item.value)} />
                        <span>{item.label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="st-assets-ws-gen-row">
                  <div className="st-field">
                    <span className="st-label">成图风格（后端 style）</span>
                    <select className="cm-select" value={genStyleValue} onChange={(event) => setGenStyleValue(event.target.value)}>
                      {CONCEPT_STYLES.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <span className="st-hint">支持 anime / illustrious / guoman；角色的 2D/3D 预设风格见「角色资料」。</span>
                  </div>
                  <div className="st-field">
                    <span className="st-label">附加提示词（可选）</span>
                    <input
                      className="cm-input"
                      value={promptExtra}
                      maxLength={200}
                      placeholder="例如：微笑、侧光、披风"
                      onChange={(event) => setPromptExtra(event.target.value)}
                    />
                    <span className="st-hint">会追加到 SDXL prompt（后端 prompt_extra）。</span>
                  </div>
                </div>

                <button
                  type="button"
                  className="cm-btn cm-btn-primary st-assets-block"
                  onClick={() => onGenerate(modes, genStyleValue, promptExtra.trim() || undefined)}
                  disabled={conceptBusy || !modes.length}
                >
                  {conceptBusy ? <Spinner size={14} /> : <IconSpark size={15} />}
                  {conceptBusy ? "生成中…" : `生成概念图（${modes.length} 个模式）`}
                </button>

                {job && conceptBusy && (
                  <div className="st-assets-ws-job">
                    <div className="cm-progress is-running">
                      <i style={{ width: conceptProgress + "%" }} />
                    </div>
                    <div className="st-assets-ws-job-meta">
                      <span className="cm-mono">{Math.round(conceptProgress)}%</span>
                      <span className="st-hint">{job.message || "AI 正在生成概念图…"}</span>
                      <span className="cm-mono st-hint">{job.id}</span>
                    </div>
                  </div>
                )}

                <p className="st-hint">
                  生成会覆盖同名概念图文件；完成后自动回到「概念图」tab，可预览并「设为头像」。同一角色同时只跑一个概念图任务。
                </p>
              </section>
            )}

            {/* ---- 3. H3 动画 ---- */}
            {tab === "h3" && (
              <section className="st-assets-ws-section">
                <div className="st-assets-ws-section-head">
                  <h4 className="st-assets-ws-section-title">H3 动画</h4>
                  <div className="st-assets-ws-section-right">
                    <span className="st-hint">立绘 → 动态视频</span>
                  </div>
                </div>

                {h3Error && (
                  <div className="st-error">
                    <IconAlert size={15} />
                    <span>{h3Error}</span>
                  </div>
                )}

                {h3Url ? (
                  <div className="st-assets-ws-h3">
                    <div className="st-player">
                      <video
                        controls
                        preload="metadata"
                        poster={character.h3_portrait_url ? mediaUrl(character.h3_portrait_url) : portrait ? mediaUrl(portrait) : undefined}
                        src={mediaUrl(h3Url)}
                      />
                    </div>
                    <div className="st-assets-ws-h3-meta">
                      <span className={"st-tag " + (h3LocalUrl ? "is-jade" : "is-gold")}>
                        {h3LocalUrl ? "本地 H3" : "云端 MetaSo"}
                      </span>
                      {h3LocalUrl && h3MetasoUrl && (
                        <a className="cm-btn cm-btn-sm cm-btn-ghost" href={mediaUrl(h3MetasoUrl)} target="_blank" rel="noreferrer">
                          云端测试版 ↗
                        </a>
                      )}
                      <span className="st-hint">结果已回填角色 h3_video_url，可在分镜 / 成片中使用。</span>
                    </div>
                  </div>
                ) : (
                  <div className="st-empty st-assets-empty-sm">
                    <span>还没有 H3 动画</span>
                    <span className="st-hint">用角色立绘生成一段动态视频（本地 ComfyUI H3 串行 GPU；或云端 MetaSo，按秒计费）。</span>
                  </div>
                )}

                {h3Job && h3Busy && (
                  <div className="st-assets-ws-job">
                    <div className="cm-progress is-running">
                      <i style={{ width: h3Progress + "%" }} />
                    </div>
                    <div className="st-assets-ws-job-meta">
                      <span className="cm-mono">{Math.round(h3Progress)}%</span>
                      <span className="st-hint">{h3Job.message || "H3 动画生成中…"}</span>
                      <span className="cm-mono st-hint">{h3Job.id}</span>
                    </div>
                  </div>
                )}

                <div className="st-assets-ws-h3-actions">
                  <button
                    type="button"
                    className="cm-btn cm-btn-primary st-assets-block"
                    onClick={onGenerateH3}
                    disabled={h3Busy || !portrait}
                    title={portrait ? "提交 H3 动画任务（需二次确认，昂贵）" : "先生成概念立绘"}
                  >
                    {h3Busy ? <Spinner size={14} /> : <IconVideo size={15} />}
                    {h3Busy ? "H3 生成中…" : h3Url ? "重新生成 H3 动画" : "生成 H3 动画"}
                  </button>
                  <p className="st-hint">
                    H3 由本地 ComfyUI 串行生成（GPU，耗时较长）或云端 MetaSo（按秒计费）；提交前会二次确认，同一角色同时只有一个 H3 任务。
                  </p>
                  {!portrait && <p className="st-hint">当前还没有可用立绘：先在「生成」里出立绘，再回来做 H3 动画。</p>}
                </div>
              </section>
            )}

            {/* ---- 4. 角色资料 ---- */}
            {tab === "profile" && (
              <section className="st-assets-ws-section">
                <div className="st-assets-ws-section-head">
                  <h4 className="st-assets-ws-section-title">角色资料</h4>
                  <div className="st-assets-ws-section-right">
                    <span className="st-hint">PATCH /characters/{character.id}</span>
                  </div>
                </div>

                {saveError && (
                  <div className="st-error">
                    <IconAlert size={15} />
                    <span>{saveError}</span>
                  </div>
                )}

                <div className="st-assets-ws-form-grid">
                  <div className="st-field">
                    <span className="st-label">名字 *</span>
                    <input
                      className="cm-input"
                      value={form.name}
                      maxLength={20}
                      onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                    />
                  </div>
                  <div className="st-field">
                    <span className="st-label">身份 / 定位</span>
                    <input
                      className="cm-input"
                      value={form.title}
                      maxLength={20}
                      onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                    />
                  </div>
                </div>

                <div className="st-field">
                  <span className="st-label">人设</span>
                  <textarea
                    className="cm-textarea"
                    rows={3}
                    value={form.persona}
                    maxLength={200}
                    onChange={(event) => setForm((prev) => ({ ...prev, persona: event.target.value }))}
                  />
                </div>

                <div className="st-assets-ws-form-grid">
                  <div className="st-field">
                    <span className="st-label">声线（edge-tts）</span>
                    <select className="cm-select" value={form.voice} onChange={(event) => setForm((prev) => ({ ...prev, voice: event.target.value }))}>
                      {VOICE_OPTIONS.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                      {!VOICE_OPTIONS.some((item) => item.value === form.voice) && form.voice && (
                        <option value={form.voice}>{form.voice}</option>
                      )}
                    </select>
                  </div>
                  <div className="st-field">
                    <span className="st-label">强调色</span>
                    <div className="st-assets-accent">
                      <input
                        type="color"
                        className="st-assets-color"
                        value={form.accent}
                        onChange={(event) => setForm((prev) => ({ ...prev, accent: event.target.value }))}
                        aria-label="强调色"
                      />
                      <div className="st-assets-swatches">
                        {ACCENT_SWATCHES.map((color) => (
                          <button
                            key={color}
                            type="button"
                            className={"st-assets-swatch" + (form.accent.toLowerCase() === color.toLowerCase() ? " is-on" : "")}
                            style={{ background: color }}
                            onClick={() => setForm((prev) => ({ ...prev, accent: color }))}
                            aria-label={"强调色 " + color}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="st-assets-ws-save-row">
                  <button type="button" className="cm-btn cm-btn-primary" onClick={() => void saveProfile()} disabled={saving}>
                    {saving ? <Spinner size={14} /> : <IconCheck size={15} />}
                    {saving ? "保存中…" : "保存资料"}
                  </button>
                  <button type="button" className="cm-btn" onClick={() => setForm(profileFormOf(character))} disabled={saving}>
                    重置
                  </button>
                  <span className="st-hint">保存会同步更新本期资产（名字 / 头衔 / 人设 / 声线 / 强调色）。</span>
                </div>

                <div className="st-assets-ws-spec">
                  <h5 className="st-assets-ws-spec-title">
                    <IconEdit size={13} /> 角色 spec · 外观摘要
                  </h5>
                  <div className="st-assets-ws-spec-chips">
                    {specChips.map((chip) => (
                      <span key={chip} className="st-tag is-slate cm-mono">
                        {chip}
                      </span>
                    ))}
                  </div>
                  <div className="st-assets-ws-spec-meta">
                    <span className="cm-mono">角色 ID {character.id}</span>
                    <span className="cm-mono">资产 {character.asset_id || "—"}</span>
                    <span className="cm-mono">创建 {fmtDateTime(character.created_at)}</span>
                    <span className="cm-mono">上次生成 {lastGenerated ? fmtDateTime(lastGenerated) : "—"}</span>
                    {spec?.appearance?.chibi !== undefined && <span className="cm-mono">Q 版度 {spec.appearance.chibi}</span>}
                  </div>
                </div>
              </section>
            )}
          </div>
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
    if (!voice.trim()) {
      setError("请选择声线");
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
      notify(`角色「${created.name}」已创建`, "success");
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
            <span className="st-label">名字 *</span>
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
            创建后会直接打开角色工作台；角色默认挂到项目第 1 期资产，外观为现代男性（30 岁 / 短碎盖 / 西装），可在工作台里生成立绘并继续调整。
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
  const [settingPortrait, setSettingPortrait] = useState<string | null>(null);

  const [concepts, setConcepts] = useState<Record<string, ConceptFiles>>({});
  const [conceptLoading, setConceptLoading] = useState(false);
  const [conceptError, setConceptError] = useState<string | null>(null);
  const [charJobs, setCharJobs] = useState<Record<string, Job>>({});
  const charJobsRef = useRef(charJobs);
  charJobsRef.current = charJobs;
  const [h3Jobs, setH3Jobs] = useState<Record<string, Job>>({});
  const h3JobsRef = useRef(h3Jobs);
  h3JobsRef.current = h3Jobs;
  const [h3Error, setH3Error] = useState<{ cid: string; message: string } | null>(null);

  // 角色列表筛选 / 搜索 / 排序
  const [charFilter, setCharFilter] = useState<CharacterFilterKey>("all");
  const [charQuery, setCharQuery] = useState("");
  const [charSort, setCharSort] = useState<CharacterSortKey>("recent");

  // ---- 数据加载（固定资产 / 角色工坊 分别降级） ----
  const loadConceptsForAll = useCallback(async (list: Character[]) => {
    if (!list.length) return;
    const entries = await Promise.allSettled(
      list.map(async (item) => [item.id, await studioApi.characterConcepts(item.id)] as const),
    );
    setConcepts((prev) => {
      const next = { ...prev };
      entries.forEach((entry) => {
        if (entry.status === "fulfilled") next[entry.value[0]] = entry.value[1];
      });
      return next;
    });
  }, []);

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
      const safe = Array.isArray(list) ? list : [];
      setCharacters(safe);
      void loadConceptsForAll(safe);
      return true;
    } catch (err) {
      setCharactersError(errorText(err));
      return false;
    } finally {
      setCharactersLoading(false);
    }
  }, [project.id, loadConceptsForAll]);

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
    setConceptLoading(true);
    setConceptError(null);
    try {
      const data = await studioApi.characterConcepts(cid);
      setConcepts((prev) => ({ ...prev, [cid]: data }));
      return data;
    } catch (err) {
      setConceptError("概念图读取失败：" + errorText(err));
      return null;
    } finally {
      setConceptLoading(false);
    }
  }, []);

  const runConcept = useCallback(
    async (character: Character, modes: string[], style: string, promptExtra?: string) => {
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
          prompt_extra: promptExtra && promptExtra.trim() ? promptExtra.trim() : undefined,
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

  // ---- H3 动画（立绘 → 视频；昂贵，严格二次确认；本次不做真实触发测试） ----
  const generateH3 = useCallback(
    async (character: Character) => {
      if (isActiveJob(h3JobsRef.current[character.id])) {
        notify(`「${character.name}」的 H3 任务还在进行中`, "info");
        return;
      }
      const image = characterPortrait(character);
      if (!image) {
        notify("先生成概念图 / 立绘，再用立绘生成 H3 动画", "error");
        return;
      }
      const confirmed = window.confirm(
        `为「${character.name}」生成 H3 动画？\n\n` +
          "会用角色立绘驱动一段动态视频：本地 ComfyUI H3（GPU 串行，耗时较长）或云端 MetaSo（按秒计费），以服务端 settings 为准。\n" +
          "同一角色同时只会有一个 H3 任务；已有结果时重新生成会覆盖 h3_video_url。\n\n确认提交？",
      );
      if (!confirmed) return;
      setH3Error(null);
      try {
        const job = await studioApi.h3Animate(character.id, { image, prompt: character.persona || undefined }, image);
        setH3Jobs((prev) => ({ ...prev, [character.id]: job }));
        notify(`已提交「${character.name}」H3 动画任务`, "success");
        const final = await waitJob(job.id, (tick) => {
          setH3Jobs((prev) => ({ ...prev, [character.id]: tick }));
        });
        if (final.status === "done") {
          notify(`「${character.name}」H3 动画已生成`, "success");
          await loadCharacters();
        } else if (final.status === "failed" || final.status === "canceled") {
          const message = "H3 动画生成失败：" + (final.error || final.message || "未知原因");
          setH3Error({ cid: character.id, message });
          notify(message, "error");
          await loadCharacters();
        } else {
          notify("H3 任务仍未结束，可稍后在「生成台」查看进度", "info");
        }
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0;
        const message =
          status === 404 || status === 405
            ? "H3 接口未就绪：后端未提供 /characters/{cid}/h3-animate，请稍后再试"
            : "H3 动画提交失败：" + errorText(err);
        setH3Error({ cid: character.id, message });
        notify(message, "error");
      } finally {
        setH3Jobs((prev) => {
          const next = { ...prev };
          delete next[character.id];
          return next;
        });
      }
    },
    [loadCharacters, notify],
  );

  // ---- 设为头像（POST /characters/{cid}/portrait/from-concept） ----
  const setPortrait = useCallback(
    async (url: string) => {
      if (!openId) return;
      setSettingPortrait(url);
      try {
        await studioApi.setPortraitFromConcept(openId, url);
        await Promise.all([loadCharacters(), loadAssets()]);
        notify("已将该概念图设为角色头像", "success");
      } catch (err) {
        notify("设为头像失败：" + errorText(err), "error");
      } finally {
        setSettingPortrait(null);
      }
    },
    [openId, loadCharacters, loadAssets, notify],
  );

  // ---- 保存角色资料（PATCH /characters/{cid}，后端会同步资产） ----
  const saveProfile = useCallback(
    async (cid: string, body: { name: string; title: string; persona: string; voice: string; accent_color: string }) => {
      const updated = await studioApi.patchCharacter(cid, body);
      setCharacters((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      await loadAssets();
      notify("角色资料已保存", "success");
    },
    [loadAssets, notify],
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

  // Esc 关闭：lightbox → 新建抽屉 → 角色工作台
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

  const conceptFor = useCallback(
    (character: Character): ConceptFiles => concepts[character.id] || conceptFromCharacter(character),
    [concepts],
  );

  const filterCounts = useMemo(() => {
    const counts: Record<CharacterFilterKey, number> = { all: characters.length, locked: 0, concept: 0, todo: 0 };
    characters.forEach((character) => {
      const asset = assetForCharacter(character);
      const art = hasConcept(character) || !!characterPortrait(character);
      if (asset?.locked) counts.locked += 1;
      if (art) counts.concept += 1;
      else counts.todo += 1;
    });
    return counts;
  }, [characters, assetForCharacter]);

  const filteredCharacters = useMemo(() => {
    const query = charQuery.trim().toLowerCase();
    const list = characters.filter((character) => {
      const asset = assetForCharacter(character);
      const art = hasConcept(character) || !!characterPortrait(character);
      if (charFilter === "locked" && !asset?.locked) return false;
      if (charFilter === "concept" && !art) return false;
      if (charFilter === "todo" && art) return false;
      if (query) {
        const haystack = [character.name, character.title, character.persona, character.voice]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
    return list.sort((a, b) => {
      if (charSort === "name") return String(a.name || "").localeCompare(String(b.name || ""), "zh-Hans-CN");
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });
  }, [characters, charFilter, charQuery, charSort, assetForCharacter]);

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

  const handleCharacterCreated = useCallback(
    async (created: Character) => {
      setNewOpen(false);
      await Promise.all([loadCharacters(), loadAssets()]);
      setOpenId(created.id);
      notify(`「${created.name}」已加入角色工坊，去「生成」生成立绘`, "info");
    },
    [loadCharacters, loadAssets, notify],
  );

  const openCharacterMeta = openCharacter ? conceptFor(openCharacter) : null;

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
            <p className="st-panel-sub">角色卡 / 概念图 / H3 动画 / 资料与锁定</p>
          </div>
          <div className="st-panel-right">
            {charactersLoading && <Spinner size={13} />}
            <span className="cm-chip is-mono">
              {filteredCharacters.length}/{characters.length} 角色
            </span>
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
          ) : charactersLoading && characters.length === 0 ? (
            <div className="st-loading">
              <Spinner size={14} /> 加载角色工坊…
            </div>
          ) : (
            <>
              <div className="st-assets-toolbar">
                <div className="st-seg st-assets-filter">
                  {CHARACTER_FILTERS.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      className={charFilter === item.key ? "is-active" : ""}
                      onClick={() => setCharFilter(item.key)}
                    >
                      {item.label}
                      <span className="st-assets-filter-count">{filterCounts[item.key]}</span>
                    </button>
                  ))}
                </div>
                <div className="st-search st-assets-search">
                  <IconSearch size={14} />
                  <input
                    value={charQuery}
                    placeholder="搜索名字 / 头衔 / 人设 / 声线"
                    onChange={(event) => setCharQuery(event.target.value)}
                    aria-label="搜索角色"
                  />
                  {charQuery && (
                    <button type="button" className="st-assets-search-clear" onClick={() => setCharQuery("")} aria-label="清空搜索">
                      <IconClose size={12} />
                    </button>
                  )}
                </div>
                <select
                  className="cm-select st-assets-sort"
                  value={charSort}
                  onChange={(event) => setCharSort(event.target.value as CharacterSortKey)}
                  aria-label="排序"
                >
                  {SORT_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>

              {characters.length === 0 ? (
                <div className="st-empty">
                  <span>还没有角色</span>
                  <span className="st-hint">点右上「新建角色」创建第一个角色，创建后会直接打开角色工作台。</span>
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => setNewOpen(true)}>
                    <IconPlus size={13} /> 新建角色
                  </button>
                </div>
              ) : filteredCharacters.length === 0 ? (
                <div className="st-empty">
                  <span>没有匹配的角色</span>
                  <span className="st-hint">
                    {charFilter !== "all" || charQuery
                      ? "试试切换筛选/清空搜索。"
                      : "当前筛选条件下没有角色。"}
                  </span>
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm"
                    onClick={() => {
                      setCharFilter("all");
                      setCharQuery("");
                    }}
                  >
                    清除筛选
                  </button>
                </div>
              ) : (
                <div className="st-assets-grid">
                  {filteredCharacters.map((character) => {
                    const concept = conceptFor(character);
                    const asset = assetForCharacter(character);
                    return (
                      <CharacterCard
                        key={character.id}
                        character={character}
                        portrait={characterPortrait(character)}
                        asset={asset}
                        job={charJobs[character.id]}
                        locking={lockingId !== null && asset?.id === lockingId}
                        completeness={conceptCompleteness(concept)}
                        lastGenerated={conceptUpdatedAt(concept)}
                        h3Ready={!!(character.h3_video_url || character.h3_metaso_url)}
                        onOpen={() => setOpenId(character.id)}
                        onGenerate={() => void runConcept(character, DEFAULT_CONCEPT_MODES, "anime")}
                        onToggleLock={() => handleCharacterLock(character)}
                      />
                    );
                  })}
                  <button type="button" className="st-assets-add-card" onClick={() => setNewOpen(true)} title="新建角色">
                    <IconPlus size={22} />
                    <span className="st-assets-add-card-title">新建角色</span>
                    <span className="st-hint">创建后直接进入角色工作台</span>
                  </button>
                </div>
              )}
            </>
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

      {openCharacter && openCharacterMeta && (
        <CharacterWorkspaceDrawer
          character={openCharacter}
          concept={openCharacterMeta}
          conceptsLoading={conceptLoading}
          conceptsError={conceptError}
          asset={assetForCharacter(openCharacter)}
          job={charJobs[openCharacter.id]}
          h3Job={h3Jobs[openCharacter.id]}
          h3Error={h3Error && h3Error.cid === openCharacter.id ? h3Error.message : null}
          locking={lockingId !== null && assetForCharacter(openCharacter)?.id === lockingId}
          settingPortrait={settingPortrait}
          onClose={() => setOpenId(null)}
          onRefreshConcepts={() => void refreshConcepts(openCharacter.id)}
          onSetPortrait={(url) => void setPortrait(url)}
          onGenerate={(modes, style, promptExtra) => void runConcept(openCharacter, modes, style, promptExtra)}
          onGenerateH3={() => void generateH3(openCharacter)}
          onToggleLock={() => handleCharacterLock(openCharacter)}
          onSaveProfile={(body) => saveProfile(openCharacter.id, body)}
          onLightbox={(url) => setLightbox(url)}
        />
      )}

      {newOpen && (
        <NewCharacterDrawer
          projectId={project.id}
          onClose={() => setNewOpen(false)}
          onCreated={(character) => void handleCharacterCreated(character)}
        />
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
