// V18 · 制片控制台 Stepper（6 步导航；每步显示状态与动态摘要）
import { IconCheck } from "../icons";

export interface PipelineStepMeta {
  no: number;
  label: string;
  en: string;
  sub: string;
  done: boolean;
  locked: boolean;
}

export function PipelineStepper({
  steps,
  current,
  onSelect,
}: {
  steps: PipelineStepMeta[];
  current: number;
  onSelect: (step: number) => void;
}) {
  return (
    <div className="v18-stepper" role="tablist" aria-label="一键成片步骤">
      {steps.map((item) => {
        const active = item.no === current;
        return (
          <button
            key={item.no}
            type="button"
            role="tab"
            aria-selected={active}
            className={"v18-step" + (active ? " is-active" : "") + (item.done && !active ? " is-done" : "")}
            onClick={() => onSelect(item.no)}
            disabled={item.locked}
            title={item.locked ? "需要完成前面的步骤" : item.label}
          >
            <span className="v18-step-no">{item.done && !active ? <IconCheck size={12} /> : String(item.no).padStart(2, "0")}</span>
            <span className="v18-step-main">
              <span className="v18-step-label">{item.label}</span>
              <span className="v18-step-sub">{item.sub}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
