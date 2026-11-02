// V5 World（#/v5/<tab>）· 外壳：Cinema Studio 设计语言的全屏 V5 世界
// 6 个子页：成片画廊 / 模板库 / 风格墙 / 场景库 / 角色库 / 渲染台。
// 数据：GET /api/v5/catalog、GET /api/cinema/templates（3D 成片；V17 起含 real_* 新模板，数量动态）
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { V5CatalogResponse } from "../../lib/v5";
import { v5api } from "../../lib/v5";
import type { CinemaTemplate } from "../../lib/cinema";
import { TEMPLATE_3D_TOTAL, cinemaApi } from "../../lib/cinema";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAlert,
  IconFilm,
  IconGrid,
  IconImage,
  IconPalette,
  IconRefresh,
  IconSpark,
  IconTemplate,
  IconUser,
} from "../icons";
import { V5Gallery } from "./Gallery";
import { V5Templates } from "./Templates";
import { V5Styles } from "./Styles";
import { V5Scenes } from "./Scenes";
import { V5Characters } from "./Characters";
import { V5Render } from "./Render";
import "../../styles/cinema.css";
import "../../styles/v5world.css";

export type V5WorldTab = "gallery" | "templates" | "styles" | "scenes" | "characters" | "render";

const TAB_KEYS: V5WorldTab[] = ["gallery", "templates", "styles", "scenes", "characters", "render"];

interface TabMeta {
  label: string;
  en: string;
  no: string;
  desc: string;
  icon: JSX.Element;
}

const TAB_META: Record<V5WorldTab, TabMeta> = {
  gallery: { label: "成片画廊", en: "GALLERY", no: "01", desc: "3D 成片画廊 · 电影版优先", icon: <IconFilm size={17} /> },
  templates: { label: "模板库", en: "TEMPLATES", no: "02", desc: "V5 模板 · 风格/时长/画幅/幕结构", icon: <IconTemplate size={17} /> },
  styles: { label: "风格墙", en: "STYLES", no: "03", desc: "视觉风格 · 色板/技法", icon: <IconPalette size={17} /> },
  scenes: { label: "场景库", en: "SCENES", no: "04", desc: "分层绘景场景", icon: <IconGrid size={17} /> },
  characters: { label: "角色库", en: "CHARACTERS", no: "05", desc: "固定声音角色", icon: <IconUser size={17} /> },
  render: { label: "渲染台", en: "RENDER", no: "06", desc: "选模板 + 时长 → render_v5 任务", icon: <IconSpark size={17} /> },
};

function tabFromHash(): V5WorldTab {
  const match = window.location.hash.match(/^#\/v5\/?([a-z]*)/i);
  const key = (match?.[1] || "").toLowerCase() as V5WorldTab;
  return TAB_KEYS.includes(key) ? key : "gallery";
}

export interface V5PageProps {
  catalog: V5CatalogResponse | null;
  /** 3D 成片（key = 模板 id；GET /api/cinema/templates，唯一成片数据源；V17 起含 real_* 新模板） */
  templates3d: Record<string, CinemaTemplate>;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onNavigate: (tab: V5WorldTab) => void;
  presetTemplate?: string | null;
  onRenderTemplate?: (id: string) => void;
}

export function V5World({ onExit, onCinema }: { onExit: () => void; onCinema: () => void }) {
  const notify = useToast();

  const [tab, setTab] = useState<V5WorldTab>(() => tabFromHash());
  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [templates3d, setTemplates3d] = useState<Record<string, CinemaTemplate>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [templates3dError, setTemplates3dError] = useState("");
  const [presetTemplate, setPresetTemplate] = useState<string | null>(null);

  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const load = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    const [catRes, tplRes] = await Promise.allSettled([v5api.catalog(), cinemaApi.templates()]);
    if (!aliveRef.current) {
      inFlightRef.current = false;
      return;
    }
    if (catRes.status === "fulfilled") {
      setCatalog(catRes.value);
      setError("");
    } else {
      setError(errMessage(catRes.reason));
    }
    if (tplRes.status === "fulfilled") {
      const map: Record<string, CinemaTemplate> = {};
      for (const item of tplRes.value.templates) map[item.id] = item;
      setTemplates3d(map);
      setTemplates3dError("");
    } else {
      setTemplates3dError(errMessage(tplRes.reason));
      setTemplates3d({});
    }
    inFlightRef.current = false;
    if (aliveRef.current) {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  // tab 与 hash 双向同步（支持直接打开 /#/v5/scenes）
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: V5WorldTab) => {
    setTab(next);
    const hash = "#/v5/" + next;
    if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
  }, []);

  // `#/v5`（无子 tab）归一化到成片画廊，避免刷新后空白路由
  useEffect(() => {
    if (/^#\/v5\/?$/i.test(window.location.hash)) navigate("gallery");
  }, [navigate]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await load(true);
    if (aliveRef.current) notify("V5 目录已刷新", "success");
  }, [load, notify]);

  const renderPreset = useCallback(
    (id: string) => {
      setPresetTemplate(id);
      navigate("render");
    },
    [navigate],
  );

  const stats = useMemo(() => {
    const templates = catalog?.templates || [];
    const threeD = Object.values(templates3d);
    return {
      styles: catalog?.styles.length || 0,
      scenes: catalog?.scenes.length || 0,
      characters: catalog?.characters.length || 0,
      props: catalog?.props?.length || 0,
      templates: templates.length,
      threeDDone: threeD.filter((item) => item.final?.exists).length,
      threeDExpected: threeD.length || TEMPLATE_3D_TOTAL,
      threeDClipsDone: threeD.reduce((sum, item) => sum + (item.clips.done || 0), 0),
      threeDClipsTotal: threeD.reduce((sum, item) => sum + (item.clips.total || 0), 0),
    };
  }, [catalog, templates3d]);

  const pageProps: V5PageProps = {
    catalog,
    templates3d,
    loading,
    error,
    onRefresh: () => void refresh(),
    onNavigate: navigate,
    presetTemplate,
    onRenderTemplate: renderPreset,
  };

  return (
    <div className="cm-app v5w-app">
      <aside className="cm-rail">
        <button type="button" className="cm-brand" onClick={() => navigate("gallery")} title="V5 World">
          <span className="cm-brand-seal">造</span>
          <span className="cm-brand-txt">
            <span className="cm-brand-name">V5 世界</span>
            <span className="cm-brand-sub">Variable Style</span>
          </span>
        </button>

        <div className="cm-rail-label">V5 内容库</div>
        <nav className="cm-nav">
          {TAB_KEYS.map((key) => (
            <button
              key={key}
              type="button"
              className={"cm-nav-item" + (tab === key ? " is-active" : "")}
              onClick={() => navigate(key)}
              title={TAB_META[key].desc}
            >
              {TAB_META[key].icon}
              <span className="cm-nav-label">{TAB_META[key].label}</span>
              <span className="cm-nav-no">{TAB_META[key].no}</span>
            </button>
          ))}
        </nav>

        <div className="cm-rail-foot">
          <div className="cm-env-row" title="GET /api/v5/catalog">
            <span className="cm-env-key">
              <i className={"cm-env-dot " + (catalog ? "is-on" : error ? "is-off" : "is-warn")} />
              <span className="cm-env-text">{catalog ? "V5 目录已连接" : error ? "V5 目录离线" : "读取中…"}</span>
            </span>
          </div>
          <div className="cm-env-row">
            <span className="cm-env-key">
              <i className="cm-env-dot is-on" />
              <span className="cm-env-text">
                {stats.styles} 风格 · {stats.scenes} 场景
              </span>
            </span>
          </div>
          <div className="cm-env-row">
            <span className="cm-env-key">
              <i className="cm-env-dot is-on" />
              <span className="cm-env-text">
                3D 成片 {stats.threeDDone}/{stats.threeDExpected}
              </span>
            </span>
          </div>
          <button type="button" className="cm-back-link" onClick={onCinema} title="Cinema Studio · 3D 电影制片">
            <IconFilm size={14} /> <span className="cm-env-text">Cinema 3D</span>
          </button>
          <a className="cm-back-link" href="#/pipeline" title="一键成片 · 制片控制台">
            <IconSpark size={14} /> <span className="cm-env-text">一键成片</span>
          </a>
          <button type="button" className="cm-back-link" onClick={onExit} title="返回项目大厅">
            <IconArrowLeft /> <span className="cm-env-text">返回平台</span>
          </button>
        </div>
      </aside>

      <div className="cm-main">
        <header className="cm-topbar">
          <div className="cm-top-project">
            <span className="cm-kicker">V5 · VARIABLE STYLE WORLD · 可变风格世界</span>
            <div className="cm-top-title-row">
              <h1 className="cm-top-title">V5 世界</h1>
              <span className="cm-top-ep">
                {TAB_META[tab].no} / {TAB_META[tab].en} · {TAB_META[tab].label}
              </span>
            </div>
          </div>

          <div className="cm-top-stats">
            <div className="cm-top-stat">
              <b style={{ color: "var(--c-gold)" }}>
                {stats.threeDDone}
                <span style={{ color: "var(--c-mut)" }}>/{stats.threeDExpected}</span>
              </b>
              <span>3D 成片</span>
            </div>
            <div className="cm-top-stat">
              <b className="is-jade">
                {stats.threeDClipsDone}
                <span style={{ color: "var(--c-mut)" }}>/{stats.threeDClipsTotal || "—"}</span>
              </b>
              <span>3D 镜头</span>
            </div>
            <div className="cm-top-stat">
              <b>
                {stats.styles}
                <span style={{ color: "var(--c-mut)" }}>/{stats.scenes}</span>
              </b>
              <span>风格 / 场景</span>
            </div>
            <div className="cm-top-stat">
              <b>{stats.threeDExpected}</b>
              <span>3D 模板</span>
            </div>
          </div>

          <div className="cm-top-actions">
            <button
              type="button"
              className={"cm-icon-btn" + (refreshing ? " is-busy" : "")}
              onClick={() => void refresh()}
              title="刷新 V5 目录 / 成片 / 3D 状态"
            >
              {refreshing ? <Spinner size={14} /> : <IconRefresh />}
            </button>
            <button type="button" className="cm-btn cm-btn-sm" onClick={onCinema}>
              <IconFilm size={14} /> Cinema 3D
            </button>
          </div>
        </header>

        <main className="cm-content">
          {(error || templates3dError) && (
            <div className="v5w-banners">
              {error && (
                <div className="cm-error">
                  <IconAlert size={16} />
                  <span>V5 目录读取失败：{error}</span>
                  <button type="button" className="cm-btn cm-btn-sm" onClick={() => void load(false)} disabled={loading}>
                    重试
                  </button>
                </div>
              )}
              {templates3dError && (
                <div className="cm-error is-soft">
                  <IconAlert size={15} />
                  <span>3D 成片状态不可用：{templates3dError}</span>
                </div>
              )}
            </div>
          )}

          {tab === "gallery" ? (
            <V5Gallery {...pageProps} />
          ) : tab === "templates" ? (
            <V5Templates {...pageProps} />
          ) : tab === "styles" ? (
            <V5Styles {...pageProps} />
          ) : tab === "scenes" ? (
            <V5Scenes {...pageProps} />
          ) : tab === "characters" ? (
            <V5Characters {...pageProps} />
          ) : (
            <V5Render {...pageProps} onRendered={() => void load(true)} />
          )}
        </main>
      </div>
    </div>
  );
}

function errMessage(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  return String(reason || "未知错误");
}

function IconArrowLeft() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 6 8 12l6 6" />
    </svg>
  );
}

// 供页面复用的空态小图标（避免每页各写一份 SVG）
export function V5EmptyIcon() {
  return <IconImage size={26} />;
}
