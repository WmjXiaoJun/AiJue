# -*- coding: utf-8 -*-
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* ------------------------------------------------ 10. 新外壳/首页补充（R5 · 追加）
   这些类由 new AppShell / PlatformHome 使用，历史 CSS 里没有，单独补齐。 */
.sh-crumb {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.sh-crumb i {
  font-style: normal;
  color: var(--line-strong);
}

.sh-title-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.sh-menu-btn {
  display: none;
}

@media (max-width: 900px) {
  .sh-menu-btn {
    display: grid;
  }
}

.home-tpl-head {
  margin-bottom: var(--s-3);
}

.home-card {
  appearance: none;
}

.home-card * {
  pointer-events: none;
}

.home-card .ui-chip,
.home-card a {
  pointer-events: auto;
}

/* 卡片内的「看风格」这类内联链接（避免整卡被 button 吞掉点击） */
.home-card-foot .ui-chip {
  color: var(--text-dim);
}

/* 首页页头：标题下方留白更紧一点 */
.home-wrap > .ui-page-head {
  margin-bottom: 0;
}

/* 旧深色遮罩在浅色下过重（追加一次，覆盖生成块里的 rgba 映射） */
:is(.cm-modal-mask, .pl-modal-mask, .v18-mask) {
  background: color-mix(in srgb, #0b0e13 38%, transparent) !important;
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('appended')
