<#
.SYNOPSIS
  停止本机 ComfyUI（按命令行匹配 ComfyUI\main.py，不误杀其他 python 进程）。
#>
param([string]$ComfyRoot = $(if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { 'D:\BaiduSyncdisk\wmj_work\XJ\ComfyUI' }))
$ErrorActionPreference = 'Stop'
$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue | Where-Object {
  $_.CommandLine -and $_.CommandLine -match 'main\.py' -and
  ($_.CommandLine -like "*$ComfyRoot*" -or $_.CommandLine -like '*ComfyUI*main.py*')
}
if (-not $procs) { Write-Output "没有找到运行中的 ComfyUI（$ComfyRoot）"; exit 0 }
foreach ($p in $procs) {
  Write-Output "停止 pid=$($p.ProcessId): $($p.CommandLine)"
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Output "已停止。"
