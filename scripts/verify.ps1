# 一键自检
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== 1. Python tests ==" -ForegroundColor Yellow
python -m pytest tests -q
Write-Host "== 2. FFmpeg ==" -ForegroundColor Yellow
$ff = "$root\tools\node_modules\ffmpeg-static\ffmpeg.exe"
& $ff -version 2>&1 | Select-Object -First 1
Write-Host "== 3. 样例成片 ==" -ForegroundColor Yellow
$fp = "$root\tools\node_modules\ffprobe-static\bin\win32\x64\ffprobe.exe"
if (Test-Path "$root\outputs\demo\final.mp4") {
  & $fp -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration,size -of default=nw=1 "$root\outputs\demo\final.mp4"
} else { Write-Host "missing outputs/demo/final.mp4" -ForegroundColor Red }
Write-Host "== 4. API ==" -ForegroundColor Yellow
try { (Invoke-WebRequest "http://127.0.0.1:8090/api/health" -UseBasicParsing -TimeoutSec 5).Content } catch { Write-Host "backend not running (start scripts/dev.ps1)" -ForegroundColor DarkYellow }
