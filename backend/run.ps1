<#
.SYNOPSIS
    字幕工厂 - 后端服务独立启动脚本 (PowerShell)
#>

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

Write-Host "🔤 字幕工厂 后端服务 (Windows)" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor DarkGray

if (-not (Test-Path $VenvPython)) {
    Write-Host "📦 创建虚拟环境..." -ForegroundColor Yellow
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        & uv venv --python 3.11 (Join-Path $ScriptDir ".venv")
        & uv pip install -r (Join-Path $ScriptDir "requirements.txt") --python $VenvPython
    } else {
        & python -m venv (Join-Path $ScriptDir ".venv")
        & $VenvPython -m pip install -r (Join-Path $ScriptDir "requirements.txt")
    }
}

$EnvFile = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
}

Write-Host "🚀 启动后端服务 (端口 $Port)..." -ForegroundColor Green
Write-Host "📖 API 文档: http://127.0.0.1:$Port/docs`n" -ForegroundColor DarkCyan

Push-Location $ScriptDir
try {
    & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
} finally {
    Pop-Location
}
