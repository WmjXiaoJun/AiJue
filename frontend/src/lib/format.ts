// 中文标签与格式化工具（避免散落在各组件里）

export const SHOT_TYPE_LABEL: Record<string, string> = {
  title_card: "片头卡",
  host_to_camera: "主持口播",
  guest_intro: "嘉宾介绍",
  shot_reverse: "正反打",
  reaction: "反应镜头",
  b_roll: "空镜素材",
  meme_card: "花字卡",
  end_card: "片尾卡",
};

export const SHOT_TYPE_SHORT: Record<string, string> = {
  title_card: "片头",
  host_to_camera: "口播",
  guest_intro: "介绍",
  shot_reverse: "正反打",
  reaction: "反应",
  b_roll: "空镜",
  meme_card: "花字",
  end_card: "片尾",
};

export const SHOT_STATUS_LABEL: Record<string, string> = {
  pending: "待生成",
  queued: "排队中",
  generating: "生成中",
  ready: "已就绪",
  failed: "生成失败",
};

export const JOB_STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export const JOB_TYPE_LABEL: Record<string, string> = {
  shot: "镜头生成",
  render: "成片渲染",
};

export const EPISODE_STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  storyboard: "分镜就绪",
  generating: "生成中",
  rendering: "渲染中",
  ready: "已成片",
  rendered: "已成片",
  done: "已完成",
  failed: "失败",
};

export const PROJECT_STATUS_LABEL: Record<string, string> = {
  draft: "筹备中",
  active: "制作中",
  generating: "生成中",
  ready: "已交付",
  rendered: "已交付",
  done: "已交付",
  archived: "已归档",
};

export const SEGMENT_LABEL: Record<string, string> = {
  open: "片头",
  cold_open: "冷开场",
  intro: "嘉宾介绍",
  topic1: "话题一",
  topic2: "话题二",
  closing: "结尾",
  outro: "片尾",
};

export const FORMAT_LABEL: Record<string, string> = {
  roundtable_5min: "横屏 AI 圆桌 · 5 分钟",
};

export const ASSET_ROLE_LABEL: Record<string, string> = {
  host: "主持",
  guest: "嘉宾",
};

export const ASSET_TYPE_LABEL: Record<string, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  style: "风格",
};

export const VERSION_STATUS_LABEL: Record<string, string> = {
  ready: "可用",
  done: "已完成",
  generating: "生成中",
  queued: "排队中",
  failed: "失败",
};

/** 镜头类型的主色（分镜宫格 / 时间线等使用） */
export const TYPE_ACCENT: Record<string, string> = {
  title_card: "#F5C518",
  host_to_camera: "#FFD84D",
  guest_intro: "#5AC8FA",
  shot_reverse: "#3C7AC8",
  reaction: "#FF6B6B",
  b_roll: "#8B7CF6",
  meme_card: "#FF9F43",
  end_card: "#C89B3C",
};

export const PACKAGING_LABEL: Record<string, string> = {
  logo_sting: "品牌片头",
  end_card: "片尾卡",
  zaopianju_yellow: "造片局黄",
  hard_cut: "硬切",
  talk_light: "轻访谈 BGM",
  variety: "综艺花字字幕",
};

export function label(map: Record<string, string>, key?: string | null): string {
  if (!key) return "—";
  return map[key] || key;
}

export function fmtDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

export function fmtShotDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || Number.isNaN(seconds)) return "—";
  return Number.isInteger(seconds) ? seconds + "s" : seconds.toFixed(1) + "s";
}

export function fmtMoney(value?: number | null): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return "¥" + n.toFixed(2);
}

export function fmtPercent(value?: number | null): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.round(Math.max(0, Math.min(100, n))) + "%";
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return hh + ":" + mm + ":" + ss;
}

export function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return date.getFullYear() + "/" + m + "/" + d + " " + fmtTime(iso).slice(0, 5);
}

export function firstChar(name?: string | null): string {
  const text = (name || "?").trim();
  return Array.from(text)[0] || "?";
}

export function isActiveJob(job: { status: string }): boolean {
  return job.status === "queued" || job.status === "running";
}

export function segmentName(id?: string | null): string {
  return label(SEGMENT_LABEL, id);
}

/** episode.progress 可能是 0-1 或 0-100，统一成 0-100。 */
export function pct(value?: number | null, total?: number | null): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, value <= 1 && total ? value * 100 : value));
  }
  if (total && typeof total === "number" && total > 0) return 0;
  return 0;
}

export function shotsDoneFromProgress(episode: { progress?: number; shots_total?: number }): number {
  const total = episode.shots_total || 0;
  if (!total) return 0;
  const raw = typeof episode.progress === "number" ? episode.progress : 0;
  const ratio = raw > 1 ? raw / 100 : raw;
  return Math.round(ratio * total);
}
