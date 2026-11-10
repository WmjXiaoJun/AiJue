import re, sys

targets = [
 ('frontend/src/components/characters/CharactersPage.tsx', [504, 696, 926]),
 ('frontend/src/components/library/LibraryPage.tsx', [178, 198, 848]),
 ('frontend/src/components/studio/Templates3D.tsx', [100]),
 ('frontend/src/components/v5world/Render.tsx', [110, 118]),
 ('frontend/src/components/pipeline/PipelineConsole.tsx', [320]),
 ('frontend/src/components/pipeline/LongformPanel.tsx', [64, 72]),
 ('frontend/src/components/shell/AppShell.tsx', [150]),
 ('frontend/src/components/studio/Render.tsx', [150]),
 ('frontend/src/components/studio/Finishing.tsx', [272]),
 ('frontend/src/components/studio/Studio.tsx', [92]),
 ('frontend/src/components/studio/Storyboard.tsx', [145, 228]),
 ('frontend/src/components/cinema/Assemble.tsx', [198]),
 ('frontend/src/components/cinema/Jobs.tsx', [150, 162]),
 ('frontend/src/components/cinema/Templates.tsx', [170, 895]),
 ('frontend/src/components/cinema/CinemaStudio.tsx', [62, 76]),
]
for f, starts in targets:
    lines = open(f, encoding='utf-8').read().splitlines()
    print('=' * 100)
    print(f)
    for s in starts:
        lo = max(0, s - 22)
        hi = min(len(lines), s + 30)
        print(f'--- around line {s} (showing {lo+1}-{hi}) ---')
        for i in range(lo, hi):
            print(f'{i+1:5d}| {lines[i]}')
