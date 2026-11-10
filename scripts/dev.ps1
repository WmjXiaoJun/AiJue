# 启动造片局后端（默认 http://127.0.0.1:8090）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== 造片局 · AI 微综艺 ==" -ForegroundColor Yellow
if (Test-Path "frontend\dist") { Write-Host "前端构建产物已存在：frontend\dist（由后端同源托管）" -ForegroundColor DarkGray }
Write-Host "API: http://127.0.0.1:8090/api/health" -ForegroundColor DarkGray
Write-Host "App: http://127.0.0.1:8090/" -ForegroundColor Green
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --app-dir backend
