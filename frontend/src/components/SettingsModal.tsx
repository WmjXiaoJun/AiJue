import { useEffect, useState } from "react";
import { API_BASE, api, errorText } from "../lib/api";
import type { Health, Settings } from "../types";
import { useToast } from "./Toast";
import { Field, Modal, Notice, Spinner } from "./ui";
import { IconRefresh } from "./icons";

interface Props {
  open: boolean;
  onClose: () => void;
  health: Health | null;
}

interface FormState {
  generation_mode: string;
  resolution: string;
  fps: string;
  tts_provider: string;
  h3_price_per_second: string;
  comfyui_url: string;
  workflow_template: string;
}

const EMPTY_FORM: FormState = {
  generation_mode: "mock",
  resolution: "1280x720",
  fps: "25",
  tts_provider: "edge-tts",
  h3_price_per_second: "0.09",
  comfyui_url: "",
  workflow_template: "",
};

function toForm(settings: Settings): FormState {
  return {
    generation_mode: settings.generation_mode || "mock",
    resolution: settings.resolution || "1280x720",
    fps: String(settings.fps ?? 25),
    tts_provider: settings.tts_provider || "edge-tts",
    h3_price_per_second: String(settings.h3_price_per_second ?? 0.09),
    comfyui_url: settings.comfyui_url || "",
    workflow_template: settings.workflow_template || "",
  };
}

export function SettingsModal({ open, onClose, health }: Props) {
  const notify = useToast();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voiceMap, setVoiceMap] = useState<Record<string, string> | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await api.settings();
      setForm(toForm(settings));
      setVoiceMap(settings.voice_map || null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const update = (key: keyof FormState, value: string) => setForm((prev) => ({ ...prev, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const next = await api.patchSettings({
        generation_mode: form.generation_mode,
        resolution: form.resolution,
        fps: Number(form.fps) || 25,
        tts_provider: form.tts_provider,
        h3_price_per_second: Number(form.h3_price_per_second) || 0.09,
        comfyui_url: form.comfyui_url,
        workflow_template: form.workflow_template,
      });
      setForm(toForm(next));
      setVoiceMap(next.voice_map || null);
      notify("设置已保存", "success");
    } catch (err) {
      notify(errorText(err), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="设置"
      subtitle="生成、配音与计价参数；demo 默认 mock 模式离线可跑"
      size="md"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving || loading || !!error}>
            {saving ? <Spinner size={14} /> : null}
            {saving ? "保存中…" : "保存设置"}
          </button>
        </>
      }
    >
      <div className="settings-env">
        <span>后端 {health?.status === "ok" ? "已连接" : "未知"}</span>
        <span>版本 {health?.version || "—"}</span>
        <span>模式 {health?.mode || form.generation_mode}</span>
        <span>API {API_BASE}</span>
      </div>

      {error && (
        <Notice tone="warn">
          设置接口不可用：{error}（后端 PATCH/GET /settings 未就绪时功能自动降级，不影响制作流程）
          <button type="button" className="link-btn" onClick={() => void load()}>
            重试
          </button>
        </Notice>
      )}

      <div className="form-stack">
        <div className="form-grid">
          <Field label="生成模式" hint="mock = 离线 ffmpeg 示意片段">
            <select className="select" value={form.generation_mode} onChange={(event) => update("generation_mode", event.target.value)}>
              <option value="mock">mock · 离线演示</option>
              <option value="comfyui">comfyui · MiniMax H3</option>
            </select>
          </Field>
          <Field label="TTS 提供方">
            <select className="select" value={form.tts_provider} onChange={(event) => update("tts_provider", event.target.value)}>
              <option value="edge-tts">edge-tts</option>
              <option value="edge">edge</option>
            </select>
          </Field>
          <Field label="分辨率">
            <input className="input" value={form.resolution} onChange={(event) => update("resolution", event.target.value)} />
          </Field>
          <Field label="帧率">
            <input className="input" value={form.fps} onChange={(event) => update("fps", event.target.value)} inputMode="numeric" />
          </Field>
          <Field label="H3 单价（元/秒）" hint="768P 参考价 0.09">
            <input
              className="input"
              value={form.h3_price_per_second}
              onChange={(event) => update("h3_price_per_second", event.target.value)}
              inputMode="decimal"
            />
          </Field>
          <Field label="ComfyUI 地址" hint="comfyui 模式才需要">
            <input
              className="input"
              value={form.comfyui_url}
              onChange={(event) => update("comfyui_url", event.target.value)}
              placeholder="http://127.0.0.1:8188"
            />
          </Field>
        </div>
        <Field label="工作流模板（API 格式）">
          <textarea
            className="textarea mono"
            rows={4}
            value={form.workflow_template}
            onChange={(event) => update("workflow_template", event.target.value)}
            placeholder="ComfyUI workflow_template"
          />
        </Field>
        <Field label="声音映射" hint="来自后端 voice_map，只读展示">
          <pre className="code-block">{voiceMap ? JSON.stringify(voiceMap, null, 2) : "—"}</pre>
        </Field>
        <div className="form-note">
          <IconRefresh size={13} /> 修改生成模式后，新任务按新设置执行；已排队任务不回溯。
        </div>
      </div>
    </Modal>
  );
}
