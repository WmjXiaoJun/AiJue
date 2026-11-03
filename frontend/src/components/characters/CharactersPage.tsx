// V18 · 角色与声音（#/characters）：32 角色网格 + 声音 chip/试听 + 详情抽屉（资料/外观/概念图 + 音色库）
// 数据：GET /api/v5/catalog（characters/templates）、GET /api/v5/characters/{id}（合同：persona/era/appearance/art/voice）、
//       GET /api/v5/voices（24 preset）、GET/PUT /api/v5/voice/{character}、POST /api/v5/voice/{character}/preview。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorText } from "../../lib/api";
import type { V5CatalogResponse, V5CharacterSummary } from "../../lib/v5";
import { mediaSrc, characterArtUrl, characterPreviewUrl, v5api } from "../../lib/v5";
import { creationApi, creationErrorMessage } from "../../lib/creation";
import type { CharacterAssets } from "../../lib/creation";
import type { CharacterContract, VoicePreset } from "../../lib/voice";
import { fetchCharacterContract, voiceApi } from "../../lib/voice";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconCheck,
  IconClose,
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
                <div className="v18-drawer-art">
                  <CharacterPortrait
                    characterId={selectedContract.id}
                    label={selectedContract.label}
                    portrait={selectedContract.art.portrait}
                    bust={artBust[selectedContract.id]}
                  />
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
