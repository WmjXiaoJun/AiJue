# 造片局 · 后端启动脚本（Windows PowerShell）
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File backend\run.ps1 [-Port 8090]
#   powershell -ExecutionPolicy Bypass -File backend\run.ps1 -Reload        # 开发热重载（会关闭任务恢复）
#   $env:ZAOPIANJU_JOBS_RECOVER = 0; ... -File backend\run.ps1             # 手动关闭导入期任务恢复
#
# 为什么默认**不带** --reload（审查 P1-3）:
#   仓库是活跃开发状态，uvicorn --reload 每保存一次文件就重新 import 一次 main.py；
#   而 main.py 在**导入期**跑 _recover_stale_jobs()，会把 queued/running 的任务一律标 failed。
#   于是"改一行代码 -> 上一次仍在渲的 GPU 任务被标失败（子进程还活着）"是必然路径。
#   需要热重载时显式加 -Reload；本脚本会自动置 ZAOPIANJU_JOBS_RECOVER=0 关掉那次恢复。
param(
    [int]$Port = 8090,
    [string]$HostAddress = "127.0.0.1",
    [switch]$Reload,
    [switch]$NoReload   # 历史参数，保留兼容：默认已经不带 --reload，本开关现在是无操作
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
if ($Reload) {
    $uvicornArgs += "--reload"
    # 热重载时不能做导入期任务恢复：上一次进程可能还在跑 GPU 任务，标 failed 就是误杀。
    $env:ZAOPIANJU_JOBS_RECOVER = "0"
    Write-Host "热重载已开启（--reload）：ZAOPIANJU_JOBS_RECOVER=0（导入期不再把在跑任务标 failed）" -ForegroundColor DarkYellow
} elseif ($NoReload) {
    Write-Host "-NoReload 已是默认行为（本参数保留兼容，可省略）" -ForegroundColor DarkGray
}

& $python @uvicornArgs
