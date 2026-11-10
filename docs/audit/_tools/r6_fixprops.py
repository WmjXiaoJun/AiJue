# -*- coding: utf-8 -*-
p = 'frontend/src/components/platform/PlatformHome.tsx'
s = open(p, encoding='utf-8').read()

s = s.replace(
    'import { useToast } from "../Toast";',
    'import type { Project } from "../../types";\nimport { useToast } from "../Toast";',
    1,
)
s = s.replace(
    '''interface Props {
  projects: unknown[];
  offline: boolean;
  onOpenProject: (project: unknown) => void;
  onOpenCinema: () => void;
  onOpenLibrary: () => void;
  onOpenFinal: () => void;
  onProjectCreated: (project: unknown) => void;
  onRefreshProjects: () => void | Promise<void>;
  openSettingsSignal?: number;
}''',
    '''interface Props {
  /** R6：以下入参保留签名（App 仍会传），首页已不再使用 */
  projects: Project[];
  offline: boolean;
  onOpenProject: (project: Project) => void;
  onOpenCinema: () => void;
  onOpenLibrary: () => void;
  onOpenFinal: () => void;
  onProjectCreated: (project: Project) => void;
  onRefreshProjects: () => void | Promise<void>;
  openSettingsSignal?: number;
}''',
    1,
)
open(p, 'w', encoding='utf-8', newline='').write(s)
print('patched props')
