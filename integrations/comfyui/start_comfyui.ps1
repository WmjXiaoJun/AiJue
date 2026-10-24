<#
.SYNOPSIS
  启动本机 ComfyUI（MiniMax H3）后台服务，日志写入 integrations/comfyui/logs/。
.DESCRIPTION
  关键点：ComfyUI 自定义节点加载时会打印 emoji/中文，如果把 stdout/stderr 重定向到文件，
  Python 3.13 默认按 GBK 编码写 stderr 会抛 UnicodeEncodeError 并导致启动崩溃，
  因此必须设置 PYTHONUTF8=1 / PYTHONIOENCODING=utf-8。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File integrations/comfyui/start_comfyui.ps1
#>
param(
  [string]$ComfyRoot = $(if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { 'D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI' }),
  [string]$Uri = 'http://127.0.0.1:8188',
  [int]$WaitSeconds = 300
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-Comfy {
  try { $r = Invoke-WebRequest -Uri "$Uri/system_stats" -TimeoutSec 3 -UseBasicParsing; return $r.StatusCode -eq 200 }
  catch { return $false }
}

if (Test-Comfy) { Write-Output "ComfyUI 已在线: $Uri"; exit 0 }

$py = Join-Path $ComfyRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = Join-Path $ComfyRoot '.venv/bin/python' }
if (-not (Test-Path $py)) { throw "找不到 ComfyUI venv python: $ComfyRoot" }
if (-not (Test-Path (Join-Path $ComfyRoot 'main.py'))) { throw "找不到 $ComfyRoot\main.py" }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = Join-Path $logDir "comfyui_$stamp.out.log"
$err = Join-Path $logDir "comfyui_$stamp.err.log"

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

$uriObj = [Uri]$Uri
$port = if ($uriObj.Port -gt 0) { $uriObj.Port } else { 8188 }
$proc = Start-Process -FilePath $py -ArgumentList 'main.py', '--listen', $uriObj.Host, '--port', "$port" -WorkingDirectory $ComfyRoot -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
Write-Output "已启动 ComfyUI pid=$($proc.Id)"
Write-Output "日志: $out"
Write-Output "      $err"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
  if ($proc.HasExited) { throw "ComfyUI 启动后退出 exit=$($proc.ExitCode)，见 $err" }
  if (Test-Comfy) { Write-Output "ComfyUI 就绪: $Uri"; exit 0 }
  Start-Sleep -Seconds 2
}
throw "等待 ComfyUI 就绪超时 ($WaitSeconds s)，见 $err"
