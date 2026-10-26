// V5 世界：把 V5 能力（成片/模板/风格/场景/角色/渲染）绑定进工作台
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { V5CatalogResponse, V5Output } from "../lib/v5";
import { v5api } from "../lib/v5";
import { useToast } from "./Toast";
import { IconFilm, IconGrid, IconPalette, IconRefresh, IconSpark, IconTemplate, IconUser } from "./icons";
import { Spinner } from "./ui";
import { Gallery } from "./v5/Gallery";
import { Templates } from "./v5/Templates";
import { StyleWall } from "./v5/StyleWall";
import { SceneLibrary } from "./v5/SceneLibrary";
import { CharacterLibrary } from "./v5/CharacterLibrary";
import { RenderPanel } from "./v5/RenderPanel";
import { AgentPanel } from "./v5/AgentPanel";

type V5Tab = "gallery" | "templates" | "styles" | "scenes" | "characters" | "render" | "agent";

const TAB_KEYS: V5Tab[] = ["gallery", "templates", "styles", "scenes", "characters", "render", "agent"];

/** 子 tab 与 hash 双向同步（#/v5/gallery、#/v5/scenes… 可直接打开/截图） */
function tabFromHash(): V5Tab {
  const match = window.location.hash.match(/^#\/v5\/([a-z]+)/i);
  const key = (match?.[1] || "").toLowerCase() as V5Tab;
  return TAB_KEYS.includes(key) ? key : "gallery";
}

function hashForTab(tab: V5Tab): string {
  return "#/v5/" + tab;
}

const TABS: { key: V5Tab; label: string; icon: ReactNode }[] = [
  { key: "gallery", label: "成片画廊", icon: <IconFilm size={15} /> },
  { key: "templates", label: "模板库", icon: <IconTemplate size={15} /> },
  { key: "styles", label: "风格墙", icon: <IconPalette size={15} /> },
  { key: "scenes", label: "场景库", icon: <IconGrid size={15} /> },
  { key: "characters", label: "角色库", icon: <IconUser size={15} /> },
  { key: "render", label: "渲染", icon: <IconSpark size={15} /> },
  { key: "agent", label: "智能体", icon: <IconSpark size={15} /> },
];

export function V5Studio() {
  const notify = useToast();
  const [tab, setTabState] = useState<V5Tab>(() => tabFromHash());
  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [outputs, setOutputs] = useState<V5Output[]>([]);
  const [expected, setExpected] = useState(16);
  const [includeCustom, setIncludeCustom] = useState(false);
  const [loading, setLoading] = useState(true);
  const [outputsLoading, setOutputsLoading] = useState(true);
  const [error, setError] = useState("");
  const [presetTemplate, setPresetTemplate] = useState<string | null>(null);

  const setTab = useCallback((next: V5Tab) => {
    setTabState(next);
    if (window.location.hash !== hashForTab(next)) {
      window.history.replaceState(null, "", hashForTab(next));
    }
  }, []);

  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await v5api.catalog();
      setCatalog(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadOutputs = useCallback(async (withCustom = includeCustom) => {
    setOutputsLoading(true);
    try {
      const res = await v5api.outputs(withCustom);
      setOutputs(res.outputs || []);
      setExpected(res.expected || (res.outputs || []).length);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOutputsLoading(false);
    }
  }, [includeCustom]);

  useEffect(() => {
    void loadCatalog();
    void loadOutputs(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadCatalog(), loadOutputs()]);
    notify("V5 目录已刷新", "success");
  }, [loadCatalog, loadOutputs, notify]);

  const handleToggleCustom = useCallback(
    (value: boolean) => {
      setIncludeCustom(value);
      void loadOutputs(value);
    },
    [loadOutputs]
  );

  const counts = useMemo(
    () => ({
      styles: catalog?.counts?.styles ?? catalog?.styles.length ?? 0,
      scenes: catalog?.counts?.scenes ?? catalog?.scenes.length ?? 0,
      characters: catalog?.counts?.characters ?? catalog?.characters.length ?? 0,
      templates: catalog?.counts?.templates ?? catalog?.templates.length ?? 0,
      props: catalog?.counts?.props ?? 0,
      packs: catalog?.counts?.packs ?? 0,
    }),
    [catalog]
  );

  const openRenderWithTemplate = useCallback(
    (templateId: string) => {
      setPresetTemplate(templateId);
      setTab("render");
    },
    [setTab]
  );

  const renderBody = () => {
    if (tab === "gallery") {
      return (
        <Gallery
          outputs={outputs}
          loading={outputsLoading}
          includeCustom={includeCustom}
          expected={expected}
          onToggleCustom={handleToggleCustom}
          onRefresh={() => void loadOutputs()}
        />
      );
    }
    if (tab === "templates") {
      return (
        <Templates
          catalog={catalog}
          outputs={outputs}
          loading={loading}
          onOpenRender={openRenderWithTemplate}
          onOpenCharacters={() => setTab("characters")}
        />
      );
    }
    if (tab === "styles") return <StyleWall catalog={catalog} loading={loading} />;
    if (tab === "scenes") return <SceneLibrary catalog={catalog} loading={loading} />;
    if (tab === "characters") return <CharacterLibrary catalog={catalog} loading={loading} />;
    if (tab === "agent") return <AgentPanel />;
    return (
      <RenderPanel
        catalog={catalog}
        outputs={outputs}
        loading={outputsLoading}
        onRendered={() => void loadOutputs()}
        presetTemplate={presetTemplate}
      />
    );
  };

  return (
    <div className="v5-view">
      <header className="v5-head">
        <div className="v5-head-main">
          <div className="v5-head-title">
            <span className="v5-head-kicker">V5 · VARIABLE STYLE WORLD</span>
            <h1>
              V5 世界 <span className="v5-head-sub">可变风格 · 全量绑定</span>
            </h1>
          </div>
          <div className="v5-head-stats">
            <span>
              <b>{outputs.length}</b> 成片
            </span>
            <span>
              <b>{counts.styles}</b> 风格
            </span>
            <span>
              <b>{counts.scenes}</b> 场景
            </span>
            <span>
              <b>{counts.characters}</b> 角色
            </span>
            <span>
              <b>{counts.templates}</b> 模板
            </span>
            <span>
              <b>{counts.props}</b> 道具
            </span>
          </div>
        </div>
        <div className="v5-head-actions">
          <button type="button" className="btn btn-ghost" onClick={() => void refreshAll()} disabled={loading}>
            {loading ? <Spinner size={14} /> : <IconRefresh size={14} />} 刷新
          </button>
        </div>
      </header>

      <nav className="v5-tabs">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={"v5-tab" + (tab === item.key ? " active" : "")}
            onClick={() => setTab(item.key)}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
        <div className="v5-tabs-spacer" />
        <span className="v5-muted v5-root-hint" title={catalog?.root || ""}>
          contract/v5 · {catalog?.root ? catalog.root.split(/[\\/]/).slice(-2).join("/") : "—"}
        </span>
      </nav>

      {error && (
        <div className="v5-banner v5-banner-error">
          后端接口不可用：{error}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refreshAll()}>
            重试
          </button>
        </div>
      )}
      {!catalog && loading && (
        <div className="v5-loading">
          <Spinner size={18} /> 正在读取 V5 目录（contract/v5）…
        </div>
      )}

      <div className="v5-body">{renderBody()}</div>
    </div>
  );
}
