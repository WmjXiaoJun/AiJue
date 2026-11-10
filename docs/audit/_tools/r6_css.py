# -*- coding: utf-8 -*-
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* =============================== 12. R6 减法：模板墙 + 去装饰 */
/* 全局去装饰：无渐变、无毛玻璃、无阴影堆叠 */
body,
.sh-app,
.sh-main,
.sh-content {
  background-image: none !important;
}

.sh-top {
  background: var(--bg) !important;
  backdrop-filter: none !important;
  border-bottom: 1px solid var(--line) !important;
}

.sh-rail {
  box-shadow: none !important;
  background: var(--bg) !important;
  border-right: 1px solid var(--line) !important;
}

:is(.ui-card, .wall-card, .home-card, .home-tpl, .home-final, .pl-panel, .v18-panel, .cm-panel, .ag-col-head, .ag-rail) {
  box-shadow: none !important;
  border-radius: var(--r-8) !important;
  background-image: none !important;
}

/* 4 项导航：竖向更松，去掉分组标题与状态行 */
.sh-rail-lite {
  gap: var(--s-2);
  padding-top: var(--s-3);
}

.sh-rail-lite .sh-groups {
  gap: 2px;
}

.sh-rail-lite .sh-nav-item {
  height: 40px;
}

/* ------------------------------- 模板墙 */
.wall {
  display: flex;
  flex-direction: column;
  gap: var(--s-3);
}

.wall-tools {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  flex-wrap: wrap;
}

.wall-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 var(--s-3);
  border: 1px solid var(--line);
  border-radius: var(--r-8);
  background: var(--surface);
  color: var(--text-mute);
  flex: 0 1 260px;
  min-width: 180px;
}

.wall-search input {
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font-size: var(--f-14);
  font-family: inherit;
  width: 100%;
}

.wall-filter {
  display: flex;
  gap: 6px;
}

.wall-filter button {
  height: 32px;
  padding: 0 var(--s-3);
  border-radius: var(--r-8);
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--text-dim);
  font-size: var(--f-13);
  font-family: inherit;
  cursor: pointer;
}

.wall-filter button.is-active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.wall-count {
  font-size: var(--f-13);
  color: var(--text-mute);
  margin-left: auto;
}

.wall-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(188px, 1fr));
  gap: var(--s-3);
}

.wall-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--r-8);
  background: var(--surface);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.12s ease;
}

.wall-card:hover {
  border-color: var(--accent);
}

.wall-card.is-current {
  border-color: var(--accent);
}

.wall-cover {
  aspect-ratio: 16 / 9;
  background: var(--surface-2);
  display: grid;
  place-items: center;
  overflow: hidden;
}

.wall-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.wall-letter {
  font-size: var(--f-28);
  color: var(--accent);
  opacity: 0.55;
}

.wall-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--s-2) var(--s-3) 0;
  min-width: 0;
}

.wall-name {
  font-size: var(--f-14);
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wall-sub {
  font-size: var(--f-12);
  color: var(--text-mute);
}

.wall-go {
  margin: var(--s-2) var(--s-3) var(--s-3);
  height: 30px;
  border-radius: var(--r-8);
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--text);
  font-size: var(--f-13);
  font-family: inherit;
  cursor: pointer;
}

.wall-go:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.wall-empty {
  padding: var(--s-6);
  text-align: center;
  color: var(--text-mute);
  font-size: var(--f-13);
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('r6 css appended')
