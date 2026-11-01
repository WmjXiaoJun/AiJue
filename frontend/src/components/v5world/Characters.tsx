// V5 World · 角色库：25 个固定声音角色（立绘 / 程序化预览回退、声线、风格标签、三视图）。
// 数据：GET /api/v5/catalog（摘要）+ GET /api/v5/characters/{id}（persona/appearance/art）。
import { useEffect, useMemo, useState } from "react";
import { characterArtUrl, characterPreviewUrl, defaultStyleForCharacter, v5api } from "../../lib/v5";
import type { V5CatalogResponse, V5CharacterSummary } from "../../lib/v5";
import type { V5PageProps } from "./V5World";
import { IconAlert, IconClose, IconRefresh, IconSearch, IconUser } from "../icons";
import { Spinner } from "../ui";

interface CharacterDetail {
  era?: string;
  persona?: string;
  voice?: { voice_id?: string; provider?: string };
  voice_legacy?: { instruction?: string; seed?: number; cfg_scale?: number };
  appearance?: {
    gender?: string;
    age?: number;
    hair?: string;
    hair_style?: string;
    outfit?: { type?: string; main?: string; accent?: string };
    accessories?: string[];
  };
  poses?: string[];
  mouth?: string[];
  style_overrides?: Record<string, Record<string, number>>;
  art?: { portrait?: string; front?: string; cutout?: string };
}

const ROLE_TEXT: Record<string, string> = {
  host: "主持",
  guest_a: "嘉宾A",
  guest_b: "嘉宾B",
  narrator: "旁白",
  expert: "专家",
  figure_a: "人物A",
  figure_b: "人物B",
};

export function V5Characters({ catalog, loading, error, onRefresh }: V5PageProps) {
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [openId, setOpenId] = useState<string | null>(null);

  const characters = catalog?.characters || [];

  const roles = useMemo(() => {
    const set = new Set<string>();
    characters.forEach((character) => (character.role_pool || []).forEach((role) => set.add(role)));
    return [...set].sort();
  }, [characters]);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return characters.filter((character) => {
      if (roleFilter !== "all" && !(character.role_pool || []).includes(roleFilter)) return false;
      if (!keyword) return true;
      return [character.id, character.label, character.voice_provider, ...(character.tags || []), ...(character.role_pool || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
  }, [characters, roleFilter, search]);

  const stats = useMemo(() => {
    const hosts = characters.filter((character) => (character.role_pool || []).includes("host")).length;
    const providers = new Set(characters.map((character) => character.voice_provider).filter(Boolean));
    return { total: characters.length, roles: roles.length, hosts, providers: providers.size };
  }, [characters, roles.length]);

  const openCharacter = openId ? characters.find((character) => character.id === openId) || null : null;

  return (
    <div className="cm-page v5w-page">
      <header className="cm-page-head">
        <div className="cm-head-main">
          <span className="cm-kicker">CHARACTERS · 角色库</span>
          <h1 className="cm-h1">角色库</h1>
          <p className="cm-sub">
            {stats.total} 个原创角色 · {stats.roles} 种角色池 · {stats.hosts} 个主持 · 固定音色 {stats.total} 套（
            {stats.providers} 家引擎）
          </p>
        </div>
        <div className="cm-head-actions">
          <button type="button" className="cm-icon-btn" onClick={onRefresh} disabled={loading} title="刷新角色库">
            {loading ? <Spinner size={14} /> : <IconRefresh />}
          </button>
        </div>
      </header>

      <section className="st-kpis v5w-kpis">
        <div className="st-kpi">
          <span className="st-kpi-label">Characters</span>
          <div className="st-kpi-value">
            {stats.total}
            <small>个角色</small>
          </div>
          <div className="st-kpi-hint">contract/v5/characters</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Role Pools</span>
          <div className="st-kpi-value">
            {stats.roles}
            <small>种角色池</small>
          </div>
          <div className="st-kpi-hint">主持 / 嘉宾 / 旁白 / 人物</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Hosts</span>
          <div className="st-kpi-value is-gold">
            {stats.hosts}
            <small>个主持</small>
          </div>
          <div className="st-kpi-hint">role_pool 含 host</div>
        </div>
        <div className="st-kpi">
          <span className="st-kpi-label">Voice Providers</span>
          <div className="st-kpi-value is-jade">
            {stats.providers}
            <small>家</small>
          </div>
          <div className="st-kpi-hint">固定音色引擎</div>
        </div>
      </section>

      <div className="st-toolbar v5w-toolbar">
        <label className="st-search">
          <IconSearch size={14} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索角色 / 声线 / 角色池"
            aria-label="搜索角色"
          />
          {search && (
            <button type="button" className="v5w-clear" onClick={() => setSearch("")} aria-label="清空搜索">
              <IconClose size={12} />
            </button>
          )}
        </label>
        <div className="st-seg" role="tablist" aria-label="角色池筛选">
          <button type="button" className={roleFilter === "all" ? "is-active" : ""} onClick={() => setRoleFilter("all")}>
            全部
          </button>
          {roles.map((role) => (
            <button key={role} type="button" className={roleFilter === role ? "is-active" : ""} onClick={() => setRoleFilter(role)}>
              {ROLE_TEXT[role] || role}
            </button>
          ))}
        </div>
        <div className="st-toolbar-right">
          <span className="v5w-count cm-mono">
            {filtered.length} / {characters.length}
          </span>
        </div>
      </div>

      {error && !characters.length ? (
        <div className="cm-error v5w-error">
          <IconAlert size={16} />
          <span>角色库读取失败：{error}</span>
          <button type="button" className="cm-btn cm-btn-sm" onClick={onRefresh}>
            重试
          </button>
        </div>
      ) : loading && !characters.length ? (
        <div className="cm-loading">
          <Spinner size={16} /> 正在读取角色库…
        </div>
      ) : filtered.length === 0 ? (
        <div className="cm-empty v5w-empty">
          <IconUser size={26} />
          <span>没有匹配的角色</span>
        </div>
      ) : (
        <div className="v5w-char-grid">
          {filtered.map((character) => (
            <CharacterCard
              key={character.id}
              character={character}
              catalog={catalog}
              onOpen={() => setOpenId(character.id)}
            />
          ))}
        </div>
      )}

      {openCharacter && <CharacterDrawer character={openCharacter} catalog={catalog} fallback={fallbackStyle(catalog, openCharacter)} onClose={() => setOpenId(null)} />}
    </div>
  );
}

/* ------------------------------ 角色卡 ------------------------------ */
function CharacterCard({
  character,
  catalog,
  onOpen,
}: {
  character: V5CharacterSummary;
  catalog: V5CatalogResponse | null;
  onOpen: () => void;
}) {
  const styleId = fallbackStyle(catalog, character);
  const sources = [
    characterArtUrl(character.id, "portrait_25d.png"),
    characterArtUrl(character.id, "portrait.png"),
    characterPreviewUrl(character.id, styleId),
  ];
  return (
    <button type="button" className="v5w-char-card" onClick={onOpen} title={`${character.label} · ${character.id}`}>
      <div className="v5w-char-portrait">
        <CharImage sources={sources} alt={character.label} fallback={character.label.slice(0, 1)} />
        <span className="v5w-char-mask" />
        {character.has_image && <span className="v5w-char-art-badge">AI 立绘</span>}
        {character.custom && <span className="v5w-char-custom">custom</span>}
        <span className="v5w-char-name">
          <b>{character.label}</b>
          <em className="cm-mono">{character.id}</em>
        </span>
      </div>
      <div className="v5w-char-body">
        <div className="v5w-char-meta cm-mono">
          <span>{genderText(character.gender)}</span>
          <span>{character.age ?? "?"} 岁</span>
          <span>{character.voice_provider || "—"}</span>
        </div>
        <div className="v5w-char-roles">
          {(character.role_pool || []).slice(0, 3).map((role) => (
            <span key={role} className="v5w-tag is-gold">
              {ROLE_TEXT[role] || role}
            </span>
          ))}
          {(character.tags || []).slice(0, 2).map((tag) => (
            <span key={tag} className="v5w-tag">
              {tag}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}

/* ------------------------------ 角色抽屉 ------------------------------ */
function CharacterDrawer({
  character,
  catalog,
  fallback,
  onClose,
}: {
  character: V5CharacterSummary;
  catalog: V5CatalogResponse | null;
  fallback?: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    v5api
      .item("characters", character.id)
      .then((res) => {
        if (alive) setDetail((res.item || {}) as CharacterDetail);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [character.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const styleId = fallback || fallbackStyle(catalog, character);
  const styles = detail?.style_overrides ? Object.keys(detail.style_overrides) : [];
  const appearance = detail?.appearance || null;
  const outfit = appearance?.outfit || null;

  return (
    <>
      <div className="st-drawer-mask" onClick={onClose} />
      <aside className="st-drawer v5w-char-drawer" role="dialog" aria-modal="true" aria-label={character.label}>
        <header className="st-drawer-head">
          <div className="v5w-char-drawer-title">
            <h3 className="st-drawer-title">{character.label}</h3>
            <span className="st-drawer-sub">
              {character.id} · {genderText(character.gender)} · {character.age ?? "?"} 岁 · {detail?.era || "—"}
            </span>
          </div>
          <button type="button" className="st-drawer-close" onClick={onClose} aria-label="关闭">
            <IconClose size={16} />
          </button>
        </header>
        <div className="st-drawer-body">
          <div className="v5w-char-drawer-hero">
            <CharImage
              sources={[
                characterArtUrl(character.id, "portrait_25d.png"),
                characterArtUrl(character.id, "portrait.png"),
                characterPreviewUrl(character.id, styleId),
              ]}
              alt={character.label}
              fallback={character.label.slice(0, 1)}
            />
          </div>

          {loading ? (
            <div className="cm-loading">
              <Spinner size={14} /> 读取角色合同…
            </div>
          ) : error ? (
            <div className="cm-error">
              <IconAlert size={14} />
              <span>详情读取失败：{error}</span>
            </div>
          ) : (
            <>
              {detail?.persona && <p className="v5w-detail-desc">{detail.persona}</p>}

              <div className="v5w-detail-block">
                <span className="v5w-detail-label">固定声音</span>
                <p className="v5w-detail-text cm-mono">
                  {detail?.voice?.voice_id || character.voice_provider || "—"} · {detail?.voice?.provider || character.voice_provider || "—"}
                  {detail?.voice_legacy?.seed ? ` · seed ${detail.voice_legacy.seed}` : ""}
                  {detail?.voice_legacy?.cfg_scale ? ` · cfg ${detail.voice_legacy.cfg_scale}` : ""}
                </p>
                {detail?.voice_legacy?.instruction && <p className="v5w-detail-text">{detail.voice_legacy.instruction}</p>}
              </div>

              {appearance && (
                <div className="v5w-detail-block">
                  <span className="v5w-detail-label">外观</span>
                  <div className="v5w-char-swatches">
                    {[appearance.hair, outfit?.main, outfit?.accent].filter(Boolean).map((color) => (
                      <span key={String(color)} className="v5w-char-swatch" style={{ background: String(color) }} title={String(color)} />
                    ))}
                    <span className="v5w-detail-text">
                      {appearance.hair_style || "—"} · {outfit?.type || "—"}
                      {appearance.accessories?.length ? ` · ${appearance.accessories.join(" / ")}` : ""}
                    </span>
                  </div>
                </div>
              )}

              <div className="v5w-detail-block">
                <span className="v5w-detail-label">角色池 / 标签</span>
                <div className="v5w-char-roles">
                  {(character.role_pool || []).map((role) => (
                    <span key={role} className="v5w-tag is-gold">
                      {ROLE_TEXT[role] || role}
                    </span>
                  ))}
                  {(character.tags || []).map((tag) => (
                    <span key={tag} className="v5w-tag is-muted">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>

              {(detail?.poses?.length || detail?.mouth?.length) && (
                <div className="v5w-detail-block">
                  <span className="v5w-detail-label">姿态 / 口型</span>
                  <p className="v5w-detail-text cm-mono">
                    {(detail?.poses || []).join(" / ") || "—"} · 口型 {(detail?.mouth || []).join(" ")}
                  </p>
                </div>
              )}

              {styles.length > 0 && (
                <div className="v5w-detail-block">
                  <span className="v5w-detail-label">风格覆盖 style_overrides</span>
                  <div className="v5w-char-roles">
                    {styles.map((style) => (
                      <span key={style} className="v5w-tag">
                        {style}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="v5w-detail-block">
                <span className="v5w-detail-label">三视图 / 原图</span>
                <div className="v5w-char-art-links">
                  <a className="cm-btn cm-btn-ghost cm-btn-sm" href={characterArtUrl(character.id, "portrait.png")} target="_blank" rel="noreferrer">
                    立绘 portrait
                  </a>
                  <a className="cm-btn cm-btn-ghost cm-btn-sm" href={characterArtUrl(character.id, "front.png")} target="_blank" rel="noreferrer">
                    正面 front
                  </a>
                  <a className="cm-btn cm-btn-ghost cm-btn-sm" href={characterArtUrl(character.id, "cutout.png")} target="_blank" rel="noreferrer">
                    抠像 cutout
                  </a>
                </div>
              </div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

/* ------------------------------ 工具 ------------------------------ */
function fallbackStyle(catalog: V5CatalogResponse | null, character: V5CharacterSummary): string | undefined {
  return defaultStyleForCharacter(catalog, character);
}

function genderText(gender?: string): string {
  if (gender === "male") return "男";
  if (gender === "female") return "女";
  return gender || "—";
}

function CharImage({ sources, alt, fallback }: { sources: string[]; alt: string; fallback: string }) {
  const list = sources.filter(Boolean);
  const key = list.join("|");
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [key]);
  if (!list.length || index >= list.length) {
    return <span className="v5w-char-fallback">{fallback}</span>;
  }
  return <img src={list[index]} alt={alt} loading="lazy" onError={() => setIndex((prev) => prev + 1)} />;
}
