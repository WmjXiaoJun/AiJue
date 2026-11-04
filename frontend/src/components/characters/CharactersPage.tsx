// V18 · 角色与声音（#/characters）：32 角色网格 + 声音 chip/试听 + 详情抽屉（资料/外观/概念图 + 音色库）
// 数据：GET /api/v5/catalog（characters/templates）、GET /api/v5/characters/{id}（合同：persona/era/appearance/art/voice）、
//       GET /api/v5/voices（24 preset）、GET/PUT /api/v5/voice/{character}、POST /api/v5/voice/{character}/preview。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import type { V5CatalogResponse, V5CharacterSummary } from "../../lib/v5";
import { mediaSrc, characterArtUrl, characterPreviewUrl, v5api } from "../../lib/v5";
import { auditionApi, CreationError, creationApi, creationErrorMessage, dialogueApi, isAuditionActive, isDialogueActive, isPromoActive, promoApi, showcaseApi, splitPackageApi } from "../../lib/creation";
import type { AuditionBackground, AuditionCamera, AuditionJob, AuditionStyle, DialogueJob, DialogueSpeaker, PromoJob, PromoSceneInput, ShowcasePackage, SplitPackage } from "../../lib/creation";
import { downloadUrlAsBlob } from "../../lib/download";
import type { CharacterAssets } from "../../lib/creation";
import type { CharacterContract, VoicePreset } from "../../lib/voice";
import { fetchCharacterContract, voiceApi } from "../../lib/voice";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClose,
  IconDownload,
  IconExternal,
  IconFilm,
  IconGlobe,
  IconGrid,
  IconImage,
  IconPlay,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconSettings,
  IconSpark,
  IconUser,
  IconUsers,
  IconVolume,
} from "../icons";
import { VoicePicker } from "../voice/VoicePicker";
import { CreateCharacterDrawer } from "./CreateCharacterDrawer";
import { AppShell } from "../shell/AppShell";
import "../../styles/cinema.css";
import "../../styles/v18.css";

interface Props {
  onExit: () => void;
  onOpenPipeline: () => void;
  onOpenCinema: () => void;
  onOpenSettings: () => void;
}

const APPEARANCE_LABELS: Record<string, string> = {
  gender: "性别",
  age: "年龄",
  skin: "肤色",
  hair: "发色",
  hair_style: "发型",
  eye: "眼睛",
  brow: "眉型",
  beard: "胡须",
  hat: "帽子",
  outfit: "服装",
  accessories: "配饰",
  chibi: "Q 版比例",
  eye_style: "眼型",
  head_ratio: "头身比",
};

function fmtAuditionSize(bytes: number): string {
  if (!bytes) return "—";
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  return Math.round(bytes / 1024) + " KB";
}

function fmtElapsed(sec: number): string {
  const total = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m${String(s).padStart(2, "0")}s` : `${s}s`;
}

function fmtAppearanceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.map((item) => String(item)).join(" / ") : "—";
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    const parts = [rec.type, rec.main, rec.accent, rec.hat].filter((part) => typeof part === "string" && part);
    return parts.length ? parts.join(" · ") : "—";
  }
  return String(value) || "—";
}

/** V28：合同/接口里的素材路径（assets/v5/... 或 /media/v5/...）→ 可访问 /media URL */
function artMediaUrl(path?: string): string {
  if (!path) return "";
  if (/^(https?:|blob:|data:)/i.test(path)) return path;
  const marker = "v5/characters";
  if (!path.startsWith("/media") && path.includes(marker)) {
    return mediaSrc("/media/" + path.slice(path.indexOf(marker)));
  }
  if (path.startsWith("assets/")) return mediaSrc("/media/" + path.slice("assets/".length));
  return mediaSrc(path);
}

/** 合同来源路径过长（绝对路径）→ 展示友好来源 */
function sourceLabel(source: string): string {
  if (!source) return "—";
  if (source.includes("custom")) return "custom 覆盖";
  if (source.includes("contract")) return "内建合同";
  return source;
}

/** 立绘多级回退：角色合同 art.portrait → /media/v5/characters/<id>/portrait.png → 程序化预览 → 首字 */
function CharacterPortrait({
  characterId,
  label,
  portrait,
  bust,
}: {
  characterId: string;
  label: string;
  portrait?: string;
  /** V24：重新生成概念图后的 cache-bust 时间戳 */
  bust?: number;
}) {
  const sources = useMemo(() => {
    const suffix = bust ? (portrait && portrait.includes("?") ? "&" : "?") + "t=" + bust : "";
    return [
      portrait ? mediaSrc(portrait) + suffix : "",
      characterArtUrl(characterId, "portrait.png", bust),
      characterPreviewUrl(characterId),
    ].filter(Boolean);
  }, [bust, characterId, portrait]);
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [characterId, bust]);
  if (!sources.length || index >= sources.length) return <span className="v18-char-initial">{label.slice(0, 1)}</span>;
  return <img src={sources[index]} alt={label} loading="lazy" onError={() => setIndex((prev) => prev + 1)} />;
}

/** V30：立绘版本 */
type PortraitVersion = "anime" | "stylized_25d" | "realistic";

const AUDITION_STYLES: { id: AuditionStyle; label: string }[] = [
  { id: "anime", label: "动漫" },
  { id: "stylized_25d", label: "2.5D" },
  { id: "realistic", label: "写实" },
];

const AUDITION_BACKGROUNDS: { id: AuditionBackground; label: string }[] = [
  { id: "studio", label: "棚拍" },
  { id: "radio", label: "深夜电台" },
  { id: "cafe", label: "咖啡馆" },
  { id: "street", label: "夜景街头" },
  { id: "office", label: "办公室" },
  { id: "ancient_hall", label: "古风大殿" },
  { id: "futuristic", label: "未来演播厅" },
];

const AUDITION_CAMERAS: { id: AuditionCamera; label: string }[] = [
  { id: "medium", label: "中景" },
  { id: "closeup", label: "近景" },
  { id: "half_body", label: "半身" },
];

const auditionBackgroundLabel = (id?: string, label?: string) =>
  label || AUDITION_BACKGROUNDS.find((item) => item.id === id)?.label || id || "";
const auditionCameraLabel = (id?: string, label?: string) =>
  label || AUDITION_CAMERAS.find((item) => item.id === id)?.label || id || "";

const AUDITION_STAGES: Record<string, { label: string; progress: number }> = {
  queued: { label: "排队中", progress: 5 },
  tts: { label: "配音中", progress: 15 },
  crop: { label: "裁切参考图", progress: 25 },
  generate: { label: "生成视频中", progress: 60 },
  done: { label: "已完成", progress: 100 },
};

const PORTRAIT_VERSIONS: { id: PortraitVersion; label: string; style: string; file: string }[] = [
  { id: "anime", label: "动漫", style: "anime", file: "portrait.png" },
  { id: "stylized_25d", label: "2.5D", style: "stylized_25d", file: "portrait_25d.png" },
  { id: "realistic", label: "写实", style: "realistic", file: "portrait_real.png" },
];

export function CharactersPage({ onExit, onOpenPipeline, onOpenCinema, onOpenSettings }: Props) {
  const notify = useToast();
  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [presets, setPresets] = useState<VoicePreset[]>([]);
  const [contracts, setContracts] = useState<Record<string, CharacterContract>>({});
  const [initialVoices, setInitialVoices] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [templateFilter, setTemplateFilter] = useState("all");
  const [templateCast, setTemplateCast] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [savedVoice, setSavedVoice] = useState<Record<string, string>>({});
  const [artBust, setArtBust] = useState<Record<string, number>>({});
  const [artBusy, setArtBusy] = useState<string | null>(null);
  const [artError, setArtError] = useState("");
  const [assetsByChar, setAssetsByChar] = useState<Record<string, CharacterAssets>>({});
  const [assetBusy, setAssetBusy] = useState<string | null>(null);
  const [assetError, setAssetError] = useState("");
  const [lightbox, setLightbox] = useState<{ src: string; label: string } | null>(null);
  // V30：立绘版本
  const [portraitVersion, setPortraitVersion] = useState<PortraitVersion>("anime");
  const [portraitAvail, setPortraitAvail] = useState<Record<string, boolean>>({});
  const [portraitOverride, setPortraitOverride] = useState<Record<string, string>>({});
  const [portraitBusy, setPortraitBusy] = useState<string | null>(null);
  const [portraitError, setPortraitError] = useState("");
  // V32：试镜视频
  const [auditionOpen, setAuditionOpen] = useState(false);
  const [auditionStyle, setAuditionStyle] = useState<AuditionStyle>("anime");
  const [auditionText, setAuditionText] = useState("");
  const [auditionSubtitles, setAuditionSubtitles] = useState(true);
  const [auditionBackground, setAuditionBackground] = useState<AuditionBackground>("studio");
  const [auditionCamera, setAuditionCamera] = useState<AuditionCamera>("medium");
  const [auditionSubmitting, setAuditionSubmitting] = useState(false);
  const [auditionJob, setAuditionJob] = useState<AuditionJob | null>(null);
  const [auditionJobs, setAuditionJobs] = useState<AuditionJob[]>([]);
  const [auditionLoading, setAuditionLoading] = useState(false);
  const [auditionError, setAuditionError] = useState("");
  // V35：角色宣传片
  const [promoOpen, setPromoOpen] = useState(false);
  const [promoStyle, setPromoStyle] = useState<AuditionStyle>("realistic");
  const [promoTitle, setPromoTitle] = useState("");
  const [promoScenes, setPromoScenes] = useState<{ background: AuditionBackground; camera: AuditionCamera; text: string }[]>([]);
  const [promoSubmitting, setPromoSubmitting] = useState(false);
  const [promoJob, setPromoJob] = useState<PromoJob | null>(null);
  const [promoJobs, setPromoJobs] = useState<PromoJob[]>([]);
  const [promoLoading, setPromoLoading] = useState(false);
  const [promoError, setPromoError] = useState("");
  // V36-A：双人对戏
  const [duoOpen, setDuoOpen] = useState(false);
  const [duoA, setDuoA] = useState("");
  const [duoB, setDuoB] = useState("");
  const [duoStyle, setDuoStyle] = useState<AuditionStyle>("realistic");
  const [duoSceneA, setDuoSceneA] = useState<{ background: AuditionBackground; camera: AuditionCamera }>({ background: "studio", camera: "closeup" });
  const [duoSceneB, setDuoSceneB] = useState<{ background: AuditionBackground; camera: AuditionCamera }>({ background: "cafe", camera: "medium" });
  const [duoTitle, setDuoTitle] = useState("");
  const [duoUseThird, setDuoUseThird] = useState(false);
  const [duoC, setDuoC] = useState("");
  const [duoSceneC, setDuoSceneC] = useState<{ background: AuditionBackground; camera: AuditionCamera }>({ background: "street", camera: "medium" });
  const [duoLInes, setDuoLines] = useState<{ speaker: DialogueSpeaker; text: string }[]>([
    { speaker: "a", text: "这么晚还来喝咖啡？" },
    { speaker: "b", text: "睡不着，想找个人聊聊。" },
    { speaker: "a", text: "那就聊聊吧。" },
    { speaker: "b", text: "你不怕我说到天亮？" },
  ]);
  const [duoSubmitting, setDuoSubmitting] = useState(false);
  const [duoJob, setDuoJob] = useState<DialogueJob | null>(null);
  const [duoJobs, setDuoJobs] = useState<DialogueJob[]>([]);
  const [duoLoading, setDuoLoading] = useState(false);
  const [duoError, setDuoError] = useState("");
  // V36-B：试镜合集发布包
  const [showcaseOpen, setShowcaseOpen] = useState(false);
  const [showcasePkg, setShowcasePkg] = useState<ShowcasePackage | null>(null);
  const [showcaseLoading, setShowcaseLoading] = useState(false);
  const [showcaseBuilding, setShowcaseBuilding] = useState(false);
  const [showcaseError, setShowcaseError] = useState("");
  const [showcaseVertical, setShowcaseVertical] = useState(true);
  const [showcaseVerticalMode, setShowcaseVerticalMode] = useState<"blur_pad" | "crop">("blur_pad");
  const [splitPkg, setSplitPkg] = useState<SplitPackage | null>(null);
  const [splitLoading, setSplitLoading] = useState(false);
  const [splitBuilding, setSplitBuilding] = useState(false);
  const [splitError, setSplitError] = useState("");

  const [previewChar, setPreviewChar] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [batch, setBatch] = useState<{ running: boolean; index: number; total: number; label: string }>({
    running: false,
    index: 0,
    total: 0,
    label: "",
  });
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const batchStopRef = useRef(false);
  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      batchStopRef.current = true;
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const presetLabel = useMemo(() => {
    const map = new Map<string, string>();
    presets.forEach((preset) => map.set(preset.voice_id, preset.label || preset.voice_id));
    return map;
  }, [presets]);

  const load = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
      const [catRes, voiceRes] = await Promise.allSettled([v5api.catalog(), voiceApi.voices(true)]);
      if (!aliveRef.current) return;
      let cat: V5CatalogResponse | null = null;
      const errors: string[] = [];
      if (catRes.status === "fulfilled") {
        cat = catRes.value;
        setCatalog(cat);
      } else {
        errors.push("角色目录：" + errorText(catRes.reason));
      }
      if (voiceRes.status === "fulfilled") setPresets(voiceRes.value);
      else errors.push("音色库：" + errorText(voiceRes.reason));

      if (cat) {
        const chars = cat.characters || [];
        const nextContracts: Record<string, CharacterContract> = {};
        const nextInitial: Record<string, string> = {};
        const chunk = 6;
        for (let i = 0; i < chars.length; i += chunk) {
          const slice = chars.slice(i, i + chunk);
          const loaded = await Promise.all(slice.map((character) => fetchCharacterContract(character.id, cat)));
          loaded.forEach((contract) => {
            nextContracts[contract.id] = contract;
            if (contract.voiceId) nextInitial[contract.id] = contract.voiceId;
          });
          if (!aliveRef.current) return;
        }
        setContracts(nextContracts);
        setInitialVoices(nextInitial);
      }
      setLoadError(errors.join("；"));
    } finally {
      inFlightRef.current = false;
      if (aliveRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load(true);
    if (aliveRef.current) notify("角色与声音数据已刷新", "success");
  }, [load, notify]);

  // 模板筛选：读模板 cast（写实/隐藏模板也支持）
  useEffect(() => {
    if (templateFilter === "all") {
      setTemplateCast([]);
      return;
    }
    const template = (catalog?.templates || []).find((item) => item.id === templateFilter);
    setTemplateCast((template?.cast || []).map((member) => member.character));
  }, [catalog, templateFilter]);

  const characters = useMemo(() => {
    const all = catalog?.characters || [];
    const order = templateCast.length ? templateCast : null;
    const list: V5CharacterSummary[] = order
      ? order
          .map((id) => all.find((item) => item.id === id))
          .filter((item): item is V5CharacterSummary => Boolean(item))
      : all;
    const keyword = search.trim().toLowerCase();
    return list
      .map((char) => ({
        char,
        contract: contracts[char.id],
      }))
      .filter(({ char, contract }) => {
        if (!keyword) return true;
        return [char.id, char.label, contract?.persona, contract?.era, ...(char.tags || [])]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(keyword);
      });
  }, [catalog, contracts, search, templateCast]);

  const currentVoiceId = useCallback(
    (characterId: string): string => savedVoice[characterId] || contracts[characterId]?.voiceId || "",
    [contracts, savedVoice],
  );

  const selectedContract = selected ? contracts[selected] : null;
  const selectedVoiceId = selected ? currentVoiceId(selected) : "";

  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setPreviewChar(null);
  }, []);

  const playFor = useCallback(
    async (characterId: string): Promise<void> => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPreviewError("");
      setPreviewChar(characterId);
      let url = "";
      try {
        const res = await voiceApi.preview(characterId);
        url = res.url;
      } catch (err) {
        if (err instanceof Error && (err as { status?: number }).status === 404) {
          // 兼容旧缓存 wav 路径；不存在则给出友好提示
          url = `/media/v5/voice/${encodeURIComponent(characterId)}.wav?t=${Date.now()}`;
        } else {
          setPreviewChar(null);
          throw err;
        }
      }
      await new Promise<void>((resolve, reject) => {
        const audio = new Audio(url + (url.includes("?") ? "&" : "?") + "v18=" + Date.now());
        audioRef.current = audio;
        audio.onended = () => {
          if (audioRef.current === audio) setPreviewChar(null);
          resolve();
        };
        audio.onerror = () => {
          if (audioRef.current === audio) setPreviewChar(null);
          reject(new Error("试听 wav 加载失败（该角色可能还没有试听产物）"));
        };
        audio.play().catch(reject);
      });
    },
    [],
  );

  /** V28：拉取角色已生成素材（三视图/表情/设定图） */
  const loadAssets = useCallback(async (characterId: string) => {
    try {
      const assets = await creationApi.getCharacterAssets(characterId);
      setAssetsByChar((prev) => ({ ...prev, [characterId]: assets }));
    } catch {
      setAssetsByChar((prev) => (prev[characterId] ? prev : { ...prev, [characterId]: {} }));
    }
  }, []);

  // V32：试镜视频 —— 最近任务/恢复/轮询/提交/取消/下载
  const auditionDefaultText = useCallback((label: string) => `各位好，我是${label}。这是用我的角色立绘生成的一段试镜。`, []);

  /** V33：多行台词解析（空行忽略、每行≤80字、最多6行、合计预计≤12s） */
  const auditionLines = auditionText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const auditionEstimateSec = Math.round(
    (auditionLines.reduce((sum, line) => sum + Math.max(1.1, line.length * 0.22), 0) + Math.max(0, auditionLines.length - 1) * 0.35) * 10,
  ) / 10;
  const auditionTooManyLines = auditionLines.length > 6;
  const auditionLineTooLong = auditionLines.some((line) => line.length > 80);
  const auditionEstimateTooLong = auditionEstimateSec > 12;
  const auditionTextInvalid = auditionTooManyLines || auditionLineTooLong || auditionEstimateTooLong;
  const auditionTextError = auditionTooManyLines
    ? "最多 6 行台词，请删减"
    : auditionLineTooLong
      ? "单行台词最多 80 字，请精简"
      : auditionEstimateTooLong
        ? `预计约 ${auditionEstimateSec}s，超过 12s 上限，请精简台词`
        : "";

  const loadAuditions = useCallback(async (characterId: string) => {
    setAuditionLoading(true);
    try {
      const jobs = await auditionApi.list(characterId, 10);
      setAuditionJobs(jobs);
      const active = jobs.find((job) => isAuditionActive(job));
      const latest = active || jobs.find((job) => job.status === "done") || jobs.find((job) => job.status === "failed") || null;
      if (latest) setAuditionJob(latest);
      setAuditionError("");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      if (status !== 404 && status !== 405) setAuditionError(creationErrorMessage(err).join("；"));
    } finally {
      setAuditionLoading(false);
    }
  }, []);

  useEffect(() => {
    setAuditionJob(null);
    setAuditionJobs([]);
    setAuditionError("");
    setAuditionOpen(false);
    if (selected) void loadAuditions(selected);
  }, [loadAuditions, selected]);

  // 进行中任务 3s 轮询；完成/失败后刷新最近列表
  useEffect(() => {
    if (!selected || !auditionJob?.id || !isAuditionActive(auditionJob)) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await auditionApi.get(auditionJob.id);
        if (!alive) return;
        setAuditionJob(next);
        setAuditionError("");
        if (!isAuditionActive(next)) {
          setAuditionJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 10));
          void loadAuditions(selected);
        }
      } catch (err) {
        if (!alive) return;
        const status = err instanceof CreationError ? err.status : 0;
        if (status === 404 || status === 405) setAuditionError("试镜任务接口尚未就绪（后端 V32 实现中）");
        else setAuditionError("试镜任务状态读取失败：" + creationErrorMessage(err).join("；"));
      }
    }, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [auditionJob?.id, auditionJob?.status, loadAuditions, selected]);

  const openAuditionModal = useCallback(() => {
    if (!selectedContract) return;
    setAuditionStyle(portraitVersion);
    setAuditionText(auditionDefaultText(selectedContract.label));
    setAuditionSubtitles(true);
    setAuditionBackground("studio");
    setAuditionCamera("medium");
    setAuditionError("");
    setAuditionOpen(true);
  }, [auditionDefaultText, portraitVersion, selectedContract]);

  const submitAudition = useCallback(async () => {
    if (!selectedContract) return;
    setAuditionSubmitting(true);
    setAuditionError("");
    try {
      if (auditionTextInvalid) {
        setAuditionError(auditionTextError);
        setAuditionSubmitting(false);
        return;
      }
      const lines = auditionLines.length ? auditionLines : [auditionDefaultText(selectedContract.label)];
      const job = await auditionApi.start(selectedContract.id, {
        lines,
        subtitles: auditionSubtitles,
        style: auditionStyle,
        background: auditionBackground,
        camera: auditionCamera,
      });
      setAuditionJob(job);
      setAuditionJobs((prev) => [job, ...prev.filter((item) => item.id !== job.id)]);
      setAuditionOpen(false);
      notify(`试镜视频任务已提交 · 约 3–4 分钟，可离开页面`, "success");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      const message = creationErrorMessage(err).join("；");
      if (status === 404 || status === 405) {
        setAuditionError("试镜视频接口尚未就绪（后端 V32 实现中）");
        notify("试镜视频接口尚未就绪（后端 V32 实现中）", "info");
      } else if (status === 409) {
        setAuditionError("GPU 正忙：" + message);
        notify("GPU 正忙：" + message, "info");
      } else {
        setAuditionError("提交试镜失败：" + message);
        notify("提交试镜失败：" + message, "error");
      }
    } finally {
      setAuditionSubmitting(false);
    }
  }, [auditionBackground, auditionCamera, auditionDefaultText, auditionEstimateSec, auditionLineTooLong, auditionLines, auditionStyle, auditionSubtitles, auditionTextError, auditionTextInvalid, auditionTooManyLines, notify, selectedContract]);

  const cancelAudition = useCallback(async () => {
    if (!auditionJob?.id) return;
    try {
      const next = await auditionApi.cancel(auditionJob.id);
      setAuditionJob(next);
      setAuditionJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 10));
      notify("已请求取消试镜任务", "info");
    } catch (err) {
      setAuditionError("取消失败：" + creationErrorMessage(err).join("；"));
      notify("取消失败：" + creationErrorMessage(err).join("；"), "error");
    }
  }, [auditionJob?.id, notify]);

  const retryAudition = useCallback(() => {
    if (auditionJob) {
      setAuditionStyle((auditionJob.style as AuditionStyle) || "anime");
      setAuditionText(auditionJob.text || (selectedContract ? auditionDefaultText(selectedContract.label) : ""));
      setAuditionBackground((auditionJob.background as AuditionBackground) || "studio");
      setAuditionCamera((auditionJob.camera as AuditionCamera) || "medium");
    } else if (selectedContract) {
      setAuditionStyle(portraitVersion);
      setAuditionText(auditionDefaultText(selectedContract.label));
    }
    setAuditionError("");
    setAuditionOpen(true);
  }, [auditionDefaultText, auditionJob, portraitVersion, selectedContract]);

  const downloadAudition = useCallback(async () => {
    if (!auditionJob?.output_url) return;
    try {
      const res = await downloadUrlAsBlob(mediaSrc(auditionJob.output_url), `audition_${auditionJob.character}_${auditionJob.id}.mp4`);
      notify(`已开始下载试镜视频（${fmtAuditionSize(res.size)}）`, "success");
    } catch (err) {
      notify("下载失败：" + (err instanceof Error ? err.message : String(err)), "error");
    }
  }, [auditionJob, notify]);

  const downloadAuditionFile = useCallback(
    async (url: string, filename: string, label: string) => {
      try {
        const res = await downloadUrlAsBlob(mediaSrc(url), filename);
        notify(`已开始下载${label}（${fmtAuditionSize(res.size)}）`, "success");
      } catch (err) {
        notify(`下载${label}失败：` + (err instanceof Error ? err.message : String(err)), "error");
      }
    },
    [notify],
  );

  const auditionStage = auditionJob ? AUDITION_STAGES[auditionJob.stage || "queued"] || AUDITION_STAGES.queued : null;
  const auditionTotalLines = auditionJob?.total_lines || auditionJob?.lines?.length || 0;
  const auditionCurrentLine = auditionJob?.current_line || 0;
  const auditionStageLabel = auditionJob
    ? auditionTotalLines > 1 && auditionJob.stage && ["tts", "generate"].includes(auditionJob.stage)
      ? `第 ${Math.max(1, auditionCurrentLine)}/${auditionTotalLines} 句 · ${auditionStage?.label || "生成中"}`
      : auditionStage?.label || "排队中"
    : "";
  const auditionProgress = auditionJob
    ? Math.max(4, Math.min(100, auditionJob.progress && auditionJob.progress > 0 ? auditionJob.progress : auditionStage?.progress || 5))
    : 0;

  // V35：角色宣传片 —— 默认两镜、校验、提交、轮询、取消、下载
  const defaultPromoScenes = useCallback(
    (label: string) => [
      { background: "studio" as AuditionBackground, camera: "closeup" as AuditionCamera, text: `大家好，我是${label}。` },
      { background: "cafe" as AuditionBackground, camera: "medium" as AuditionCamera, text: "深夜的咖啡馆，最适合聊点真的。" },
    ],
    [],
  );

  const promoSceneInfo = promoScenes.map((scene) => {
    const lines = scene.text.split("\n").map((line) => line.trim()).filter(Boolean);
    const estimate =
      Math.round((lines.reduce((sum, line) => sum + Math.max(1.1, line.length * 0.22), 0) + Math.max(0, lines.length - 1) * 0.35) * 10) / 10;
    const tooMany = lines.length > 2;
    const tooLong = lines.some((line) => line.length > 60);
    const tooSlow = estimate > 8;
    return { lines, estimate, tooMany, tooLong, tooSlow, invalid: tooMany || tooLong || tooSlow || lines.length === 0 };
  });
  const promoInvalid = promoScenes.length < 1 || promoScenes.length > 3 || promoSceneInfo.some((scene) => scene.invalid);
  const promoErrorText = promoSceneInfo.some((scene) => scene.tooMany)
    ? "每镜最多 2 行台词"
    : promoSceneInfo.some((scene) => scene.tooLong)
      ? "单行台词最多 60 字"
      : promoSceneInfo.some((scene) => scene.tooSlow)
        ? "单镜台词预计超过 8s，请精简"
        : promoSceneInfo.some((scene) => scene.lines.length === 0)
          ? "每镜至少 1 行台词"
          : "";

  const loadPromos = useCallback(async (characterId: string) => {
    setPromoLoading(true);
    try {
      const jobs = await promoApi.list(characterId, 5);
      setPromoJobs(jobs);
      const active = jobs.find((job) => isPromoActive(job));
      const latest = active || jobs.find((job) => job.status === "done") || jobs.find((job) => job.status === "failed") || null;
      if (latest) setPromoJob(latest);
      setPromoError("");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      if (status !== 404 && status !== 405) setPromoError(creationErrorMessage(err).join("；"));
    } finally {
      setPromoLoading(false);
    }
  }, []);

  useEffect(() => {
    setPromoJob(null);
    setPromoJobs([]);
    setPromoError("");
    setPromoOpen(false);
    if (selected) void loadPromos(selected);
  }, [loadPromos, selected]);

  useEffect(() => {
    if (!selected || !promoJob?.id || !isPromoActive(promoJob)) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await promoApi.get(promoJob.id);
        if (!alive) return;
        setPromoJob(next);
        setPromoError("");
        if (!isPromoActive(next)) {
          setPromoJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 5));
          void loadPromos(selected);
        }
      } catch (err) {
        if (!alive) return;
        const status = err instanceof CreationError ? err.status : 0;
        if (status === 404 || status === 405) setPromoError("宣传片任务接口尚未就绪（后端 V35 实现中）");
        else setPromoError("宣传片状态读取失败：" + creationErrorMessage(err).join("；"));
      }
    }, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [loadPromos, promoJob?.id, promoJob?.status, selected]);

  const openPromoModal = useCallback(() => {
    if (!selectedContract) return;
    setPromoStyle("realistic");
    setPromoTitle("");
    setPromoScenes(defaultPromoScenes(selectedContract.label));
    setPromoError("");
    setPromoOpen(true);
  }, [defaultPromoScenes, selectedContract]);

  const submitPromo = useCallback(async () => {
    if (!selectedContract) return;
    if (promoInvalid) {
      setPromoError(promoErrorText || "请检查宣传片场景参数");
      return;
    }
    setPromoSubmitting(true);
    setPromoError("");
    try {
      const shots: PromoSceneInput[] = promoSceneInfo.map((scene, index) => ({
        lines: scene.lines,
        background: promoScenes[index].background,
        camera: promoScenes[index].camera,
        subtitles: true,
      }));
      const job = await promoApi.start(selectedContract.id, {
        style: promoStyle,
        title: promoTitle.trim() || undefined,
        shots,
      });
      setPromoJob(job);
      setPromoJobs((prev) => [job, ...prev.filter((item) => item.id !== job.id)].slice(0, 5));
      setPromoOpen(false);
      notify(`角色宣传片任务已提交 · ${shots.length} 镜，约 6–12 分钟，可离开页面`, "success");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      const message = creationErrorMessage(err).join("；");
      if (status === 404 || status === 405) {
        setPromoError("角色宣传片接口尚未就绪（后端 V35 实现中）");
        notify("角色宣传片接口尚未就绪（后端 V35 实现中）", "info");
      } else if (status === 409) {
        setPromoError("GPU 正忙：" + message);
        notify("GPU 正忙：" + message, "info");
      } else {
        setPromoError("提交宣传片失败：" + message);
        notify("提交宣传片失败：" + message, "error");
      }
    } finally {
      setPromoSubmitting(false);
    }
  }, [notify, promoErrorText, promoInvalid, promoSceneInfo, promoScenes, promoStyle, promoTitle, selectedContract]);

  const cancelPromo = useCallback(async () => {
    if (!promoJob?.id) return;
    try {
      const next = await promoApi.cancel(promoJob.id);
      setPromoJob(next);
      setPromoJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 5));
      notify("已请求取消角色宣传片", "info");
    } catch (err) {
      setPromoError("取消失败：" + creationErrorMessage(err).join("；"));
    }
  }, [notify, promoJob?.id]);

  const promoStageInfo = (() => {
    if (!promoJob) return { label: "", progress: 0 };
    const stage = promoJob.stage || "";
    const match = /shot[_\s]*(\d+)(?:[_\s]*\/[_\s]*(\d+))?[_\s]*([a-z_]*)/i.exec(stage);
    if (match) {
      const currentShot = Number(match[1]) || promoJob.current_shot || 1;
      const totalShots = Number(match[2]) || promoJob.total_shots || promoJob.shots?.length || 0;
      const kind = match[3] || "";
      const kindLabel = kind.includes("tts") || kind.includes("voice") ? "配音中" : kind.includes("generate") || kind.includes("h3") ? "生成中" : kind.includes("subtitle") ? "字幕处理中" : kind.includes("assemble") ? "场景处理中" : "处理中";
      return { label: `第 ${currentShot}/${totalShots} 镜 · ${kindLabel}`, progress: promoJob.progress || 10 };
    }
    if (/assemble|concat|拼接/i.test(stage)) return { label: "拼接中（片头 / 场景卡 / 正片）", progress: promoJob.progress || 90 };
    if (stage === "done") return { label: "已完成", progress: 100 };
    if (promoJob.status === "queued") return { label: "排队中", progress: promoJob.progress || 5 };
    return { label: promoJob.status === "running" ? "生成中" : stage || "处理中", progress: promoJob.progress || 5 };
  })();

  const buildSplitPackage = useCallback(
    async (force = false) => {
      setSplitBuilding(true);
      setSplitError("");
      try {
        const pkg = await splitPackageApi.build({ force });
        setSplitPkg(pkg);
        notify(force ? "单角色切条包已重新构建" : pkg.cached ? "单角色切条包已存在（cached）" : "单角色切条包已生成", "success");
      } catch (err) {
        const status = err instanceof CreationError ? err.status : 0;
        const message = creationErrorMessage(err).join("；");
        if (status === 404 || status === 405) setSplitError("切条包接口尚未就绪（后端 V38 实现中）");
        else setSplitError("构建切条包失败：" + message);
      } finally {
        setSplitBuilding(false);
      }
    },
    [notify],
  );

  const downloadSplitPackage = useCallback(async () => {
    if (!splitPkg?.package_url) return;
    try {
      const res = await downloadUrlAsBlob(mediaSrc(splitPkg.package_url), "character_audition_clips.zip");
      notify(`已开始下载单角色切条包（${fmtAuditionSize(res.size)}）`, "success");
    } catch (err) {
      notify("下载切条包失败：" + (err instanceof Error ? err.message : String(err)), "error");
    }
  }, [notify, splitPkg?.package_url]);

  // V36：双人对戏 —— 行编辑/校验/提交/轮询/取消/最近列表
  const duoCharacters = useMemo(() => Object.values(contracts), [contracts]);
  /** V37：双人双场景标签（A 场景 · B 场景，优先后端 scenes/每镜字段） */
  /** V38：对戏角色标签（2–3 人） */
  const duoCastLabel = useCallback((job: DialogueJob | null) => {
    if (!job) return "";
    const thirdId = job.character_c || job.characters?.c || "";
    const list = [
      job.a_label || job.character_a || job.characters?.a,
      job.b_label || job.character_b || job.characters?.b,
      thirdId ? job.c_label || thirdId : "",
    ].filter(Boolean);
    return list.join(" × ");
  }, []);

  const duoScenesLabel = useCallback(
    (job: DialogueJob | null) => {
      if (!job) return "";
      const lineScene = (speaker: "a" | "b") => job.lines?.find((line) => line.speaker === speaker);
      const sceneA = job.scenes?.a || (() => {
        const line = lineScene("a");
        return line ? { background: line.background, background_label: line.background_label, camera: line.camera, camera_label: line.camera_label } : undefined;
      })();
      const sceneB = job.scenes?.b || (() => {
        const line = lineScene("b");
        return line ? { background: line.background, background_label: line.background_label, camera: line.camera, camera_label: line.camera_label } : undefined;
      })();
      const label = (scene: typeof sceneA) =>
        scene
          ? `${auditionBackgroundLabel(scene.background, scene.background_label) || auditionBackgroundLabel(job.background, job.background_label)}·${auditionCameraLabel(scene.camera, scene.camera_label) || auditionCameraLabel(job.camera, job.camera_label)}`
          : "";
      const sceneC = job.scenes?.c;
      return [`A ${label(sceneA) || "—"}`, `B ${label(sceneB) || "—"}`, sceneC ? `C ${label(sceneC)}` : ""].filter(Boolean).join(" · ");
    },
    [],
  );

  const duoLineInfo = duoLInes.map((line) => {
    const text = line.text.trim();
    const estimate = Math.round(Math.max(1.2, text.length * 0.22) * 10) / 10;
    return { text, estimate, tooLong: text.length > 60, empty: !text };
  });
  const duoEstimatedSec = Math.round((duoLineInfo.reduce((sum, line) => sum + line.estimate, 0) + Math.max(0, duoLInes.length - 1) * 0.35) * 10) / 10;
  const duoEstimateLimit = duoUseThird ? 24 : 16;
  const duoCastIds = [duoA, duoB, ...(duoUseThird ? [duoC] : [])].filter(Boolean);
  const duoDuplicateCast = new Set(duoCastIds).size !== duoCastIds.length;
  const duoMissingThird = duoUseThird && !duoC;
  const duoSpeakerMissingC = duoLInes.some((line) => line.speaker === "c") && !duoUseThird;
  const duoInvalid =
    duoLInes.length < 2 ||
    duoLInes.length > 6 ||
    duoLineInfo.some((line) => line.empty || line.tooLong) ||
    duoEstimatedSec > duoEstimateLimit ||
    !duoA ||
    !duoB ||
    duoDuplicateCast ||
    duoMissingThird ||
    duoSpeakerMissingC;
  const duoErrorText =
    duoDuplicateCast
      ? "角色 A/B/C 不能重复"
      : duoMissingThird
        ? "已开启第三角色但未选择角色 C"
        : duoSpeakerMissingC
          ? "台词中有 C 说话，但未开启第三角色"
          : duoLineInfo.some((line) => line.empty)
            ? "每行台词不能为空"
            : duoLineInfo.some((line) => line.tooLong)
              ? "单行台词最多 60 字"
              : duoEstimatedSec > duoEstimateLimit
                ? `台词总时长预计超过 ${duoEstimateLimit}s，请精简`
                : "";

  const loadDialogues = useCallback(async () => {
    setDuoLoading(true);
    try {
      const jobs = await dialogueApi.list(5);
      setDuoJobs(jobs);
      const active = jobs.find((job) => isDialogueActive(job));
      const latest = active || jobs.find((job) => job.status === "done") || jobs.find((job) => job.status === "failed") || null;
      if (latest) setDuoJob(latest);
      setDuoError("");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      if (status !== 404 && status !== 405) setDuoError(creationErrorMessage(err).join("；"));
    } finally {
      setDuoLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDialogues();
  }, [loadDialogues]);

  useEffect(() => {
    if (!duoJob?.id || !isDialogueActive(duoJob)) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await dialogueApi.get(duoJob.id);
        if (!alive) return;
        setDuoJob(next);
        setDuoError("");
        if (!isDialogueActive(next)) {
          setDuoJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 5));
          void loadDialogues();
        }
      } catch (err) {
        if (!alive) return;
        const status = err instanceof CreationError ? err.status : 0;
        if (status === 404 || status === 405) {
          // 兼容后端单任务路由未注册/冲突：回退到列表接口取最新状态
          try {
            const jobs = await dialogueApi.list(5);
            const found = jobs.find((item) => item.id === duoJob.id);
            if (found) {
              setDuoJob(found);
              setDuoJobs(jobs);
              setDuoError("");
              return;
            }
          } catch {
            /* 列表也失败则走友好提示 */
          }
          setDuoError("双人对戏接口尚未就绪（后端 V36 实现中）");
        } else {
          setDuoError("双人对戏状态读取失败：" + creationErrorMessage(err).join("；"));
        }
      }
    }, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [duoJob?.id, duoJob?.status, loadDialogues]);

  const openDuoModal = useCallback(() => {
    setDuoA(Object.values(contracts)[0]?.id || "");
    setDuoB(Object.values(contracts)[1]?.id || "");
    setDuoStyle("realistic");
    setDuoSceneA({ background: "studio", camera: "closeup" });
    setDuoSceneB({ background: "cafe", camera: "medium" });
    setDuoUseThird(false);
    setDuoC(Object.values(contracts)[2]?.id || "");
    setDuoSceneC({ background: "street", camera: "medium" });
    setDuoTitle("");
    setDuoLines([
      { speaker: "a", text: "这么晚还来喝咖啡？" },
      { speaker: "b", text: "睡不着，想找个人聊聊。" },
      { speaker: "a", text: "那就聊聊吧。" },
      { speaker: "b", text: "你不怕我说到天亮？" },
    ]);
    setDuoError("");
    setDuoOpen(true);
  }, [contracts]);

  const submitDuo = useCallback(async () => {
    if (duoInvalid) {
      setDuoError(duoErrorText || "请检查双人对戏参数");
      return;
    }
    setDuoSubmitting(true);
    setDuoError("");
    try {
      const job = await dialogueApi.start({
        character_a: duoA,
        character_b: duoB,
        style: duoStyle,
        background: duoSceneA.background,
        camera: duoSceneA.camera,
        scene_a: duoSceneA,
        scene_b: duoSceneB,
        ...(duoUseThird && duoC ? { character_c: duoC, scene_c: duoSceneC } : {}),
        title: duoTitle.trim() || undefined,
        lines: duoLineInfo.map((line, index) => ({ speaker: duoLInes[index].speaker, text: line.text })),
      });
      setDuoJob(job);
      setDuoJobs((prev) => [job, ...prev.filter((item) => item.id !== job.id)].slice(0, 5));
      setDuoOpen(false);
      notify(`双人对戏任务已提交 · ${duoLInes.length} 句，约 8–10 分钟，可离开页面`, "success");
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      const message = creationErrorMessage(err).join("；");
      if (status === 404 || status === 405) {
        setDuoError("双人对戏接口尚未就绪（后端 V36 实现中）");
        notify("双人对戏接口尚未就绪（后端 V36 实现中）", "info");
      } else if (status === 409) {
        setDuoError("GPU 正忙：" + message);
        notify("GPU 正忙：" + message, "info");
      } else {
        setDuoError("提交双人对戏失败：" + message);
        notify("提交双人对戏失败：" + message, "error");
      }
    } finally {
      setDuoSubmitting(false);
    }
  }, [duoA, duoB, duoC, duoErrorText, duoInvalid, duoLineInfo, duoLInes, duoSceneA, duoSceneB, duoSceneC, duoStyle, duoTitle, duoUseThird, notify]);

  const cancelDuo = useCallback(async () => {
    if (!duoJob?.id) return;
    try {
      const next = await dialogueApi.cancel(duoJob.id);
      setDuoJob(next);
      setDuoJobs((prev) => [next, ...prev.filter((job) => job.id !== next.id)].slice(0, 5));
      notify("已请求取消双人对戏", "info");
    } catch (err) {
      setDuoError("取消失败：" + creationErrorMessage(err).join("；"));
    }
  }, [notify, duoJob?.id]);

  const duoStageInfo = (() => {
    if (!duoJob) return { label: "", progress: 0 };
    const stage = duoJob.stage || "";
    const match = /line[_\s]*(\d+)[_\s]*([a-z_]*)/i.exec(stage);
    const total = duoJob.total_lines || duoJob.lines?.length || duoLInes.length;
    if (match) {
      const kind = match[2] || "";
      const kindLabel = kind.includes("tts") || kind.includes("voice") ? "配音中" : kind.includes("generate") || kind.includes("h3") ? "生成中" : kind.includes("subtitle") ? "字幕处理中" : "处理中";
      return { label: `第 ${match[1]}/${total} 句 · ${kindLabel}`, progress: duoJob.progress || 10 };
    }
    if (/assemble|concat|拼接/i.test(stage)) return { label: "拼接中（片头 / 正反打）", progress: duoJob.progress || 90 };
    if (stage === "done") return { label: "已完成", progress: 100 };
    return { label: duoJob.status === "queued" ? "排队中" : duoJob.status === "running" ? "生成中" : stage || "处理中", progress: duoJob.progress || 5 };
  })();

  // V36-B：试镜合集发布包
  const openShowcase = useCallback(async () => {
    setShowcaseOpen(true);
    setShowcaseError("");
    setShowcaseLoading(true);
    try {
      const packages = await showcaseApi.list();
      setShowcasePkg(packages[0] || null);
      splitPackageApi
        .list()
        .then((items) => setSplitPkg(items[0] || null))
        .catch(() => setSplitPkg(null));
    } catch (err) {
      const status = err instanceof CreationError ? err.status : 0;
      if (status === 404 || status === 405) setShowcaseError("合集发布包接口尚未就绪（后端 V36 实现中）；可点「导出发布包」重试");
      else setShowcaseError("读取合集发布包失败：" + creationErrorMessage(err).join("；"));
    } finally {
      setShowcaseLoading(false);
    }
  }, []);

  const buildShowcase = useCallback(
    async (force = false) => {
      setShowcaseBuilding(true);
      setShowcaseError("");
      try {
        const pkg = await showcaseApi.build({ force, vertical: showcaseVertical, vertical_mode: showcaseVerticalMode });
        setShowcasePkg(pkg);
        notify(force ? "合集发布包已重新构建" : pkg.cached ? "合集发布包已存在（cached）" : "合集发布包已生成", "success");
      } catch (err) {
        const status = err instanceof CreationError ? err.status : 0;
        const message = creationErrorMessage(err).join("；");
        if (status === 404 || status === 405) setShowcaseError("合集发布包接口尚未就绪（后端 V36 实现中）");
        else setShowcaseError("导出合集发布包失败：" + message);
      } finally {
        setShowcaseBuilding(false);
      }
    },
    [notify, showcaseVertical, showcaseVerticalMode],
  );

  const downloadShowcase = useCallback(async () => {
    if (!showcasePkg?.package_url) return;
    try {
      const res = await downloadUrlAsBlob(mediaSrc(showcasePkg.package_url), "character_audition_showcase.zip");
      notify(`已开始下载试镜合集发布包（${fmtAuditionSize(res.size)}）`, "success");
    } catch (err) {
      notify("下载失败：" + (err instanceof Error ? err.message : String(err)), "error");
    }
  }, [notify, showcasePkg?.package_url]);

  /** V28：生成三视图/表情包/全部素材（imagegen，串行 40–90s） */
  const generateAssets = useCallback(
    async (characterId: string, modes: string[], label: string) => {
      setAssetBusy(characterId);
      setAssetError("");
      try {
        const res = await creationApi.generateCharacterArt(characterId, { modes, force: true });
        const stamp = Date.now();
        setArtBust((prev) => ({ ...prev, [characterId]: stamp }));
        if (res.assets) setAssetsByChar((prev) => ({ ...prev, [characterId]: res.assets || {} }));
        else await loadAssets(characterId);
        notify(`${label}已生成${res.elapsed_sec ? ` · ${Math.round(Number(res.elapsed_sec))}s` : ""}`, "success");
      } catch (err) {
        const items = creationErrorMessage(err);
        setAssetError(items.join("；"));
        notify(`${label}生成失败：${items.join("；")}`, "error");
      } finally {
        setAssetBusy((prev) => (prev === characterId ? null : prev));
      }
    },
    [loadAssets, notify],
  );

  useEffect(() => {
    if (!selected) return;
    void loadAssets(selected);
  }, [loadAssets, selected]);

  // V30：切换角色时重置立绘版本
  useEffect(() => {
    setPortraitVersion("anime");
    setPortraitError("");
    setPortraitBusy(null);
  }, [selected]);

  const portraitKey = (characterId: string, version: PortraitVersion) => characterId + ":" + version;

  /** V30：某版立绘 URL（优先 assets.portraits / 生成返回 override，否则按约定文件名 + cache-bust） */
  const portraitUrlFor = useCallback(
    (characterId: string, version: PortraitVersion): string => {
      const key = portraitKey(characterId, version);
      if (portraitOverride[key]) return portraitOverride[key];
      const bust = artBust[characterId];
      const assets = assetsByChar[characterId];
      const fromAssets =
        version === "anime"
          ? assets?.portraits?.anime || assets?.portrait
          : version === "stylized_25d"
            ? assets?.portraits?.stylized_25d
            : assets?.portraits?.realistic;
      if (fromAssets) {
        const url = artMediaUrl(fromAssets);
        return bust ? url + (url.includes("?") ? "&" : "?") + "t=" + bust : url;
      }
      const contractPortrait = version === "anime" ? contracts[characterId]?.art.portrait : undefined;
      if (contractPortrait) {
        const url = artMediaUrl(contractPortrait);
        return bust ? url + (url.includes("?") ? "&" : "?") + "t=" + bust : url;
      }
      return characterArtUrl(characterId, PORTRAIT_VERSIONS.find((item) => item.id === version)?.file || "portrait.png", bust);
    },
    [artBust, assetsByChar, contracts, portraitOverride],
  );

  // V30：预加载检测可用性（404 → 缺失）
  useEffect(() => {
    if (!selected) return;
    const characterId = selected;
    const key = portraitKey(characterId, portraitVersion);
    if (portraitOverride[key]) {
      setPortraitAvail((prev) => ({ ...prev, [key]: true }));
      return;
    }
    let alive = true;
    const img = new Image();
    img.onload = () => {
      if (alive) setPortraitAvail((prev) => ({ ...prev, [key]: true }));
    };
    img.onerror = () => {
      if (alive) setPortraitAvail((prev) => ({ ...prev, [key]: false }));
    };
    img.src = portraitUrlFor(characterId, portraitVersion);
    return () => {
      alive = false;
    };
  }, [portraitOverride, portraitUrlFor, portraitVersion, selected]);

  /** V30：生成/重新生成某版本立绘（style 落盘 25d/real） */
  const generatePortraitVersion = useCallback(
    async (characterId: string, version: PortraitVersion) => {
      const meta = PORTRAIT_VERSIONS.find((item) => item.id === version);
      if (!meta) return;
      const key = portraitKey(characterId, version);
      setPortraitBusy(key);
      setPortraitError("");
      try {
        const res = await creationApi.generateCharacterArt(characterId, { force: true, style: meta.style });
        const stamp = Date.now();
        setArtBust((prev) => ({ ...prev, [characterId]: stamp }));
        if (res.portrait_url) {
          const url = artMediaUrl(res.portrait_url);
          const withBust = url + (url.includes("?") ? "&" : "?") + "t=" + stamp;
          setPortraitOverride((prev) => ({ ...prev, [key]: withBust }));
        }
        setPortraitAvail((prev) => ({ ...prev, [key]: true }));
        if (version === "anime" && res.portrait_url) {
          setContracts((prev) => {
            const current = prev[characterId];
            if (!current) return prev;
            return { ...prev, [characterId]: { ...current, art: { ...current.art, portrait: res.portrait_url } } };
          });
        }
        notify(`「${contracts[characterId]?.label || characterId}」${meta.label}版立绘已生成${res.elapsed_sec ? ` · ${Math.round(Number(res.elapsed_sec))}s` : ""}`, "success");
        void loadAssets(characterId);
      } catch (err) {
        const items = creationErrorMessage(err);
        setPortraitError(items.join("；"));
        setPortraitAvail((prev) => ({ ...prev, [key]: prev[key] ?? false }));
        notify(`${meta.label}版立绘生成失败：${items.join("；")}`, "error");
      } finally {
        setPortraitBusy((prev) => (prev === key ? null : prev));
      }
    },
    [contracts, loadAssets, notify],
  );

  /** V24：生成/重新生成角色概念图（SDXL，20–60s） */
  const generateArt = useCallback(
    async (characterId: string, force: boolean) => {
      setArtBusy(characterId);
      setArtError("");
      try {
        const res = await creationApi.generateCharacterArt(characterId, { force });
        const stamp = Date.now();
        setArtBust((prev) => ({ ...prev, [characterId]: stamp }));
        setContracts((prev) => {
          const current = prev[characterId];
          if (!current) return prev;
          return { ...prev, [characterId]: { ...current, art: { ...current.art, portrait: res.portrait_url } } };
        });
        notify(
          res.cached ? "已存在概念图（缓存），可强制重新生成" : `「${characterId}」概念图已生成${res.elapsed_sec ? ` · ${Math.round(Number(res.elapsed_sec))}s` : ""}`,
          "success",
        );
      } catch (err) {
        const items = creationErrorMessage(err);
        setArtError(items.join("；"));
        notify("概念图生成失败：" + items.join("；"), "error");
      } finally {
        setArtBusy((prev) => (prev === characterId ? null : prev));
      }
    },
    [notify],
  );

  const previewOne = useCallback(
    async (characterId: string) => {
      if (previewChar === characterId) {
        stopAudio();
        return;
      }
      try {
        await playFor(characterId);
      } catch (err) {
        const message = errorText(err);
        setPreviewError(
          message.includes("404") || message.includes("Not Found")
            ? "试听接口尚未就绪（V18 后端 API 实现中）；保存音色不受影响，生成时生效。"
            : "试听失败：" + message,
        );
      }
    },
    [playFor, previewChar, stopAudio],
  );

  const batchPreview = useCallback(async () => {
    if (batch.running) {
      batchStopRef.current = true;
      stopAudio();
      setBatch({ running: false, index: 0, total: 0, label: "" });
      notify("已停止批量试听", "info");
      return;
    }
    const list = characters.map((item) => item.char.id);
    if (!list.length) return;
    batchStopRef.current = false;
    setPreviewError("");
    setBatch({ running: true, index: 0, total: list.length, label: "" });
    let failed = 0;
    for (let i = 0; i < list.length; i += 1) {
      if (batchStopRef.current || !aliveRef.current) break;
      const characterId = list[i];
      const label = contracts[characterId]?.label || characterId;
      setBatch({ running: true, index: i + 1, total: list.length, label });
      try {
        await playFor(characterId);
      } catch {
        failed += 1;
      }
    }
    if (!aliveRef.current) return;
    batchStopRef.current = false;
    setBatch({ running: false, index: 0, total: 0, label: "" });
    notify(
      failed
        ? `批量试听结束：${list.length - failed} 个成功 / ${failed} 个失败（试听接口可能尚未就绪）`
        : `批量试听完成：${list.length} 个角色`,
      failed ? "info" : "success",
    );
  }, [batch.running, characters, contracts, notify, playFor, stopAudio]);

  const templatesWithCast = useMemo(
    () => (catalog?.templates || []).filter((template) => (template.cast || []).length > 0 && !template.custom),
    [catalog],
  );
  const realCount = useMemo(
    () => (catalog?.characters || []).filter((char) => /^real_/i.test(char.id)).length,
    [catalog],
  );

  // 抽屉打开时 ESC 关闭（音色弹层打开时不拦截）
  useEffect(() => {
    if (!selected || pickerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickerOpen, selected]);

  // V21：新建后高亮 + 滚动定位
  useEffect(() => {
    if (!highlightId) return;
    const timer = window.setTimeout(() => {
      const node = document.querySelector(`[data-char-id="${highlightId}"]`);
      if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [highlightId]);

  return (
    <AppShell
      active="characters"
      kicker="CAST & VOICE · 角色与声音"
      title="角色与声音"
      breadcrumb={[{ label: "造片局" }, { label: `${catalog?.characters.length ?? "—"} 角色 · ${presets.length} 音色` }]}
      chips={[
        { label: "角色", value: String(catalog?.characters.length ?? "—") },
        { label: "音色", value: String(presets.length || "—"), tone: "gold" },
        { label: "写实", value: String(realCount), tone: "slate" },
        { label: "已调整", value: String(Object.keys(savedVoice).length), tone: "jade" },
      ]}
      primaryAction={{ label: "新建角色", onClick: () => setCreateOpen(true), icon: <IconPlus size={14} />, title: "创建自定义角色（写角色合同，不触发 GPU）" }}
      extraActions={
        <>
          <button type="button" className="cm-btn cm-btn-sm" onClick={openDuoModal} title="两个角色正反打对戏（GPU 约 8–10 分钟）">
            <IconUsers size={12} /> 双人对戏
          </button>
          <button type="button" className="cm-btn cm-btn-sm" onClick={() => void openShowcase()} title="角色试镜合集发布包（视频+封面+元数据+SRT）">
            <IconFilm size={12} /> 试镜合集
          </button>
        </>
      }
      onRefresh={() => void refresh()}
      refreshing={refreshing}
      onOpenSettings={onOpenSettings}
    >
      <div className="v18-page v18-char-page">
            {loadError && (
              <div className="v18-error">
                <IconAlert size={15} />
                <div className="v18-error-main">
                  <b>部分数据读取失败</b>
                  <p className="v18-error-text">{loadError}</p>
                </div>
                <div className="v18-error-actions">
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void refresh()}>
                    <IconRefresh size={12} /> 重试
                  </button>
                </div>
              </div>
            )}
            {previewError && (
              <div className="v18-error">
                <IconAlert size={15} />
                <div className="v18-error-main">
                  <b>试听提示</b>
                  <p className="v18-error-text">{previewError}</p>
                </div>
                <div className="v18-error-actions">
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => setPreviewError("")}>
                    知道了
                  </button>
                </div>
              </div>
            )}

            {(duoJob || duoJobs.length > 0) && (
              <div className="v36-duo-zone">
                <div className="v36-duo-head">
                  <span className="v36-duo-title">
                    <IconUsers size={13} /> 双人对戏
                  </span>
                  {duoJob && (
                    <span className={"v18-tag" + (duoJob.status === "done" ? " is-jade" : duoJob.status === "failed" ? " is-red" : " is-gold")}>
                      {isDialogueActive(duoJob) ? duoStageInfo.label : duoJob.status === "done" ? "已完成" : duoJob.status === "canceled" ? "已取消" : "失败"}
                    </span>
                  )}
                  {duoLoading && !duoJob && <span className="v36-duo-loading"><Spinner size={11} /> 读取最近对戏…</span>}
                  <span className="v36-duo-cast">
                    {(duoJob ? [duoJob.a_label || duoJob.character_a, duoJob.b_label || duoJob.character_b] : []).filter(Boolean).join(" × ") || "选择角色 A/B 后生成"}
                  </span>
                </div>
                {duoJob && isDialogueActive(duoJob) && (
                  <div className="v32-progress-card">
                    <div className="v32-progress-head">
                      <span className="v32-progress-stage"><Spinner size={13} /> {duoStageInfo.label}</span>
                      <span className="v32-progress-elapsed">已用 {fmtElapsed(duoJob.elapsed_sec || 0)} · 预计约 8–10 分钟</span>
                    </div>
                    <div className="v32-progress-bar">
                      <i style={{ width: `${Math.max(4, Math.min(100, duoJob.progress || duoStageInfo.progress))}%` }} />
                      <span>{Math.round(Math.max(4, Math.min(100, duoJob.progress || duoStageInfo.progress)))}%</span>
                    </div>
                    <div className="v32-progress-meta">
                      <span className="v34-scene-tag">{duoScenesLabel(duoJob)}</span>
                      <span>{duoCastLabel(duoJob)}</span>
                      <span>{duoJob.total_lines || duoJob.lines?.length || 0} 句 · 正反打</span>
                      <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void cancelDuo()}>取消任务</button>
                    </div>
                  </div>
                )}
                {duoJob && duoJob.status === "done" && duoJob.output_url && (
                  <div className="v32-video-card">
                    <video className="v32-video v36-duo-video" src={mediaSrc(duoJob.output_url)} controls preload="metadata" playsInline />
                    <div className="v32-video-meta">
                      <span className="v18-tag is-jade">已完成</span>
                      <span className="v18-tag">{AUDITION_STYLES.find((item) => item.id === duoJob.style)?.label || duoJob.style}</span>
                      <span className="v18-tag v38-cast-tag">{duoCastLabel(duoJob)}</span>
                      <span className="cm-mono">{duoJob.total_lines || duoJob.lines?.length || 0} 句</span>
                      <span className="v18-tag v34-scene-tag">{duoScenesLabel(duoJob)}</span>
                      <span className="cm-mono">{duoJob.duration_sec ? duoJob.duration_sec.toFixed(1) + "s" : "—"}</span>
                      <span className="cm-mono">{fmtAuditionSize(duoJob.size_bytes || 0)}</span>
                      <button
                        type="button"
                        className="cm-btn cm-btn-sm"
                        onClick={() =>
                          void downloadAuditionFile(
                            duoJob.output_url as string,
                            `dialogue_${duoJob.character_a}_${duoJob.character_b}_${duoJob.id}.mp4`,
                            "双人对戏视频",
                          )
                        }
                      >
                        <IconDownload size={11} /> 下载视频
                      </button>
                      {duoJob.poster_url && (
                        <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void downloadAuditionFile(duoJob.poster_url as string, `dialogue_${duoJob.id}_poster.jpg`, "海报")}>
                          <IconDownload size={11} /> 海报
                        </button>
                      )}
                      {duoJob.srt_url && (
                        <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void downloadAuditionFile(duoJob.srt_url as string, `dialogue_${duoJob.id}.srt`, "SRT")}>
                          <IconDownload size={11} /> SRT
                        </button>
                      )}
                    </div>
                  </div>
                )}
                {duoJob && (duoJob.status === "failed" || duoJob.status === "canceled") && (
                  <div className="v32-fail-card">
                    <b>{duoJob.status === "canceled" ? "双人对戏已取消" : "双人对戏生成失败"}</b>
                    <span>{duoJob.error || "未知错误；可重试"}</span>
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={openDuoModal}>重试</button>
                  </div>
                )}
                {duoError && (
                  <div className="v24-art-error" role="alert">
                    <b>双人对戏</b>
                    <span>{duoError}</span>
                  </div>
                )}
                {duoJobs.length > 0 && (
                  <div className="v32-recent">
                    <span className="v32-recent-title">最近双人对戏</span>
                    {duoJobs.slice(0, 3).map((job) => (
                      <div key={job.id} className={"v32-recent-row" + (duoJob?.id === job.id ? " is-active" : "")}>
                        <span className={"v18-tag" + (job.status === "done" ? " is-jade" : job.status === "failed" ? " is-red" : job.status === "canceled" ? "" : " is-gold")}>
                          {job.status === "done" ? "已完成" : job.status === "failed" ? "失败" : job.status === "canceled" ? "已取消" : job.status === "running" ? "生成中" : "排队中"}
                        </span>
                        <span className="cm-mono">{duoCastLabel(job)}</span>
                        <span className="cm-mono">{job.total_lines || job.lines?.length || 0} 句</span>
                        <span className="v18-tag v34-scene-tag">{duoScenesLabel(job)}</span>
                        {job.title && <span className="v32-recent-id">{job.title}</span>}
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm"
                          onClick={() => {
                            setDuoJob(job);
                            setDuoError("");
                          }}
                        >
                          {job.status === "done" ? "查看" : "跟踪"}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="v18-char-toolbar">
              <label className="st-search">
                <IconSearch size={14} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索角色 / persona / era"
                  aria-label="搜索角色"
                />
              </label>
              <select
                className="cm-select"
                value={templateFilter}
                onChange={(event) => setTemplateFilter(event.target.value)}
                aria-label="按模板筛选 cast"
                style={{ width: "auto", minWidth: 230 }}
              >
                <option value="all">全部角色（{catalog?.characters.length ?? 0}）</option>
                {templatesWithCast.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.label} · {(template.cast || []).length} 角色
                  </option>
                ))}
              </select>
              <div className="v18-char-batch">
                <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={() => void batchPreview()}>
                  {batch.running ? <Spinner size={12} /> : <IconPlay size={12} />}
                  {batch.running ? "停止全部试听" : "全部试听"}
                </button>
                {batch.running && (
                  <span className="v18-batch-note">
                    {batch.index}/{batch.total} · {batch.label}
                  </span>
                )}
              </div>
              <span className="v18-filter-count">
                {characters.length} / {catalog?.characters.length ?? "—"} 角色
              </span>
            </div>

            {loading && !Object.keys(contracts).length ? (
              <div className="v18-empty">
                <Spinner size={16} />
                <span>正在读取 32 个角色与 24 个音色…</span>
              </div>
            ) : !characters.length ? (
              <div className="v18-empty">
                <IconUser size={26} />
                <b>没有匹配的角色</b>
                <span>换个关键词，或把「按模板筛选」切回「全部角色」。</span>
              </div>
            ) : (
              <div className="v18-char-grid">
                {characters.map(({ char, contract }) => {
                  const voiceId = currentVoiceId(char.id);
                  const voiceText = presetLabel.get(voiceId) || voiceId || "未绑定";
                  return (
                    <div
                      key={char.id}
                      data-char-id={char.id}
                      className={"v18-char-card" + (highlightId === char.id ? " is-new" : "")}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelected(char.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelected(char.id);
                        }
                      }}
                      title={`查看 ${char.label} 的资料与声音`}
                    >
                      <div className="v18-char-portrait">
                        <CharacterPortrait characterId={char.id} label={char.label} portrait={contract?.art.portrait} bust={artBust[char.id]} />
                        {contract?.era && <span className="v18-char-era"><span className="v18-tag">{contract.era}</span></span>}
                      </div>
                      <div className="v18-char-body">
                        <div className="v18-char-name">
                          <b>{char.label}</b>
                          <span className="v18-char-id">{char.id}</span>
                        </div>
                        <p className="v18-char-persona">{contract?.persona || "暂无 persona（catalog 摘要）"}</p>
                        <div className="v18-char-foot">
                          <span className="v18-voice-chip" title={voiceId}>
                            <IconVolume size={11} />
                            <span>{voiceText}</span>
                          </span>
                          <button
                            type="button"
                            className="cm-btn cm-btn-sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              void previewOne(char.id);
                            }}
                          >
                            {previewChar === char.id ? <Spinner size={11} /> : <IconPlay size={11} />}
                            {previewChar === char.id ? "播放中" : "试听"}
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

      {/* 角色详情抽屉 */}
      {selected && selectedContract && (
        <div className="v18-drawer-mask" onMouseDown={() => setSelected(null)} role="presentation">
          <aside className="v18-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`${selectedContract.label} 详情`}>
            <header className="v18-drawer-head">
              <div className="v18-drawer-head-main">
                <span className="v18-drawer-kicker">CHARACTER · 角色资料 &amp; 声音</span>
                <h3 className="v18-drawer-title">
                  {selectedContract.label}
                  <small>{selectedContract.id}</small>
                </h3>
                <p className="v18-drawer-sub">
                  {selectedContract.era ? `${selectedContract.era} · ` : ""}
                  {(selectedContract.rolePool || []).join(" / ") || "角色池未标注"}
                  {selectedContract.source ? ` · ${sourceLabel(selectedContract.source)}` : ""}
                </p>
              </div>
              <button type="button" className="v18-drawer-close" onClick={() => setSelected(null)} aria-label="关闭">
                <IconClose size={16} />
              </button>
            </header>
            <div className="v18-drawer-body">
              <div className="v18-drawer-hero">
                <div className="v18-drawer-art v30-drawer-art">
                  <div className="v30-portrait">
                    <div className="v30-portrait-tabs" role="group" aria-label="立绘版本">
                      {PORTRAIT_VERSIONS.map((item) => {
                        const key = portraitKey(selectedContract.id, item.id);
                        const avail = portraitAvail[key];
                        return (
                          <button
                            key={item.id}
                            type="button"
                            className={"v30-portrait-tab" + (portraitVersion === item.id ? " is-active" : "")}
                            onClick={() => {
                              setPortraitVersion(item.id);
                              setPortraitError("");
                            }}
                            title={avail === false ? `${item.label}版尚未生成` : `${item.label}版立绘`}
                          >
                            <i className={"v30-portrait-dot" + (avail === true ? " is-on" : avail === false ? " is-off" : "")} />
                            {item.label}
                          </button>
                        );
                      })}
                    </div>
                    <div className="v30-portrait-frame">
                      {portraitBusy === portraitKey(selectedContract.id, portraitVersion) ? (
                        <div className="v30-portrait-loading">
                          <Spinner size={16} />
                          <b>正在生成{portraitVersion === "anime" ? "动漫" : portraitVersion === "stylized_25d" ? "2.5D" : "写实"}版立绘…</b>
                          <span>约 10–60 秒（imagegen 串行）</span>
                        </div>
                      ) : portraitAvail[portraitKey(selectedContract.id, portraitVersion)] === false ? (
                        <div className="v30-portrait-missing">
                          <IconImage size={26} />
                          <b>{PORTRAIT_VERSIONS.find((item) => item.id === portraitVersion)?.label}版立绘尚未生成</b>
                          <span>点击下方按钮生成该版本（约 10–60 秒）</span>
                          <button
                            type="button"
                            className="cm-btn cm-btn-primary cm-btn-sm"
                            onClick={() => void generatePortraitVersion(selectedContract.id, portraitVersion)}
                          >
                            <IconSpark size={12} /> 生成此版本
                          </button>
                        </div>
                      ) : (
                        <img
                          src={portraitUrlFor(selectedContract.id, portraitVersion)}
                          alt={`${selectedContract.label} · ${portraitVersion}`}
                          onClick={() => setLightbox({ src: portraitUrlFor(selectedContract.id, portraitVersion), label: `${selectedContract.label} · 立绘（${portraitVersion}）` })}
                          title="点击放大"
                        />
                      )}
                    </div>
                    <div className="v30-portrait-actions">
                      {portraitAvail[portraitKey(selectedContract.id, portraitVersion)] !== false && (
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm cm-btn-ghost"
                          disabled={portraitBusy === portraitKey(selectedContract.id, portraitVersion)}
                          onClick={() => void generatePortraitVersion(selectedContract.id, portraitVersion)}
                          title="重新生成当前版本立绘（imagegen，10–60s）"
                        >
                          {portraitBusy === portraitKey(selectedContract.id, portraitVersion) ? <Spinner size={12} /> : <IconSpark size={12} />}
                          重新生成{PORTRAIT_VERSIONS.find((item) => item.id === portraitVersion)?.label}
                        </button>
                      )}
                    </div>
                    {portraitError && (
                      <div className="v24-art-error" role="alert">
                        <b>立绘生成失败</b>
                        <span>{portraitError}</span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="v18-drawer-facts">
                  <p className="v18-drawer-persona">{selectedContract.persona || "暂无 persona 描述。"}</p>
                  <div className="v18-fact-grid">
                    {Object.entries(selectedContract.appearance)
                      .filter(([key]) => APPEARANCE_LABELS[key])
                      .slice(0, 8)
                      .map(([key, value]) => (
                        <div key={key} className="v18-fact">
                          <span>{APPEARANCE_LABELS[key]}</span>
                          <b>{fmtAppearanceValue(value)}</b>
                        </div>
                      ))}
                  </div>
                  <div className="v18-art-links">
                    <button
                      type="button"
                      className="cm-btn cm-btn-primary cm-btn-sm"
                      onClick={() => void generateArt(selectedContract.id, true)}
                      disabled={artBusy === selectedContract.id}
                      title="调用本地 imagegen/SDXL 生成 portrait.png（串行，不触发 3D）"
                    >
                      {artBusy === selectedContract.id ? <Spinner size={12} /> : <IconImage size={12} />}
                      {artBusy === selectedContract.id
                        ? "概念图生成中…（约 20–60s）"
                        : selectedContract.art.portrait || artBust[selectedContract.id]
                          ? "重新生成概念图"
                          : "生成概念图"}
                    </button>
                    {(["portrait", "front", "cutout"] as const).map((kind) => {
                      const path = selectedContract.art[kind];
                      if (!path) return null;
                      const href = kind === "portrait" && artBust[selectedContract.id] ? mediaSrc(path) + (path.includes("?") ? "&" : "?") + "t=" + artBust[selectedContract.id] : mediaSrc(path);
                      return (
                        <a key={kind} className="cm-btn cm-btn-sm" href={href} target="_blank" rel="noreferrer">
                          <IconImage size={12} /> {kind === "portrait" ? "立绘" : kind === "front" ? "正面" : "抠像"}
                        </a>
                      );
                    })}
                  </div>
                  {artError && (
                    <div className="v24-art-error" role="alert">
                      <b>概念图生成失败</b>
                      <span>{artError}</span>
                    </div>
                  )}
                </div>
              </div>

              <section className="v18-drawer-sec v32-audition">
                <div className="v18-drawer-sec-title">
                  <IconFilm size={15} /> 试镜视频
                  {auditionJob && (
                    <span className={"v18-tag" + (auditionJob.status === "done" ? " is-jade" : auditionJob.status === "failed" ? " is-red" : " is-gold")}>
                      {isAuditionActive(auditionJob) ? auditionStage?.label : auditionJob.status === "done" ? "已完成" : auditionJob.status === "canceled" ? "已取消" : auditionJob.status === "failed" ? "失败" : "排队中"}
                    </span>
                  )}
                  <div className="v28-assets-actions">
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" disabled={auditionSubmitting} onClick={openAuditionModal}>
                      <IconFilm size={12} /> 生成试镜视频
                    </button>
                    <button type="button" className="cm-btn cm-btn-sm" disabled={promoSubmitting} onClick={openPromoModal}>
                      <IconSpark size={12} /> 生成角色宣传片
                    </button>
                  </div>
                </div>
                {auditionError && (
                  <div className="v24-art-error" role="alert">
                    <b>试镜视频</b>
                    <span>{auditionError}</span>
                  </div>
                )}
                {auditionLoading && !auditionJob && (
                  <div className="v32-audition-loading">
                    <Spinner size={14} /> 正在读取最近试镜任务…
                  </div>
                )}
                {auditionJob && isAuditionActive(auditionJob) && (
                  <div className="v32-progress-card">
                    <div className="v32-progress-head">
                      <span className="v32-progress-stage">
                        <Spinner size={13} /> {auditionStageLabel}
                      </span>
                      <span className="v32-progress-elapsed">
                        已用 {fmtElapsed(auditionJob.elapsed_sec || 0)} · 预计约 3–4 分钟
                      </span>
                    </div>
                    <div className="v32-progress-bar">
                      <i style={{ width: `${auditionProgress}%` }} />
                      <span>{Math.round(auditionProgress)}%</span>
                    </div>
                    <div className="v32-progress-meta">
                      <span>{AUDITION_STYLES.find((item) => item.id === auditionJob.style)?.label || auditionJob.style}版立绘</span>
                      <span>{auditionTotalLines ? `${auditionTotalLines} 句台词` : "单句台词"}{auditionJob.subtitles_burned === false ? "" : " · 烧录字幕"}</span>
                      <span className="v34-scene-tag">
                        {auditionBackgroundLabel(auditionJob.background, auditionJob.background_label)} · {auditionCameraLabel(auditionJob.camera, auditionJob.camera_label)}
                      </span>
                      <span>可离开页面，回到角色详情自动恢复</span>
                      <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void cancelAudition()}>
                        取消任务
                      </button>
                    </div>
                  </div>
                )}
                {auditionJob && auditionJob.status === "done" && auditionJob.output_url && (
                  <div className="v32-video-card">
                    <video
                      className="v32-video"
                      src={mediaSrc(auditionJob.output_url)}
                      controls
                      preload="metadata"
                      playsInline
                    />
                    <div className="v32-video-meta">
                      <span className="v18-tag is-jade">已完成</span>
                      <span className="v18-tag">{AUDITION_STYLES.find((item) => item.id === auditionJob.style)?.label || auditionJob.style}</span>
                      {auditionJob.subtitles_burned && <span className="v18-tag is-gold">字幕</span>}
                      {auditionJob.background && (
                        <span className="v18-tag v34-scene-tag">
                          {auditionBackgroundLabel(auditionJob.background, auditionJob.background_label)} · {auditionCameraLabel(auditionJob.camera, auditionJob.camera_label)}
                        </span>
                      )}
                      <span className="cm-mono">{auditionJob.duration_sec ? auditionJob.duration_sec.toFixed(1) + "s" : "—"}</span>
                      <span className="cm-mono">{fmtAuditionSize(auditionJob.size_bytes || 0)}</span>
                      <button type="button" className="cm-btn cm-btn-sm" onClick={() => void downloadAudition()}>
                        <IconDownload size={11} /> {auditionJob.subtitles_burned ? "烧录版下载" : "下载"}
                      </button>
                      {auditionJob.plain_output_url && (
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm cm-btn-ghost"
                          onClick={() => void downloadAuditionFile(auditionJob.plain_output_url as string, `audition_${auditionJob.character}_${auditionJob.id}_plain.mp4`, "无字幕版")}
                        >
                          <IconDownload size={11} /> 无字幕版下载
                        </button>
                      )}
                      {auditionJob.srt_url && (
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm cm-btn-ghost"
                          onClick={() => void downloadAuditionFile(auditionJob.srt_url as string, `audition_${auditionJob.character}_${auditionJob.id}.srt`, "SRT 字幕")}
                        >
                          <IconDownload size={11} /> SRT 下载
                        </button>
                      )}
                      <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={retryAudition}>
                        重新生成
                      </button>
                    </div>
                  </div>
                )}
                {auditionJob && (auditionJob.status === "failed" || auditionJob.status === "canceled") && (
                  <div className="v32-fail-card">
                    <b>{auditionJob.status === "canceled" ? "试镜任务已取消" : "试镜视频生成失败"}</b>
                    <span>{auditionJob.error || "未知错误；可重试"}</span>
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={retryAudition}>
                      重试
                    </button>
                  </div>
                )}
                {auditionJobs.length > 0 && (
                  <div className="v32-recent">
                    <span className="v32-recent-title">最近试镜</span>
                    {auditionJobs.slice(0, 3).map((job) => (
                      <div key={job.id} className={"v32-recent-row" + (auditionJob?.id === job.id ? " is-active" : "")}>
                        <span className={"v18-tag" + (job.status === "done" ? " is-jade" : job.status === "failed" ? " is-red" : job.status === "canceled" ? "" : " is-gold")}>
                          {job.status === "done" ? "已完成" : job.status === "failed" ? "失败" : job.status === "canceled" ? "已取消" : job.status === "running" ? "生成中" : "排队中"}
                        </span>
                        <span className="cm-mono">{AUDITION_STYLES.find((item) => item.id === job.style)?.label || job.style}</span>
                        {(job.subtitles_burned || job.srt_url) && <span className="v18-tag is-gold">字幕</span>}
                        {job.background && (
                          <span className="v18-tag v34-scene-tag">
                            {auditionBackgroundLabel(job.background, job.background_label)} · {auditionCameraLabel(job.camera, job.camera_label)}
                          </span>
                        )}
                        {job.lines && job.lines.length > 1 && <span className="cm-mono">{job.lines.length} 句</span>}
                        <span className="v32-recent-id cm-mono">{job.id}</span>
                        <button
                          type="button"
                          className="cm-btn cm-btn-sm"
                          onClick={() => {
                            setAuditionJob(job);
                            setAuditionError("");
                          }}
                        >
                          {job.status === "done" ? "查看" : "跟踪"}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {(promoJob || promoJobs.length > 0 || promoLoading || promoError) && (
                  <div className="v35-promo">
                    <div className="v35-promo-head">
                      <span className="v35-promo-title">角色宣传片</span>
                      {promoJob && (
                        <span className={"v18-tag" + (promoJob.status === "done" ? " is-jade" : promoJob.status === "failed" ? " is-red" : " is-gold")}>
                          {isPromoActive(promoJob) ? promoStageInfo.label : promoJob.status === "done" ? "已完成" : promoJob.status === "canceled" ? "已取消" : "失败"}
                        </span>
                      )}
                      {promoLoading && !promoJob && (
                        <span className="v35-promo-loading">
                          <Spinner size={11} /> 读取最近宣传片…
                        </span>
                      )}
                    </div>
                    {promoJob && isPromoActive(promoJob) && (
                      <div className="v32-progress-card">
                        <div className="v32-progress-head">
                          <span className="v32-progress-stage">
                            <Spinner size={13} /> {promoStageInfo.label}
                          </span>
                          <span className="v32-progress-elapsed">已用 {fmtElapsed(promoJob.elapsed_sec || 0)} · 预计约 6–12 分钟</span>
                        </div>
                        <div className="v32-progress-bar">
                          <i style={{ width: `${Math.max(4, Math.min(100, promoJob.progress || promoStageInfo.progress))}%` }} />
                          <span>{Math.round(Math.max(4, Math.min(100, promoJob.progress || promoStageInfo.progress)))}%</span>
                        </div>
                        <div className="v32-progress-meta">
                          <span className="v34-scene-tag">{promoJob.scene_count || promoJob.shots?.length || 0} 镜 · 片头 + 场景卡</span>
                          <span>可离开页面，回来自动恢复</span>
                          <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void cancelPromo()}>
                            取消任务
                          </button>
                        </div>
                      </div>
                    )}
                    {promoJob && promoJob.status === "done" && promoJob.output_url && (
                      <div className="v32-video-card">
                        <video className="v32-video" src={mediaSrc(promoJob.output_url)} controls preload="metadata" playsInline />
                        <div className="v32-video-meta">
                          <span className="v18-tag is-jade">已完成</span>
                          <span className="v18-tag">{AUDITION_STYLES.find((item) => item.id === promoJob.style)?.label || promoJob.style}</span>
                          <span className="cm-mono">{promoJob.scene_count || promoJob.shots?.length || 0} 镜</span>
                          <span className="cm-mono">{promoJob.duration_sec ? promoJob.duration_sec.toFixed(1) + "s" : "—"}</span>
                          <span className="cm-mono">{fmtAuditionSize(promoJob.size_bytes || 0)}</span>
                          <button
                            type="button"
                            className="cm-btn cm-btn-sm"
                            onClick={() => void downloadAuditionFile(promoJob.output_url as string, `promo_${promoJob.character}_${promoJob.id}.mp4`, "宣传片")}
                          >
                            <IconDownload size={11} /> 下载视频
                          </button>
                          {promoJob.poster_url && (
                            <button
                              type="button"
                              className="cm-btn cm-btn-sm cm-btn-ghost"
                              onClick={() => void downloadAuditionFile(promoJob.poster_url as string, `promo_${promoJob.character}_${promoJob.id}_poster.jpg`, "海报")}
                            >
                              <IconDownload size={11} /> 下载海报
                            </button>
                          )}
                          {promoJob.srt_url && (
                            <button
                              type="button"
                              className="cm-btn cm-btn-sm cm-btn-ghost"
                              onClick={() => void downloadAuditionFile(promoJob.srt_url as string, `promo_${promoJob.character}_${promoJob.id}.srt`, "SRT 字幕")}
                            >
                              <IconDownload size={11} /> SRT
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                    {promoJob && (promoJob.status === "failed" || promoJob.status === "canceled") && (
                      <div className="v32-fail-card">
                        <b>{promoJob.status === "canceled" ? "宣传片任务已取消" : "宣传片生成失败"}</b>
                        <span>{promoJob.error || "未知错误；可重试"}</span>
                        <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={openPromoModal}>
                          重试
                        </button>
                      </div>
                    )}
                    {promoError && (
                      <div className="v24-art-error" role="alert">
                        <b>角色宣传片</b>
                        <span>{promoError}</span>
                      </div>
                    )}
                    {promoJobs.length > 0 && (
                      <div className="v32-recent">
                        <span className="v32-recent-title">最近宣传片</span>
                        {promoJobs.slice(0, 3).map((job) => (
                          <div key={job.id} className={"v32-recent-row" + (promoJob?.id === job.id ? " is-active" : "")}>
                            <span className={"v18-tag" + (job.status === "done" ? " is-jade" : job.status === "failed" ? " is-red" : job.status === "canceled" ? "" : " is-gold")}>
                              {job.status === "done" ? "已完成" : job.status === "failed" ? "失败" : job.status === "canceled" ? "已取消" : job.status === "running" ? "生成中" : "排队中"}
                            </span>
                            <span className="cm-mono">{AUDITION_STYLES.find((item) => item.id === job.style)?.label || job.style}</span>
                            <span className="cm-mono">{job.scene_count || job.shots?.length || 0} 镜</span>
                            {job.title && <span className="v32-recent-id">{job.title}</span>}
                            <button
                              type="button"
                              className="cm-btn cm-btn-sm"
                              onClick={() => {
                                setPromoJob(job);
                                setPromoError("");
                              }}
                            >
                              {job.status === "done" ? "查看" : "跟踪"}
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <p className="v21-hint">
                  试镜视频 = 立绘 + 角色音色 TTS + H3 图生视频口型同步；约 3–4 分钟（GPU 串行），生成期间可离开本页。
                </p>
              </section>

              <section className="v18-drawer-sec v28-assets">
                <div className="v18-drawer-sec-title">
                  <IconImage size={15} /> 角色素材
                  <span className="v18-tag is-gold">
                    {1 +
                      Object.keys((assetsByChar[selectedContract.id]?.three_view || {})).length +
                      Object.keys((assetsByChar[selectedContract.id]?.expressions || {})).length +
                      (assetsByChar[selectedContract.id]?.sheet ? 1 : 0)}{" "}
                    张
                  </span>
                  <div className="v28-assets-actions">
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm"
                      disabled={assetBusy === selectedContract.id}
                      onClick={() => void generateAssets(selectedContract.id, ["three_view"], "三视图")}
                    >
                      {assetBusy === selectedContract.id ? <Spinner size={12} /> : <IconImage size={12} />} 生成三视图
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm"
                      disabled={assetBusy === selectedContract.id}
                      onClick={() => void generateAssets(selectedContract.id, ["expressions"], "表情包")}
                    >
                      {assetBusy === selectedContract.id ? <Spinner size={12} /> : <IconImage size={12} />} 生成表情包
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-primary cm-btn-sm"
                      disabled={assetBusy === selectedContract.id}
                      onClick={() => void generateAssets(selectedContract.id, ["all"], "全部素材")}
                    >
                      {assetBusy === selectedContract.id ? <Spinner size={12} /> : <IconSpark size={12} />} 全部生成
                    </button>
                  </div>
                </div>
                {assetBusy === selectedContract.id && (
                  <div className="v28-assets-loading">
                    <Spinner size={16} />
                    <div>
                      <b>正在生成角色素材…</b>
                      <span>三视图 ×3 + 表情 ×4 + 设定图 ×1；imagegen 串行，约 40–90 秒，可稍候</span>
                    </div>
                  </div>
                )}
                {assetError && (
                  <div className="v24-art-error" role="alert">
                    <b>角色素材生成失败</b>
                    <span>{assetError}</span>
                  </div>
                )}
                {(() => {
                  const current = assetsByChar[selectedContract.id] || {};
                  const bust = artBust[selectedContract.id];
                  const portraitSrc = selectedContract.art.portrait
                    ? artMediaUrl(selectedContract.art.portrait) + (bust ? (selectedContract.art.portrait.includes("?") ? "&" : "?") + "t=" + bust : "")
                    : characterArtUrl(selectedContract.id, "portrait.png", bust);
                  const bustCharUrl = (rel: string) => characterArtUrl(selectedContract.id, rel, bust);
                  const groups: { key: string; label: string; items: { name: string; src: string }[] }[] = [
                    { key: "portrait", label: "立绘", items: [{ name: "portrait", src: portraitSrc }] },
                    {
                      key: "three_view",
                      label: "三视图",
                      items: [
                        { name: "front", src: current.three_view?.front ? artMediaUrl(current.three_view.front) + (bust ? "?t=" + bust : "") : bustCharUrl("three_view/front.png") },
                        { name: "side", src: current.three_view?.side ? artMediaUrl(current.three_view.side) + (bust ? "?t=" + bust : "") : bustCharUrl("three_view/side.png") },
                        { name: "three_quarter", src: current.three_view?.three_quarter ? artMediaUrl(current.three_view.three_quarter) + (bust ? "?t=" + bust : "") : bustCharUrl("three_view/three_quarter.png") },
                      ],
                    },
                    {
                      key: "expressions",
                      label: "表情包",
                      items: ["neutral", "happy", "surprised", "thinking"].map((name) => ({
                        name,
                        src: (current.expressions && current.expressions[name])
                          ? artMediaUrl(current.expressions[name] as string) + (bust ? "?t=" + bust : "")
                          : bustCharUrl("expressions/" + name + ".png"),
                      })),
                    },
                    {
                      key: "sheet",
                      label: "设定图",
                      items: [{ name: "sheet", src: current.sheet ? artMediaUrl(current.sheet) + (bust ? "?t=" + bust : "") : bustCharUrl("sheet.png") }],
                    },
                  ];
                  return (
                    <div className="v28-asset-groups">
                      {groups.map((group) => (
                        <div key={group.key} className="v28-asset-group">
                          <span className="v28-asset-group-label">{group.label}</span>
                          <div className="v28-asset-grid">
                            {group.items.map((item) => (
                              <button
                                key={group.key + item.name}
                                type="button"
                                className="v28-asset-thumb"
                                onClick={() => setLightbox({ src: item.src, label: `${selectedContract.label} · ${group.label} · ${item.name}` })}
                                title={`点击放大 · ${item.name}`}
                              >
                                <img
                                  src={item.src}
                                  alt={item.name}
                                  loading="lazy"
                                  onError={(event) => {
                                    event.currentTarget.style.display = "none";
                                    (event.currentTarget.parentElement as HTMLElement)?.classList.add("is-missing");
                                  }}
                                  onLoad={(event) => {
                                    (event.currentTarget.parentElement as HTMLElement)?.classList.remove("is-missing");
                                  }}
                                />
                                <span>{item.name}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  );
                })()}
                <p className="v21-hint">素材写入 assets/v5/characters/&lt;id&gt;/（三视图/表情/设定图）；生成后即时刷新，可点击缩略图放大。</p>
              </section>

              <section className="v18-drawer-sec">
                <div className="v18-drawer-sec-title">
                  <IconVolume size={15} /> 声音卡
                  <span className="v18-tag is-gold">{presetLabel.get(selectedVoiceId) || selectedVoiceId || "未绑定"}</span>
                  {savedVoice[selectedContract.id] && <span className="v18-tag is-jade"><IconCheck size={10} /> 已保存本轮</span>}
                </div>
                <div className="v18-voice-current">
                  <span className="v18-crumb">
                    当前 voice_id：<b className="cm-mono">{selectedVoiceId || "—"}</b>
                  </span>
                  <span className="v18-crumb">音色库 {presets.length} 个 preset</span>
                </div>
                <div className="v18-actions">
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm"
                    onClick={() => void previewOne(selectedContract.id)}
                  >
                    {previewChar === selectedContract.id ? <Spinner size={12} /> : <IconPlay size={12} />}
                    {previewChar === selectedContract.id ? "播放中" : "试听当前音色"}
                  </button>
                  <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => setPickerOpen(true)}>
                    <IconVolume size={12} /> 换声音（音色库）
                  </button>
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm cm-btn-ghost"
                    onClick={async () => {
                      const defaultId = initialVoices[selectedContract.id];
                      if (!defaultId) return;
                      try {
                        await voiceApi.setVoice(selectedContract.id, { voice_id: defaultId });
                        setSavedVoice((prev) => ({ ...prev, [selectedContract.id]: defaultId }));
                        setContracts((prev) => ({
                          ...prev,
                          [selectedContract.id]: { ...prev[selectedContract.id], voiceId: defaultId },
                        }));
                        notify(`已恢复「${selectedContract.label}」默认音色：${presetLabel.get(defaultId) || defaultId}`, "success");
                      } catch (err) {
                        notify("恢复默认失败：" + errorText(err), "error");
                      }
                    }}
                    disabled={!initialVoices[selectedContract.id]}
                    title="写回页面加载时的内建音色"
                  >
                    <IconRefresh size={12} /> 恢复默认
                  </button>
                </div>
                <div className="v18-fact-grid">
                  <div className="v18-fact">
                    <span>provider</span>
                    <b>{selectedContract.voiceProvider || "breeze"}</b>
                  </div>
                  <div className="v18-fact">
                    <span>试听来源</span>
                    <b>POST /api/v5/voice/{`{character}`}/preview</b>
                  </div>
                </div>
              </section>

              <section className="v18-drawer-sec">
                <div className="v18-drawer-sec-title">
                  <IconGlobe size={15} /> 资料
                </div>
                <div className="v18-fact-grid">
                  <div className="v18-fact">
                    <span>era</span>
                    <b>{selectedContract.era || "—"}</b>
                  </div>
                  <div className="v18-fact">
                    <span>role_pool</span>
                    <b>{(selectedContract.rolePool || []).join(" / ") || "—"}</b>
                  </div>
                  <div className="v18-fact">
                    <span>tags</span>
                    <b>{(selectedContract.tags || []).join(" / ") || "—"}</b>
                  </div>
                  <div className="v18-fact">
                    <span>source</span>
                    <b>{sourceLabel(selectedContract.source)}</b>
                  </div>
                </div>
              </section>
            </div>
          </aside>
        </div>
      )}

      {createOpen && (
        <CreateCharacterDrawer
          open={createOpen}
          presets={presets}
          auditionCharacter={(catalog?.characters || [])[0]?.id}
          onClose={() => setCreateOpen(false)}
          onCreated={(id, res) => {
            setCreateOpen(false);
            setHighlightId(id);
            setSelected(id);
            if (res?.art?.generated) {
              setArtBust((prev) => ({ ...prev, [id]: Date.now() }));
              void loadAssets(id);
              setContracts((prev) => {
                const current = prev[id];
                if (!current) return prev;
                return { ...prev, [id]: { ...current, art: { ...current.art, portrait: res.art?.portrait_url } } };
              });
            }
            notify(
              res?.art?.generated ? "角色已创建，概念图已生成，正在刷新…" : "角色已创建，正在刷新角色目录…",
              "info",
            );
            void load(true);
          }}
        />
      )}

      {selectedContract && pickerOpen && (
        <VoicePicker
          open
          character={selectedContract.id}
          characterLabel={selectedContract.label}
          presets={presets}
          currentVoiceId={currentVoiceId(selectedContract.id)}
          defaultVoiceId={initialVoices[selectedContract.id]}
          onClose={() => setPickerOpen(false)}
          onSaved={(voiceId) => {
            setSavedVoice((prev) => ({ ...prev, [selectedContract.id]: voiceId }));
            setContracts((prev) => ({
              ...prev,
              [selectedContract.id]: { ...prev[selectedContract.id], voiceId },
            }));
          }}
        />
      )}
      {duoOpen && (
        <div className="v32-mask" onMouseDown={() => !duoSubmitting && setDuoOpen(false)} role="presentation">
          <div className="v32-modal v36-duo-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="双人对戏">
            <header className="v32-modal-head">
              <div>
                <span className="v21-drawer-kicker">DIALOGUE · 双人对戏</span>
                <h3 className="v32-modal-title">生成双人对戏</h3>
              </div>
              <button type="button" className="v21-close" onClick={() => setDuoOpen(false)} aria-label="关闭" disabled={duoSubmitting}>
                <IconClose size={16} />
              </button>
            </header>
            <div className="v32-modal-body">
              <div className="v36-duo-cast-row">
                {(["a", "b"] as const).map((side) => {
                  const value = side === "a" ? duoA : duoB;
                  const char = contracts[value];
                  const styleFile = duoStyle === "realistic" ? "portrait_real.png" : duoStyle === "stylized_25d" ? "portrait_25d.png" : "portrait.png";
                  return (
                    <label key={side} className="v21-field">
                      <span className="v21-field-label">角色 {side.toUpperCase()}</span>
                      <div className="v36-duo-char">
                        <div className="v36-duo-mini">
                          {value ? (
                            <img
                              src={characterArtUrl(value, styleFile, artBust[value])}
                              alt={char?.label || value}
                              onError={(event) => {
                                (event.currentTarget as HTMLImageElement).style.visibility = "hidden";
                              }}
                            />
                          ) : (
                            <IconUser size={16} />
                          )}
                        </div>
                        <select
                          className="v21-select"
                          value={value}
                          onChange={(event) => (side === "a" ? setDuoA(event.target.value) : setDuoB(event.target.value))}
                          disabled={duoSubmitting}
                        >
                          {duoCharacters.map((item) => (
                            <option key={item.id} value={item.id}>{item.label}</option>
                          ))}
                        </select>
                      </div>
                    </label>
                  );
                })}
              </div>
              <label className="v38-third-toggle">
                <input type="checkbox" checked={duoUseThird} onChange={(event) => setDuoUseThird(event.target.checked)} disabled={duoSubmitting} />
                <span>加入第三角色（三人群聊）</span>
                <em>开启后可设置角色 C 与场景 C，台词 speaker 可选 C</em>
              </label>
              {duoUseThird && (
                <label className="v21-field">
                  <span className="v21-field-label">角色 C</span>
                  <div className="v36-duo-char">
                    <div className="v36-duo-mini">
                      {duoC && contracts[duoC] ? (
                        <img
                          src={characterArtUrl(
                            duoC,
                            duoStyle === "realistic" ? "portrait_real.png" : duoStyle === "stylized_25d" ? "portrait_25d.png" : "portrait.png",
                            artBust[duoC],
                          )}
                          alt={contracts[duoC]?.label || duoC}
                          onError={(event) => {
                            (event.currentTarget as HTMLImageElement).style.visibility = "hidden";
                          }}
                        />
                      ) : (
                        <IconUser size={16} />
                      )}
                    </div>
                    <select className="v21-select" value={duoC} onChange={(event) => setDuoC(event.target.value)} disabled={duoSubmitting}>
                      <option value="">选择角色 C…</option>
                      {duoCharacters.map((item) => (
                        <option key={item.id} value={item.id}>{item.label}</option>
                      ))}
                    </select>
                  </div>
                </label>
              )}
              <label className="v21-field">
                <span className="v21-field-label">风格</span>
                <select className="v21-select" value={duoStyle} onChange={(event) => setDuoStyle(event.target.value as AuditionStyle)} disabled={duoSubmitting}>
                  {AUDITION_STYLES.map((item) => (<option key={item.id} value={item.id}>{item.label}</option>))}
                </select>
              </label>
              <div className={"v37-scene-grid" + (duoUseThird ? " is-three" : "")}>
                {([
                  { key: "a", label: "角色 A 场景", value: duoSceneA, set: setDuoSceneA },
                  { key: "b", label: "角色 B 场景", value: duoSceneB, set: setDuoSceneB },
                  ...(duoUseThird ? [{ key: "c" as const, label: "角色 C 场景", value: duoSceneC, set: setDuoSceneC }] : []),
                ] as const).map((scene) => (
                  <div key={scene.key} className="v37-scene">
                    <span className="v37-scene-title">{scene.label}</span>
                    <div className="v37-scene-row">
                      <select
                        className="v21-select"
                        value={scene.value.background}
                        onChange={(event) => scene.set((prev) => ({ ...prev, background: event.target.value as AuditionBackground }))}
                        disabled={duoSubmitting}
                        aria-label={scene.label + "背景"}
                      >
                        {AUDITION_BACKGROUNDS.map((item) => (<option key={item.id} value={item.id}>{item.label}</option>))}
                      </select>
                      <select
                        className="v21-select"
                        value={scene.value.camera}
                        onChange={(event) => scene.set((prev) => ({ ...prev, camera: event.target.value as AuditionCamera }))}
                        disabled={duoSubmitting}
                        aria-label={scene.label + "机位"}
                      >
                        {AUDITION_CAMERAS.map((item) => (<option key={item.id} value={item.id}>{item.label}</option>))}
                      </select>
                    </div>
                  </div>
                ))}
              </div>
              <label className="v21-field">
                <span className="v21-field-label">标题（可选，片头卡）</span>
                <input className="v21-input" value={duoTitle} maxLength={40} placeholder="例如：老周 & 甜妹 · 深夜咖啡馆" onChange={(event) => setDuoTitle(event.target.value)} disabled={duoSubmitting} />
              </label>
              <div className="v36-duo-lines">
                <div className="v36-duo-lines-head">
                  <span>台词（每行一句，2–6 句）</span>
                  <span className={duoEstimatedSec > duoEstimateLimit ? "is-error" : ""}>预计约 {duoEstimatedSec}s（上限 {duoEstimateLimit}s）{duoUseThird ? " · 三人" : ""}</span>
                </div>
                {duoLInes.map((line, index) => (
                  <div key={index} className="v36-duo-line">
                    <select
                      className={"v38-speaker-select is-" + line.speaker}
                      value={line.speaker}
                      onChange={(event) =>
                        setDuoLines((prev) => prev.map((item, i) => (i === index ? { ...item, speaker: event.target.value as DialogueSpeaker } : item)))
                      }
                      disabled={duoSubmitting}
                      title="选择说话角色"
                      aria-label={"第 " + (index + 1) + " 句说话角色"}
                    >
                      <option value="a">A</option>
                      <option value="b">B</option>
                      <option value="c" disabled={!duoUseThird}>C</option>
                    </select>
                    <input
                      className="v21-input"
                      value={line.text}
                      maxLength={80}
                      placeholder={line.speaker === "a" ? "角色 A 的台词…" : "角色 B 的台词…"}
                      onChange={(event) => setDuoLines((prev) => prev.map((item, i) => (i === index ? { ...item, text: event.target.value } : item)))}
                      disabled={duoSubmitting}
                    />
                    <span className="v36-line-len cm-mono">{line.text.length}/60</span>
                    {duoLInes.length > 2 && (
                      <button type="button" className="v35-scene-del" disabled={duoSubmitting} onClick={() => setDuoLines((prev) => prev.filter((_, i) => i !== index))} title="删除该句">
                        <IconClose size={12} />
                      </button>
                    )}
                  </div>
                ))}
                {duoLInes.length < 6 && (
                  <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() =>
                      setDuoLines((prev) => [
                        ...prev,
                        { speaker: (duoUseThird ? (["a", "b", "c"] as const)[prev.length % 3] : prev.length % 2 === 0 ? "a" : "b") as DialogueSpeaker, text: "" },
                      ])
                    } disabled={duoSubmitting}>
                    <IconPlus size={12} /> 添加台词
                  </button>
                )}
              </div>
              <p className="v32-modal-hint">约 8–10 分钟（每句一镜正反打 + 各自音色 + 字幕 + 拼接）· 可离开页面，回来自动恢复。</p>
              {(duoErrorText || duoError) && (
                <div className="v24-art-error" role="alert">
                  <b>请检查双人对戏参数</b>
                  <span>{duoErrorText || duoError}</span>
                </div>
              )}
            </div>
            <footer className="v32-modal-foot">
              <span className="v21-foot-note">
                {duoLInes.length} 句 · {AUDITION_STYLES.find((item) => item.id === duoStyle)?.label} · A {auditionBackgroundLabel(duoSceneA.background)}·{auditionCameraLabel(duoSceneA.camera)} / B {auditionBackgroundLabel(duoSceneB.background)}·{auditionCameraLabel(duoSceneB.camera)}
              </span>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => setDuoOpen(false)} disabled={duoSubmitting}>取消</button>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void submitDuo()} disabled={duoSubmitting || duoInvalid}>
                {duoSubmitting ? <Spinner size={12} /> : <IconUsers size={12} />} 生成双人对戏
              </button>
            </footer>
          </div>
        </div>
      )}

      {showcaseOpen && (
        <div className="v32-mask" onMouseDown={() => !showcaseBuilding && setShowcaseOpen(false)} role="presentation">
          <div className="v32-modal v36-showcase-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="试镜合集发布包">
            <header className="v32-modal-head">
              <div>
                <span className="v21-drawer-kicker">SHOWCASE · 试镜合集</span>
                <h3 className="v32-modal-title">试镜合集发布包</h3>
              </div>
              <button type="button" className="v21-close" onClick={() => setShowcaseOpen(false)} aria-label="关闭" disabled={showcaseBuilding}>
                <IconClose size={16} />
              </button>
            </header>
            <div className="v32-modal-body">
              <div className="v36-showcase-info">
                <div className="v36-showcase-cover"><IconFilm size={28} /></div>
                <div>
                  <b>角色试镜合集 · 10 角色</b>
                  <span>outputs/character_demos/character_audition_reel.mp4 · 1024×576 · 字幕已烧录</span>
                  <span>打包内容：视频 + 封面 + metadata + README + 各角色 SRT</span>
                </div>
              </div>
              <div className="v37-showcase-vertical">
                <label className={"v29-batchbar-toggle v37-vertical-toggle" + (showcaseVertical ? " is-on" : "")}>
                  <input type="checkbox" checked={showcaseVertical} onChange={(event) => setShowcaseVertical(event.target.checked)} disabled={showcaseBuilding} />
                  附带竖版 9:16（TikTok / IG）
                </label>
                <div className="v29-batchbar-modes" role="group" aria-label="合集竖版模式">
                  <button
                    type="button"
                    className={"v28-vmode-btn" + (showcaseVerticalMode === "blur_pad" ? " is-active" : "")}
                    onClick={() => setShowcaseVerticalMode("blur_pad")}
                    disabled={!showcaseVertical || showcaseBuilding}
                    title="模糊背景填充（完整保留 16:9 画面）"
                  >
                    模糊填充
                  </button>
                  <button
                    type="button"
                    className={"v28-vmode-btn" + (showcaseVerticalMode === "crop" ? " is-active" : "")}
                    onClick={() => setShowcaseVerticalMode("crop")}
                    disabled={!showcaseVertical || showcaseBuilding}
                    title="中心裁切 9:16（主体居中素材）"
                  >
                    中心裁切
                  </button>
                </div>
                <span className="v37-vertical-hint">打包竖版视频 1080×1920 + 竖版封面；重新构建按当前选项</span>
              </div>
              {showcasePkg && (
                <div className="v37-vertical-state">
                  {showcasePkg.files.some((file) => file.name.includes("_vertical")) ? (
                    <span className="v18-tag is-jade">当前包已含竖版</span>
                  ) : (
                    <span className="v18-tag">当前包不含竖版 · 点「重新构建」按新选项生成</span>
                  )}
                </div>
              )}
              {showcaseLoading && (
                <div className="v32-audition-loading"><Spinner size={14} /> 正在读取已有发布包…</div>
              )}
              {showcasePkg && (
                <div className="v36-showcase-pkg">
                  <div className="v36-showcase-pkg-head">
                    <span className="v18-tag is-jade">已生成</span>
                    {showcasePkg.cached && <span className="v18-tag">cached</span>}
                    <span className="cm-mono">{showcasePkg.files.length} 文件</span>
                    <span className="cm-mono">{fmtAuditionSize(showcasePkg.size_bytes || 0)}</span>
                    <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void downloadShowcase()} disabled={!showcasePkg.package_url}>
                      <IconDownload size={11} /> 下载 zip
                    </button>
                  </div>
                  <div className="v36-showcase-files">
                    {showcasePkg.files
                      .slice()
                      .sort((a, b) => Number(b.name.includes("_vertical")) - Number(a.name.includes("_vertical")))
                      .slice(0, 14)
                      .map((file) => (
                        <div key={file.name} className={"v36-showcase-file" + (file.name.includes("_vertical") ? " is-vertical" : "")}>
                          <span className="cm-mono">
                            {file.name}
                            {file.name.includes("_vertical") && <em className="v37-vertical-badge">竖版 9:16</em>}
                          </span>
                          <span className="cm-mono">{fmtAuditionSize(file.size_bytes || 0)}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}
              <div className="v38-split">
                <div className="v36-showcase-pkg-head">
                  <span className="v38-split-title">
                    <IconFilm size={12} /> 单角色切条包
                  </span>
                  <span className="v38-split-desc">每角色最新一条已烧录字幕试镜条（mp4 + srt + meta + manifest）</span>
                  {splitPkg && (
                    <>
                      <span className="v18-tag is-jade">{splitPkg.cached ? "cached" : "已生成"}</span>
                      <span className="cm-mono">{splitPkg.characters.length} 角色</span>
                      <span className="cm-mono">{splitPkg.files.length} 文件</span>
                      <span className="cm-mono">{fmtAuditionSize(splitPkg.size_bytes || 0)}</span>
                    </>
                  )}
                  {splitBuilding ? (
                    <span className="v38-split-loading"><Spinner size={11} /> 构建中…</span>
                  ) : (
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void buildSplitPackage(true)}>
                      重新构建（force）
                    </button>
                  )}
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void buildSplitPackage(false)} disabled={splitBuilding}>
                    {splitBuilding ? <Spinner size={11} /> : <IconDownload size={11} />} 构建切条包
                  </button>
                  {splitPkg?.package_url && (
                    <button type="button" className="cm-btn cm-btn-sm cm-btn-primary" onClick={() => void downloadSplitPackage()}>
                      <IconDownload size={11} /> 下载 zip
                    </button>
                  )}
                </div>
                {splitLoading && <div className="v36-showcase-file"><span>正在读取切条包…</span></div>}
                {splitPkg && (
                  <>
                    <div className="v38-split-chars">
                      {splitPkg.characters.slice(0, 16).map((character) => (
                        <span key={character} className="v18-tag">{character}</span>
                      ))}
                    </div>
                    <div className="v36-showcase-files">
                      {splitPkg.files.slice(0, 8).map((file) => (
                        <div key={file.name} className="v36-showcase-file">
                          <span className="cm-mono">{file.name}</span>
                          <span className="cm-mono">{fmtAuditionSize(file.size_bytes || 0)}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                {splitError && (
                  <div className="v24-art-error" role="alert">
                    <b>单角色切条包</b>
                    <span>{splitError}</span>
                  </div>
                )}
                <p className="v21-hint">切条包 = 每个角色一条独立试镜视频 + SRT + meta，按角色逐个发布；缺失试镜任务时原生提示。</p>
              </div>
              {showcaseError && (
                <div className="v24-art-error" role="alert">
                  <b>试镜合集发布包</b>
                  <span>{showcaseError}</span>
                </div>
              )}
              <p className="v32-modal-hint">发布包为 zip：video / cover / metadata.json / README.txt / subtitles/*.srt，可手动上传视频平台。</p>
            </div>
            <footer className="v32-modal-foot">
              <span className="v21-foot-note">{showcasePkg ? `已有包 · ${showcasePkg.cached ? "cached" : "新构建"}` : "首次导出会构建 zip（约数秒）"}</span>
              <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void buildShowcase(true)} disabled={showcaseBuilding}>
                {showcaseBuilding ? <Spinner size={12} /> : <IconRefresh size={12} />} 重新构建（force）
              </button>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void buildShowcase(false)} disabled={showcaseBuilding}>
                {showcaseBuilding ? <Spinner size={12} /> : <IconDownload size={12} />} 导出发布包
              </button>
            </footer>
          </div>
        </div>
      )}

      {promoOpen && selectedContract && (
        <div className="v32-mask" onMouseDown={() => !promoSubmitting && setPromoOpen(false)} role="presentation">
          <div className="v32-modal v35-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="生成角色宣传片">
            <header className="v32-modal-head">
              <div>
                <span className="v21-drawer-kicker">PROMO · 角色宣传片</span>
                <h3 className="v32-modal-title">生成角色宣传片 · {selectedContract.label}</h3>
              </div>
              <button type="button" className="v21-close" onClick={() => setPromoOpen(false)} aria-label="关闭" disabled={promoSubmitting}>
                <IconClose size={16} />
              </button>
            </header>
            <div className="v32-modal-body">
              <div className="v34-select-row">
                <label className="v21-field">
                  <span className="v21-field-label">风格</span>
                  <select className="v21-select" value={promoStyle} onChange={(event) => setPromoStyle(event.target.value as AuditionStyle)} disabled={promoSubmitting}>
                    {AUDITION_STYLES.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}{item.id === "realistic" ? "（默认）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="v21-field">
                  <span className="v21-field-label">标题（可选，片头副标题）</span>
                  <input
                    className="v21-input"
                    value={promoTitle}
                    maxLength={40}
                    placeholder="例如：老周 · 深夜电台主理人"
                    onChange={(event) => setPromoTitle(event.target.value)}
                    disabled={promoSubmitting}
                  />
                </label>
              </div>
              <div className="v35-scenes">
                {promoScenes.map((scene, index) => {
                  const info = promoSceneInfo[index];
                  return (
                    <div key={index} className={"v35-scene" + (info?.invalid ? " is-invalid" : "")}>
                      <div className="v35-scene-head">
                        <span>第 {index + 1} 镜</span>
                        {info && <span className="cm-mono">{info.estimate}s{info.invalid ? " · 超限" : ""}</span>}
                        {promoScenes.length > 1 && (
                          <button
                            type="button"
                            className="v35-scene-del"
                            disabled={promoSubmitting}
                            onClick={() => setPromoScenes((prev) => prev.filter((_, i) => i !== index))}
                            title="删除该镜"
                          >
                            <IconClose size={12} /> 删除
                          </button>
                        )}
                      </div>
                      <div className="v35-scene-row">
                        <select
                          className="v21-select"
                          value={scene.background}
                          onChange={(event) =>
                            setPromoScenes((prev) => prev.map((item, i) => (i === index ? { ...item, background: event.target.value as AuditionBackground } : item)))
                          }
                          disabled={promoSubmitting}
                          aria-label={`第 ${index + 1} 镜背景`}
                        >
                          {AUDITION_BACKGROUNDS.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                        <select
                          className="v21-select"
                          value={scene.camera}
                          onChange={(event) =>
                            setPromoScenes((prev) => prev.map((item, i) => (i === index ? { ...item, camera: event.target.value as AuditionCamera } : item)))
                          }
                          disabled={promoSubmitting}
                          aria-label={`第 ${index + 1} 镜机位`}
                        >
                          {AUDITION_CAMERAS.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <textarea
                        className="v21-textarea v35-scene-text"
                        value={scene.text}
                        rows={2}
                        disabled={promoSubmitting}
                        placeholder={"每行一句台词（1–2 行，单行 ≤60 字）"}
                        onChange={(event) =>
                          setPromoScenes((prev) => prev.map((item, i) => (i === index ? { ...item, text: event.target.value } : item)))
                        }
                      />
                    </div>
                  );
                })}
              </div>
              {promoScenes.length < 3 && (
                <button
                  type="button"
                  className="cm-btn cm-btn-sm cm-btn-ghost"
                  disabled={promoSubmitting}
                  onClick={() =>
                    setPromoScenes((prev) => [...prev, { background: "radio", camera: "medium", text: "" }])
                  }
                >
                  <IconPlus size={12} /> 添加第 {promoScenes.length + 1} 镜
                </button>
              )}
              <p className="v32-modal-hint">约 6–12 分钟（多镜 GPU 串行 + 拼接；片头 2.2s + 每镜场景卡 1.0s）· 可离开页面，回来自动恢复。</p>
              {(promoErrorText || promoError) && (
                <div className="v24-art-error" role="alert">
                  <b>请检查宣传片参数</b>
                  <span>{promoErrorText || promoError}</span>
                </div>
              )}
            </div>
            <footer className="v32-modal-foot">
              <span className="v21-foot-note">{promoScenes.length} 镜 · {AUDITION_STYLES.find((item) => item.id === promoStyle)?.label} · 片头 + 场景卡拼接</span>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => setPromoOpen(false)} disabled={promoSubmitting}>
                取消
              </button>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void submitPromo()} disabled={promoSubmitting || promoInvalid}>
                {promoSubmitting ? <Spinner size={12} /> : <IconSpark size={12} />} 生成角色宣传片
              </button>
            </footer>
          </div>
        </div>
      )}

      {auditionOpen && selectedContract && (
        <div className="v32-mask" onMouseDown={() => !auditionSubmitting && setAuditionOpen(false)} role="presentation">
          <div className="v32-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="生成试镜视频">
            <header className="v32-modal-head">
              <div>
                <span className="v21-drawer-kicker">AUDITION · 角色试镜</span>
                <h3 className="v32-modal-title">生成试镜视频 · {selectedContract.label}</h3>
              </div>
              <button type="button" className="v21-close" onClick={() => setAuditionOpen(false)} aria-label="关闭" disabled={auditionSubmitting}>
                <IconClose size={16} />
              </button>
            </header>
            <div className="v32-modal-body">
              <label className="v21-field">
                <span className="v21-field-label">立绘版本</span>
                <select
                  className="v21-select"
                  value={auditionStyle}
                  onChange={(event) => setAuditionStyle(event.target.value as AuditionStyle)}
                  disabled={auditionSubmitting}
                >
                  {AUDITION_STYLES.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}版立绘
                    </option>
                  ))}
                </select>
              </label>
              <label className="v21-field">
                <span className="v21-field-label">试镜台词（每行一句，最多 6 行，空行忽略）</span>
                <textarea
                  className="v21-textarea v32-textarea"
                  value={auditionText}
                  rows={5}
                  onChange={(event) => setAuditionText(event.target.value)}
                  disabled={auditionSubmitting}
                  placeholder={"第一句台词\n第二句台词\n第三句台词"}
                />
                <div className="v32-line-meta">
                  <span className={auditionTooManyLines ? "is-error" : ""}>{auditionLines.length}/6 行</span>
                  <span className={auditionEstimateTooLong ? "is-error" : ""}>预计约 {auditionEstimateSec}s（上限 12s）</span>
                  <span className="v21-hint">空行自动忽略；单行 ≤80 字</span>
                </div>
                {auditionTextError && (
                  <span className="v32-line-error" role="alert">
                    {auditionTextError}
                  </span>
                )}
              </label>
              <div className="v34-select-row">
                <label className="v21-field">
                  <span className="v21-field-label">背景</span>
                  <select
                    className="v21-select"
                    value={auditionBackground}
                    onChange={(event) => setAuditionBackground(event.target.value as AuditionBackground)}
                    disabled={auditionSubmitting}
                  >
                    {AUDITION_BACKGROUNDS.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}{item.id === "studio" ? "（默认）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="v21-field">
                  <span className="v21-field-label">机位</span>
                  <select
                    className="v21-select"
                    value={auditionCamera}
                    onChange={(event) => setAuditionCamera(event.target.value as AuditionCamera)}
                    disabled={auditionSubmitting}
                  >
                    {AUDITION_CAMERAS.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}{item.id === "medium" ? "（默认）" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="v33-subtitle-toggle">
                <input
                  type="checkbox"
                  checked={auditionSubtitles}
                  onChange={(event) => setAuditionSubtitles(event.target.checked)}
                  disabled={auditionSubmitting}
                />
                <span>
                  <b>烧录字幕</b>
                  <em>白字黑边、底部居中；完成后同时提供无字幕版与 SRT 字幕</em>
                </span>
              </label>
              <p className="v32-modal-hint">约 3–4 分钟（GPU 串行）· 可离开页面，回到角色详情自动恢复进度。</p>
              {auditionError && (
                <div className="v24-art-error" role="alert">
                  <b>提交失败</b>
                  <span>{auditionError}</span>
                </div>
              )}
            </div>
            <footer className="v32-modal-foot">
              <span className="v21-foot-note">完成后可在角色详情在线播放 / 下载。</span>
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => setAuditionOpen(false)} disabled={auditionSubmitting}>
                取消
              </button>
              <button
                type="button"
                className="cm-btn cm-btn-primary cm-btn-sm"
                onClick={() => void submitAudition()}
                disabled={auditionSubmitting || auditionTextInvalid}
                title={auditionTextInvalid ? auditionTextError : "提交试镜任务"}
              >
                {auditionSubmitting ? <Spinner size={12} /> : <IconFilm size={12} />} 生成试镜视频
              </button>
            </footer>
          </div>
        </div>
      )}

      {lightbox && (
        <div className="v28-lightbox" onMouseDown={() => setLightbox(null)} role="presentation">
          <div className="v28-lightbox-body" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={lightbox.label}>
            <header className="v28-lightbox-head">
              <span>{lightbox.label}</span>
              <button type="button" className="v21-close" onClick={() => setLightbox(null)} aria-label="关闭预览">
                <IconClose size={16} />
              </button>
            </header>
            <img src={lightbox.src} alt={lightbox.label} />
          </div>
        </div>
      )}
    </AppShell>
  );
}
