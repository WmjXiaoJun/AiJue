import { useEffect, useMemo, useState } from "react";
import { ApiError, api, errorText } from "../lib/api";
import type { Episode, GenerateScriptResult, Project } from "../types";
import { TEMPLATES, templateLabel } from "../lib/templates";
import { fmtDuration } from "../lib/format";
import { useToast } from "./Toast";
import { Field, Modal, Notice, Spinner } from "./ui";
import { IconCheck, IconCopy, IconSpark } from "./icons";

interface Props {
  open: boolean;
  project: Project;
  episode: Episode;
  onClose: () => void;
  onAdopt: (script: string) => void;
}

const TONES = [
  { value: "轻松幽默", label: "轻松幽默" },
  { value: "犀利吐槽", label: "犀利吐槽" },
  { value: "温暖治愈", label: "温暖治愈" },
  { value: "理性深度", label: "理性深度" },
  { value: "综艺夸张", label: "综艺夸张" },
];

const DURATIONS = [45, 60, 90, 120, 180, 296];

const TOPIC_SUGGESTIONS = ["AI 会取代综艺导演吗？", "年轻人为什么爱看 AI 综艺", "AI 剪辑师会抢活吗？"];

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function AiScriptModal({ open, project, episode, onClose, onAdopt }: Props) {
  const notify = useToast();
  const [topic, setTopic] = useState(episode.title || "");
  const [templateId, setTemplateId] = useState(
    TEMPLATES.some((item) => item.id === project.format_id) ? project.format_id : TEMPLATES[0].id
  );
  const [duration, setDuration] = useState(String(episode.target_duration || 90));
  const [tone, setTone] = useState(TONES[0].value);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateScriptResult | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTopic(episode.title || "");
    setTemplateId(TEMPLATES.some((item) => item.id === project.format_id) ? project.format_id : TEMPLATES[0].id);
    setDuration(String(episode.target_duration || 90));
    setTone(TONES[0].value);
    setLoading(false);
    setError(null);
    setResult(null);
    setCopied(false);
  }, [open, episode.title, episode.target_duration, project.format_id]);

  const template = useMemo(() => TEMPLATES.find((item) => item.id === templateId) || TEMPLATES[0], [templateId]);

  const generate = async () => {
    if (!topic.trim()) {
      setError("请先填写话题");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const generated = await api.generateScript(project.id, {
        topic: topic.trim(),
        template_id: templateId,
        duration: Number(duration) || 90,
        tone,
      });
      setResult(generated);
      notify("AI 台本生成完成", "success");
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setError("AI 台本接口未就绪（等待后端 task-45：POST /api/projects/{pid}/generate-script）");
      } else {
        setError("生成失败：" + errorText(err));
      }
    } finally {
      setLoading(false);
    }
  };

  const adopt = () => {
    if (!result?.script) return;
    onAdopt(result.script);
    onClose();
  };

  const handleCopy = async () => {
    if (!result?.script) return;
    const ok = await copyText(result.script);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="AI 生成台本"
      subtitle="话题 + 模板 + 时长 + 语气 → LLM 生成分段台本；采纳后写入台本编辑器，再点「保存台本」生效"
      size="lg"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={loading}>
            关闭
          </button>
          {result && (
            <button type="button" className="btn btn-ghost" onClick={() => void handleCopy()} disabled={loading}>
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              {copied ? "已复制" : "复制台本"}
            </button>
          )}
          <button type="button" className="btn btn-outline" onClick={() => void generate()} disabled={loading}>
            {loading ? <Spinner size={14} /> : <IconSpark size={15} />}
            {loading ? "生成中…" : result ? "重新生成" : "生成台本"}
          </button>
          <button type="button" className="btn btn-primary" onClick={adopt} disabled={!result?.script || loading}>
            <IconCheck size={15} /> 采纳写入台本
          </button>
        </>
      }
    >
      <div className="ai-script-form">
        <Field label="话题" hint="这一期聊什么；可点击下面建议快速填入">
          <input
            className="input"
            value={topic}
            maxLength={120}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="例如：AI 会取代综艺导演吗？"
            autoFocus
          />
          <div className="ai-topic-suggest">
            {TOPIC_SUGGESTIONS.map((item) => (
              <button key={item} type="button" className="ai-topic-chip" onClick={() => setTopic(item)}>
                {item}
              </button>
            ))}
          </div>
        </Field>

        <div className="form-grid">
          <Field label="模板" hint={template.id}>
            <select className="select" value={templateId} onChange={(event) => setTemplateId(event.target.value)}>
              {TEMPLATES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="时长">
            <select className="select" value={duration} onChange={(event) => setDuration(event.target.value)}>
              {Array.from(new Set([...DURATIONS, Number(episode.target_duration) || 90]))
                .sort((a, b) => a - b)
                .map((seconds) => (
                  <option key={seconds} value={String(seconds)}>
                    {fmtDuration(seconds)}
                  </option>
                ))}
            </select>
          </Field>
        </div>

        <Field label="语气">
          <div className="ai-tone-chips">
            {TONES.map((item) => (
              <button
                key={item.value}
                type="button"
                className={"ai-tone-chip" + (tone === item.value ? " active" : "")}
                onClick={() => setTone(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </Field>

        <div className="ai-script-context">
          <span>{templateLabel(templateId)}</span>
          <i />
          <span>{template.aspect}</span>
          <i />
          <span>{fmtDuration(Number(duration) || 90)}</span>
          <i />
          <span>{tone}</span>
          <i />
          <span>{project.name}</span>
        </div>
      </div>

      {error && <Notice tone="warn">{error}</Notice>}

      {loading && (
        <div className="ai-script-loading">
          <Spinner size={18} />
          <div>
            <b>AI 正在生成台本…</b>
            <span>本地 Ollama 通常 10–30s；OpenAI 兼容 API 依赖网络与模型速度。</span>
          </div>
        </div>
      )}

      {result && (
        <div className="ai-script-result">
          <div className="ai-script-result-head">
            <div>
              <b>生成预览</b>
              <span>
                {(result.provider || "llm") + " · " + (result.model || "model")}
                {typeof result.elapsed_sec === "number" ? " · " + result.elapsed_sec.toFixed(1) + "s" : ""}
                {result.title ? " · " + result.title : ""}
              </span>
            </div>
            {result.warning && <span className="ai-script-warning">{result.warning}</span>}
          </div>
          <pre className="ai-script-preview">{result.script}</pre>
        </div>
      )}

      {!result && !loading && !error && (
        <div className="ai-script-empty">
          <SparkPlaceholder />
          <div>
            <b>还没有生成内容</b>
            <p>选择话题、模板、时长与语气后点击「生成台本」；生成结果可预览、复制或采纳写入编辑器。</p>
          </div>
        </div>
      )}
    </Modal>
  );
}

function SparkPlaceholder() {
  return (
    <span className="ai-script-empty-icon">
      <IconSpark size={20} />
    </span>
  );
}
