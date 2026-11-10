// V49 · 智能体编排（#/agent）
// 左栏：33 模板（内建 16 / 自定义 17，2D/3D/写实徽标 + 成片状态点 + 搜索）
// 中栏：选中模板的工作流阶段（agent / tool / depends / enabled / params 编辑，保存 → contract/v6/workflows）
// 右栏：单集运行（时长/seed/goal/阶段多选/dry-run → 1.5s 轮询日志 + 证据链接）+ Muse 口型修正 + 智能体 edit
// 季（M3）：系列 → 集列表 → 季计划（dry-run）/ 整季生产（only_missing），复用同一日志面板
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ApiError, errorText, mediaUrl } from "../../lib/api";
import { cinemaApi, fmtSize, renderStyleMeta, renderTypeMeta } from "../../lib/cinema";
import type { CinemaTemplate } from "../../lib/cinema";
import type { V5CatalogResponse, V5TemplateSummary } from "../../lib/v5";
import { v5api } from "../../lib/v5";
import {
  AGENT_EDIT_OPS,
  agentApi,
  agentEvidence,
  agentStatusLabel,
  isAgentActive,
  isAgentNotReady,
  isMuseActive,
  MUSE_VIDEO_PROVIDERS,
  museVideoTone,
  stageLabel,
  stageTone,
  summarizeRun,
  syncTone,
  type AgentKind,
  type AgentRun,
  type AgentSeries,
  type AgentSeriesMember,
  type AgentStage,
  type AgentTemplate,
  type AgentWorkflowFile,
  type MuseJob,
  type MuseStatus,
  type MuseVideoJob,
  type MuseVideoStatus,
} from "../../lib/agent";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import {
  IconAgent,
  IconAlert,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconClock,
  IconClose,
  IconDot,
  IconExternal,
  IconFilm,
  IconFlow,
  IconInfo,
  IconPlay,
  IconRefresh,
  IconSearch,
  IconTemplate,
  IconVideo,
  IconVolume,
} from "../icons";
import { AppShell } from "../shell/AppShell";
import { PageHeader } from "../ui/PageHeader";
import { Stat, StatRow } from "../ui/Stat";
import "../../styles/cinema.css";
import "../../styles/v18.css";
import "../../styles/ui-consistency.css";
import "../../styles/agent.css";

export type AgentTab = "workflow" | "season";

interface Props {
  tab: AgentTab;
  onTabChange: (tab: AgentTab) => void;
  onOpenSettings: () => void;
}

const TABS = [
  { key: "workflow", no: "01", label: "单集工作流", en: "WORKFLOW" },
  { key: "season", no: "02", label: "季生产", en: "SEASON" },
];

/** 生产栈（V49：内建综艺走 v5_render，custom_tpl_* 短片走 hq_shorts） */
const STACK_LABEL: Record<string, string> = {
  v5_render: "V5 渲染栈",
  hq_shorts: "HQ 短片栈",
};

const EMPTY_RUN: AgentRun = {
  job_id: "",
  status: "",
  logs: [],
  result: null,
  run_dir: "",
  run_id: "",
  error: "",
  created: "",
  started: "",
  ended: "",
};

/** 概览 + 目录（/api/v5/catalog）合并：概览若只给了内建模板，用目录补齐 custom_* */
function mergeTemplates(list: AgentTemplate[], catalog: V5CatalogResponse | null): AgentTemplate[] {
  const seen = new Set(list.map((item) => item.id));
  const extra: AgentTemplate[] = [];
  (catalog?.templates || []).forEach((raw) => {
    const item = raw as V5TemplateSummary & { render_type?: string };
    if (!item?.id || seen.has(item.id)) return;
    extra.push({
      id: item.id,
      label: item.label || item.id,
      style: item.style || "",
      render_style: "",
      render_type: item.render_type || "",
      stack: "",
      kind: (item.id.startsWith("custom_") || item.custom ? "custom" : "base") as AgentKind,
      duration: Number(item.target_duration) || 0,
      cast: (item.cast || []).map((member) => ({ role: member.role, character: member.character })),
      ready: null,
      series: [],
      workflow_file: "",
      stages: [],
    });
  });
  return [...list, ...extra].sort((a, b) => a.id.localeCompare(b.id));
}

function parseParams(text: string | undefined, fallback: Record<string, unknown>): Record<string, unknown> {
  if (text === undefined || !text.trim()) return fallback;
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : fallback;
  } catch {
    return fallback;
  }
}

/** V52：空 = 合法（无参数）；否则必须是 JSON 对象（数组/标量/null 一律非法，避免静默回退） */
function paramsInvalid(text: string | undefined): boolean {
  if (text === undefined || !text.trim()) return false;
  try {
    const value = JSON.parse(text);
    return !value || typeof value !== "object" || Array.isArray(value);
  } catch {
    return true;
  }
}

/** 高级参数（stage_params）校验：空 = {}；必须是 JSON 对象 */
function stageParamsError(text: string): string {
  const raw = text.trim();
  if (!raw) return "";
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return "必须是 JSON 对象（例如 {'muse':{'apply':true}}）";
    return "";
  } catch {
    return "不是合法 JSON";
  }
}

/** provider 自检值渲染：boolean → ✓/✗，其他原样 */
function checkValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  return String(value);
}

function shortJson(value: unknown, limit = 360): string {
  try {
    return JSON.stringify(value, null, 1).slice(0, limit);
  } catch {
    return String(value);
  }
}

export function AgentPage({ tab, onTabChange, onOpenSettings }: Props) {
  const notify = useToast();
  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);
  const templatesRef = useRef<AgentTemplate[]>([]);
  /** V52：workflow / season 请求序号守卫（过期响应丢弃） */
  const wfSeqRef = useRef(0);
  const seasonSeqRef = useRef(0);
  /** 当前 draft 属于哪个模板（保存前一致性校验） */
  const draftForRef = useRef("");
  const selectedIdRef = useRef("");
  const museChipTimerRef = useRef<number | null>(null);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");

  const [overviewTemplates, setOverviewTemplates] = useState<AgentTemplate[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [cinemaTemplates, setCinemaTemplates] = useState<CinemaTemplate[]>([]);
  const [seriesList, setSeriesList] = useState<AgentSeries[]>([]);
  const [seriesSource, setSeriesSource] = useState<"v6" | "v5" | "">("");

  const [query, setQuery] = useState("");
  // R6：首页模板墙点卡片会写 localStorage，这里作为初始选中
  const [selectedId, setSelectedId] = useState<string>(() => {
    try {
      return localStorage.getItem("zpj_current_template") || "";
    } catch {
      return "";
    }
  });

  const [workflow, setWorkflow] = useState<AgentWorkflowFile | null>(null);
  const [draft, setDraft] = useState<AgentStage[] | null>(null);
  const [paramsText, setParamsText] = useState<Record<string, string>>({});
  const [wfLoading, setWfLoading] = useState(false);
  const [wfError, setWfError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const [form, setForm] = useState({ duration: "40", seed: "20261101", goal: "", dryRun: true });
  const [runStages, setRunStages] = useState<string[]>([]);

  const [job, setJob] = useState<AgentRun | null>(null);
  const [jobScope, setJobScope] = useState<"single" | "season">("single");
  /** V52：job 归属（模板 id 或 "season:<sid>"），切换模板时保留运行中的 job */
  const [jobTemplate, setJobTemplate] = useState("");
  /** V52：单集 run 的高级参数（JSON） */
  const [advancedJson, setAdvancedJson] = useState("{}");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [pollError, setPollError] = useState("");

  const [seasonSid, setSeasonSid] = useState("");
  const [seasonEpisodes, setSeasonEpisodes] = useState<AgentSeriesMember[] | null>(null);
  const [seasonNote, setSeasonNote] = useState("");
  const [seasonError, setSeasonError] = useState("");
  const [seasonBusy, setSeasonBusy] = useState<"" | "plan" | "run">("");
  const [seasonUpdated, setSeasonUpdated] = useState("");

  const [editOp, setEditOp] = useState(AGENT_EDIT_OPS[0].id);
  const [editChar, setEditChar] = useState("");
  const [editText, setEditText] = useState(AGENT_EDIT_OPS[0].placeholder);
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState("");
  const [editResult, setEditResult] = useState<Record<string, unknown> | null>(null);

  // V49 · Muse 口型（MuseTalk）：状态 chip + 右栏修正卡片 + 2s 轮询
  const [muse, setMuse] = useState<MuseStatus | null>(null);
  const [museError, setMuseError] = useState("");
  const [museJob, setMuseJob] = useState<MuseJob | null>(null);
  const [museJobError, setMuseJobError] = useState("");
  const [museStarting, setMuseStarting] = useState(false);
  const [museChipOpen, setMuseChipOpen] = useState(false);
  const [museChipPos, setMuseChipPos] = useState({ top: 0, right: 16 });
  const museChipRef = useRef<HTMLButtonElement | null>(null);

  // V50 · Muse 视频生成 + 季「同时跑口型」
  const [museVideo, setMuseVideo] = useState<MuseVideoStatus | null>(null);
  const [museVideoError, setMuseVideoError] = useState("");
  const [museVideoJob, setMuseVideoJob] = useState<MuseVideoJob | null>(null);
  const [museVideoJobError, setMuseVideoJobError] = useState("");
  const [museVideoStarting, setMuseVideoStarting] = useState(false);
  const [videoForm, setVideoForm] = useState({ prompt: "", duration: "4", aspect: "16:9", provider: "" });
  const [withMuse, setWithMuse] = useState(false);
  /** V52：口型写回 shots 并重新合成（后端 body muse_apply:true） */
  const [museApply, setMuseApply] = useState(false);
  const [museChipPinned, setMuseChipPinned] = useState(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  /* ------------------------------ 加载 ------------------------------ */
  /** 返回 true 表示本轮真的刷新且没有接口错误；false = 被并发跳过或有关键接口失败 */
  const loadAll = useCallback(async (silent: boolean): Promise<boolean> => {
    if (inFlightRef.current) return false;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
    const [ovRes, catRes, seriesRes, cinemaRes, museRes, museVideoRes] = await Promise.allSettled([
      agentApi.overview(),
      v5api.catalog(),
      agentApi.series(),
      cinemaApi.templates(),
      agentApi.museStatus(),
      agentApi.museVideoStatus(),
    ]);
    if (!aliveRef.current) return false;
    const errors: string[] = [];
    let overviewOk = false;
    if (ovRes.status === "fulfilled") {
      overviewOk = true;
      setOverviewTemplates(ovRes.value.templates);
      const map: Record<string, string> = {};
      ovRes.value.roles.forEach((role) => {
        map[role.id] = role.name;
      });
      setRoles(map);
    } else {
      errors.push("概览：" + errorText(ovRes.reason));
      setOverviewTemplates([]);
    }
    if (catRes.status === "fulfilled") setCatalog(catRes.value);
    else if (overviewOk) errors.push("模板目录：" + errorText(catRes.reason));
    if (seriesRes.status === "fulfilled") {
      setSeriesList(seriesRes.value.series);
      setSeriesSource(seriesRes.value.source);
    } else {
      errors.push("季列表：" + errorText(seriesRes.reason));
      setSeriesList([]);
    }
    if (cinemaRes.status === "fulfilled") setCinemaTemplates(cinemaRes.value.templates);
    // Muse 口型：404 属契约先行（后端未落地），只在 Muse 卡片里提示，不污染整页错误条
    if (museRes.status === "fulfilled") {
      setMuse(museRes.value);
      setMuseError("");
    } else {
      setMuse(null);
      setMuseError(
        isAgentNotReady(museRes.reason)
          ? "Muse 状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"
          : "Muse 状态读取失败：" + errorText(museRes.reason),
      );
    }
    if (museVideoRes.status === "fulfilled") {
      setMuseVideo(museVideoRes.value);
      setMuseVideoError("");
    } else {
      setMuseVideo(null);
      setMuseVideoError(
        isAgentNotReady(museVideoRes.reason)
          ? "Muse 视频状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"
          : "Muse 视频状态读取失败：" + errorText(museVideoRes.reason),
      );
    }
    setLoadError(errors.join("；"));
    return errors.length === 0;
    } finally {
      inFlightRef.current = false;
      if (aliveRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadAll(false);
  }, [loadAll]);

  const templates = useMemo(() => mergeTemplates(overviewTemplates, catalog), [overviewTemplates, catalog]);
  templatesRef.current = templates;
  selectedIdRef.current = selectedId;

  const readyMap = useMemo(() => {
    const map: Record<string, boolean> = {};
    seriesList.forEach((series) =>
      series.members.forEach((member) => {
        if (member.ready) map[member.template] = true;
      }),
    );
    cinemaTemplates.forEach((item) => {
      if (item.final?.exists) map[item.id] = true;
    });
    return map;
  }, [seriesList, cinemaTemplates]);

  /** 概览给了 render_style（2D 子风格）但没给 render_type；用目录 / render_style / id 兜底 */
  const catalogTypeMap = useMemo(() => {
    const map: Record<string, string> = {};
    (catalog?.templates || []).forEach((item) => {
      const value = (item as V5TemplateSummary & { render_type?: string }).render_type;
      if (item?.id && value) map[item.id] = value;
    });
    return map;
  }, [catalog]);

  const typeMetaOf = useCallback(
    (item: AgentTemplate | null) => {
      const explicit = item ? item.render_type || (item.render_style ? "2d" : "") || catalogTypeMap[item.id] || "" : "";
      const meta = renderTypeMeta({ render_type: explicit, id: item?.id || "" });
      const style = item?.render_style ? renderStyleMeta({ render_style: item.render_style }) : null;
      return { meta, style, explicit };
    },
    [catalogTypeMap],
  );

  const readyOf = useCallback(
    (template: AgentTemplate): boolean | null => (template.ready === null ? readyMap[template.id] ?? null : template.ready),
    [readyMap],
  );

  const selected = useMemo(() => templates.find((item) => item.id === selectedId) || null, [templates, selectedId]);
  const readyCount = useMemo(() => templates.filter((item) => readyOf(item) === true).length, [templates, readyOf]);

  /* ------------------------------ 工作流 ------------------------------ */
  const applyWorkflow = useCallback((wf: AgentWorkflowFile) => {
    // V52：记录这份 draft 属于哪个模板，保存前用它做一致性校验
    draftForRef.current = wf.id || "";
    setWorkflow(wf);
    setDraft(wf.stages);
    const texts: Record<string, string> = {};
    wf.stages.forEach((stage) => {
      texts[stage.id] = Object.keys(stage.params || {}).length ? JSON.stringify(stage.params, null, 1) : "";
    });
    setParamsText(texts);
    setRunStages(wf.stages.filter((stage) => stage.enabled).map((stage) => stage.id));
    setDirty(false);
  }, []);

  const loadWorkflow = useCallback(
    async (id: string) => {
      if (!id) return;
      // V52：请求序号守卫 —— 连点模板时只认最后一次请求，避免旧响应覆盖新模板的 draft
      const seq = ++wfSeqRef.current;
      setWfLoading(true);
      setWfError("");
      try {
        const wf = await agentApi.workflow(id);
        if (!aliveRef.current || seq !== wfSeqRef.current) return;
        applyWorkflow(wf);
      } catch (err) {
        if (!aliveRef.current || seq !== wfSeqRef.current) return;
        const fallback = templatesRef.current.find((item) => item.id === id)?.stages || [];
        setWorkflow({ id, name: id, description: "", stack: "", variables: {}, stages: fallback });
        applyWorkflow({ id, name: id, description: "", stack: "", variables: {}, stages: fallback });
        setWfError(
          isAgentNotReady(err)
            ? "工作流接口返回 404，已用概览阶段兜底；请确认该模板的合同存在后重试。"
            : "工作流读取失败：" + errorText(err),
        );
      } finally {
        if (aliveRef.current && seq === wfSeqRef.current) setWfLoading(false);
      }
    },
    [applyWorkflow],
  );

  const selectTemplate = useCallback(
    (id: string) => {
      if (id === selectedId) return;
      if (dirty && !window.confirm("当前模板的工作流有未保存改动，确定切换模板？")) return;
      setSelectedId(id);
      // V52：进行中的 run 不随模板切换丢弃（后端单并发，丢了就只能刷新页面找回）
      if (!isAgentActive(job)) {
        setJob(null);
        setJobTemplate("");
      }
      setStartError("");
      setPollError("");
      setEditResult(null);
      setEditError("");
      setParamsText({});
      setDraft(null);
      draftForRef.current = "";
      setDirty(false);
      void loadWorkflow(id);
    },
    [dirty, job, loadWorkflow, selectedId],
  );

  // 首次进入 / 模板列表变化：保证有选中项
  useEffect(() => {
    if (!templates.length) return;
    if (selectedId && templates.some((item) => item.id === selectedId)) return;
    selectTemplate(templates[0].id);
  }, [templates, selectedId, selectTemplate]);

  const toggleStage = useCallback((stageId: string, enabled: boolean) => {
    setDraft((prev) => (prev ? prev.map((stage) => (stage.id === stageId ? { ...stage, enabled } : stage)) : prev));
    setDirty(true);
  }, []);

  /** 阶段顺序（M2：stages 顺序可改；保存时按 draft 顺序写回合同） */
  const moveStage = useCallback((stageId: string, delta: -1 | 1) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const index = prev.findIndex((stage) => stage.id === stageId);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= prev.length) return prev;
      const next = prev.slice();
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return next;
    });
    setDirty(true);
  }, []);

  const changeParams = useCallback((stageId: string, text: string) => {
    setParamsText((prev) => ({ ...prev, [stageId]: text }));
    setDirty(true);
  }, []);

  const saveWorkflow = useCallback(async () => {
    if (!selectedId || !draft) return;
    // V52：保存前校验 draft 归属，防止竞态下把 A 的阶段写进 B 的合同
    if (draftForRef.current && draftForRef.current !== selectedId) {
      const message =
        "当前编辑的是 " + draftForRef.current + " 的工作流，但选中模板是 " + selectedId + "；已阻止保存（重新选择模板即可）。";
      setWfError(message);
      notify(message, "error");
      return;
    }
    const invalid = draft.find((stage) => paramsInvalid(paramsText[stage.id]));
    if (invalid) {
      notify("阶段「" + stageLabel(invalid.id) + "」的 params 必须是 JSON 对象（当前不是）", "error");
      return;
    }
    setSaving(true);
    setWfError("");
    try {
      const stages = draft.map((stage) => ({ ...stage, params: parseParams(paramsText[stage.id], stage.params) }));
      const payload = {
        id: selectedId,
        name: workflow?.name || selectedId,
        description: workflow?.description || "",
        stack: workflow?.stack || "",
        variables: workflow?.variables || {},
        stages,
      };
      const res = await agentApi.saveWorkflow(selectedId, payload);
      if (!aliveRef.current) return;
      if (res.template && res.template !== selectedId) {
        // 双保险：后端写入的 template 与当前选中不一致时明确报错
        setWfError("后端写入的模板是 " + res.template + "，与当前选中 " + selectedId + " 不一致。");
        notify("保存模板不一致：" + res.template, "error");
        return;
      }
      setDirty(false);
      notify("工作流已保存 · " + (res.stages.length || stages.length) + " 阶段 → " + (res.path || "contract/v6/workflows"), "success");
      await loadWorkflow(selectedId);
      void loadAll(true);
    } catch (err) {
      if (aliveRef.current) setWfError("保存失败：" + errorText(err));
    } finally {
      if (aliveRef.current) setSaving(false);
    }
  }, [draft, loadAll, loadWorkflow, notify, paramsText, selectedId, workflow]);

  const restoreDefault = useCallback(async () => {
    if (!selectedId) return;
    if (!window.confirm("把模板 " + selectedId + " 的工作流重置为内建默认？\n\n会调用智能体 reset_workflow 覆盖 contract/v6/workflows/" + selectedId + ".json，未保存的改动会丢失。")) return;
    setSaving(true);
    setWfError("");
    try {
      await agentApi.edit({ template: selectedId, op: "reset_workflow" });
      if (!aliveRef.current) return;
      notify("已重置为内建默认工作流", "success");
      await loadWorkflow(selectedId);
      void loadAll(true);
    } catch (err) {
      if (aliveRef.current) setWfError("重置失败：" + errorText(err));
    } finally {
      if (aliveRef.current) setSaving(false);
    }
  }, [loadAll, loadWorkflow, notify, selectedId]);

  /* ------------------------------ 单集运行 ------------------------------ */
  const toggleRunStage = useCallback((stageId: string) => {
    setRunStages((prev) => (prev.includes(stageId) ? prev.filter((id) => id !== stageId) : [...prev, stageId]));
  }, []);

  const startRun = useCallback(async () => {
    if (!selectedId) {
      notify("先在左栏选择一个模板", "info");
      return;
    }
    if (isAgentActive(job)) {
      notify("已有 run 在跑（" + (jobTemplate || job?.job_id || "") + "），等它结束再启动", "info");
      return;
    }
    // V52：高级参数（stage_params）必须是 JSON 对象
    let stageParams: Record<string, unknown> | undefined;
    if (advancedJson.trim() && advancedJson.trim() !== "{}") {
      try {
        const parsed = JSON.parse(advancedJson);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setStartError("高级参数必须是 JSON 对象，例如 {'muse':{'apply':true}}");
          return;
        }
        stageParams = parsed as Record<string, unknown>;
      } catch {
        setStartError("高级参数不是合法 JSON，已取消启动");
        return;
      }
    }
    setStarting(true);
    setStartError("");
    setPollError("");
    try {
      const res = await agentApi.run({
        template: selectedId,
        duration: Number(form.duration) || undefined,
        seed: Number(form.seed) || undefined,
        stages: runStages.length ? runStages : undefined,
        goal: form.goal.trim() || undefined,
        dry_run: form.dryRun,
        stage_params: stageParams,
      });
      if (!aliveRef.current) return;
      setJob({ ...EMPTY_RUN, job_id: res.job_id, status: "queued" });
      setJobScope("single");
      setJobTemplate(selectedId);
      notify(form.dryRun ? "已启动 dry-run 计划（只跑规划，不渲染）" : "已启动智能体 run：" + res.job_id, "success");
    } catch (err) {
      if (!aliveRef.current) return;
      if (err instanceof ApiError && err.status === 409) {
        setStartError("已有 run 在跑（409）：后端单并发限制，等当前 run 结束再启动。");
      } else if (isAgentNotReady(err)) {
        setStartError("智能体 run 接口返回 404：请确认后端已启动（/api/v6/agent/run 可访问）后再试。");
      } else {
        setStartError("启动失败：" + errorText(err));
      }
    } finally {
      if (aliveRef.current) setStarting(false);
    }
  }, [advancedJson, form, job, jobTemplate, notify, runStages, selectedId]);

  /* ------------------------------ 季 ------------------------------ */
  const refreshSeason = useCallback(
    async (sid: string) => {
      if (!sid) return;
      const seq = ++seasonSeqRef.current;
      try {
        const res = await agentApi.seasonStatus(sid);
        if (!aliveRef.current || seq !== seasonSeqRef.current) return;
        const fallback = seriesList.find((item) => item.id === sid)?.members || [];
        setSeasonEpisodes(res.episodes.length ? res.episodes : fallback);
        setSeasonNote("");
        setSeasonUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      } catch (err) {
        if (!aliveRef.current || seq !== seasonSeqRef.current) return;
        const fallback = seriesList.find((item) => item.id === sid)?.members || [];
        setSeasonEpisodes(fallback.length ? fallback : null);
        setSeasonNote(
          isAgentNotReady(err)
            ? "季状态接口返回 404：集列表暂用系列合同 /api/v5/series 兜底，成片/最近 run 以合同为准。"
            : "季状态读取失败：" + errorText(err),
        );
      }
    },
    [seriesList],
  );

  useEffect(() => {
    if (tab !== "season") return;
    if (!seriesList.length) return;
    const current = seriesList.some((item) => item.id === seasonSid) ? seasonSid : seriesList[0].id;
    if (current !== seasonSid) {
      setSeasonSid(current);
      return;
    }
    void refreshSeason(current);
  }, [tab, seriesList, seasonSid, refreshSeason]);

  const startSeason = useCallback(
    async (mode: "plan" | "run") => {
      if (!seasonSid) {
        notify("先在左栏选择一个系列", "info");
        return;
      }
      if (isAgentActive(job)) {
        notify("已有 run 在跑，等它结束再启动季生产", "info");
        return;
      }
      setSeasonBusy(mode);
      setSeasonError("");
      setPollError("");
      try {
        // V50：勾了「同时跑口型」→ with_muse:true（只跑 muse 阶段，不重渲）
        // V52：muse_apply → 口型写回 shots 并重新合成（后端转成 stage_params.muse.apply）
        const res = await agentApi.seasonRun(
          seasonSid,
          mode === "plan"
            ? { dry_run: true }
            : {
                only_missing: true,
                with_muse: withMuse || undefined,
                muse_apply: withMuse && museApply ? true : undefined,
              },
        );
        if (!aliveRef.current) return;
        // V52：后端在「无需生产」时返回 job_id=null → 不进 queued，避免 runActive 永久锁死
        if (!res.job_id) {
          const message = res.message || "全部集数已出片，无需生产";
          setSeasonNote(message);
          notify(message, "info");
          void refreshSeason(seasonSid);
          return;
        }
        setJob({ ...EMPTY_RUN, job_id: res.job_id, status: "queued" });
        setJobScope("season");
        setJobTemplate("season:" + seasonSid);
        notify(
          mode === "plan"
            ? "已启动季计划 dry-run：" + (res.episodes.length || 0) + " 集待规划"
            : withMuse
              ? "已启动整季口型（with_muse）：" + (res.episodes.length || 0) + " 集" + (museApply ? "，写回成片并重新合成" : "，只跑 muse 阶段")
              : "已启动整季生产（only_missing）：" + (res.episodes.length || 0) + " 集排队",
          "success",
        );
      } catch (err) {
        if (!aliveRef.current) return;
        if (isAgentNotReady(err)) {
          setSeasonError(
            "季生产接口返回 404（POST /api/v6/agent/season/" + seasonSid + "/run）：请确认系列 id 存在、后端已启动；单集 run 与工作流编辑不受影响。",
          );
        } else {
          setSeasonError("季生产启动失败：" + errorText(err));
        }
      } finally {
        if (aliveRef.current) setSeasonBusy("");
      }
    },
    [job, museApply, notify, refreshSeason, seasonSid, withMuse],
  );

  /* ------------------------------ 轮询 ------------------------------ */
  const pollTarget = job?.job_id || "";
  const pollStatus = job?.status || "";
  const runActive = isAgentActive(job);

  useEffect(() => {
    if (!pollTarget || (pollStatus !== "queued" && pollStatus !== "running")) return;
    let alive = true;
    // V74：in-flight 互斥 + 页面隐藏跳过 + 卸载/切任务时 abort —— 避免慢后端下请求叠加（最坏 13 并发）
    let busy = false;
    const controller = new AbortController();
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const next = await agentApi.runStatus(pollTarget, controller.signal);
        if (!alive || !aliveRef.current) return;
        setPollError("");
        setJob(next);
        if (!isAgentActive(next)) {
          if (next.status === "failed") notify("run 失败：" + (next.error || "未知原因"), "error");
          else notify("run 结束：" + agentStatusLabel(next.status) + " · " + (next.run_id || next.job_id), "success");
          if (jobScope === "season" && seasonSid) void refreshSeason(seasonSid);
          void loadAll(true);
        }
      } catch (err) {
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        if (isAgentNotReady(err)) {
          // V52：任务在后端内存表里已不存在（重启/清理）→ 解除运行态，避免 runActive 永久锁死界面
          setJob((prev) => (prev ? { ...prev, status: "failed", error: "run 状态接口 404：任务已不存在（后端重启或已清理）" } : prev));
          setPollError("run 状态接口 404：任务已不存在，已解除运行态（日志不可用）。");
          return;
        }
        setPollError("轮询失败：" + errorText(err));
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void tick(), 1500);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [pollTarget, pollStatus, jobScope, seasonSid, notify, refreshSeason, loadAll]);

  /* ------------------------------ 智能体 edit ------------------------------ */
  const applyEdit = useCallback(async () => {
    if (!selectedId) {
      notify("先选一个模板", "info");
      return;
    }
    const op = AGENT_EDIT_OPS.find((item) => item.id === editOp) || AGENT_EDIT_OPS[0];
    setEditing(true);
    setEditError("");
    setEditResult(null);
    try {
      let result: Record<string, unknown>;
      if (op.field === "value") {
        result = await agentApi.edit({
          template: selectedId,
          op: op.id,
          character: op.needsCharacter ? editChar.trim() : undefined,
          value: editText.trim(),
        });
      } else {
        let payload: unknown = {};
        if (editText.trim()) {
          try {
            payload = JSON.parse(editText);
          } catch {
            setEditError("JSON 不合法，无法应用");
            return;
          }
          // V52：patch_* 只接受 JSON 对象（数组/标量会被后端当空 patch 静默跳过）
          if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
            setEditError("patch 需要 JSON 对象，例如 {'style':'ink_chronicle'}");
            return;
          }
        }
        result = await agentApi.edit({ template: selectedId, op: op.id, json: payload });
      }
      if (!aliveRef.current) return;
      setEditResult(result);
      notify("智能体已应用 " + op.id, "success");
      if (op.id === "patch_workflow") void loadWorkflow(selectedId);
      void loadAll(true);
    } catch (err) {
      if (aliveRef.current) setEditError("应用失败：" + errorText(err));
    } finally {
      if (aliveRef.current) setEditing(false);
    }
  }, [editChar, editOp, editText, loadAll, loadWorkflow, notify, selectedId]);


  /* ------------------------------ Muse 口型（MuseTalk） ------------------------------ */
  const museReady = Boolean(muse?.available.ok);
  const museActive = isMuseActive(museJob);

  const refreshMuse = useCallback(async () => {
    try {
      const next = await agentApi.museStatus();
      if (!aliveRef.current) return;
      setMuse(next);
      setMuseError("");
    } catch (err) {
      if (!aliveRef.current) return;
      setMuse(null);
      setMuseError(
        isAgentNotReady(err)
          ? "Muse 状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"
          : "Muse 状态读取失败：" + errorText(err),
      );
    }
  }, []);

  const startMuseSync = useCallback(async () => {
    if (!selectedId) {
      notify("先选一个模板，再给本集对白镜跑口型", "info");
      return;
    }
    if (museActive) {
      notify("已有 Muse 口型任务在跑，等它结束", "info");
      return;
    }
    setMuseStarting(true);
    setMuseJobError("");
    try {
      const res = await agentApi.museSync({ template: selectedId, limit: 2 });
      if (!aliveRef.current) return;
      setMuseJob({ job_id: res.job_id, status: "queued", logs: [], items: [], error: "" });
      notify("已启动 Muse 口型：" + selectedId + " · limit 2", "success");
    } catch (err) {
      if (!aliveRef.current) return;
      if (isAgentNotReady(err)) setMuseJobError("Muse 口型接口返回 404（POST /api/v6/muse/sync）：请确认后端已启动、local_models/lipsync 就绪。");
      else setMuseJobError("启动失败：" + errorText(err));
    } finally {
      if (aliveRef.current) setMuseStarting(false);
    }
  }, [museActive, notify, selectedId]);

  const openMuseChip = useCallback(() => {
    const rect = museChipRef.current?.getBoundingClientRect();
    if (rect) {
      setMuseChipPos({ top: Math.round(rect.bottom + 8), right: Math.max(12, Math.round(window.innerWidth - rect.right)) });
    }
    setMuseChipOpen(true);
  }, []);

  const closeMuseChip = useCallback(() => setMuseChipOpen(false), []);

  /** V52：mouseleave 延时关闭（点过的 chip 固定，不自动关） */
  const scheduleCloseMuseChip = useCallback(() => {
    if (museChipPinned) return;
    if (museChipTimerRef.current) window.clearTimeout(museChipTimerRef.current);
    museChipTimerRef.current = window.setTimeout(() => setMuseChipOpen(false), 180);
  }, [museChipPinned]);

  const cancelCloseMuseChip = useCallback(() => {
    if (museChipTimerRef.current) {
      window.clearTimeout(museChipTimerRef.current);
      museChipTimerRef.current = null;
    }
  }, []);

  const toggleMuseChip = useCallback(() => {
    const next = !museChipPinned;
    setMuseChipPinned(next);
    if (next) openMuseChip();
    else setMuseChipOpen(false);
  }, [museChipPinned, openMuseChip]);

  useEffect(() => () => cancelCloseMuseChip(), [cancelCloseMuseChip]);

  useEffect(() => {
    if (!museChipOpen) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".ag-muse-pop") || target?.closest(".ag-muse-chip")) return;
      setMuseChipOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMuseChipOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", closeMuseChip, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", closeMuseChip, true);
    };
  }, [museChipOpen, closeMuseChip]);

  const museJobId = museJob?.job_id || "";
  const museJobStatus = museJob?.status || "";

  useEffect(() => {
    if (!museJobId || !isMuseActive({ status: museJobStatus })) return;
    let alive = true;
    let busy = false;
    const controller = new AbortController();
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const next = await agentApi.museJob(museJobId, controller.signal);
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        setMuseJobError("");
        setMuseJob(next);
        if (!isMuseActive(next)) {
          if (next.status === "failed") notify("Muse 口型失败：" + (next.error || "未知原因"), "error");
          else notify("Muse 口型完成：" + next.items.length + " 个镜头", "success");
          void refreshMuse();
        }
      } catch (err) {
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        if (isAgentNotReady(err)) {
          // V52：任务已被清理 → 解除运行态
          setMuseJob((prev) => (prev ? { ...prev, status: "failed", error: "Muse 任务已不存在（404）" } : prev));
          setMuseJobError("Muse 任务接口 404：任务已不存在，已解除运行态。");
          return;
        }
        setMuseJobError("Muse 轮询失败：" + errorText(err));
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void tick(), 2000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [museJobId, museJobStatus, notify, refreshMuse]);

  /** Muse 任务 → 复用 RunLog 的 AgentRun 形状（日志 stage 统一 muse，走 stageTone 兜底色） */
  const museRun = useMemo<AgentRun | null>(() => {
    if (!museJob) return null;
    return {
      job_id: museJob.job_id,
      status: museJob.status,
      logs: museJob.logs.map((line) => ({ t: line.t, stage: "muse", msg: line.msg })),
      result: null,
      run_dir: "",
      run_id: "",
      error: museJob.error,
      created: "",
      started: "",
      ended: "",
    };
  }, [museJob]);

  const museDeps = muse?.available.deps || [];
  const museDepsBad = museDeps.filter((dep) => !dep.ok);
  const museRecent = muse?.recent || [];

  /* ------------------------------ Muse 视频（V50） ------------------------------ */
  const museVideoState = museVideo?.state || null;
  const museVideoReady = Boolean(museVideoState?.ok);
  const museVideoActive = isMuseActive(museVideoJob);
  const effectiveVideoProvider = videoForm.provider || museVideoState?.provider || "";
  const museVideoChecks = museVideoState?.checks || {};

  const refreshMuseVideo = useCallback(async () => {
    try {
      const next = await agentApi.museVideoStatus();
      if (!aliveRef.current) return;
      setMuseVideo(next);
      setMuseVideoError("");
    } catch (err) {
      if (!aliveRef.current) return;
      setMuseVideo(null);
      setMuseVideoError(
        isAgentNotReady(err)
          ? "Muse 视频状态读取失败（404）：请确认后端已在 8090 启动，再点「刷新」重试。"
          : "Muse 视频状态读取失败：" + errorText(err),
      );
    }
  }, []);

  const startMuseVideo = useCallback(
    async (allowStub: boolean) => {
      const prompt = videoForm.prompt.trim();
      if (!prompt) {
        notify("先写 prompt（一句话镜头描述）", "info");
        return;
      }
      if (museVideoActive) {
        notify("已有 Muse 视频任务在跑，等它结束", "info");
        return;
      }
      if (allowStub && !window.confirm("占位测试只跑 stub 接线：产物标 stub=true，不可交付。继续？")) return;
      if (!allowStub && (videoForm.provider || museVideoState?.provider || "") === "stub") {
        notify("provider=stub 只做占位：请用「占位测试」显式 allow_stub", "info");
        return;
      }
      setMuseVideoStarting(true);
      setMuseVideoJobError("");
      try {
        const res = await agentApi.museVideo({
          prompt,
          duration: Number(videoForm.duration) || 4,
          aspect: videoForm.aspect,
          provider: videoForm.provider || undefined,
          template: selectedId || undefined,
          allow_stub: allowStub || undefined,
        });
        if (!aliveRef.current) return;
        setMuseVideoJob({ job_id: res.job_id, status: "queued", logs: [], result: null, error: "" });
        notify("已启动 Muse 视频：" + (res.provider || effectiveVideoProvider || "auto") + " · " + res.job_id, "success");
      } catch (err) {
        if (!aliveRef.current) return;
        if (isAgentNotReady(err)) setMuseVideoJobError("Muse 视频接口返回 404（POST /api/v6/muse/video）：请确认后端已启动。");
        else if (err instanceof ApiError && err.status === 400) setMuseVideoJobError(err.message);
        else setMuseVideoJobError("启动失败：" + errorText(err));
      } finally {
        if (aliveRef.current) setMuseVideoStarting(false);
      }
    },
    [effectiveVideoProvider, museVideoActive, museVideoState?.provider, notify, selectedId, videoForm],
  );

  const museVideoJobId = museVideoJob?.job_id || "";
  const museVideoJobStatus = museVideoJob?.status || "";

  useEffect(() => {
    if (!museVideoJobId || !isMuseActive({ status: museVideoJobStatus })) return;
    let alive = true;
    let busy = false;
    const controller = new AbortController();
    const tick = async () => {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const next = await agentApi.museVideoJob(museVideoJobId, controller.signal);
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        setMuseVideoJobError("");
        setMuseVideoJob(next);
        if (!isMuseActive(next)) {
          if (next.status === "failed") notify("Muse 视频失败：" + (next.error || "未知原因"), "error");
          else notify("Muse 视频完成：" + (next.result?.provider || "auto") + " · " + (next.result?.elapsed_sec || 0).toFixed(1) + "s", "success");
          void refreshMuseVideo();
        }
      } catch (err) {
        if (!alive || !aliveRef.current || controller.signal.aborted) return;
        if (isAgentNotReady(err)) {
          setMuseVideoJob((prev) => (prev ? { ...prev, status: "failed", error: "Muse 视频任务已不存在（404）" } : prev));
          setMuseVideoJobError("Muse 视频任务接口 404：任务已不存在，已解除运行态。");
          return;
        }
        setMuseVideoJobError("Muse 视频轮询失败：" + errorText(err));
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(() => void tick(), 2000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [museVideoJobId, museVideoJobStatus, notify, refreshMuseVideo]);

  /** Muse 视频任务 → RunLog 形状（日志 stage 用后端 cloud/local/stub） */
  const museVideoRun = useMemo<AgentRun | null>(() => {
    if (!museVideoJob) return null;
    return {
      job_id: museVideoJob.job_id,
      status: museVideoJob.status,
      logs: museVideoJob.logs.map((line) => ({ t: line.t, stage: line.stage || "cloud", msg: line.msg })),
      result: null,
      run_dir: "",
      run_id: "",
      error: museVideoJob.error,
      created: "",
      started: "",
      ended: "",
    };
  }, [museVideoJob]);

  const museVideoRecent = museVideo?.recent || [];
  const museVideoResult = museVideoJob?.result || null;

  const refresh = useCallback(async () => {
    setRefreshing(true);
    // V52：loadAll 返回是否真的刷新成功；被并发跳过或有关键接口失败时不假装成功
    const ok = await loadAll(true);
    if (selectedId) await loadWorkflow(selectedId);
    if (tab === "season" && seasonSid) await refreshSeason(seasonSid);
    await refreshMuse();
    await refreshMuseVideo();
    if (aliveRef.current) {
      setRefreshing(false);
      if (ok) notify("智能体数据已刷新", "success");
      else notify("刷新未完成：有接口失败（或上一轮还没结束），详情见页面错误条", "error");
    }
  }, [loadAll, loadWorkflow, notify, refreshMuse, refreshMuseVideo, refreshSeason, seasonSid, selectedId, tab]);

  /* ------------------------------ 派生视图数据 ------------------------------ */
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = (item: AgentTemplate) => !q || item.id.toLowerCase().includes(q) || item.label.toLowerCase().includes(q);
    const filtered = templates.filter(hit);
    return {
      base: filtered.filter((item) => item.kind === "base"),
      custom: filtered.filter((item) => item.kind === "custom"),
      hit: filtered.length,
      total: templates.length,
    };
  }, [templates, query]);

  const stages = draft || [];
  const enabledStages = stages.filter((stage) => stage.enabled);
  const agentName = useCallback((agentId: string) => roles[agentId] || agentId || "—", [roles]);
  const evidence = useMemo(() => agentEvidence(job), [job]);
  const runSummary = useMemo(() => summarizeRun(job), [job]);
  const seasonSelected = useMemo(() => seriesList.find((item) => item.id === seasonSid) || null, [seriesList, seasonSid]);
  const episodes = seasonEpisodes || seasonSelected?.members || [];
  const editOpMeta = AGENT_EDIT_OPS.find((item) => item.id === editOp) || AGENT_EDIT_OPS[0];

  const longformReady = templates.filter((item) => item.duration >= 300).length;
  const chips = [
    { label: "模板", value: String(templates.length || "—"), tone: "gold" as const },
    { label: "5 分钟", value: longformReady + "/" + (templates.length || "—"), tone: "slate" as const },
    { label: "已出片", value: readyCount + "/" + (templates.length || "—"), tone: "jade" as const },
    { label: "角色", value: String(Object.keys(roles).length || "—"), tone: "slate" as const },
    {
      label: "run",
      value: job ? agentStatusLabel(job.status) : "空闲",
      tone: runActive ? ("gold" as const) : job?.status === "failed" ? ("red" as const) : ("slate" as const),
    },
  ];

  const selectedType = typeMetaOf(selected);

  return (
    <AppShell
      active="agent"
      kicker="AGENT ORCHESTRATION · 智能体编排"
      title="智能体编排"
      breadcrumb={[{ label: "造片局" }, { label: tab === "season" ? "季生产" : "单集工作流" }]}
      tabs={TABS}
      activeTab={tab}
      onTabChange={(key) => onTabChange(key === "season" ? "season" : "workflow")}
      chips={chips}
      primaryAction={
        tab === "season"
          ? {
              label: seasonBusy === "run" ? "整季生产启动中…" : "整季生产",
              onClick: () => void startSeason("run"),
              disabled: !seasonSid || Boolean(seasonBusy) || runActive,
              title: "按系列合同顺序生产未出片的集（only_missing，单并发）",
              icon: <IconPlay size={14} />,
            }
          : {
              label: starting ? "启动中…" : form.dryRun ? "运行（dry-run）" : "运行工作流",
              onClick: () => void startRun(),
              disabled: !selectedId || starting || runActive,
              title: selected ? "按选中的阶段跑 " + selected.id + " 智能体工作流" : "先在左栏选择模板",
              icon: <IconPlay size={14} />,
            }
      }
      chipsExtra={
        <MuseChip
          available={muse ? muse.available.ok : null}
          pending={!muse && !museError}
          open={museChipOpen}
          pinned={museChipPinned}
          chipRef={museChipRef}
          pos={museChipPos}
          muse={muse}
          museError={museError}
          onToggle={toggleMuseChip}
          onHoverOpen={openMuseChip}
          onHoverClose={scheduleCloseMuseChip}
          onPopEnter={cancelCloseMuseChip}
          onPopClose={scheduleCloseMuseChip}
        />
      }
      onRefresh={() => void refresh()}
      refreshing={refreshing}
      onOpenSettings={onOpenSettings}
    >
      <div className="ag-page">
        {loadError && (
          <div className="v18-error">
            <IconAlert size={15} />
            <div className="v18-error-main">
              <b>部分数据读取失败</b>
              <p className="v18-error-text">{loadError}</p>
            </div>
            <div className="v18-error-actions">
              <button type="button" className="cm-btn cm-btn-sm" onClick={() => void loadAll(false)}>
                <IconRefresh size={12} /> 重试
              </button>
            </div>
          </div>
        )}

        <PageHeader
          title={tab === "workflow" ? "单集工作流" : "整季生产"}
          subtitle={
            tab === "workflow"
              ? "选一个模板 → 勾阶段 → 跑一集；日志与产物在右栏。"
              : "一个世界一季：串行跑多集，只补缺失的集。"
          }
          meta={
            <StatRow>
              <Stat label="模板" value={templates.length} hint="综艺 + 短片" />
              <Stat
                label="阶段"
                value={stages.length}
                hint={`已启用 ${enabledStages.length}`}
                tone="accent"
              />
              <Stat label="本次 run" value={job ? agentStatusLabel(job.status) : "空闲"} hint={jobTemplate || "未选择模板"} />
              <Stat label="季" value={seriesList.length} hint={seasonSelected ? seasonSelected.label : "未选择系列"} />
            </StatRow>
          }
        />

        {tab === "workflow" ? (
          <div className="ag-grid ag-grid-workflow">
            {/* ---------------- 左栏：模板 ---------------- */}
            <aside className="ag-col ag-rail">
              <div className="ag-rail-head">
                <label className="st-search ag-search">
                  <IconSearch size={14} />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索模板 id / 名称"
                    aria-label="搜索模板"
                  />
                  {query && (
                    <button type="button" className="ag-search-clear" onClick={() => setQuery("")} aria-label="清空搜索">
                      <IconClose size={12} />
                    </button>
                  )}
                </label>
                <div className="ag-rail-meta">
                  <span>内建 {groups.base.length}</span>
                  <span>自定义 {groups.custom.length}</span>
                  <em>
                    {groups.hit}/{groups.total}
                  </em>
                </div>
              </div>

              <div className="ag-rail-list">
                {loading ? (
                  <div className="ag-skel-list">
                    {Array.from({ length: 8 }).map((_, index) => (
                      <div key={index} className="ag-skel ag-skel-row" />
                    ))}
                  </div>
                ) : (
                  <>
                    <TemplateGroup
                      title="内建"
                      items={groups.base}
                      selectedId={selectedId}
                      readyOf={readyOf}
                      typeOf={typeMetaOf}
                      onSelect={selectTemplate}
                    />
                    <TemplateGroup
                      title="自定义"
                      items={groups.custom}
                      selectedId={selectedId}
                      readyOf={readyOf}
                      typeOf={typeMetaOf}
                      onSelect={selectTemplate}
                    />
                    {groups.hit === 0 && (
                      <div className="v18-empty">
                        <IconSearch size={20} />
                        <b>没有匹配的模板</b>
                        <span>试试清空搜索，或换一个 id / 名称关键词。</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </aside>

            {/* ---------------- 中栏：工作流 ---------------- */}
            <section className="ag-col ag-stages-col">
              <header className="ag-col-head">
                <div className="ag-col-head-main">
                  <h2 className="ag-col-title">
                    <IconFlow size={17} />
                    {selected ? selected.label : "选择模板"}
                  </h2>
                  <p className="ag-col-sub">
                    {selected ? (
                      <>
                        <span className="cm-mono">{selected.id}</span>
                        <span className="ag-sep">·</span>
                        <span>
                          {selectedType.meta.label}
                          {selectedType.style ? " · " + selectedType.style.label : ""}
                        </span>
                        <span className="ag-sep">·</span>
                        <span>{workflow?.stack ? STACK_LABEL[workflow.stack] || workflow.stack : "栈 —"}</span>
                        <span className="ag-sep">·</span>
                        <span>
                          {enabledStages.length}/{stages.length} 阶段启用
                        </span>
                        <span className="ag-sep">·</span>
                        <span title={selected.duration ? "模板目标时长（/api/v6/agent/overview duration）" : "后端未提供目标时长"}>
                          时长 {selected.duration ? selected.duration + "s" : "—"}
                        </span>
                        {selected.duration > 0 && (
                          <span
                            className={"v54-dur " + (selected.duration >= 300 ? "is-ok" : "is-miss")}
                            title={"5 分钟长版目标 " + 300 + "s · 当前 " + selected.duration + "s"}
                          >
                            长版 {selected.duration >= 300 ? "达标" : "未达标"}
                          </span>
                        )}
                      </>
                    ) : (
                      "从左栏选一个模板查看它的智能体工作流"
                    )}
                  </p>
                </div>
                <div className="ag-col-actions">
                  {dirty && <span className="ag-dirty">未保存</span>}
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm cm-btn-primary"
                    onClick={() => void saveWorkflow()}
                    disabled={!draft || !dirty || saving}
                    title="PUT /api/v6/agent/workflow/{template} → contract/v6/workflows/<id>.json"
                  >
                    {saving ? <Spinner size={12} /> : <IconCheck size={12} />} 保存工作流
                  </button>
                  <button
                    type="button"
                    className="cm-btn cm-btn-sm"
                    onClick={() => void restoreDefault()}
                    disabled={!selectedId || saving}
                    title="调用智能体 reset_workflow，把工作流文件重置为内建默认"
                  >
                    <IconRefresh size={12} /> 恢复默认
                  </button>
                </div>
              </header>

              {wfError && (
                <div className="v18-error ag-inline-error">
                  <IconAlert size={15} />
                  <div className="v18-error-main">
                    <b>工作流</b>
                    <p className="v18-error-text">{wfError}</p>
                  </div>
                </div>
              )}

              <div className="ag-stage-list">
                {wfLoading && !draft ? (
                  Array.from({ length: 4 }).map((_, index) => <div key={index} className="ag-skel ag-skel-stage" />)
                ) : stages.length ? (
                  stages.map((stage, index) => {
                    const tone = stageTone(stage.id);
                    const invalid = paramsInvalid(paramsText[stage.id]);
                    return (
                      <article key={stage.id} className={"ag-stage is-" + tone + (stage.enabled ? "" : " is-off")}>
                        <div className="ag-stage-top">
                          <span className="ag-stage-no">{String(index + 1).padStart(2, "0")}</span>
                          <div className="ag-stage-title">
                            <b>{stageLabel(stage.id)}</b>
                            <span className="cm-mono ag-stage-id">{stage.id}</span>
                          </div>
                          <span className={"ag-stage-agent is-" + tone} title={"负责智能体：" + stage.agent}>
                            <IconAgent size={12} /> {agentName(stage.agent)}
                          </span>
                          <span className="ag-stage-move">
                            <button
                              type="button"
                              className="ag-move"
                              onClick={() => moveStage(stage.id, -1)}
                              disabled={index === 0}
                              title="上移（保存后写回 stages 顺序）"
                              aria-label={"上移阶段 " + stage.id}
                            >
                              <IconChevronUp size={12} />
                            </button>
                            <button
                              type="button"
                              className="ag-move"
                              onClick={() => moveStage(stage.id, 1)}
                              disabled={index === stages.length - 1}
                              title="下移（保存后写回 stages 顺序）"
                              aria-label={"下移阶段 " + stage.id}
                            >
                              <IconChevronDown size={12} />
                            </button>
                          </span>
                          <label className={"ag-switch" + (stage.enabled ? " is-on" : "")} title={stage.enabled ? "已启用（点击禁用）" : "已禁用（点击启用）"}>
                            <input
                              type="checkbox"
                              checked={stage.enabled}
                              onChange={(event) => toggleStage(stage.id, event.target.checked)}
                              aria-label={"启用阶段 " + stage.id}
                            />
                            <i />
                          </label>
                        </div>

                        <div className="ag-stage-meta">
                          <span className="v18-tag is-slate">
                            <IconTemplate size={9} /> {stage.tool || "—"}
                          </span>
                          {stage.optional && <span className="v18-tag is-gold">可选</span>}
                          <span className="ag-stage-deps">
                            <em>依赖</em>
                            {stage.depends.length ? (
                              stage.depends.map((dep) => (
                                <span key={dep} className="v18-tag is-jade">
                                  {dep}
                                </span>
                              ))
                            ) : (
                              <span className="ag-muted">无</span>
                            )}
                          </span>
                        </div>

                        {stage.desc && <p className="ag-stage-desc">{stage.desc}</p>}

                        <div className="ag-stage-params">
                          <label className="ag-params-label">
                            <span>params</span>
                            {invalid && <em>JSON 不合法</em>}
                          </label>
                          <textarea
                            className={"cm-input ag-params" + (invalid ? " is-invalid" : "")}
                            value={paramsText[stage.id] ?? ""}
                            onChange={(event) => changeParams(stage.id, event.target.value)}
                            placeholder="{}（留空 = 无参数）"
                            spellCheck={false}
                            rows={2}
                          />
                        </div>
                      </article>
                    );
                  })
                ) : (
                  <div className="v18-empty">
                    <IconFlow size={20} />
                    <b>没有阶段</b>
                    <span>{selected ? "该模板的工作流为空，可点「恢复默认」重建内建阶段。" : "先从左栏选一个模板。"}</span>
                  </div>
                )}
              </div>
            </section>

            {/* ---------------- 右栏：运行 + 编辑 ---------------- */}
            <div className="ag-col ag-run-col">
              <section className="v18-panel">
                <header className="v18-panel-head">
                  <div className="v18-panel-head-main">
                    <h3 className="v18-panel-title">
                      <IconPlay size={15} /> 单集运行
                    </h3>
                    <p className="v18-panel-sub">RUN · {selectedId || "未选择模板"}</p>
                  </div>
                  <div className="v18-panel-actions">
                    <span className={"ag-status" + (runActive ? " is-running" : job ? " is-" + job.status : "")}>
                      <IconDot size={10} /> {job ? agentStatusLabel(job.status) : "空闲"}
                    </span>
                  </div>
                </header>

                <div className="v18-panel-body">
                  <div className="ag-form-grid">
                    <label className="ag-field">
                      <span>时长（s）</span>
                      <input
                        className="cm-input"
                        type="number"
                        min={5}
                        max={300}
                        value={form.duration}
                        onChange={(event) => setForm((prev) => ({ ...prev, duration: event.target.value }))}
                      />
                    </label>
                    <label className="ag-field">
                      <span>seed</span>
                      <input
                        className="cm-input"
                        type="number"
                        value={form.seed}
                        onChange={(event) => setForm((prev) => ({ ...prev, seed: event.target.value }))}
                      />
                    </label>
                  </div>

                  <label className="ag-field">
                    <span>goal（写进 run 报告）</span>
                    <input
                      className="cm-input"
                      value={form.goal}
                      onChange={(event) => setForm((prev) => ({ ...prev, goal: event.target.value }))}
                      placeholder="例如：重做片头节奏 / 只验证 story 合同"
                    />
                  </label>

                  <div className="ag-field">
                    <span className="ag-field-label">
                      阶段（默认全选启用阶段 · 依赖会自动补齐）
                      <em>
                        {runStages.length}/{enabledStages.length}
                      </em>
                    </span>
                    <div className="ag-stage-pick">
                      {enabledStages.length ? (
                        enabledStages.map((stage) => (
                          <label key={stage.id} className={"ag-pick" + (runStages.includes(stage.id) ? " is-on" : "")}>
                            <input
                              type="checkbox"
                              checked={runStages.includes(stage.id)}
                              onChange={() => toggleRunStage(stage.id)}
                            />
                            <span>{stageLabel(stage.id)}</span>
                          </label>
                        ))
                      ) : (
                        <span className="ag-muted">没有启用的阶段 —— 先在中栏打开至少一个阶段</span>
                      )}
                    </div>
                  </div>

                  <label className="ag-toggle">
                    <input
                      type="checkbox"
                      checked={form.dryRun}
                      onChange={(event) => setForm((prev) => ({ ...prev, dryRun: event.target.checked }))}
                    />
                    <span>dry-run（只规划不渲染，秒级返回）</span>
                  </label>

                  {/* V52：stage_params 透传（高级） */}
                  <div className="ag-advanced">
                    <button
                      type="button"
                      className="ag-advanced-toggle"
                      onClick={() => setAdvancedOpen((prev) => !prev)}
                      aria-expanded={advancedOpen}
                    >
                      <IconChevronDown size={12} /> 高级参数（stage_params）{advancedJson.trim() && advancedJson.trim() !== "{}" ? " · 已填写" : ""}
                    </button>
                    {advancedOpen && (
                      <>
                        <textarea
                          className={"cm-input ag-params" + (stageParamsError(advancedJson) ? " is-invalid" : "")}
                          rows={3}
                          value={advancedJson}
                          onChange={(event) => setAdvancedJson(event.target.value)}
                          spellCheck={false}
                          placeholder={'{"muse":{"apply":true}}'}
                          aria-label="高级参数 JSON"
                        />
                        <span className="ag-advanced-hint">
                          {stageParamsError(advancedJson) || "只对本次 run 生效；留空 = {}"}
                        </span>
                      </>
                    )}
                  </div>

                  {isAgentActive(job) && jobTemplate && jobTemplate !== selectedId && (
                    <p className="v18-note is-warn">
                      <IconInfo size={13} />
                      <span>
                        另一个模板在跑：<b>{jobTemplate}</b>（job {job?.job_id || "—"}）；日志在下方，切换模板不会丢。
                      </span>
                    </p>
                  )}

                  <button
                    type="button"
                    className="cm-btn cm-btn-primary ag-run-btn"
                    onClick={() => void startRun()}
                    disabled={!selectedId || starting || runActive}
                  >
                    {starting ? <Spinner size={13} /> : <IconPlay size={13} />}
                    {runActive ? "运行中…" : form.dryRun ? "运行（dry-run）" : "运行工作流"}
                  </button>

                  <RunLog job={job} startError={startError} pollError={pollError} evidence={evidence} summary={runSummary} />
                </div>
              </section>

              <section className="v18-panel ag-muse-card">
                <header className="v18-panel-head">
                  <div className="v18-panel-head-main">
                    <h3 className="v18-panel-title">
                      <IconVolume size={15} /> Muse 口型修正
                    </h3>
                    <p className="v18-panel-sub">
                      MuseTalk 口型 · {selectedId || "未选择模板"}
                    </p>
                  </div>
                  <div className="v18-panel-actions">
                    <span className={"ag-status " + (museReady ? "is-ok" : muse ? "is-failed" : museError ? "is-failed" : "is-pending")}>
                      <IconDot size={10} /> {museReady ? "可用" : muse ? "不可用" : museError ? "未接入" : "检测中"}
                    </span>
                  </div>
                </header>
                <div className="v18-panel-body">
                  <div className="ag-muse-deps">
                    <span className="v18-tag is-gold">provider · {muse?.available.providerDefault || "musetalk"}</span>
                    <span className={"v18-tag " + (muse?.available.musetalk ? "is-jade" : "is-red")}>
                      {muse?.available.musetalk ? "已就绪" : "未检出"}
                    </span>
                    <span className={"v18-tag " + (muse?.available.torchInEnv ? "is-jade" : "is-red")}>
                      torch {muse?.available.torchInEnv ? "ok" : "—"}
                    </span>
                    <span className={"v18-tag " + (muse?.available.syncnet ? "is-jade" : "is-slate")}>
                      syncnet {muse?.available.syncnet ? "ok" : "—"}
                    </span>
                    {museDeps.length > 0 && (
                      <span
                        className={"v18-tag " + (museDepsBad.length ? "is-red" : "is-jade")}
                        title={museDeps.map((dep) => dep.key + "=" + dep.value).join(" · ")}
                      >
                        deps {museDeps.length - museDepsBad.length}/{museDeps.length}
                      </span>
                    )}
                    {museDepsBad.map((dep) => (
                      <span key={dep.key} className="v18-tag is-red" title={dep.key + " = " + dep.value}>
                        {dep.key} {dep.value}
                      </span>
                    ))}
                    {muse?.available.venv && (
                      <span className="v18-tag is-slate" title={muse.available.venv}>
                        venv ok
                      </span>
                    )}
                  </div>

                  {muse?.available.venv && <code className="ag-muse-venv">venv_python · {muse.available.venv}</code>}

                  {museError && (
                    <div className="v18-error ag-inline-error">
                      <IconAlert size={14} />
                      <div className="v18-error-main">
                        <p className="v18-error-text">{museError}</p>
                      </div>
                    </div>
                  )}
                  {!museReady && muse?.available.reason && (
                    <p className="v18-note is-warn">
                      <IconAlert size={13} />
                      <span>{muse.available.reason}</span>
                    </p>
                  )}

                  <button
                    type="button"
                    className="cm-btn cm-btn-primary ag-run-btn"
                    onClick={() => void startMuseSync()}
                    disabled={!museReady || !selectedId || museStarting || museActive}
                    title={
                      !museReady
                        ? "Muse 不可用：" + (muse?.available.reason || museError || "本机未就绪")
                        : "给本集有台词镜头跑 MuseTalk 口型（默认 2 个镜头）"
                    }
                  >
                    {museStarting ? <Spinner size={13} /> : <IconVolume size={13} />}
                    {museActive ? "口型合成中…" : "给本集对白镜跑口型"}
                  </button>

                  {museJobError && (
                    <div className="v18-error ag-inline-error">
                      <IconAlert size={14} />
                      <div className="v18-error-main">
                        <p className="v18-error-text">{museJobError}</p>
                      </div>
                    </div>
                  )}

                  {museJob && (
                    <RunLog
                      job={museRun}
                      startError=""
                      pollError=""
                      evidence={[]}
                      summary={{ done: 0, skipped: 0, failed: 0, total: 0 }}
                    />
                  )}

                  {museJob && museJob.items.length > 0 && (
                    <div className="ag-muse-items">
                      {museJob.items.map((item) => (
                        <div key={item.shot} className="ag-muse-item">
                          {item.out_url ? (
                            <a
                              className="cm-btn cm-btn-sm"
                              href={mediaUrl(item.out_url)}
                              target="_blank"
                              rel="noreferrer"
                              title={item.out_url}
                            >
                              <IconExternal size={12} /> {item.shot} · {item.elapsed.toFixed(1)}s
                            </a>
                          ) : (
                            <span className="ag-muse-fail" title={item.error || "该镜头口型失败"}>
                              <IconAlert size={12} /> {item.shot} · 失败
                            </span>
                          )}
                          {!item.out_url && item.error && <span className="ag-muse-fail-text">{item.error}</span>}
                          {item.sync && (
                            <span
                              className={"ag-sync is-" + syncTone(item.sync.corr_best)}
                              title={
                                "嘴动 ↔ 语音包络相关（" +
                                (item.sync.provider || "mouth_audio_corr") +
                                "）；>0.25 同向 · ≈0 无关 · lag " +
                                item.sync.lag_frames +
                                " 帧 · face " +
                                item.sync.face_ratio
                              }
                            >
                              同步 {item.sync.corr_best.toFixed(2)}（lag {item.sync.lag_frames} 帧）
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="ag-muse-recent">
                    <div className="ag-muse-recent-head">
                      <span>最近生成</span>
                      <em>{museRecent.length}</em>
                    </div>
                    {museRecent.length ? (
                      <div className="ag-muse-recent-list">
                        {museRecent.slice(0, 4).map((item, index) => {
                          const src = mediaUrl(item.url);
                          return (
                            <a
                              key={item.template + item.shot + index}
                              className="ag-muse-clip"
                              href={src}
                              target="_blank"
                              rel="noreferrer"
                              title={item.template + " · " + item.shot + " · " + src}
                            >
                              {src ? (
                                <video className="ag-muse-video" src={src + "#t=0.3"} preload="metadata" muted playsInline />
                              ) : (
                                <span className="ag-muse-video is-empty" aria-hidden="true" />
                              )}
                              <span className="ag-muse-clip-meta">
                                <b>{item.shot || "—"}</b>
                                <em>{item.duration ? item.duration.toFixed(1) + "s" : item.template}</em>
                              </span>
                            </a>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="ag-muted">还没有 Muse 片段；跑一次口型后这里会出现缩略视频。</p>
                    )}
                  </div>
                </div>
              </section>

              <section className="v18-panel ag-mv-card">
                <header className="v18-panel-head">
                  <div className="v18-panel-head-main">
                    <h3 className="v18-panel-title">
                      <IconVideo size={15} /> Muse 视频
                    </h3>
                    <p className="v18-panel-sub">
                      MUSE VIDEO · /api/v6/muse/video · {museVideoState?.provider || "—"}
                    </p>
                  </div>
                  <div className="v18-panel-actions">
                    <span
                      className={"v18-tag is-" + museVideoTone(museVideoState?.provider || "", museVideoReady)}
                      title={
                        museVideoReady
                          ? "当前 provider：" + (museVideoState?.provider || "auto")
                          : museVideoError || museVideoState?.hint || "未接入"
                      }
                    >
                      {museVideoState
                        ? museVideoReady
                          ? museVideoState.provider || "auto"
                          : "未接入"
                        : museVideoError
                          ? "未接入"
                          : "检测中"}
                    </span>
                  </div>
                </header>
                <div className="v18-panel-body">
                  {museVideoError && (
                    <div className="v18-error ag-inline-error">
                      <IconAlert size={14} />
                      <div className="v18-error-main">
                        <p className="v18-error-text">{museVideoError}</p>
                      </div>
                    </div>
                  )}

                  {!museVideoReady && (
                    <p className="v18-note is-warn">
                      <IconAlert size={13} />
                      <span>
                        {museVideoState?.hint || "Muse 视频后端未就绪（provider 全部不可用）。"}
                        {" "}
                        <a
                          href={museVideoState?.docs || "docs/MUSE_VIDEO_INTEGRATION.md"}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看接法
                        </a>
                      </span>
                    </p>
                  )}
                  {museVideoReady && museVideoState?.hint && (
                    <p className="v18-note">
                      <IconInfo size={13} />
                      <span>{museVideoState.hint}</span>
                    </p>
                  )}

                  <details className="ag-mv-checks">
                    <summary>provider 自检（{Object.keys(museVideoChecks).length}）</summary>
                    {Object.keys(museVideoChecks).length ? (
                      Object.keys(museVideoChecks).map((key) => (
                        <div
                          key={key}
                          className={"ag-mv-check" + (key === museVideoState?.provider ? " is-current" : "")}
                        >
                          <b>
                            {key}
                            {key === museVideoState?.provider ? " · 当前" : ""}
                          </b>
                          <span>
                            {Object.keys(museVideoChecks[key])
                              .map((field) => field + " " + checkValue(museVideoChecks[key][field]))
                              .join(" · ")}
                          </span>
                        </div>
                      ))
                    ) : (
                      <p className="ag-muted">没有自检数据。</p>
                    )}
                  </details>

                  <label className="ag-field">
                    <span>prompt（一句话镜头）</span>
                    <textarea
                      className="cm-input ag-mv-prompt"
                      rows={3}
                      value={videoForm.prompt}
                      onChange={(event) => setVideoForm((prev) => ({ ...prev, prompt: event.target.value }))}
                      placeholder="例如：夜雨霓虹街口，一只全息锦鲤绕过镜头，浅景深电影感"
                      spellCheck={false}
                    />
                  </label>

                  <div className="ag-mv-form">
                    <label className="ag-field">
                      <span>时长</span>
                      <select
                        className="cm-select"
                        value={videoForm.duration}
                        onChange={(event) => setVideoForm((prev) => ({ ...prev, duration: event.target.value }))}
                      >
                        {["2", "4", "6"].map((value) => (
                          <option key={value} value={value}>
                            {value}s
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="ag-field">
                      <span>画幅</span>
                      <select
                        className="cm-select"
                        value={videoForm.aspect}
                        onChange={(event) => setVideoForm((prev) => ({ ...prev, aspect: event.target.value }))}
                      >
                        {["16:9", "9:16", "1:1"].map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <label className="ag-field">
                    <span>provider</span>
                    <select
                      className="cm-select"
                      value={videoForm.provider}
                      onChange={(event) => setVideoForm((prev) => ({ ...prev, provider: event.target.value }))}
                    >
                      {MUSE_VIDEO_PROVIDERS.map((item) => (
                        <option key={item.id || "auto"} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="ag-mv-actions">
                    <button
                      type="button"
                      className="cm-btn cm-btn-primary ag-run-btn"
                      onClick={() => void startMuseVideo(false)}
                      disabled={
                        !museVideoReady ||
                        !videoForm.prompt.trim() ||
                        museVideoStarting ||
                        museVideoActive ||
                        effectiveVideoProvider === "stub"
                      }
                      title={
                        !museVideoReady
                          ? "Muse 视频未就绪：" + (museVideoState?.hint || museVideoError || "provider 全部不可用")
                          : effectiveVideoProvider === "stub"
                            ? "provider=stub 只做占位：默认禁用，请用「占位测试」"
                            : "POST /api/v6/muse/video → 2s 轮询 /video/jobs/{id}"
                      }
                    >
                      {museVideoStarting ? <Spinner size={13} /> : <IconVideo size={13} />}
                      {museVideoActive ? "生成中…" : "生成视频"}
                    </button>
                    {effectiveVideoProvider === "stub" && (
                      <button
                        type="button"
                        className="cm-btn cm-btn-sm"
                        onClick={() => void startMuseVideo(true)}
                        disabled={!museVideoReady || !videoForm.prompt.trim() || museVideoStarting || museVideoActive}
                        title="provider=stub：显式 allow_stub=true，产物标 stub=true，不可交付（会二次确认）"
                      >
                        占位测试
                      </button>
                    )}
                  </div>

                  {effectiveVideoProvider === "stub" && (
                    <p className="v18-note is-warn">
                      <IconAlert size={13} />
                      <span>stub 只做接线自测（产物 stub=true，不可交付）：默认禁用「生成视频」，走「占位测试」二次确认。</span>
                    </p>
                  )}

                  {museVideoJobError && (
                    <div className="v18-error ag-inline-error">
                      <IconAlert size={14} />
                      <div className="v18-error-main">
                        <p className="v18-error-text">{museVideoJobError}</p>
                      </div>
                    </div>
                  )}

                  {museVideoJob && (
                    <RunLog
                      job={museVideoRun}
                      startError=""
                      pollError=""
                      evidence={[]}
                      summary={{ done: 0, skipped: 0, failed: 0, total: 0 }}
                    />
                  )}

                  {museVideoResult && museVideoResult.out_url && (
                    <div className="ag-mv-result">
                      <video
                        className="ag-video"
                        src={mediaUrl(museVideoResult.out_url)}
                        controls
                        playsInline
                        preload="metadata"
                      />
                      <div className="ag-mv-result-meta">
                        <span className="v18-tag is-gold">{museVideoResult.provider || "—"}</span>
                        {museVideoResult.stub && <span className="v18-tag is-red">stub 占位</span>}
                        {museVideoResult.bytes > 0 && (
                          <span className="v18-tag is-slate">{fmtSize(museVideoResult.bytes)}</span>
                        )}
                        <span className="v18-tag is-slate">{museVideoResult.elapsed_sec.toFixed(1)}s</span>
                        {museVideoResult.probe && (
                          <span className="v18-tag is-jade" title={museVideoResult.probe}>
                            {museVideoResult.probe}
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="ag-mv-recent">
                    <div className="ag-muse-recent-head">
                      <span>最近生成</span>
                      <em>{museVideoRecent.length}</em>
                    </div>
                    {museVideoRecent.length ? (
                      <div className="ag-muse-recent-list">
                        {museVideoRecent.slice(0, 4).map((item) => {
                          const src = mediaUrl(item.url);
                          return (
                            <a
                              key={item.name + item.url}
                              className="ag-muse-clip"
                              href={src}
                              target="_blank"
                              rel="noreferrer"
                              title={item.name + " · " + fmtSize(item.bytes) + " · " + item.mtime}
                            >
                              {src ? (
                                <video className="ag-muse-video" src={src + "#t=0.3"} preload="metadata" muted playsInline />
                              ) : (
                                <span className="ag-muse-video is-empty" aria-hidden="true" />
                              )}
                              <span className="ag-muse-clip-meta">
                                <b>{item.name || "—"}</b>
                                <em>
                                  {fmtSize(item.bytes)}
                                  {item.mtime ? " · " + item.mtime.slice(5) : ""}
                                </em>
                              </span>
                            </a>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="ag-muted">还没有 Muse 视频；生成一条后这里会出现缩略视频。</p>
                    )}
                  </div>
                </div>
              </section>

              <section className="v18-panel">
                <header className="v18-panel-head">
                  <div className="v18-panel-head-main">
                    <h3 className="v18-panel-title">
                      <IconAgent size={15} /> 智能体编辑
                    </h3>
                    <p className="v18-panel-sub">EDIT · POST /api/v6/agent/edit</p>
                  </div>
                </header>
                <div className="v18-panel-body">
                  <label className="ag-field">
                    <span>op</span>
                    <select
                      className="cm-select"
                      value={editOp}
                      onChange={(event) => {
                        const next = event.target.value;
                        setEditOp(next);
                        const meta = AGENT_EDIT_OPS.find((item) => item.id === next);
                        setEditText(meta?.placeholder || "");
                        setEditError("");
                        setEditResult(null);
                      }}
                    >
                      {AGENT_EDIT_OPS.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  {editOpMeta.needsCharacter && (
                    <label className="ag-field">
                      <span>character</span>
                      <input
                        className="cm-input"
                        value={editChar}
                        onChange={(event) => setEditChar(event.target.value)}
                        placeholder="例如 laozhou / guanyu"
                      />
                    </label>
                  )}

                  <label className="ag-field">
                    <span>{editOpMeta.field === "value" ? "value" : "json"}</span>
                    <textarea
                      className="cm-input ag-params"
                      rows={3}
                      value={editText}
                      onChange={(event) => setEditText(event.target.value)}
                      placeholder={editOpMeta.placeholder}
                      spellCheck={false}
                    />
                  </label>

                  <p className="v18-note">
                    <IconInfo size={14} />
                    <span>{editOpMeta.hint}</span>
                  </p>

                  <button
                    type="button"
                    className="cm-btn cm-btn-sm ag-run-btn"
                    onClick={() => void applyEdit()}
                    disabled={!selectedId || editing}
                  >
                    {editing ? <Spinner size={12} /> : <IconCheck size={12} />} 应用
                  </button>

                  {editError && (
                    <div className="v18-error ag-inline-error">
                      <IconAlert size={14} />
                      <div className="v18-error-main">
                        <p className="v18-error-text">{editError}</p>
                      </div>
                    </div>
                  )}
                  {editResult && (
                    <div className="ag-edit-result">
                      <span className="v18-tag is-jade">已应用</span>
                      <pre className="ag-json">{shortJson(editResult)}</pre>
                    </div>
                  )}
                </div>
              </section>
            </div>
          </div>
        ) : (
          <div className="ag-grid ag-grid-season">
            {/* ---------------- 季：系列列表 ---------------- */}
            <aside className="ag-col ag-rail">
              <div className="ag-rail-head">
                <div className="ag-rail-meta">
                  <span>系列 {seriesList.length}</span>
                  <em>{seriesSource === "v5" ? "合同回退" : seriesSource === "v6" ? "M3 接口" : "—"}</em>
                </div>
              </div>
              <div className="ag-rail-list">
                {loading ? (
                  <div className="ag-skel-list">
                    {Array.from({ length: 4 }).map((_, index) => (
                      <div key={index} className="ag-skel ag-skel-row" />
                    ))}
                  </div>
                ) : seriesList.length ? (
                  seriesList.map((series) => (
                    <button
                      key={series.id}
                      type="button"
                      className={"ag-tpl ag-season-item" + (series.id === seasonSid ? " is-active" : "")}
                      onClick={() => {
                        setSeasonSid(series.id);
                        setSeasonError("");
                        setSeasonNote("");
                        void refreshSeason(series.id);
                      }}
                      title={series.description || series.id}
                    >
                      <span className="ag-tpl-line">
                        <b>{series.label}</b>
                        <em className={"v18-tag is-" + (series.ready_count >= series.total ? "jade" : "gold")}>
                          {series.ready_count}/{series.total}
                        </em>
                      </span>
                      <span className="ag-tpl-sub">
                        <span className="cm-mono">{series.id}</span>
                        <span>{Math.round(series.total_duration)}s</span>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="v18-empty">
                    <IconFilm size={20} />
                    <b>没有系列合同</b>
                    <span>在 contract/v5/series/&lt;sid&gt;.json 里定义一个季（members 有序）后刷新。</span>
                  </div>
                )}
              </div>
            </aside>

            {/* ---------------- 季：集列表 + 生产 ---------------- */}
            <div className="ag-col ag-season-main">
              <section className="v18-panel">
                <header className="v18-panel-head">
                  <div className="v18-panel-head-main">
                    <h3 className="v18-panel-title">
                      <IconFilm size={16} />
                      {seasonSelected ? seasonSelected.label : "选择一个系列"}
                    </h3>
                    <p className="v18-panel-sub">
                      SEASON · {seasonSelected ? seasonSelected.id + " · 就绪 " + seasonSelected.ready_count + "/" + seasonSelected.total : "—"}
                      {seasonUpdated ? " · 更新 " + seasonUpdated : ""}
                    </p>
                  </div>
                  <div className="v18-panel-actions">
                    <label
                      className={"ag-toggle ag-season-muse" + (withMuse ? " is-on" : "")}
                      title="POST body 带 with_muse:true —— 只跑每集 muse 口型阶段，不重渲镜头"
                    >
                      <input
                        type="checkbox"
                        checked={withMuse}
                        onChange={(event) => setWithMuse(event.target.checked)}
                      />
                      <span>同时跑口型（Muse）</span>
                    </label>
                    <label
                      className={"ag-toggle ag-season-muse" + (withMuse && museApply ? " is-on" : "")}
                      title="body 带 muse_apply:true —— 口型写回 shots 并重新合成（仅 with_muse 时生效）"
                      style={withMuse ? undefined : { opacity: 0.5 }}
                    >
                      <input
                        type="checkbox"
                        checked={museApply}
                        disabled={!withMuse}
                        onChange={(event) => setMuseApply(event.target.checked)}
                      />
                      <span>写回成片并重新合成</span>
                    </label>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm"
                      onClick={() => void startSeason("plan")}
                      disabled={!seasonSid || Boolean(seasonBusy) || runActive}
                      title="POST /api/v6/agent/season/{sid}/run {dry_run:true}"
                    >
                      {seasonBusy === "plan" ? <Spinner size={12} /> : <IconClock size={12} />} 季计划预览（dry-run）
                    </button>
                    <button
                      type="button"
                      className="cm-btn cm-btn-sm cm-btn-primary"
                      onClick={() => void startSeason("run")}
                      disabled={!seasonSid || Boolean(seasonBusy) || runActive}
                      title={
                        "POST /api/v6/agent/season/{sid}/run {only_missing:true" +
                        (withMuse ? ", with_muse:true" : "") +
                        (withMuse && museApply ? ", muse_apply:true" : "") +
                        "}"
                      }
                    >
                      {seasonBusy === "run" ? <Spinner size={12} /> : <IconPlay size={12} />} 整季生产（only_missing）
                    </button>
                  </div>
                </header>

                <div className="v18-panel-body">
                  {withMuse && (
                    <p className="v18-note">
                      <IconInfo size={14} />
                      <span>
                        整季生产会带 with_muse:true：对全部集数只跑 muse 口型阶段，不重渲镜头
                        {museApply ? "；并写回 shots 后重新合成" : ""}。
                      </span>
                    </p>
                  )}
                  {seasonError && (
                    <div className="v18-error">
                      <IconAlert size={15} />
                      <div className="v18-error-main">
                        <b>季生产</b>
                        <p className="v18-error-text">{seasonError}</p>
                      </div>
                    </div>
                  )}
                  {seasonNote && (
                    <p className="v18-note">
                      <IconInfo size={14} />
                      <span>{seasonNote}</span>
                    </p>
                  )}

                  <div className="ag-ep-head">
                    <span>序号</span>
                    <span>集</span>
                    <span>成片</span>
                    <span>最近 run</span>
                    <span>状态</span>
                  </div>
                  <div className="ag-ep-list">
                    {episodes.length ? (
                      episodes.map((episode, index) => (
                        <div key={episode.template + index} className={"ag-ep" + (episode.ready ? " is-ready" : "")}>
                          <span className="ag-ep-no">{String(index + 1).padStart(2, "0")}</span>
                          <span className="ag-ep-main">
                            <b>{episode.label}</b>
                            <em className="cm-mono">{episode.template}</em>
                          </span>
                          <span className="ag-ep-final">
                            {episode.ready ? (
                              <span className="v18-tag is-jade">
                                <IconCheck size={9} /> 已出片
                              </span>
                            ) : (
                              <span className="v18-tag is-slate">待生产</span>
                            )}
                            {episode.duration ? <em>{episode.duration.toFixed(1)}s</em> : null}
                          </span>
                          <span className="ag-ep-run cm-mono" title={episode.last_run || undefined}>
                            {episode.last_run || "—"}
                          </span>
                          <span className="ag-ep-status">
                            {episode.last_run_status || episode.status ? (
                              <span
                                className={"ag-status is-" + (episode.last_run_status || episode.status)}
                                title={episode.last_run_started ? "开始于 " + episode.last_run_started : undefined}
                              >
                                {agentStatusLabel(episode.last_run_status || episode.status)}
                              </span>
                            ) : episode.stages ? (
                              <span className="v18-tag is-slate">{episode.stages} 阶段</span>
                            ) : (
                              <span className="ag-muted">—</span>
                            )}
                          </span>
                        </div>
                      ))
                    ) : (
                      <div className="v18-empty">
                        <IconFilm size={20} />
                        <b>没有集</b>
                        <span>该系列合同的 members 为空，或季状态接口返回 404。</span>
                      </div>
                    )}
                  </div>

                  <RunLog job={job} startError="" pollError={pollError} evidence={evidence} summary={runSummary} />
                </div>
              </section>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

/* ------------------------------ 子组件 ------------------------------ */

function MuseChip({
  available,
  pending,
  open,
  pinned,
  chipRef,
  pos,
  muse,
  museError,
  onToggle,
  onHoverOpen,
  onHoverClose,
  onPopEnter,
  onPopClose,
}: {
  available: boolean | null;
  pending: boolean;
  open: boolean;
  pinned: boolean;
  chipRef: RefObject<HTMLButtonElement>;
  pos: { top: number; right: number };
  muse: MuseStatus | null;
  museError: string;
  onToggle: () => void;
  onHoverOpen: () => void;
  onHoverClose: () => void;
  onPopEnter: () => void;
  onPopClose: () => void;
}) {
  const tone = available === null ? "slate" : available ? "jade" : "red";
  const text = available === null ? (pending ? "检测中" : "未接入") : available ? "可用" : "不可用";
  const deps = muse?.available.deps || [];
  return (
    <>
      <button
        ref={chipRef}
        type="button"
        className={"shell-chip ag-muse-chip is-" + tone + (pinned ? " is-pinned" : "")}
        onClick={onToggle}
        onMouseEnter={onHoverOpen}
        onMouseLeave={onHoverClose}
        aria-expanded={open}
        title="Muse 口型（MuseTalk 本机推理）：悬停预览，点击固定/取消固定"
      >
        <em>Muse 口型</em>
        {text}
      </button>
      {open &&
        createPortal(
          <div
            className="ag-muse-pop"
            style={{ top: pos.top, right: pos.right }}
            role="dialog"
            aria-label="Muse 口型状态"
            onMouseDown={(event) => event.stopPropagation()}
            onMouseEnter={onPopEnter}
            onMouseLeave={onPopClose}
          >
            <div className="ag-muse-pop-head">
              <b>Muse 口型 · MuseTalk</b>
              <span className={"ag-status " + (available ? "is-ok" : available === false ? "is-failed" : "is-pending")}>
                <IconDot size={10} /> {text}
              </span>
            </div>
            <div className="ag-muse-pop-rows">
              <div className="ag-muse-row">
                <em>provider</em>
                <b className={muse?.available.musetalk ? "is-ok" : "is-bad"}>musetalk {muse?.available.musetalk ? "✓" : "✗"}</b>
              </div>
              <div className="ag-muse-row">
                <em>torch</em>
                <b className={muse?.available.torchInEnv ? "is-ok" : "is-bad"}>{muse?.available.torchInEnv ? "in venv" : "缺失"}</b>
              </div>
              <div className="ag-muse-row">
                <em>syncnet</em>
                <b className={muse?.available.syncnet ? "is-ok" : "is-bad"}>{muse?.available.syncnet ? "ok" : "缺失"}</b>
              </div>
              {deps.map((dep) => (
                <div key={dep.key} className="ag-muse-row">
                  <em>{dep.key}</em>
                  <b className={dep.ok ? "is-ok" : "is-bad"}>{dep.value}</b>
                </div>
              ))}
              {muse?.available.venv && (
                <div className="ag-muse-row">
                  <em>venv</em>
                  <b>{muse.available.venv}</b>
                </div>
              )}
            </div>
            {museError && <p className="ag-muse-pop-note">{museError}</p>}
            {!muse?.available.ok && muse?.available.reason && <p className="ag-muse-pop-note">{muse.available.reason}</p>}
            {muse && <p className="ag-muse-pop-foot">最近生成 {muse.recent.length} 段 · 队列 {muse.jobs.length} 个任务</p>}
            <p className="ag-muse-pop-foot">在右栏「Muse 口型修正」卡片里给本集对白镜跑口型（limit 2）。</p>
          </div>,
          document.body,
        )}
    </>
  );
}

interface TypeBadge {
  meta: { short: string; tone: string; label: string };
  style: { short: string; label: string } | null;
}

function TemplateGroup({
  title,
  items,
  selectedId,
  readyOf,
  typeOf,
  onSelect,
}: {
  title: string;
  items: AgentTemplate[];
  selectedId: string;
  readyOf: (item: AgentTemplate) => boolean | null;
  typeOf: (item: AgentTemplate | null) => TypeBadge;
  onSelect: (id: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="ag-group">
      <div className="ag-group-head">
        <span>{title}</span>
        <em>{items.length}</em>
      </div>
      {items.map((item) => {
        const { meta, style } = typeOf(item);
        const ready = readyOf(item);
        const typeText = style ? meta.short + "·" + style.short : meta.short;
        const typeLabel = style ? meta.label + " · " + style.label : meta.label;
        return (
          <button
            key={item.id}
            type="button"
            className={"ag-tpl" + (item.id === selectedId ? " is-active" : "")}
            onClick={() => onSelect(item.id)}
            title={item.id + " · " + typeLabel + (item.stack ? " · " + (STACK_LABEL[item.stack] || item.stack) : "")}
          >
            <span className="ag-tpl-line">
              <b>{item.label}</b>
              <em className={"v18-tag is-" + meta.tone}>{typeText}</em>
              <i
                className={"ag-ready is-" + (ready === true ? "ready" : ready === false ? "pending" : "unknown")}
                title={ready === true ? "已有成片" : ready === false ? "尚未出片" : "成片状态未知"}
              />
            </span>
            <span className="ag-tpl-sub">
              <span className="cm-mono">{item.id}</span>
              <span>{item.stages.length ? item.stages.length + " 阶段" : "工作流待载入"}</span>
              {item.duration > 0 && (
                <span
                  className={"v54-dur " + (item.duration >= 300 ? "is-ok" : "is-miss")}
                  title={"目标时长 " + item.duration + "s · 5 分钟长版未达标"}
                >
                  {item.duration}s
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function RunLog({
  job,
  startError,
  pollError,
  evidence,
  summary,
}: {
  job: AgentRun | null;
  startError: string;
  pollError: string;
  evidence: { label: string; href: string; file: string }[];
  summary: { done: number; skipped: number; failed: number; total: number };
}) {
  const logRef = useRef<HTMLDivElement | null>(null);
  const logCount = job?.logs.length || 0;

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logCount]);

  return (
    <div className="ag-runlog">
      {startError && (
        <div className="v18-error">
          <IconAlert size={15} />
          <div className="v18-error-main">
            <b>启动失败</b>
            <p className="v18-error-text">{startError}</p>
          </div>
        </div>
      )}
      {pollError && (
        <div className="v18-error">
          <IconAlert size={15} />
          <div className="v18-error-main">
            <p className="v18-error-text">{pollError}</p>
          </div>
        </div>
      )}

      <div className="ag-runlog-head">
        <span className="ag-runlog-title">
          <IconFlow size={13} /> 日志
        </span>
        {job && (
          <>
            <span className={"ag-status" + (isAgentActive(job) ? " is-running" : job.status ? " is-" + job.status : "")}>
              <IconDot size={10} /> {agentStatusLabel(job.status)}
            </span>
            <span className="ag-runlog-count cm-mono">{logCount} 行</span>
            {summary.total > 0 && (
              <span className="ag-runlog-count cm-mono">
                阶段 {summary.done}✓ / {summary.skipped}⤼ / {summary.failed}✗
              </span>
            )}
          </>
        )}
      </div>

      <div className="ag-log" ref={logRef} role="log" aria-live="polite">
        {job && job.logs.length ? (
          job.logs.map((line, index) => (
            <div key={index} className={"ag-log-line is-" + stageTone(line.stage)}>
              <span className="ag-log-t cm-mono">{line.t || "--:--:--"}</span>
              <span className="ag-log-stage">{line.stage || "run"}</span>
              <span className="ag-log-msg">{line.msg}</span>
            </div>
          ))
        ) : isAgentActive(job) ? (
          <div className="ag-log-idle">
            <Spinner size={13} /> 等待智能体输出（每 1.5s 轮询 /runs/&lt;job&gt;）…
          </div>
        ) : (
          <div className="ag-log-idle">还没有运行日志 —— 设好参数后点「运行」。</div>
        )}
      </div>

      {job && !isAgentActive(job) && job.run_id && (
        <div className="ag-evidence">
          <div className="ag-evidence-head">
            <span>证据 · /media/agent_runs/{job.run_id}/</span>
          </div>
          <code className="ag-run-dir">{job.run_dir || "outputs/agent_runs/" + job.run_id}</code>
          <div className="ag-evidence-links">
            {evidence.map((item) => (
              <a key={item.file} className="cm-btn cm-btn-sm" href={item.href} target="_blank" rel="noreferrer">
                <IconExternal size={12} /> {item.label}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
