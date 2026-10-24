// 一键发布（海外）常量与默认文案 — contract/PUBLISHING.md
export const PUBLISH_BRAND_TITLE = "造片局 · AI微综艺";
export const UPLOAD_POST_URL = "https://upload-post.com";

export interface PublishPlatformInfo {
  id: string;
  label: string;
  limits: string[];
}

export const PUBLISH_PLATFORMS: PublishPlatformInfo[] = [
  {
    id: "youtube",
    label: "YouTube",
    limits: ["长视频支持", "标题 ≤100", "建议先 unlisted 审后公开"],
  },
  {
    id: "tiktok",
    label: "TikTok",
    limits: ["单条视频 ≤10 分钟", "需在 upload-post 授权 TikTok 账号"],
  },
  {
    id: "instagram",
    label: "Instagram Reels",
    limits: ["建议 ≤15 分钟", "竖屏最佳；横屏 16:9 会加黑边", "需授权 IG 账号"],
  },
];

export interface PublishLangInfo {
  code: string;
  label: string;
  native: string;
}

export const PUBLISH_LANGS: PublishLangInfo[] = [
  { code: "zh", label: "中文原版", native: "Chinese" },
  { code: "en", label: "英语", native: "English" },
  { code: "es", label: "西语", native: "Español" },
  { code: "ja", label: "日语", native: "日本語" },
  { code: "ko", label: "韩语", native: "한국어" },
  { code: "pt", label: "葡语", native: "Português" },
];

export const PUBLISH_PRIVACY_OPTIONS = [
  { value: "unlisted", label: "unlisted · 不公开（先审后公开，推荐）" },
  { value: "public", label: "public · 公开" },
  { value: "private", label: "private · 仅自己可见" },
];

export const PUBLISH_BASE_HASHTAGS = ["#造片局", "#AI微综艺", "#Zaopianju", "#AIVariety", "#AIGenerated"];

export function platformLabel(id: string): string {
  return PUBLISH_PLATFORMS.find((item) => item.id === id)?.label || id;
}

export function langLabel(code: string): string {
  return PUBLISH_LANGS.find((item) => item.code === code)?.label || code.toUpperCase();
}

export function defaultPublishCopy(episodeTitle: string, episodeNo: number): { title: string; description: string; hashtags: string[] } {
  const brand = PUBLISH_BRAND_TITLE;
  return {
    title: episodeTitle + "｜" + brand,
    description:
      episodeTitle +
      " — " +
      brand +
      " 第" +
      String(episodeNo || 1).padStart(3, "0") +
      "期。AI 主持人 + 2 位 AI 嘉宾的圆桌微综艺，聊 AI 与内容行业。\n\n本片由 AI 生成（" +
      brand +
      "）。",
    hashtags: PUBLISH_BASE_HASHTAGS,
  };
}

export function parseHashtags(text: string): string[] {
  return text
    .split(/[\s,，]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => (item.startsWith("#") ? item : "#" + item));
}

export function formatHashtags(tags: string[]): string {
  return tags.join(" ");
}
