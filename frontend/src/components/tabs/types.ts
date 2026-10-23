import type { Asset, Episode, Project, Shot } from "../../types";

export interface TabBaseProps {
  project: Project;
  episode: Episode;
  assets: Asset[];
  shots: Shot[];
  /** 拉取最新 episode / shots / jobs */
  onChanged: () => void;
  onOpenShot: (shot: Shot) => void;
}
