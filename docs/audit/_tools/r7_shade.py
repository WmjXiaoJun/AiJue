# -*- coding: utf-8 -*-
p = 'frontend/src/styles/design.css'
s = open(p, encoding='utf-8').read()
block = '''
/* ---- R7 修正：封面之上的压暗层必须保持深色渐变（生成块把它压成了浅面，导致封面发白） */
:is(.pl-cover-shade, .pl-hero-shade, [class*="-shade"], .home-final-cap) {
  background: linear-gradient(180deg, rgba(8, 9, 13, 0.1) 35%, rgba(8, 9, 13, 0.82) 100%) !important;
}

/* 项目卡封面里的文字/角标在深色压暗层上：保持浅色字 */
:is(.pl-cover-format, .pl-cover-no) {
  color: #f5f7fa !important;
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + '\n' + block)
print('shade fix appended')
