// R7 · 模板墙（从首页迁出）：33 个模板 = 快速起一集的入口。
// 点「用这个起一集」= 新建项目（套用同名节目格式；短片模板用默认格式）并进入工作台。
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Project } from "../../types";
import { api, mediaUrl } from "../../lib/api";
import { cinemaApi } from "../../lib/cinema";
import { agentApi } from "../../lib/agent";
import type { AgentTemplate } from "../../lib/agent";
import { TEMPLATES } from "../../lib/templates";
import { useToast } from "../Toast";
import { IconSearch } from "../icons";
import { AppShell } from "../shell/AppShell";

type TplFilter = "all" | "base" | "custom";

interface TplCard {
  id: string;
  label: string;
  kind: "base" | "custom";
  duration: number;
  poster: string;
}

const FILTERS: { id: TplFilter; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "base", label: "综艺" },
  { id: "custom", label: "短片" },
];

export function TemplateWall({
  onProjectCreated,
  onOpenSettings,
}: {
  onProjectCreated: (project: Project) => void;
  onOpenSettings: () => void;
}) {
  const notify = useToast();
  const [overview, setOverview] = useState<AgentTemplate[]>([]);
  const [posters, setPosters] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<TplFilter>("all");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    const [ovRes, tplRes] = await Promise.allSettled([agentApi.overview(), cinemaApi.templates()]);
    if (ovRes.status === "fulfilled") setOverview(ovRes.value.templates);
    if (tplRes.status === "fulfilled") {
      const map: Record<string, string> = {};
      tplRes.value.templates.forEach((item) => {
        if (item.poster_url) map[item.id] = item.poster_url;
      });
      setPosters(map);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cards = useMemo<TplCard[]>(() => {
    const list = overview.map((item) => ({
      id: item.id,
      label: item.label || item.id,
      kind: (item.kind === "custom" ? "custom" : "base") as "base" | "custom",
      duration: Number(item.duration) || 0,
      poster: posters[item.id] || "",
    }));
    if (list.length) return list;
    return Object.keys(posters).map((id) => ({
      id,
      label: id,
      kind: (id.startsWith("custom_") ? "custom" : "base") as "base" | "custom",
      duration: 0,
      poster: posters[id],
    }));
  }, [overview, posters]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cards.filter((item) => {
      if (filter !== "all" && item.kind !== filter) return false;
      if (!q) return true;
      return item.id.toLowerCase().includes(q) || item.label.toLowerCase().includes(q);
    });
  }, [cards, filter, query]);

  /** 用这个模板起一集：新建项目 → 进工作台（App 会打开第 1 期） */
  const startOne = useCallback(
    async (card: TplCard) => {
      if (busy) return;
      setBusy(card.id);
      try {
        const formatId = `${card.id}_5min`;
        const format = TEMPLATES.some((item) => item.id === formatId)
          ? formatId
          : TEMPLATES[0]?.id || "roundtable_5min";
        const project = await api.createProject({
          name: `${card.label} · ${new Date().toLocaleDateString("zh-CN")}`,
          format_id: format,
        });
        try {
          localStorage.setItem("zpj_current_template", card.id);
        } catch {
          /* ignore */
        }
        notify(`已用「${card.label}」新建一集`, "success");
        onProjectCreated(project);
      } catch (err) {
        notify("新建失败：" + (err instanceof Error ? err.message : String(err)), "error");
      } finally {
        setBusy("");
      }
    },
    [busy, notify, onProjectCreated],
  );

  return (
    <AppShell active="library" title="模板" onOpenSettings={onOpenSettings}>
      <div className="wall">
        <div className="wall-tools">
          <label className="wall-search">
            <IconSearch size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索模板"
              aria-label="搜索模板"
            />
          </label>
          <div className="wall-filter" role="group" aria-label="筛选模板">
            {FILTERS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={filter === item.id ? "is-active" : ""}
                onClick={() => setFilter(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <span className="wall-count">{filtered.length} 个</span>
        </div>

        <div className="wall-grid">
          {filtered.map((card) => (
            <article key={card.id} className="wall-card">
              <div className="wall-cover">
                {card.poster ? (
                  <img src={mediaUrl(card.poster)} alt={card.label} loading="lazy" />
                ) : (
                  <span className="wall-letter">{card.label.slice(0, 1)}</span>
                )}
              </div>
              <div className="wall-body">
                <b className="wall-name" title={card.id}>
                  {card.label}
                </b>
                <span className="wall-sub">
                  {card.duration ? `${card.duration}s` : "时长未记录"} · {card.kind === "custom" ? "短片" : "综艺"}
                </span>
              </div>
              <button
                type="button"
                className="wall-go"
                onClick={() => void startOne(card)}
                disabled={Boolean(busy)}
              >
                {busy === card.id ? "正在新建…" : "用这个起一集"}
              </button>
            </article>
          ))}
        </div>

        {filtered.length === 0 && <p className="wall-empty">没有匹配的模板。</p>}
      </div>
    </AppShell>
  );
}
