# -*- coding: utf-8 -*-
"""R8：把 MuseTemplateCard 接进生成台（Render.tsx）+ 样式。"""
p = 'frontend/src/components/studio/Render.tsx'
s = open(p, encoding='utf-8').read()

# 1) import
old_imp = 'import type { StudioTabProps } from "../../lib/studio";'
new_imp = ('import type { StudioTabProps } from "../../lib/studio";\n'
           'import { MuseTemplateCard } from "./MuseTemplateCard";')
assert s.count(old_imp) == 1
s = s.replace(old_imp, new_imp, 1)

# 2) 解构 project
old_sig = 'export function StudioRender({ episode, onNavigate }: StudioTabProps) {'
new_sig = 'export function StudioRender({ project, episode, onNavigate }: StudioTabProps) {'
assert s.count(old_sig) == 1
s = s.replace(old_sig, new_sig, 1)

# 3) 卡片插在页头之后、KPI 之前
old_body = '''      </header>

      <section className="st-kpis">'''
new_body = '''      </header>

      <MuseTemplateCard project={project} episode={episode} />

      <section className="st-kpis">'''
assert s.count(old_body) == 1
s = s.replace(old_body, new_body, 1)
open(p, 'w', encoding='utf-8', newline='').write(s)
print('Render.tsx wired')

# 4) 样式
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* ============================ 14. R8 生成台「云端 Muse」卡片 */
.muse-gen {
  margin-bottom: var(--s-4);
}

.muse-gen-desc {
  margin: 0 0 var(--s-3);
  font-size: var(--f-13);
  color: var(--text-dim);
}

.muse-gen-controls {
  display: flex;
  align-items: flex-end;
  gap: var(--s-3);
  flex-wrap: wrap;
}

.muse-gen-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--f-12);
  color: var(--text-mute);
}

.muse-gen-field input {
  width: 96px;
  height: 30px;
  padding: 0 var(--s-2);
  border: 1px solid var(--line);
  border-radius: var(--r-8);
  background: var(--surface);
  color: var(--text);
  font-size: var(--f-14);
  font-family: var(--font-mono);
}

.muse-gen-field em {
  font-style: normal;
  font-size: var(--f-12);
  color: var(--text-mute);
}

.muse-gen-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  font-size: var(--f-13);
  color: var(--text-dim);
  cursor: pointer;
}

.muse-gen-switch input {
  width: 15px;
  height: 15px;
  accent-color: var(--accent);
}

.muse-gen-hint {
  font-size: var(--f-12);
  color: var(--text-mute);
}

.muse-gen-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: var(--s-3) 0 0;
  font-size: var(--f-13);
  color: var(--warn);
}

.muse-gen-notice.is-err {
  color: var(--err);
}

.muse-gen-status {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  margin-top: var(--s-3);
  padding-top: var(--s-3);
  border-top: 1px solid var(--line);
  font-size: var(--f-13);
  color: var(--text-dim);
}

.muse-gen-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-mute);
  flex: 0 0 8px;
}

.muse-gen-dot.is-run {
  background: var(--warn);
  animation: muse-pulse 1.2s ease-in-out infinite;
}

.muse-gen-dot.is-ok {
  background: var(--ok);
}

.muse-gen-dot.is-err {
  background: var(--err);
}

@keyframes muse-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.muse-gen-stage {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.muse-gen-count {
  font-family: var(--font-mono);
  font-size: var(--f-12);
  color: var(--text-mute);
}

.muse-gen-logs-toggle {
  border: 0;
  background: transparent;
  color: var(--accent);
  font-size: var(--f-12);
  cursor: pointer;
  font-family: inherit;
}

.muse-gen-logs {
  margin: var(--s-2) 0 0;
  padding: var(--s-2);
  max-height: 132px;
  overflow: auto;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-8);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-dim);
  white-space: pre-wrap;
}

.muse-gen-fail {
  margin-top: var(--s-2);
  font-size: var(--f-12);
  color: var(--err);
}

.muse-gen-fail p {
  margin: 2px 0;
}

.muse-gen-done {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  flex-wrap: wrap;
  margin-top: var(--s-3);
}

.ui-btn.is-disabled {
  opacity: 0.5;
  pointer-events: none;
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('css ok')
