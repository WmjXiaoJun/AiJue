# Chrome headless 截图（用于最终交付预览）
param(
  [string]$Url = "http://127.0.0.1:8090/",
  [string]$Out = "outputs/screenshots/app.png",
  [int]$W = 1600,
  [int]$H = 1000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { throw "chrome not found: $chrome" }
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
& $chrome --headless=new --disable-gpu --hide-scrollbars --window-size="$W,$H" --virtual-time-budget=6000 --screenshot="$Out" $Url
Write-Host "screenshot -> $Out" -ForegroundColor Green
