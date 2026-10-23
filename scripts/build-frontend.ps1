# 构建前端（产物 frontend/dist，由后端托管）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\frontend"
if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
npm run build
Write-Host "built -> frontend/dist" -ForegroundColor Green
