# -*- coding: utf-8 -*-
import os, subprocess

files = [
 'frontend/src/App.tsx',
 'frontend/src/components/library/LibraryPage.tsx',
 'frontend/src/components/agent/AgentPage.tsx',
 'frontend/src/components/characters/CharactersPage.tsx',
 'frontend/src/lib/agent.ts',
 'frontend/src/lib/templates.ts',
 'frontend/src/lib/v5.ts',
 'frontend/src/types.ts',
]
for f in files:
    b = open(f, 'rb').read()
    crlf = b.count(b'\r\n')
    lf = b.count(b'\n') - crlf
    print(f'{f:58s} CRLF={crlf:5d} LF={lf:5d}')
