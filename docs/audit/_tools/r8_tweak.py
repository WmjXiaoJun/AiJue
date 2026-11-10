# -*- coding: utf-8 -*-
p = 'frontend/src/components/studio/MuseTemplateCard.tsx'
s = open(p, encoding='utf-8').read()
old = '          <em>{Math.max(1, Number(segments) || 0) * SEGMENT_SECONDS} 秒 ≈ {Math.max(1, Number(segments) || 0)} 段 × 10s</em>'
new = '          <em>≈ {Math.max(1, Math.round(Number(segments) || 0)) * SEGMENT_SECONDS} 秒</em>'
assert s.count(old) == 1
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('hint deduped')
