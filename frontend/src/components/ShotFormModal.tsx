import { useEffect, useState } from "react";
import type { Asset, NewShot, Shot } from "../types";
import { SHOT_TYPE_LABEL } from "../lib/format";
import { Field, Modal, Spinner } from "./ui";
import { IconCheck } from "./icons";

const SHOT_TYPE_OPTIONS = [
  "host_to_camera",
  "talking_head",
  "shot_reverse",
  "reaction",
  "narration",
  "b_roll",
  "meme_card",
  "title_card",
  "end_card",
];

interface Props {
  open: boolean;
  mode: "create" | "edit";
  shot?: Shot | null;
  assets: Asset[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (body: NewShot) => void | Promise<void>;
}

interface FormState {
  type: string;
  speaker: string;
  speakerName: string;
  line: string;
  duration: string;
  visual: string;
  punchline: string;
}

const EMPTY: FormState = {
  type: "host_to_camera",
  speaker: "",
  speakerName: "",
  line: "",
  duration: "5",
  visual: "",
  punchline: "",
};

export function ShotFormModal({ open, mode, shot, assets, busy, onClose, onSubmit }: Props) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const characters = assets.filter((asset) => asset.type === "character");

  useEffect(() => {
    if (!open) return;
    setError(null);
    if (mode === "edit" && shot) {
      setForm({
        type: shot.type || "host_to_camera",
        speaker: shot.speaker || "",
        speakerName: shot.speaker_name || "",
        line: shot.line || "",
        duration: String(shot.duration ?? 5),
        visual: shot.visual || "",
        punchline: shot.punchline || "",
      });
    } else {
      setForm(EMPTY);
    }
  }, [open, mode, shot]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const submit = async () => {
    const duration = Number(form.duration);
    if (!Number.isFinite(duration) || duration < 0.5 || duration > 120) {
      setError("时长需在 0.5–120 秒之间");
      return;
    }
    const body: NewShot = {
      type: form.type,
      line: form.line.trim(),
      duration: Math.round(duration * 10) / 10,
      visual: form.visual.trim(),
      punchline: form.punchline.trim(),
    };
    if (form.speaker) {
      body.speaker = form.speaker;
      body.speaker_name = form.speakerName || characters.find((item) => item.id === form.speaker)?.name || "";
    } else if (form.speakerName.trim()) {
      body.speaker_name = form.speakerName.trim();
    }
    try {
      await onSubmit(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === "edit" ? "编辑镜头" : "添加镜头"}
      subtitle={mode === "edit" ? "修改台词 / 时长 / 画面描述，保存后立即刷新分镜" : "手动追加一个镜头，序号由后端自动重排"}
      size="md"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? <Spinner size={14} /> : <IconCheck size={15} />}
            {busy ? "保存中…" : mode === "edit" ? "保存修改" : "添加镜头"}
          </button>
        </>
      }
    >
      {error && <div className="notice notice-danger" style={{ marginBottom: 12 }}>{error}</div>}
      <div className="form-stack">
        <div className="form-grid">
          <Field label="镜头类型">
            <select className="select select-wide" value={form.type} onChange={(event) => update("type", event.target.value)}>
              {SHOT_TYPE_OPTIONS.map((type) => (
                <option key={type} value={type}>
                  {SHOT_TYPE_LABEL[type] || type}
                </option>
              ))}
            </select>
          </Field>
          <Field label="时长（秒）">
            <input
              className="input"
              value={form.duration}
              onChange={(event) => update("duration", event.target.value)}
              inputMode="decimal"
            />
          </Field>
        </div>
        <Field label="说话人" hint="从资产库角色中选择；旁白/画外可留空并填写名字">
          <select
            className="select select-wide"
            value={form.speaker}
            onChange={(event) => {
              const id = event.target.value;
              const asset = characters.find((item) => item.id === id);
              setForm((prev) => ({ ...prev, speaker: id, speakerName: asset ? asset.name : prev.speakerName }));
            }}
          >
            <option value="">无（画外 / 旁白）</option>
            {characters.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.name} · {asset.title || asset.role}
              </option>
            ))}
            {form.speaker && !characters.some((item) => item.id === form.speaker) && (
              <option value={form.speaker}>{form.speakerName || form.speaker}（沿用原说话人）</option>
            )}
          </select>
        </Field>
        <Field label="台词">
          <textarea
            className="textarea"
            rows={3}
            value={form.line}
            onChange={(event) => update("line", event.target.value)}
            placeholder="例如：今天这期，我们聊一个得罪人的话题。"
          />
        </Field>
        <Field label="画面描述">
          <input
            className="input"
            value={form.visual}
            onChange={(event) => update("visual", event.target.value)}
            placeholder="例如：虚拟演播厅 · 暖黄灯 · 中景"
          />
        </Field>
        <Field label="花字（可选）">
          <input
            className="input"
            value={form.punchline}
            onChange={(event) => update("punchline", event.target.value)}
            placeholder="例如：停三秒，是人的胆子"
          />
        </Field>
      </div>
    </Modal>
  );
}
