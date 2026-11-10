# -*- coding: utf-8 -*-
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* ========================= 13. R7 一级导航（8 项）+ 模板墙页 */
.sh-nav-no {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-mute);
  flex: 0 0 18px;
  width: 18px;
}

.sh-app:not(.is-open) .sh-nav-no {
  display: none;
}

.sh-nav-item.is-disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.sh-nav-item.is-disabled:hover {
  background: transparent;
  color: var(--text-dim);
}

/* 模板墙卡片按钮允许禁用态 */
.wall-go:disabled {
  opacity: 0.55;
  cursor: default;
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('r7 css appended')
