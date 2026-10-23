import { useEffect, useState } from "react";
import { mediaUrl } from "../lib/api";
import type { Character } from "../types";
import { Avatar } from "./ui";

interface Props {
  character?: Character | null;
  name?: string;
  color?: string;
  size?: number;
  square?: boolean;
  className?: string;
}

/** 角色头像：有真实 portrait 时用图片，加载失败/未生成时回落首字色块（不伪造图片） */
export function CharacterAvatar({ character, name, color, size = 36, square = true, className }: Props) {
  const [failed, setFailed] = useState(false);
  const portrait = character?.portrait_url || null;

  useEffect(() => {
    setFailed(false);
  }, [portrait]);

  if (portrait && !failed) {
    return (
      <img
        className={"char-avatar" + (className ? " " + className : "")}
        src={mediaUrl(portrait)}
        alt={character?.name || name || "角色"}
        title={character?.name || name}
        width={size}
        height={size}
        loading="lazy"
        style={{ width: size, height: size, borderRadius: square ? Math.max(6, Math.round(size * 0.22)) : "50%" }}
        onError={() => setFailed(true)}
      />
    );
  }
  return <Avatar name={character?.name || name} color={character?.accent_color || color} size={size} square={square} />;
}
