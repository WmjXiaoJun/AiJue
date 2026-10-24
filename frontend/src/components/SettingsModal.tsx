import { useEffect, useState } from "react";
import { ApiError, API_BASE, api, errorText } from "../lib/api";
import type { Health, Settings } from "../types";
import { useToast } from "./Toast";
import { Field, Modal, Notice, Spinner } from "./ui";
import { IconCheck, IconRefresh } from "./icons";

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
  h3_provider: string;
  comfyui_url: string;
  workflow_template: string;
}

interface PublishForm {
  apiKey: string;
  keyDirty: boolean;
  username: string;
  enabled: boolean;
  platforms: string[];
  privacy: string;
  madeForKids: boolean;
  configured: boolean;
}

const EMPTY_PUBLISH: PublishForm = {
  apiKey: "",
  keyDirty: false,
  username: "",
  enabled: true,
  platforms: ["youtube", "tiktok", "instagram"],
  privacy: "unlisted",
  madeForKids: false,
  configured: false,
};

const UPLOAD_PLATFORM_OPTIONS = [
  { id: "youtube", label: "YouTube" },
  { id: "tiktok", label: "TikTok" },
  { id: "instagram", label: "Instagram" },
];

const EMPTY_FORM: FormState = {
  generation_mode: "mock",
  resolution: "1280x720",
  fps: "25",
  tts_provider: "edge-tts",
  h3_price_per_second: "0.09",
  h3_provider: "local",
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
    h3_provider: settings.h3_provider === "metaso" ? "metaso" : "local",
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
  const [metasoKeyConfigured, setMetasoKeyConfigured] = useState<boolean | null>(null);
  const [h3ProviderSupported, setH3ProviderSupported] = useState(true);
  const [publish, setPublish] = useState<PublishForm>(EMPTY_PUBLISH);
  const [publishSupported, setPublishSupported] = useState(true);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await api.settings();
      setForm(toForm(settings));
      setVoiceMap(settings.voice_map || null);
      setMetasoKeyConfigured(typeof settings.metaso_key_configured === "boolean" ? settings.metaso_key_configured : null);
      setH3ProviderSupported(typeof settings.h3_provider === "string");
      const hasPublishConfig =
        typeof settings.upload_post_username === "string" ||
        typeof settings.upload_post_api_key_configured === "boolean" ||
        Array.isArray(settings.upload_post_platforms);
      setPublishSupported(hasPublishConfig);
      setPublish({
        apiKey: "",
        keyDirty: false,
        username: settings.upload_post_username || "",
        enabled: settings.upload_post_enabled !== false,
        platforms:
          Array.isArray(settings.upload_post_platforms) && settings.upload_post_platforms.length
            ? settings.upload_post_platforms
            : ["youtube", "tiktok", "instagram"],
        privacy: settings.upload_post_youtube_privacy_status || "unlisted",
        madeForKids: !!settings.upload_post_youtube_made_for_kids,
        configured: !!settings.upload_post_api_key_configured,
      });
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
    const base = {
      generation_mode: form.generation_mode,
      resolution: form.resolution,
      fps: Number(form.fps) || 25,
      tts_provider: form.tts_provider,
      h3_price_per_second: Number(form.h3_price_per_second) || 0.09,
      comfyui_url: form.comfyui_url,
      workflow_template: form.workflow_template,
    };
    const publishPatch: Partial<Settings> = {
      upload_post_username: publish.username.trim(),
      upload_post_enabled: publish.enabled,
      upload_post_platforms: publish.platforms,
      upload_post_youtube_privacy_status: publish.privacy,
      upload_post_youtube_made_for_kids: publish.madeForKids,
    };
    // api_key 只在用户修改时提交；空串表示清除（GET 永不返回明文）
    if (publish.keyDirty) publishPatch.upload_post_api_key = publish.apiKey;
    try {
      let next: Settings;
      try {
        next = await api.patchSettings({ ...base, h3_provider: form.h3_provider, ...publishPatch });
      } catch (err) {
        // 后端暂未支持 h3_provider 时回退保存其它设置
        if (err instanceof ApiError && (err.status === 422 || err.status === 400)) {
          next = await api.patchSettings({ ...base, ...publishPatch });
          setH3ProviderSupported(false);
          notify("后端暂不支持 H3 生成方式切换，已保存其它设置", "info");
        } else {
          throw err;
        }
      }
      setForm(toForm(next));
      setVoiceMap(next.voice_map || null);
      if (typeof next.metaso_key_configured === "boolean") setMetasoKeyConfigured(next.metaso_key_configured);
      if (typeof next.upload_post_api_key_configured === "boolean") {
        setPublishSupported(true);
        setPublish((prev) => ({
          ...prev,
          apiKey: "",
          keyDirty: false,
          configured: !!next.upload_post_api_key_configured,
        }));
        notify(
          "设置已保存 · 海外发布" + (next.upload_post_api_key_configured ? "（Upload-Post 已配置）" : "（Upload-Post 未配置）"),
          "success"
        );
      } else if (typeof next.h3_provider === "string") {
        setH3ProviderSupported(true);
        notify("设置已保存 · H3 " + (next.h3_provider === "metaso" ? "云端测试（MetaSo）" : "本地 ComfyUI"), "success");
      } else {
        notify("设置已保存", "success");
      }
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
        <div className="field">
          <span className="field-label">H3 生成方式</span>
          <div className="h3-provider-options">
            <button
              type="button"
              className={"h3-provider-option" + (form.h3_provider === "local" ? " active" : "")}
              onClick={() => update("h3_provider", "local")}
            >
              <span className="h3-provider-emoji">🖥</span>
              <span className="h3-provider-main">
                <b>本地 ComfyUI</b>
                <em>生产用 · 慢 / 免费</em>
              </span>
              <span className="h3-provider-tag h3-provider-tag-local">生产</span>
            </button>
            <button
              type="button"
              className={"h3-provider-option" + (form.h3_provider === "metaso" ? " active" : "")}
              onClick={() => update("h3_provider", "metaso")}
            >
              <span className="h3-provider-emoji">☁️</span>
              <span className="h3-provider-main">
                <b>MetaSo 云端</b>
                <em>测试用 · 快 / 计费</em>
              </span>
              <span className="h3-provider-tag h3-provider-tag-cloud">云端测试</span>
            </button>
          </div>
          <div className="h3-provider-meta">
            <span>
              MetaSo Key：
              <b className={metasoKeyConfigured === null ? "pending" : metasoKeyConfigured ? "ok" : "warn"}>
                {metasoKeyConfigured === null ? "未知" : metasoKeyConfigured ? "已配置" : "未配置"}
              </b>
            </span>
            <i />
            <span>
              计价 ¥{(Number(form.h3_price_per_second) || 0.09).toFixed(2)}/秒 · 5s ≈ ¥
              {((Number(form.h3_price_per_second) || 0.09) * 5).toFixed(2)}
            </span>
          </div>
          {form.h3_provider === "metaso" && (
            <span className="h3-provider-note">
              云端测试产物写入 /media/h3/{"{角色ID}"}_h3_metaso.mp4，并附带「云端测试」徽标，不替代本地生产成片。
            </span>
          )}
          {!h3ProviderSupported && (
            <span className="field-hint">当前后端尚未支持 h3_provider 字段（等后端更新）；保存时会自动忽略该字段并提示。</span>
          )}
        </div>

        <div className="field publish-settings">
          <span className="field-label">
            海外发布（Upload-Post）
            <span className={"publish-settings-status " + (publish.configured ? "ok" : "warn")}>
              {publish.configured ? "已配置" : "未配置"}
            </span>
          </span>
          <p className="field-hint publish-settings-hint">
            在{" "}
            <a href="https://upload-post.com" target="_blank" rel="noreferrer">
              upload-post.com
            </a>{" "}
            注册并连接 YouTube / TikTok / Instagram 后，填入 API Key 与 username。Key 只写不读，GET /settings 不返回明文。
          </p>
          <div className="publish-settings-grid">
            <Field
              label="Upload-Post API Key"
              hint={publish.configured ? "已配置（不回显）；留空保持不变" : "从 upload-post 控制台复制"}
            >
              <div className="publish-key-row">
                <input
                  className="input"
                  type="password"
                  value={publish.apiKey}
                  placeholder={publish.configured ? "••••••••（已配置）" : "粘贴 API Key"}
                  autoComplete="new-password"
                  onChange={(event) =>
                    setPublish((prev) => ({ ...prev, apiKey: event.target.value, keyDirty: true }))
                  }
                />
                {publish.configured && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      if (!window.confirm("清除已保存的 Upload-Post API Key？")) return;
                      setPublish((prev) => ({ ...prev, apiKey: "", keyDirty: true, configured: false }));
                    }}
                  >
                    清除
                  </button>
                )}
              </div>
            </Field>
            <Field label="Username" hint="upload-post 的 profile 用户名">
              <input
                className="input"
                value={publish.username}
                placeholder="例如：zaopianju"
                onChange={(event) => setPublish((prev) => ({ ...prev, username: event.target.value }))}
              />
            </Field>
          </div>

          <div className="publish-settings-row">
            <label className="check">
              <input
                type="checkbox"
                checked={publish.enabled}
                onChange={(event) => setPublish((prev) => ({ ...prev, enabled: event.target.checked }))}
              />
              <span className="check-box">{publish.enabled && <IconCheck size={12} />}</span>
              <span>启用海外发布</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={publish.madeForKids}
                onChange={(event) => setPublish((prev) => ({ ...prev, madeForKids: event.target.checked }))}
              />
              <span className="check-box">{publish.madeForKids && <IconCheck size={12} />}</span>
              <span>YouTube：made for kids</span>
            </label>
          </div>

          <div className="publish-settings-platforms">
            <span className="publish-label">默认发布平台</span>
            {UPLOAD_PLATFORM_OPTIONS.map((platform) => {
              const active = publish.platforms.includes(platform.id);
              return (
                <button
                  key={platform.id}
                  type="button"
                  className={"publish-platform-chip" + (active ? " active" : "")}
                  onClick={() =>
                    setPublish((prev) => ({
                      ...prev,
                      platforms: active
                        ? prev.platforms.filter((item) => item !== platform.id)
                        : [...prev.platforms, platform.id],
                    }))
                  }
                >
                  {active && <IconCheck size={11} />} {platform.label}
                </button>
              );
            })}
          </div>

          <div className="form-grid">
            <Field label="YouTube 可见性" hint="默认 unlisted 先审后公开">
              <select
                className="select"
                value={publish.privacy}
                onChange={(event) => setPublish((prev) => ({ ...prev, privacy: event.target.value }))}
              >
                <option value="unlisted">unlisted · 不公开（推荐）</option>
                <option value="public">public · 公开</option>
                <option value="private">private · 仅自己可见</option>
              </select>
            </Field>
          </div>

          {!publishSupported && (
            <span className="field-hint">
              当前后端尚未支持 upload_post_* 字段（等 task-33）；保存会自动忽略这些字段并提示。
            </span>
          )}
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
