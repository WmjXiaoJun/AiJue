# -*- coding: utf-8 -*-
p = 'frontend/src/components/platform/PlatformHome.tsx'
s = open(p, encoding='utf-8').read()

a = 'import { BrandMark, IconClock, IconFilm, IconSearch, IconTemplate } from "../icons";'
b = 'import { BrandMark, IconClock, IconSearch, IconTemplate } from "../icons";'
assert s.count(a) == 1, ('import', s.count(a))
s = s.replace(a, b)

c = '<IconFilm size={16} />'
d = '<IconFilm />'
assert s.count(c) == 1, ('film', s.count(c))
s = s.replace(c, d)

open(p, 'w', encoding='utf-8', newline='').write(s)
print('ok')
