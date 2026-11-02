// V18 · 制片控制台（#/pipeline）：从模板到成片的六步向导 + 一键全流程。
// 数据：GET /api/v5/catalog、GET /api/cinema/templates（19 个 3D 模板）、
//       GET /api/v5/casting?template=（404 时 catalog 兜底）、GET /api/v5/voices、
//       POST /api/pipeline/run + GET /api/pipeline/run/{id}（1.5s 轮询）、
//       发布前检查读 GET /api/settings。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Settings } from "../../types";
import type { CinemaTemplate } from "../../lib/cinema";
import { cinemaApi } from "../../lib/cinema";
import type { V5CatalogResponse, V5TemplateSummary } from "../../lib/v5";
import { v5api, isV5NotFound } from "../../lib/v5";
import { pipelineApi, isPipelineNotReady, PIPELINE_STAGE_LABEL, type PipelineRun } from "../../lib/pipeline";
import type { CastingMember, VoicePreset } from "../../lib/voice";
import { fallbackCasting, voiceApi } from "../../lib/voice";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconFilm, IconGlobe, IconGrid, IconLock, IconRefresh, IconSettings, IconSpark, IconTemplate, IconUsers, IconVideo } from "../icons";
import { AppShell } from "../shell/AppShell";
import { PipelineStepper, type PipelineStepMeta } from "./PipelineStepper";
import {
  ErrorBar,
  StepCast,
  StepFinal,
  StepPreflight,
  StepRun,
  StepScript,
  StepTemplates,
  type ScriptLine,
} from "./steps";
import { VoicePicker } from "../voice/VoicePicker";
import "../../styles/cinema.css";
import "../../styles/v18.css";
import "../../styles/ui-consistency.css";

interface TemplateActLine {
  no: number;
  scene: string;
  speaker: string;
  text: string;
  duration: number;
  camera?: string;
}

interface Props {
  onExit: () => void;
  onOpenCharacters: () => void;
  onOpenCinema: () => void;
  onOpenLibrary: () => void;
  onOpenSettings: () => void;
}

const STEP_LABELS = ["选模板", "角色与声音", "剧本预览", "生成 3D", "成片", "发布前检查"];

export function PipelineConsole({ onExit, onOpenCharacters, onOpenCinema, onOpenLibrary, onOpenSettings }: Props) {
  const notify = useToast();

  const [catalog, setCatalog] = useState<V5CatalogResponse | null>(null);
  const [templates, setTemplates] = useState<CinemaTemplate[]>([]);
  const [presets, setPresets] = useState<VoicePreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [step, setStep] = useState(1);

  const [casting, setCasting] = useState<CastingMember[]>([]);
  const [castingLoading, setCastingLoading] = useState(false);
  const [castingError, setCastingError] = useState("");
  const [castingSource, setCastingSource] = useState("api");

  const [lines, setLines] = useState<ScriptLine[]>([]);
  const [shotsInfo, setShotsInfo] = useState("");
  const [scriptLoading, setScriptLoading] = useState(false);
  const [scriptError, setScriptError] = useState("");

  const [run, setRun] = useState<PipelineRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [pollError, setPollError] = useState("");
  const [logOpen, setLogOpen] = useState(false);

  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState("");

  const lastReuseRef = useRef(true);
  const aliveRef = useRef(true);
  const inFlightRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.id === selectedId) || null,
    [selectedId, templates],
  );
  const finalReady = Boolean(selectedTemplate?.final.exists && selectedTemplate.final.url);

  /* ------------------------------ 数据加载 ------------------------------ */
  const loadAll = useCallback(async (silent: boolean) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    const [catRes, tplRes, voiceRes] = await Promise.allSettled([
      v5api.catalog(),
      cinemaApi.templates(),
      voiceApi.voices(true),
    ]);
    if (!aliveRef.current) {
      inFlightRef.current = false;
      return;
    }
    const errors: string[] = [];
    if (catRes.status === "fulfilled") setCatalog(catRes.value);
    else errors.push("V5 目录：" + errorText(catRes.reason));
    if (tplRes.status === "fulfilled") {
      setTemplates(tplRes.value.templates);
      setLoadError("");
    } else {
      errors.push("3D 模板：" + errorText(tplRes.reason));
      setTemplates([]);
    }
    if (voiceRes.status === "fulfilled") setPresets(voiceRes.value);
    else errors.push("音色库：" + errorText(voiceRes.reason));
    if (errors.length) setLoadError(errors.join("；"));
    inFlightRef.current = false;
    if (aliveRef.current) {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadAll(false);
  }, [loadAll]);

  const refreshTemplates = useCallback(async () => {
    try {
      const res = await cinemaApi.templates();
      if (aliveRef.current) setTemplates(res.templates);
    } catch (err) {
      if (aliveRef.current) notify("刷新模板状态失败：" + errorText(err), "error");
    }
  }, [notify]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await loadAll(true);
    if (aliveRef.current) {
      setRefreshing(false);
      notify("制片控制台数据已刷新", "success");
    }
  }, [loadAll, notify]);

  /* ------------------------------ 选角（casting） ------------------------------ */
  const loadCasting = useCallback(
    async (templateId: string) => {
      setCastingLoading(true);
      setCastingError("");
      try {
        const res = await voiceApi.casting(templateId);
        if (!aliveRef.current) return;
        setCasting(res.cast);
        setCastingSource(res.source || "api");
        setCastingError("");
      } catch (err) {
        const fallback = await fallbackCasting(templateId, catalog, presets);
        if (!aliveRef.current) return;
        setCasting(fallback.cast);
        setCastingSource("fallback");
        setCastingError(
          isV5NotFound(err)
            ? "casting 接口尚未就绪（V18 后端实现中），已用 catalog + 角色合同兜底；音色信息以后端为准。"
            : "casting 读取失败：" + errorText(err),
        );
      } finally {
        if (aliveRef.current) setCastingLoading(false);
      }
    },
    [catalog, presets],
  );

  /* ------------------------------ 剧本（semantic） ------------------------------ */
  const loadScript = useCallback(async (templateId: string) => {
    setScriptLoading(true);
    setScriptError("");
    try {
      const res = (await v5api.item("templates", templateId)) as { item?: Record<string, unknown> };
      const item = (res.item || {}) as Record<string, unknown>;
      const acts = Array.isArray(item.acts) ? (item.acts as Record<string, unknown>[]) : [];
      const scriptLines: TemplateActLine[] = acts.map((act, index) => {
        const characters = Array.isArray(act.characters) ? (act.characters as string[]) : [];
        return {
          no: index + 1,
          scene: String(act.scene || ""),
          speaker: characters.length ? characters.join(" / ") : String(act.speaker || ""),
          text: String(act.line || act.text || ""),
          duration: Number(act.duration) || 0,
          camera: String(act.camera || ""),
        };
      });
      if (aliveRef.current) {
        setLines(scriptLines.filter((line) => line.text || line.scene));
        setShotsInfo(
          `${scriptLines.length} 幕 · 目标 ${Number(item.target_duration) || "—"}s` +
            (String(item.style || "") ? ` · ${String(item.style)}` : ""),
        );
        setScriptError("");
      }
    } catch (err) {
      if (!aliveRef.current) return;
      if (isV5NotFound(err)) {
        try {
          const detail = await cinemaApi.templateDetail(templateId);
          if (!aliveRef.current) return;
          const shotLines = (detail.shots || []).map((shot, index) => ({
            no: index + 1,
            scene: shot.scene || shot.kind || "",
            speaker: "",
            text: shot.valid ? "（已生成镜头）" : "（尚未生成）",
            duration: shot.duration || 0,
          }));
          setLines(shotLines);
          setShotsInfo(`${shotLines.length} 镜 · 来自 cinema/templates/{id}`);
          setScriptError("");
        } catch (fallbackErr) {
          if (aliveRef.current) setScriptError("剧本读取失败：" + errorText(fallbackErr));
        }
      } else {
        setScriptError("剧本读取失败：" + errorText(err));
      }
    } finally {
      if (aliveRef.current) setScriptLoading(false);
    }
  }, []);

  /* ------------------------------ 导航 / 懒加载 ------------------------------ */
  const gotoStep = useCallback(
    (next: number) => {
      setStep(next);
      if (!selectedId) return;
      if (next === 2 && !casting.length && !castingLoading) void loadCasting(selectedId);
      if (next === 3 && !lines.length && !scriptLoading) void loadScript(selectedId);
      if (next === 6 && !settings && !settingsLoading) {
        setSettingsLoading(true);
        api
          .settings()
          .then((value) => {
            if (!aliveRef.current) return;
            setSettings(value);
            setSettingsError("");
          })
          .catch((err) => {
            if (!aliveRef.current) return;
            setSettingsError(
              isPipelineNotReady(err) || (err instanceof Error && err.message.includes("404"))
                ? "设置接口未就绪；发布账号状态无法读取。"
                : errorText(err),
            );
          })
          .finally(() => {
            if (aliveRef.current) setSettingsLoading(false);
          });
      }
    },
    [casting.length, castingLoading, lines.length, loadCasting, loadScript, scriptLoading, selectedId, settings, settingsLoading],
  );

  const selectTemplate = useCallback(
    (templateId: string) => {
      setSelectedId(templateId);
      setCasting([]);
      setCastingError("");
      setLines([]);
      setScriptError("");
      setShotsInfo("");
      setRun(null);
      setStartError("");
      setPollError("");
      setLogOpen(false);
      setStep(2);
      void loadCasting(templateId);
      void loadScript(templateId);
    },
    [loadCasting, loadScript],
  );

  /* ------------------------------ 任务轮询 ------------------------------ */
  useEffect(() => {
    if (!run || (run.status !== "queued" && run.status !== "running")) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await pipelineApi.runStatus(run.id);
        if (!alive || !aliveRef.current) return;
        setPollError("");
        setRun(next);
        if (next.status === "done") {
          notify("pipeline 生成完成，成片已就绪", "success");
          void refreshTemplates();
          setStep(5);
        } else if (next.status === "failed") {
          notify("pipeline 任务失败：" + (next.error || "未知原因"), "error");
        } else if (next.status === "canceled") {
          notify("pipeline 任务已取消", "info");
        }
      } catch (err) {
        if (!alive || !aliveRef.current) return;
        setPollError(
          isPipelineNotReady(err)
            ? "pipeline 状态接口不可用（后端 V18 API 实现中）；请稍后刷新后重试。"
            : "任务状态读取失败：" + errorText(err),
        );
      }
    }, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [notify, refreshTemplates, run]);

  /* ------------------------------ 启动 / 取消 ------------------------------ */
  const startRun = useCallback(
    async (reuseShots: boolean) => {
      if (!selectedId) {
        notify("先在第 ① 步选择一个模板", "info");
        setStep(1);
        return;
      }
      if (run && (run.status === "queued" || run.status === "running")) {
        notify("已有运行中的 pipeline 任务，等它结束或先取消", "info");
        return;
      }
      lastReuseRef.current = reuseShots;
      setStarting(true);
      setStartError("");
      setPollError("");
      setStep(4);
      try {
        const res = await pipelineApi.run(selectedId, reuseShots);
        if (!aliveRef.current) return;
        setRun({
          id: res.job_id,
          template_id: res.template_id || selectedId,
          status: "queued",
          stage: "prepare",
          stage_index: 0,
          progress: { done: 0, total: 0 },
          log_tail: [],
          elapsed_sec: 0,
          final_url: "",
          error: "",
        });
        setLogOpen(true);
        notify(reuseShots ? "已启动复用已有镜头快速合成任务" : "已启动 3D 完整生成任务", "success");
      } catch (err) {
        if (!aliveRef.current) return;
        if (err instanceof Error && (err as { status?: number }).status === 409) {
          setStartError("已有运行中的 pipeline 任务（409）：单并发限制，等当前任务结束或先取消，再启动新的生成。");
        } else if (isPipelineNotReady(err)) {
          setStartError(
            "pipeline 接口尚未就绪（V18 后端 API 实现中，404）。可先用 Cinema 模板库 / 工作台 3D 模板页手动生成与合成；接口就绪后此处即可一键运行。",
          );
        } else {
          setStartError("启动生成失败：" + errorText(err));
        }
      } finally {
        if (aliveRef.current) setStarting(false);
      }
    },
    [notify, run, selectedId],
  );

  const cancelRun = useCallback(async () => {
    if (!run) return;
    if (!window.confirm(`确定取消 pipeline 任务 ${run.id}？\n\n已生成的镜头可能被保留，但本次合成会中止。`)) return;
    setCancelling(true);
    try {
      await pipelineApi.cancel(run.id);
      notify("已请求取消 pipeline 任务", "info");
      const next = await pipelineApi.runStatus(run.id).catch(() => null);
      if (aliveRef.current && next) setRun(next);
    } catch (err) {
      if (aliveRef.current) setStartError("取消任务失败：" + errorText(err));
    } finally {
      if (aliveRef.current) setCancelling(false);
    }
  }, [notify, run]);

  const oneClick = useCallback(() => {
    if (!selectedId) {
      notify("一键全流程：请先在第 ① 步选一个模板", "info");
      setStep(1);
      return;
    }
    notify("一键全流程：复用已有镜头 → 合成 3D 成片 → 发布前检查", "info");
    if (!casting.length) void loadCasting(selectedId);
    if (!lines.length) void loadScript(selectedId);
    void startRun(true);
  }, [casting.length, lines.length, loadCasting, loadScript, notify, selectedId, startRun]);

  const copyError = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        notify("错误信息已复制到剪贴板", "success");
      } catch {
        notify("复制失败，请手动选择错误文本", "error");
      }
    },
    [notify],
  );

  /* ------------------------------ Stepper ------------------------------ */
  const runStatusText = run
    ? run.status === "queued"
      ? "排队中"
      : run.status === "running"
        ? `${PIPELINE_STAGE_LABEL[run.stage] || run.stage || "运行中"} ${run.progress.total ? `${run.progress.done}/${run.progress.total}` : ""}`
        : run.status === "done"
          ? "已完成"
          : run.status === "canceled"
            ? "已取消"
            : "失败"
    : "未启动";
  const steps: PipelineStepMeta[] = [
    { no: 1, label: STEP_LABELS[0], en: "TEMPLATE", sub: selectedId || "未选择", done: Boolean(selectedId), locked: false },
    {
      no: 2,
      label: STEP_LABELS[1],
      en: "CAST",
      sub: casting.length ? `${casting.length} 角色` : selectedId ? "待读取" : "—",
      done: casting.length > 0,
      locked: !selectedId,
    },
    {
      no: 3,
      label: STEP_LABELS[2],
      en: "SCRIPT",
      sub: lines.length ? `${lines.length} 条` : selectedId ? "待读取" : "—",
      done: lines.length > 0,
      locked: !selectedId,
    },
    {
      no: 4,
      label: STEP_LABELS[3],
      en: "GENERATE",
      sub: runStatusText,
      done: run?.status === "done" || finalReady,
      locked: !selectedId,
    },
    { no: 5, label: STEP_LABELS[4], en: "FINAL", sub: finalReady ? "已就绪" : "待生成", done: finalReady, locked: !selectedId },
    { no: 6, label: STEP_LABELS[5], en: "PREFLIGHT", sub: settings ? "已读取" : "账号/母版", done: Boolean(settings), locked: !selectedId },
  ];

  const activeRun = Boolean(run && (run.status === "queued" || run.status === "running"));
  const doneCount = templates.filter((item) => item.final.exists).length;

  return (
    <AppShell
      active="pipeline"
      kicker="ONE-CLICK PRODUCTION · 一键成片"
      title="制片控制台"
      breadcrumb={selectedTemplate ? [{ label: "造片局" }, { label: `${selectedTemplate.id} · ${selectedTemplate.label}` }] : [{ label: "造片局" }]}
      chips={[
        { label: "3D 成片", value: `${doneCount}/${templates.length || "—"}`, tone: "gold" },
        { label: "pipeline", value: runStatusText, tone: run?.status === "failed" ? "red" : activeRun ? "gold" : "jade" },
        { label: "音色", value: String(presets.length || "—"), tone: "slate" },
      ]}
      primaryAction={{
        label: "一键全流程",
        onClick: oneClick,
        disabled: starting || activeRun || !selectedId,
        title: !selectedId ? "先在第 ① 步选模板" : "复用已有镜头 → 快速合成 3D 成片 → 发布前检查",
        icon: <IconSpark size={14} />,
      }}
      onRefresh={() => void refresh()}
      refreshing={refreshing}
      onOpenSettings={onOpenSettings}
    >
      <div className="v18-page">
            {loadError && (
              <ErrorBar
                title="部分数据读取失败"
                error={loadError}
                onRetry={() => void refresh()}
                onCopy={() => void copyError(loadError)}
              />
            )}
            <PipelineStepper steps={steps} current={step} onSelect={gotoStep} />

            {step === 1 && (
              <StepTemplates
                templates={templates}
                selectedId={selectedId}
                onSelect={selectTemplate}
                loading={loading}
                error={loadError}
                onRefresh={() => void refresh()}
                onNext={() => gotoStep(2)}
                onOpenCinema={onOpenLibrary}
                totalHint={`${templates.length} 个 3D 模板 · 成片 ${doneCount}/${templates.length || "—"}`}
              />
            )}
            {step === 2 && (
              <StepCast
                templateId={selectedId}
                casting={casting}
                loading={castingLoading}
                error={castingError}
                source={castingSource}
                onRefresh={() => selectedId && void loadCasting(selectedId)}
                presets={presets}
                onVoiceSaved={(character, voiceId, label) => {
                  setCasting((prev) =>
                    prev.map((member) =>
                      member.character === character
                        ? { ...member, voice: { ...member.voice, voice_id: voiceId, label } }
                        : member,
                    ),
                  );
                }}
                onOpenCharacters={onOpenCharacters}
                onNext={() => gotoStep(3)}
                onPrev={() => gotoStep(1)}
              />
            )}
            {step === 3 && (
              <StepScript
                templateLabel={selectedTemplate ? `${selectedTemplate.label}（${selectedTemplate.id}）` : ""}
                lines={lines}
                shotsInfo={shotsInfo}
                loading={scriptLoading}
                error={scriptError}
                onRefresh={() => selectedId && void loadScript(selectedId)}
                onNext={() => gotoStep(4)}
                onPrev={() => gotoStep(2)}
                onOpenCinema={onOpenCinema}
              />
            )}
            {step === 4 && (
              <StepRun
                template={selectedTemplate}
                run={run}
                starting={starting}
                startError={startError}
                cancelling={cancelling}
                pollError={pollError}
                logOpen={logOpen}
                onToggleLog={() => setLogOpen((prev) => !prev)}
                onStart={(reuse) => void startRun(reuse)}
                onCancel={() => void cancelRun()}
                onRetry={() => void startRun(lastReuseRef.current)}
                onNext={() => gotoStep(5)}
                onPrev={() => gotoStep(3)}
                finalReady={finalReady || run?.status === "done"}
                onOpenShots={onOpenCinema}
                onOpenJobs={() => (window.location.hash = "#/cinema/jobs")}
              />
            )}
            {step === 5 && (
              <StepFinal
                template={selectedTemplate}
                onPrev={() => gotoStep(4)}
                onNext={() => gotoStep(6)}
                onRegenerate={() => {
                  setStep(4);
                  void startRun(true);
                }}
                onOpenCinema={onOpenLibrary}
                busy={starting || activeRun}
              />
            )}
            {step === 6 && (
              <StepPreflight
                template={selectedTemplate}
                settings={settings}
                settingsLoading={settingsLoading}
                settingsError={settingsError}
                onOpenSettings={onOpenSettings}
                onPrev={() => gotoStep(5)}
                onDone={onExit}
              />
            )}
          </div>
    </AppShell>
  );
}
