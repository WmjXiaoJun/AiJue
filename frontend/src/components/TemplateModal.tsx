import { useEffect, useState } from "react";
import { Modal } from "./ui";
import { IconTemplate, IconSpark } from "./icons";
import { SHOT_TYPE_LABEL, fmtDuration } from "../lib/format";
import { TEMPLATES } from "../lib/templates";

interface Props {
  open: boolean;
  onClose: () => void;
  onUse: () => void;
}

export function TemplateModal({ open, onClose, onUse }: Props) {
  const [selectedId, setSelectedId] = useState(TEMPLATES[0].id);
  const template = TEMPLATES.find((item) => item.id === selectedId) || TEMPLATES[0];

  useEffect(() => {
    if (open) setSelectedId(TEMPLATES[0].id);
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="节目模板"
      subtitle="固定节目模式 = 工业化生产的第一步；选择任意模式直接新建项目"
      size="lg"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
          <button type="button" className="btn btn-primary" onClick={onUse}>
            <IconSpark size={16} /> 用「{template.name}」新建项目
          </button>
        </>
      }
    >
      <div className="template-picker">
        <span className="template-picker-label">
          节目模式 · 已上架 {TEMPLATES.length} 个
          <em>（来自 contract/formats/*.json）</em>
        </span>
        <div className="template-picker-grid">
          {TEMPLATES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={"template-option" + (item.id === selectedId ? " active" : "")}
              onClick={() => setSelectedId(item.id)}
            >
              <span className="template-option-swatch" style={{ background: item.accent }} />
              <span className="template-option-main">
                <b>{item.name}</b>
                <em>{item.tagline}</em>
              </span>
              <span className="template-option-meta">
                {item.aspect} · {fmtDuration(item.target_duration)}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="template-card">
        <div className="template-head">
          <span className="template-icon">
            <IconTemplate size={22} />
          </span>
          <div>
            <h4>{template.name}</h4>
            <p>{template.tagline}</p>
          </div>
          <span className="template-format">{template.id}</span>
        </div>
        <div className="template-meta">
          <span>{template.aspect}</span>
          <span>目标时长 {fmtDuration(template.target_duration)}</span>
          <span>{template.segments.length} 个段落</span>
          <span>{template.accent}</span>
        </div>
        <div className="template-segments">
          {template.segments.map((segment, index) => (
            <div key={segment.id} className="template-segment">
              <span className="template-segment-index">{String(index + 1).padStart(2, "0")}</span>
              <span className="template-segment-name">{segment.name}</span>
              <span className="template-segment-duration">{fmtDuration(segment.duration)}</span>
              <span className="template-segment-pattern">
                {segment.pattern.map((type) => SHOT_TYPE_LABEL[type] || type).join(" / ")}
              </span>
            </div>
          ))}
        </div>
        <div className="template-packaging">
          {template.packaging.map(([key, value]) => (
            <span key={key} className="tag">
              {key} · {value}
            </span>
          ))}
        </div>
      </div>
    </Modal>
  );
}
