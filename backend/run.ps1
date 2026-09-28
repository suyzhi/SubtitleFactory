<#
.SYNOPSIS
    字幕工厂 - 后端服务独立启动脚本 (PowerShell)
.DESCRIPTION
    准备虚拟环境与依赖后，以前台热重载模式启动 FastAPI 后端。
.NOTES
    请在 Windows PowerShell 5.1 或 PowerShell 7 下运行。脚本以 UTF-8 (带 BOM) 保存，
    以便 Windows PowerShell 5.1 正确解码其中的中文与 emoji。
#>

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

. (Join-Path $ScriptDir "..\scripts\windows\common.ps1")

Write-Host "🔤 字幕工厂 后端服务 (Windows)" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor DarkGray

$EnvFile = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
}

$VenvPython = Ensure-BackendVenv -BackendDir $ScriptDir

Write-ExternalToolHint -Name "ffmpeg" -Hint "音频提取、预览与视频导出需要它。可运行 'winget install Gyan.FFmpeg'，或设置环境变量 FFMPEG_PATH 指向 ffmpeg.exe。"

Write-Host "🚀 启动后端服务 (端口 $Port)..." -ForegroundColor Green
Write-Host "📖 API 文档: http://127.0.0.1:$Port/docs`n" -ForegroundColor DarkCyan

Push-Location $ScriptDir
try {
    & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
    # 把 uvicorn 的退出码透传给调用方（backend/run.bat 依赖 errorlevel 决定是否暂停）。
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
