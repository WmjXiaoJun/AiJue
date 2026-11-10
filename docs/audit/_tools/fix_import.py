# -*- coding: utf-8 -*-
p = 'frontend/src/components/platform/PlatformHome.tsx'
s = open(p, encoding='utf-8').read()
old = 'import { BrandMark, IconClock, IconSearch, IconTemplate } from "../icons";'
new = 'import { BrandMark, IconClock, IconFilm, IconSearch, IconTemplate } from "../icons";'
assert s.count(old) == 1, s.count(old)
open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
print('ok')
