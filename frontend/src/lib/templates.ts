// 节目模式静态说明（展示用；与 contract/formats/roundtable_5min.json 对齐）
export interface TemplateSegmentInfo {
  id: string;
  name: string;
  duration: number;
  pattern: string[];
}

export interface TemplateInfo {
  id: string;
  name: string;
  tagline: string;
  aspect: string;
  target_duration: number;
  accent: string;
  segments: TemplateSegmentInfo[];
  packaging: [string, string][];
}

export const TEMPLATES: TemplateInfo[] = [
  {
    id: "roundtable_5min",
    name: "横屏 AI 圆桌 · 5 分钟",
    tagline: "主持 + 2 嘉宾，固定虚拟棚，正反打 + 反应镜头 + 花字收口",
    aspect: "16:9",
    target_duration: 296,
    accent: "#F5C518",
    segments: [
      { id: "open", name: "片头", duration: 6, pattern: ["title_card"] },
      { id: "cold_open", name: "冷开场", duration: 17, pattern: ["host_to_camera", "reaction"] },
      { id: "intro", name: "嘉宾介绍", duration: 32, pattern: ["host_to_camera", "shot_reverse", "reaction"] },
      { id: "topic1", name: "话题一 · AI 与导演", duration: 113, pattern: ["host_to_camera", "shot_reverse", "reaction", "meme_card", "b_roll"] },
      { id: "topic2", name: "话题二 · 现场改台本", duration: 76, pattern: ["host_to_camera", "shot_reverse", "reaction", "meme_card", "b_roll"] },
      { id: "closing", name: "结尾", duration: 52, pattern: ["host_to_camera", "shot_reverse", "meme_card", "end_card"] },
    ],
    packaging: [
      ["片头", "品牌片头 logo_sting"],
      ["片尾", "片尾卡 end_card"],
      ["花字", "造片局黄 zaopianju_yellow"],
      ["转场", "硬切 hard_cut"],
      ["BGM", "轻访谈 talk_light"],
      ["字幕", "综艺花字 variety"],
    ],
  },
];

export function templateLabel(id?: string | null): string {
  const found = TEMPLATES.find((item) => item.id === id);
  return found ? found.name : id || "未指定模式";
}
