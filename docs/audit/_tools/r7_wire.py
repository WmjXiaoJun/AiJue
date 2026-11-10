# -*- coding: utf-8 -*-
"""R7：8 项一级导航（项目大厅 + 五步创作 + 模板 + 设置），模板墙页面接入 #/library/templates。"""
import io, re, shutil

# ---------- 1) AppShell：8 项导航 ----------
p = 'frontend/src/components/shell/AppShell.tsx'
s = open(p, encoding='utf-8').read()

start = s.index('interface NavItem {')
end = s.index('const STORAGE_KEY = "zpj_shell_open_v1";')
nav_block = '''interface NavItem {
  key: string;
  no: string;
  label: string;
  href?: string;
  icon: ReactNode;
  match: ShellNavKey[];
  /** 02–06 需要「当前这一集」，没有就禁用并提示 */
  needEpisode?: boolean;
  onClick?: "settings";
}

/** 一级导航：项目大厅 + 五步创作 + 模板 + 设置（其余页面留在路由里，不进导航） */
const NAV_ITEMS: NavItem[] = [
  { key: "projects", no: "01", label: "项目大厅", href: "#/projects", icon: <IconProjects />, match: ["projects"] },
  { key: "script", no: "02", label: "剧本间", icon: <IconScript />, match: [], needEpisode: true },
  { key: "assets", no: "03", label: "资产库", icon: <IconUsers />, match: [], needEpisode: true },
  { key: "storyboard", no: "04", label: "分镜台", icon: <IconBoard />, match: [], needEpisode: true },
  { key: "generate", no: "05", label: "生成台", icon: <IconJobs />, match: [], needEpisode: true },
  { key: "finish", no: "06", label: "成片房", icon: <IconFilm />, match: [], needEpisode: true },
  { key: "templates", no: "07", label: "模板", href: "#/library/templates", icon: <IconTemplate />, match: ["library"] },
];

const NAV_SETTINGS: NavItem = {
  key: "settings",
  no: "08",
  label: "设置",
  icon: <IconSettings />,
  match: ["settings"],
  onClick: "settings",
};

/** 当前这一集（App 打开工作台时写入），用于 02–06 的直达链接 */
function readEpisode(): string {
  try {
    return localStorage.getItem("zpj_current_episode") || "";
  } catch {
    return "";
  }
}

function episodeHref(key: string): string {
  const eid = readEpisode();
  return eid ? `#/episodes/${encodeURIComponent(eid)}/${key}` : "";
}

'''
s = s[:start] + nav_block + s[end:]

# imports：补 6 个图标
s = s.replace(
    '''import {
  IconChevronLeft,
  IconChevronRight,
  IconSettings,
  IconSpark,
  IconTemplate,
  IconVideo,
} from "../icons";''',
    '''import {
  IconBoard,
  IconChevronLeft,
  IconChevronRight,
  IconFilm,
  IconJobs,
  IconProjects,
  IconScript,
  IconSettings,
  IconTemplate,
  IconUsers,
} from "../icons";''',
    1,
)

# renderItem：支持 needEpisode（无当前集 → 禁用）
old_render = s[s.index('  const renderItem = (item: NavItem) => {'):s.index('  return (\n    <div className={"sh-app"')]
new_render = '''  const renderItem = (item: NavItem) => {
    const cls = "sh-nav-item" + (isActive(item) ? " is-active" : "");
    const label = (
      <>
        <span className="sh-nav-icon">{item.icon}</span>
        <span className="sh-nav-no">{item.no}</span>
        <span className="sh-nav-label">{item.label}</span>
      </>
    );
    if (item.onClick === "settings") {
      return (
        <button key={item.key} type="button" className={cls} onClick={onOpenSettings} title={item.label}>
          {label}
        </button>
      );
    }
    const href = item.needEpisode ? episodeHref(item.key) : item.href;
    if (!href) {
      return (
        <span key={item.key} className="sh-nav-item is-disabled" title="先在项目大厅选一个项目/一期">
          {label}
        </span>
      );
    }
    return (
      <a
        key={item.key}
        className={cls}
        href={href}
        title={item.label}
        aria-current={isActive(item) ? "page" : undefined}
      >
        {label}
      </a>
    );
  };

'''
s = s.replace(old_render, new_render, 1)

open(p, 'w', encoding='utf-8', newline='').write(s)
print('AppShell 8-item nav ok')

# ---------- 2) 模板墙组件 ----------
shutil.copyfile('tmp/ui_r5/TemplateWall.r7.tsx', 'frontend/src/components/library/TemplateWall.tsx')
print('TemplateWall installed')

# ---------- 3) App.tsx：写当前集 + 模板 tab 走模板墙 ----------
p = 'frontend/src/App.tsx'
s = open(p, encoding='utf-8').read()

# 3.1 记住当前集（一处 effect）
old = '  const viewRef = useRef(view);\n  viewRef.current = view;'
new = ('  const viewRef = useRef(view);\n  viewRef.current = view;\n'
       '  // R7：把「当前这一集」写进 localStorage，供一级导航 02–06 直达\n'
       '  useEffect(() => {\n'
       '    if (view.mode !== "workspace") return;\n'
       '    try {\n'
       '      localStorage.setItem("zpj_current_episode", view.episode.id);\n'
       '    } catch {\n'
       '      /* ignore */\n'
       '    }\n'
       '  }, [view]);')
assert s.count(old) == 1
s = s.replace(old, new, 1)

# 3.2 模板 tab → TemplateWall
old_lib = '''  if (view.mode === "library") {
    return (
      <LibraryPage'''
new_lib = '''  if (view.mode === "library" && view.tab === "templates") {
    return (
      <TemplateWall
        onProjectCreated={(project) => void handleProjectCreated(project)}
        onOpenSettings={openSettings}
      />
    );
  }

  if (view.mode === "library") {
    return (
      <LibraryPage'''
assert s.count(old_lib) == 1
s = s.replace(old_lib, new_lib, 1)

s = s.replace('import { LibraryPage, type LibraryTab } from "./components/library/LibraryPage";',
              'import { LibraryPage, type LibraryTab } from "./components/library/LibraryPage";\n'
              'import { TemplateWall } from "./components/library/TemplateWall";', 1)

open(p, 'w', encoding='utf-8', newline='').write(s)
print('App.tsx wired')
