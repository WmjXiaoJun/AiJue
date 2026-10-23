# Zaopianju backend - detached launcher (survives terminal/agent session exit).
# Usage:
#   powershell -ExecutionPolicy Bypass -File backend\run-detached.ps1            # start on 8090
#   powershell -ExecutionPolicy Bypass -File backend\run-detached.ps1 -Port 8091
#   powershell -ExecutionPolicy Bypass -File backend\run-detached.ps1 -Stop
# PID and logs live in backend/data/ (server-<port>.pid / .out.log / .err.log).
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 parses it correctly.
param(
    [int]$Port = 8090,
    [string]$HostAddress = "127.0.0.1",
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$backendDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $backendDir
$dataDir = Join-Path $backendDir "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$pidFile = Join-Path $dataDir ("server-" + $Port + ".pid")
$outLog = Join-Path $dataDir ("server-" + $Port + ".out.log")
$errLog = Join-Path $dataDir ("server-" + $Port + ".err.log")
$healthUrl = "http://" + $HostAddress + ":" + $Port + "/api/health"

function Stop-Server {
    $stopped = @()
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($oldPid) {
            Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
            $stopped += [int]$oldPid
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
        $stopped += $connection.OwningProcess
    }
    return ($stopped | Select-Object -Unique)
}

if ($Stop) {
    $stopped = Stop-Server
    Write-Host ("stopped port " + $Port + " (pid: " + ($stopped -join ",") + ")") -ForegroundColor Yellow
    exit 0
}

Stop-Server | Out-Null
Start-Sleep -Milliseconds 800
Set-Location $repoRoot
$python = $env:PYTHON
if (-not $python) { $python = "python" }
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $HostAddress,
    "--port", "$Port",
    "--app-dir", "backend"
)
$proc = Start-Process -FilePath $python -ArgumentList $uvicornArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Set-Content -Path $pidFile -Value $proc.Id
Write-Host ("backend started detached: pid=" + $proc.Id + "  url=" + $healthUrl) -ForegroundColor Green
Write-Host ("logs: " + $outLog + " / " + $errLog) -ForegroundColor DarkGray

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}
if ($healthy) {
    Write-Host ("health ok: " + $healthUrl) -ForegroundColor Green
} else {
    Write-Host "health check failed after start; see logs" -ForegroundColor Red
    exit 1
}
