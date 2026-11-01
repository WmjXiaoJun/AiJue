import { useEffect, useState } from "react";
import { ApiError, API_BASE, api, errorText } from "../lib/api";
import type { Health, LLMConfig, LLMProviderInfo, LLMTestResult, Settings } from "../types";
import { useToast } from "./Toast";
import { Spinner } from "./ui";
import { IconAlert, IconCheck, IconClose, IconGlobe, IconRefresh, IconSettings, IconSpark } from "./icons";
import "../styles/settings.css";

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

interface LLMForm {
  provider: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  keyDirty: boolean;
  keyConfigured: boolean;
  masked: string;
}

const EMPTY_LLM_FORM: LLMForm = {
  provider: "ollama",
  baseUrl: "http://127.0.0.1:11434",
  model: "qwen2.5:7b",
  apiKey: "",
  keyDirty: false,
  keyConfigured: false,
  masked: "",
};

const LLM_FALLBACK_PROVIDERS: LLMProviderInfo[] = [
  {
    id: "ollama",
    label: "本地 Ollama",
    requires_key: false,
    default_base_url: "http://127.0.0.1:11434",
    default_model: "qwen2.5:7b",
    hint: "本机 Ollama 服务，免费离线",
  },
  {
    id: "openai_compatible",
    label: "OpenAI 兼容",
    requires_key: true,
    default_base_url: "https://api.deepseek.com/v1",
    default_model: "deepseek-chat",
    hint: "DeepSeek / OpenAI 等 /v1 兼容端点，需 API Key",
  },
  {
    id: "mock",
    label: "关闭 / mock",
    requires_key: false,
    default_model: "mock",
    hint: "不调用模型，返回固定文本（仅测试/演示）",
  },
];

function strValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toLLMForm(config: LLMConfig | null, settings: Settings | null): LLMForm {
  const provider =
    strValue(config?.provider) || strValue(config?.llm_provider) || strValue(settings?.llm_provider) || "ollama";
  const baseUrl =
    strValue(config?.base_url) ||
    strValue(config?.llm_base_url) ||
    strValue(settings?.llm_base_url) ||
    (provider === "ollama"
      ? "http://127.0.0.1:11434"
      : provider === "mock"
        ? ""
        : "https://api.openai.com/v1");
  const model =
    strValue(config?.model) ||
    strValue(config?.llm_model) ||
    strValue(settings?.llm_model) ||
    (provider === "ollama" ? "qwen2.5:7b" : "");
  const configured =
    typeof config?.has_api_key === "boolean"
      ? config.has_api_key
      : typeof config?.api_key_configured === "boolean"
        ? config.api_key_configured
        : typeof settings?.llm_api_key_configured === "boolean"
          ? settings.llm_api_key_configured
          : false;
  return {
    provider,
    baseUrl,
    model,
    apiKey: "",
    keyDirty: false,
    keyConfigured: configured,
    masked: strValue(config?.api_key_masked),
  };
}

const EMPTY_FORM: FormState = {
  generation_mode: "mock",
  resolution: "1280x720",
  fps: "25",
  tts_provider: "edge-tts",
  h3_price_per_second: "0.09",
  h3_provider: "local",
  comfyui_url: "",
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
  };
}

export function SettingsModal({ open, onClose, health }: Props) {
  const notify = useToast();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metasoKeyConfigured, setMetasoKeyConfigured] = useState<boolean | null>(null);
  const [h3ProviderSupported, setH3ProviderSupported] = useState(true);
  const [publish, setPublish] = useState<PublishForm>(EMPTY_PUBLISH);
  const [publishSupported, setPublishSupported] = useState(true);
  const [llm, setLlm] = useState<LLMForm>(EMPTY_LLM_FORM);
  const [llmProviders, setLlmProviders] = useState<LLMProviderInfo[]>(LLM_FALLBACK_PROVIDERS);
  const [llmSupported, setLlmSupported] = useState(true);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmTestResult, setLlmTestResult] = useState<LLMTestResult | null>(null);
  const [llmTestError, setLlmTestError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const settings = await api.settings();
      setForm(toForm(settings));
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

      // AI 模型：优先 GET /api/llm/config，未就绪时回退 settings.llm_*
      let llmConfig: LLMConfig | null = null;
      try {
        llmConfig = await api.llmConfig();
        setLlmSupported(true);
        if (Array.isArray(llmConfig.providers) && llmConfig.providers.length) {
          setLlmProviders(llmConfig.providers);
        }
      } catch {
        setLlmSupported(false);
      }
      if (!llmConfig || !Array.isArray(llmConfig.providers) || !llmConfig.providers.length) {
        try {
          const providers = await api.llmProviders();
          const list = Array.isArray(providers) ? providers : providers.providers || [];
          if (list.length) {
            setLlmProviders(list);
            setLlmSupported(true);
          }
        } catch {
          /* /llm/providers 未就绪：使用内置 provider 列表 */
        }
      }
      setLlm(toLLMForm(llmConfig, settings));
      setLlmTestResult(null);
      setLlmTestError(null);
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

    const llmPatch: Partial<Settings> = {
      llm_provider: llm.provider,
      llm_base_url: llm.baseUrl.trim(),
      llm_model: llm.model.trim(),
    };
    if (llm.keyDirty) llmPatch.llm_api_key = llm.apiKey;

    try {
      let next: Settings;
      try {
        next = await api.patchSettings({ ...base, h3_provider: form.h3_provider, ...publishPatch, ...llmPatch });
      } catch (err) {
        // 后端暂未支持 h3_provider 时回退保存其它设置
        if (err instanceof ApiError && (err.status === 422 || err.status === 400)) {
          next = await api.patchSettings({ ...base, ...publishPatch, ...llmPatch });
          setH3ProviderSupported(false);
          notify("后端暂不支持部分新字段，已保存其它设置", "info");
        } else {
          throw err;
        }
      }
      setForm(toForm(next));
      if (typeof next.metaso_key_configured === "boolean") setMetasoKeyConfigured(next.metaso_key_configured);
      if (typeof next.llm_provider === "string" || typeof next.llm_api_key_configured === "boolean") {
        setLlmSupported(true);
        setLlm((prev) => ({
          ...prev,
          apiKey: "",
          keyDirty: false,
          keyConfigured:
            typeof next.llm_api_key_configured === "boolean" ? next.llm_api_key_configured : prev.keyConfigured,
          provider: typeof next.llm_provider === "string" ? next.llm_provider : prev.provider,
          baseUrl: strValue(next.llm_base_url) || prev.baseUrl,
          model: strValue(next.llm_model) || prev.model,
        }));
        notify("设置已保存 · AI 模型 " + (llm.provider === "ollama" ? "本地 Ollama" : llm.provider), "success");
      } else if (typeof next.upload_post_api_key_configured === "boolean") {
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

  const testLLM = async () => {
    setLlmTesting(true);
    setLlmTestResult(null);
    setLlmTestError(null);
    try {
      const body: { provider?: string; base_url?: string; model?: string; api_key?: string } = {
        provider: llm.provider,
        base_url: llm.baseUrl.trim(),
        model: llm.model.trim(),
      };
      if (llm.keyDirty && llm.apiKey) body.api_key = llm.apiKey;
      const result = await api.llmTest(body);
      setLlmTestResult(result);
      setLlm((prev) => ({ ...prev, keyDirty: body.api_key ? false : prev.keyDirty }));
      if (result.ok) {
        notify("AI 模型连接成功" + (result.model ? "（" + result.model + "）" : ""), "success");
      } else {
        notify("AI 模型连接失败：" + (result.error || "未知原因"), "error");
      }
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setLlmTestError("AI 模型测试接口未就绪（等待后端 task-45：POST /api/llm/test）");
      } else {
        setLlmTestError("测试连接失败：" + errorText(err));
      }
    } finally {
      setLlmTesting(false);
    }
  };

  // ESC 关闭（与其它新设计弹层一致；旧 Modal 组件已不再使用）
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="cm-set-overlay" onMouseDown={onClose} role="presentation">
      <section
        className="cm-set"
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="cm-set-head">
          <div className="cm-set-head-main">
            <span className="cm-set-kicker">SETTINGS · 系统设置</span>
            <h2 className="cm-set-title">设置</h2>
            <p className="cm-set-sub">生成 / 配音 / 计价 · 海外发布 · AI 模型</p>
          </div>
          <div className="cm-set-head-side">
            <span className={"cm-set-env-chip " + (health?.status === "ok" ? "is-on" : "is-warn")}>
              <i className="cm-set-dot" />
              {health?.status === "ok" ? "后端已连接" : "后端状态未知"}
            </span>
            <button type="button" className="cm-set-close" onClick={onClose} aria-label="关闭">
              <IconClose size={16} />
            </button>
          </div>
        </header>

        <div className="cm-set-body">
          {error && (
            <div className="cm-set-notice is-warn">
              <IconAlert size={15} />
              <span>
                设置接口不可用：{error}（后端 PATCH/GET /settings 未就绪时功能自动降级，不影响制作流程）
              </span>
              <button type="button" className="cm-set-link" onClick={() => void load()}>
                重试
              </button>
            </div>
          )}

          <div className="cm-set-env">
            <span>
              <em>后端</em>
              {health?.status === "ok" ? "已连接" : "未知"}
            </span>
            <span>
              <em>版本</em>
              {health?.version || "—"}
            </span>
            <span>
              <em>模式</em>
              {health?.mode || form.generation_mode}
            </span>
            <span className="cm-set-env-api">
              <em>API</em>
              {API_BASE}
            </span>
          </div>

          {/* 01 生成与计价 */}
          <section className="cm-set-section">
            <header className="cm-set-section-head">
              <span className="cm-set-no">01</span>
              <div className="cm-set-section-main">
                <h3 className="cm-set-section-title">
                  <IconSpark size={14} /> 生成与计价
                </h3>
                <p className="cm-set-section-sub">GENERATION · 生成模式 / 配音 / 分辨率 / H3 计费</p>
              </div>
            </header>

            <div className="cm-set-grid">
              <label className="cm-set-field">
                <span className="cm-set-label">生成模式</span>
                <select
                  className="cm-set-select"
                  value={form.generation_mode}
                  onChange={(event) => update("generation_mode", event.target.value)}
                >
                  <option value="mock">mock · 离线演示</option>
                  <option value="comfyui">comfyui · MiniMax H3</option>
                </select>
                <span className="cm-set-hint">mock = 离线 ffmpeg 示意片段</span>
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">TTS 提供方</span>
                <select
                  className="cm-set-select"
                  value={form.tts_provider}
                  onChange={(event) => update("tts_provider", event.target.value)}
                >
                  <option value="edge-tts">edge-tts</option>
                  <option value="edge">edge</option>
                </select>
                <span className="cm-set-hint">固定音色（edge-tts 为主）</span>
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">分辨率</span>
                <input className="cm-set-input" value={form.resolution} onChange={(event) => update("resolution", event.target.value)} />
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">帧率</span>
                <input
                  className="cm-set-input"
                  value={form.fps}
                  onChange={(event) => update("fps", event.target.value)}
                  inputMode="numeric"
                />
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">H3 单价（元/秒）</span>
                <input
                  className="cm-set-input"
                  value={form.h3_price_per_second}
                  onChange={(event) => update("h3_price_per_second", event.target.value)}
                  inputMode="decimal"
                />
                <span className="cm-set-hint">768P 参考价 0.09</span>
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">ComfyUI 地址</span>
                <input
                  className="cm-set-input"
                  value={form.comfyui_url}
                  onChange={(event) => update("comfyui_url", event.target.value)}
                  placeholder="http://127.0.0.1:8188"
                />
                <span className="cm-set-hint">comfyui 模式才需要</span>
              </label>
            </div>

            <div className="cm-set-field cm-set-field-wide">
              <span className="cm-set-label">H3 生成方式</span>
              <div className="cm-set-choices">
                <button
                  type="button"
                  className={"cm-set-choice" + (form.h3_provider === "local" ? " is-active" : "")}
                  onClick={() => update("h3_provider", "local")}
                >
                  <span className="cm-set-choice-main">
                    <b>本地 ComfyUI</b>
                    <em>生产用 · 慢 / 免费</em>
                  </span>
                  <span className="cm-set-badge">生产</span>
                </button>
                <button
                  type="button"
                  className={"cm-set-choice" + (form.h3_provider === "metaso" ? " is-active" : "")}
                  onClick={() => update("h3_provider", "metaso")}
                >
                  <span className="cm-set-choice-main">
                    <b>MetaSo 云端</b>
                    <em>测试用 · 快 / 计费</em>
                  </span>
                  <span className="cm-set-badge is-soft">云端测试</span>
                </button>
              </div>
              <div className="cm-set-meta-row">
                <span>
                  MetaSo Key：
                  <b className={"cm-set-state " + (metasoKeyConfigured === null ? "" : metasoKeyConfigured ? "is-jade" : "is-red")}>
                    {metasoKeyConfigured === null ? "未知" : metasoKeyConfigured ? "已配置" : "未配置"}
                  </b>
                </span>
                <span className="cm-set-meta-divider" />
                <span>
                  计价 ¥{(Number(form.h3_price_per_second) || 0.09).toFixed(2)}/秒 · 5s ≈ ¥
                  {((Number(form.h3_price_per_second) || 0.09) * 5).toFixed(2)}
                </span>
              </div>
              {form.h3_provider === "metaso" && (
                <span className="cm-set-hint">
                  云端测试产物写入 /media/h3/{"{角色ID}"}_h3_metaso.mp4，并附带「云端测试」徽标，不替代本地生产成片。
                </span>
              )}
              {!h3ProviderSupported && (
                <span className="cm-set-hint is-warn">当前后端尚未支持 h3_provider 字段；保存时会自动忽略该字段并提示。</span>
              )}
            </div>
          </section>

          {/* 02 海外发布 */}
          <section className="cm-set-section">
            <header className="cm-set-section-head">
              <span className="cm-set-no">02</span>
              <div className="cm-set-section-main">
                <h3 className="cm-set-section-title">
                  <IconGlobe size={14} /> 海外发布（Upload-Post）
                </h3>
                <p className="cm-set-section-sub">PUBLISHING · YouTube / TikTok / Instagram 一键分发</p>
              </div>
              <span className={"cm-set-badge " + (publish.configured ? "is-jade" : "is-soft")}>
                {publish.configured ? "已配置" : "未配置"}
              </span>
            </header>

            <p className="cm-set-hint cm-set-hint-block">
              在{" "}
              <a href="https://upload-post.com" target="_blank" rel="noreferrer">
                upload-post.com
              </a>{" "}
              注册并连接 YouTube / TikTok / Instagram 后，填入 API Key 与 username。Key 只写不读，GET /settings 不返回明文。
            </p>

            <div className="cm-set-grid">
              <label className="cm-set-field">
                <span className="cm-set-label">Upload-Post API Key</span>
                <div className="cm-set-inline">
                  <input
                    className="cm-set-input"
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
                      className="cm-btn cm-btn-ghost cm-btn-sm"
                      onClick={() => {
                        if (!window.confirm("清除已保存的 Upload-Post API Key？")) return;
                        setPublish((prev) => ({ ...prev, apiKey: "", keyDirty: true, configured: false }));
                      }}
                    >
                      清除
                    </button>
                  )}
                </div>
                <span className="cm-set-hint">
                  {publish.configured ? "已配置（不回显）；留空保持不变" : "从 upload-post 控制台复制"}
                </span>
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">Username</span>
                <input
                  className="cm-set-input"
                  value={publish.username}
                  placeholder="例如：zaopianju"
                  onChange={(event) => setPublish((prev) => ({ ...prev, username: event.target.value }))}
                />
                <span className="cm-set-hint">upload-post 的 profile 用户名</span>
              </label>
            </div>

            <div className="cm-set-checks">
              <label className="cm-set-check">
                <input
                  type="checkbox"
                  checked={publish.enabled}
                  onChange={(event) => setPublish((prev) => ({ ...prev, enabled: event.target.checked }))}
                />
                <span className="cm-set-check-box">{publish.enabled && <IconCheck size={11} />}</span>
                <span>启用海外发布</span>
              </label>
              <label className="cm-set-check">
                <input
                  type="checkbox"
                  checked={publish.madeForKids}
                  onChange={(event) => setPublish((prev) => ({ ...prev, madeForKids: event.target.checked }))}
                />
                <span className="cm-set-check-box">{publish.madeForKids && <IconCheck size={11} />}</span>
                <span>YouTube：made for kids</span>
              </label>
            </div>

            <div className="cm-set-field cm-set-field-wide">
              <span className="cm-set-label">默认发布平台</span>
              <div className="cm-set-chips">
                {UPLOAD_PLATFORM_OPTIONS.map((platform) => {
                  const active = publish.platforms.includes(platform.id);
                  return (
                    <button
                      key={platform.id}
                      type="button"
                      className={"cm-set-chip" + (active ? " is-active" : "")}
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
            </div>

            <div className="cm-set-grid">
              <label className="cm-set-field">
                <span className="cm-set-label">YouTube 可见性</span>
                <select
                  className="cm-set-select"
                  value={publish.privacy}
                  onChange={(event) => setPublish((prev) => ({ ...prev, privacy: event.target.value }))}
                >
                  <option value="unlisted">unlisted · 不公开（推荐）</option>
                  <option value="public">public · 公开</option>
                  <option value="private">private · 仅自己可见</option>
                </select>
                <span className="cm-set-hint">默认 unlisted 先审后公开</span>
              </label>
            </div>

            {!publishSupported && (
              <span className="cm-set-hint is-warn">
                当前后端尚未支持 upload_post_* 字段；保存会自动忽略这些字段并提示。
              </span>
            )}
          </section>

          {/* 03 AI 模型 */}
          <section className="cm-set-section">
            <header className="cm-set-section-head">
              <span className="cm-set-no">03</span>
              <div className="cm-set-section-main">
                <h3 className="cm-set-section-title">
                  <IconSettings size={14} /> AI 模型（LLM）
                </h3>
                <p className="cm-set-section-sub">LLM · 台本 / 内容生成（本地 Ollama 或 OpenAI 兼容）</p>
              </div>
              <span className={"cm-set-badge " + (llmSupported ? "is-jade" : "is-soft")}>
                {llmSupported ? "接口可用" : "接口未就绪"}
              </span>
            </header>

            <p className="cm-set-hint cm-set-hint-block">
              本地 Ollama（免费离线）或 OpenAI 兼容 API（DeepSeek / OpenAI，需 Key）；用于「AI 生成台本」等 AI 内容。
              API Key 只写不读，GET 永不返回明文。
            </p>

            <div className="cm-set-grid">
              <label className="cm-set-field">
                <span className="cm-set-label">Provider</span>
                <select
                  className="cm-set-select"
                  value={llm.provider}
                  onChange={(event) => {
                    const id = event.target.value;
                    const info = llmProviders.find((item) => item.id === id);
                    setLlm((prev) => ({
                      ...prev,
                      provider: id,
                      baseUrl: id === "mock" ? "" : info?.default_base_url || prev.baseUrl,
                      model: info?.default_model || prev.model,
                    }));
                    setLlmTestResult(null);
                    setLlmTestError(null);
                  }}
                >
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label || item.name || item.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="cm-set-field">
                <span className="cm-set-label">模型</span>
                <input
                  className="cm-set-input"
                  value={llm.model}
                  onChange={(event) => setLlm((prev) => ({ ...prev, model: event.target.value }))}
                  placeholder="qwen2.5:7b / deepseek-chat"
                />
                <span className="cm-set-hint">本地默认 qwen2.5:7b；API 如 deepseek-chat</span>
              </label>
            </div>

            <label className="cm-set-field cm-set-field-wide">
              <span className="cm-set-label">Base URL</span>
              <input
                className="cm-set-input"
                value={llm.baseUrl}
                onChange={(event) => setLlm((prev) => ({ ...prev, baseUrl: event.target.value }))}
                placeholder={llm.provider === "ollama" ? "http://127.0.0.1:11434" : "https://api.deepseek.com/v1"}
                disabled={llm.provider === "mock"}
              />
              <span className="cm-set-hint">
                {llm.provider === "ollama" ? "默认 http://127.0.0.1:11434" : "如 https://api.deepseek.com/v1"}
              </span>
            </label>

            <label className="cm-set-field cm-set-field-wide">
              <span className="cm-set-label">API Key</span>
              <div className="cm-set-inline">
                <input
                  className="cm-set-input"
                  type="password"
                  value={llm.apiKey}
                  placeholder={llm.keyConfigured ? "••••••••（已保存）" : "粘贴 API Key"}
                  autoComplete="new-password"
                  onChange={(event) => setLlm((prev) => ({ ...prev, apiKey: event.target.value, keyDirty: true }))}
                />
                {llm.keyConfigured && (
                  <button
                    type="button"
                    className="cm-btn cm-btn-ghost cm-btn-sm"
                    onClick={() => {
                      if (!window.confirm("清除已保存的 LLM API Key？")) return;
                      setLlm((prev) => ({ ...prev, apiKey: "", keyDirty: true, keyConfigured: false }));
                    }}
                  >
                    清除
                  </button>
                )}
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void testLLM()} disabled={llmTesting}>
                  {llmTesting ? <Spinner size={12} /> : null}
                  {llmTesting ? "测试中…" : "测试连接"}
                </button>
              </div>
              <span className="cm-set-hint">
                {llm.keyConfigured
                  ? "已保存（不回显）；留空保持不变"
                  : llm.provider === "ollama"
                    ? "本地 Ollama 无需 Key"
                    : "OpenAI 兼容 provider 需要 API Key"}
              </span>
            </label>

            {llmTestResult?.ok && (
              <div className="cm-set-result is-ok">
                <IconCheck size={13} /> 连接成功
                {llmTestResult.model ? " · " + llmTestResult.model : ""}
                {typeof llmTestResult.latency_ms === "number"
                  ? " · " + Math.round(llmTestResult.latency_ms) + "ms"
                  : typeof llmTestResult.elapsed_sec === "number"
                    ? " · " + llmTestResult.elapsed_sec.toFixed(1) + "s"
                    : ""}
              </div>
            )}
            {llmTestResult && !llmTestResult.ok && (
              <div className="cm-set-result is-error">
                <IconAlert size={13} /> 连接失败：{llmTestResult.error || "未知原因"}
              </div>
            )}
            {llmTestError && (
              <div className="cm-set-result is-error">
                <IconAlert size={13} /> {llmTestError}
              </div>
            )}
            {!llmSupported && (
              <span className="cm-set-hint is-warn">
                当前后端尚未提供 /api/llm/config；字段仍会随保存 PATCH 尝试写入 llm_*。
              </span>
            )}
          </section>
        </div>

        <footer className="cm-set-foot">
          <span className="cm-set-foot-hint">
            <IconRefresh size={13} /> 修改生成模式后，新任务按新设置执行；已排队任务不回溯。
          </span>
          <div className="cm-set-foot-actions">
            <button type="button" className="cm-btn cm-btn-sm" onClick={onClose}>
              关闭
            </button>
            <button
              type="button"
              className="cm-btn cm-btn-primary cm-btn-sm"
              onClick={() => void save()}
              disabled={saving || loading || !!error}
            >
              {saving ? <Spinner size={13} /> : null}
              {saving ? "保存中…" : "保存设置"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
