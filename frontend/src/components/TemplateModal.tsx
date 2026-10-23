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
  const template = TEMPLATES[0];
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="节目模板"
      subtitle="固定节目模式 = 工业化生产的第一步；MVP 先稳定跑通一种模式"
      size="lg"
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
          <button type="button" className="btn btn-primary" onClick={onUse}>
            <IconSpark size={16} /> 用此模板新建项目
          </button>
        </>
      }
    >
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
          <span>主持 + 2 嘉宾 · 固定虚拟棚</span>
        </div>
        <div className="template-segments">
          {template.segments.map((segment, index) => (
            <div key={segment.id} className="template-segment">
              <span className="template-segment-index">0{index + 1}</span>
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
