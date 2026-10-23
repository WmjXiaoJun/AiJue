# 造片局 · 后端启动脚本（Windows PowerShell）
# 用法: powershell -ExecutionPolicy Bypass -File backend\run.ps1 [-Port 8090]
param(
    [int]$Port = 8090,
    [string]$HostAddress = "127.0.0.1",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = $env:PYTHON
if (-not $python) { $python = "python" }

Write-Host ("造片局后端启动: http://" + $HostAddress + ":" + $Port + "  (OpenAPI: /docs)") -ForegroundColor Yellow
Write-Host ("仓库根: " + $repoRoot)

$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $HostAddress,
    "--port", "$Port",
    "--app-dir", "backend"
)
if (-not $NoReload) { $uvicornArgs += "--reload" }

& $python @uvicornArgs
