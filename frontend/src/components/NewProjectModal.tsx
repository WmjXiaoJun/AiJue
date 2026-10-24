import { useEffect, useState } from "react";
import { api, errorText } from "../lib/api";
import type { Project } from "../types";
import { useToast } from "./Toast";
import { Field, Modal, Spinner } from "./ui";
import { IconPlus } from "./icons";
import { TEMPLATES } from "../lib/templates";
import { fmtDuration } from "../lib/format";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (project: Project) => void;
}

export function NewProjectModal({ open, onClose, onCreated }: Props) {
  const notify = useToast();
  const [name, setName] = useState("造片局 · 新节目");
  const [formatId, setFormatId] = useState(TEMPLATES[0].id);
  const [aspect, setAspect] = useState(TEMPLATES[0].aspect);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setBusy(false);
      setName("造片局 · 新节目");
    }
  }, [open]);

  const submit = async () => {
    const finalName = name.trim() || "未命名项目";
    setBusy(true);
    try {
      const project = await api.createProject({ name: finalName, format_id: formatId, aspect });
      onCreated(project);
    } catch (err) {
      notify(errorText(err), "error");
      setBusy(false);
    }
  };

  const template = TEMPLATES.find((item) => item.id === formatId) || TEMPLATES[0];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新建项目"
      subtitle="项目 = 一档节目；创建后由后端初始化剧集、资产与分镜"
      size="md"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? <Spinner size={14} /> : <IconPlus size={16} />}
            {busy ? "创建中…" : "创建项目"}
          </button>
        </>
      }
    >
      <div className="form-stack">
        <Field label="项目名称">
          <input
            className="input"
            value={name}
            maxLength={40}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：造片局 · 第二季"
            autoFocus
          />
        </Field>
        <Field label="节目模式" hint={"共 " + TEMPLATES.length + " 种节目模式，来自 contract/formats/*.json"}>
          <div className="template-pick-list">
            {TEMPLATES.map((item) => (
              <button
                key={item.id}
                type="button"
                className={"template-pick" + (formatId === item.id ? " active" : "")}
                onClick={() => {
                  setFormatId(item.id);
                  setAspect(item.aspect);
                }}
              >
                <span className="template-pick-swatch" style={{ background: item.accent }} />
                <span className="template-pick-main">
                  <b>{item.name}</b>
                  <em>{item.tagline}</em>
                </span>
                <span className="template-pick-meta">
                  {item.aspect} · {fmtDuration(item.target_duration)}
                </span>
              </button>
            ))}
          </div>
        </Field>
        <Field label="画幅">
          <div className="segmented">
            {["16:9", "9:16", "1:1"].map((item) => (
              <button
                key={item}
                type="button"
                className={"segmented-item" + (aspect === item ? " active" : "")}
                onClick={() => setAspect(item)}
                disabled={item !== template.aspect}
                title={item === template.aspect ? "该模式默认画幅" : "当前节目模式不支持"}
              >
                {item}
              </button>
            ))}
          </div>
        </Field>
        <div className="form-note">模式结构：{template.segments.map((segment) => segment.name).join(" → ")}</div>
      </div>
    </Modal>
  );
}
