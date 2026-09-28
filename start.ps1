<#
.SYNOPSIS
    字幕工厂 Windows 一键启动脚本 (开发/浏览器模式)
.DESCRIPTION
    检查并配置环境，同时启动 FastAPI 后端和 Vite 前端服务，并在浏览器中打开。
    按 Ctrl+C 自动安全停止前后端服务。
.NOTES
    请在 Windows PowerShell 5.1 或 PowerShell 7 下运行。脚本以 UTF-8 (带 BOM) 保存，
    以便 Windows PowerShell 5.1 正确解码其中的中文与 emoji。
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

# 日志与子进程输出都含中文，统一按 UTF-8 处理，避免中文 Windows 下出现乱码。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # 输出被重定向到管道时无法设置控制台编码，忽略即可。
}

$ScriptDir = $PSScriptRoot
$BackendDir = Join-Path $ScriptDir "backend"
$FrontendDir = Join-Path $ScriptDir "frontend"

. (Join-Path $ScriptDir "scripts\windows\common.ps1")

Write-Host "`n🎬 字幕工厂 - Windows 启动中..." -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor DarkGray

# 1. 检查或创建 backend/.env
$EnvFile = Join-Path $BackendDir ".env"
$EnvExample = Join-Path $BackendDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Write-Host "ℹ️ 未找到 backend/.env，正在从模板创建..." -ForegroundColor Yellow
    Copy-Item $EnvExample $EnvFile
}

# 2. 检查并准备 Python 虚拟环境与依赖
$VenvPython = Ensure-BackendVenv -BackendDir $BackendDir

# 3. 检查前端 node_modules
$NodeModules = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path $NodeModules)) {
    Write-Host "📦 安装前端依赖..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        npm install
    } finally {
        Pop-Location
    }
}

# 4. 外部工具提示（缺失不阻断：只有在导出/下载时才会用到）
Write-ExternalToolHint -Name "ffmpeg" -Hint "音频提取、预览与视频导出需要它。可运行 'winget install Gyan.FFmpeg'，或设置环境变量 FFMPEG_PATH 指向 ffmpeg.exe。"
Write-ExternalToolHint -Name "deno" -Hint "YouTube 下载的 JS 运行时。可运行 'winget install DenoLand.Deno'。"

# 5. 检查端口占用
foreach ($port in @(8000, 5173)) {
    $inUse = $false
    try {
        $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop
        $inUse = [bool]$connections
    } catch {
        # NetTCPIP 模块不可用时退回 netstat，避免把已占用端口误判为可用。
        $inUse = [bool](netstat -ano | Select-String -Pattern ":$port\s" | Select-String -Pattern "LISTENING")
    }
    if ($inUse) {
        Write-Host "❌ 端口 $port 已被占用，请先停止占用进程或原有服务。" -ForegroundColor Red
        exit 1
    }
}

# 6. 启动前后端服务
$BackendProc = $null
$FrontendProc = $null

function Cleanup-Services {
    Write-Host "`n🛑 正在停止字幕工厂服务..." -ForegroundColor Yellow
    # 前后端都可能派生同名子进程（vite → esbuild；uvicorn → worker），
    # 因此统一用 /T 结束整棵进程树，避免残留进程占用端口与文件。
    foreach ($proc in @($FrontendProc, $BackendProc)) {
        if ($proc -and -not $proc.HasExited) {
            & taskkill /F /T /PID $proc.Id 2>$null | Out-Null
        }
    }
    Write-Host "✨ 服务已安全退出。" -ForegroundColor Green
}

try {
    Write-Host "🚀 启动后端服务 (http://127.0.0.1:8000)..." -ForegroundColor DarkCyan
    $BackendStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $BackendStartInfo.FileName = $VenvPython
    $BackendStartInfo.Arguments = "-m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    $BackendStartInfo.WorkingDirectory = $BackendDir
    $BackendStartInfo.UseShellExecute = $false
    $BackendStartInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $BackendStartInfo.EnvironmentVariables["PYTHONUTF8"] = "1"
    $BackendProc = [System.Diagnostics.Process]::Start($BackendStartInfo)

    Write-Host "🚀 启动前端服务 (http://127.0.0.1:5173)..." -ForegroundColor DarkCyan
    $FrontendStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $FrontendStartInfo.FileName = "node.exe"
    $FrontendStartInfo.Arguments = "node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort"
    $FrontendStartInfo.WorkingDirectory = $FrontendDir
    $FrontendStartInfo.EnvironmentVariables["VITE_API_BASE_URL"] = "same-origin"
    $FrontendStartInfo.UseShellExecute = $false
    $FrontendProc = [System.Diagnostics.Process]::Start($FrontendStartInfo)

    # 7. 等待就绪
    $Ready = $false
    for ($i = 1; $i -le 60; $i++) {
        if ($BackendProc.HasExited -or $FrontendProc.HasExited) {
            Write-Host "❌ 服务异常退出，请检查依赖与日志。" -ForegroundColor Red
            break
        }
        try {
            $respApi = Invoke-WebRequest -Uri "http://127.0.0.1:8000/openapi.json" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            $respUi = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($respApi.StatusCode -eq 200 -and $respUi.StatusCode -eq 200) {
                $Ready = $true
                break
            }
        } catch {
            # 服务尚未监听，等下一轮重试。
        }
        Start-Sleep -Seconds 1
    }

    if (-not $Ready) {
        Write-Host "❌ 服务启动超时，正在清理..." -ForegroundColor Red
        exit 1
    }

    Write-Host "`n🎉 字幕工厂已就绪！" -ForegroundColor Green
    Write-Host "👉 访问地址: " -NoNewline
    Write-Host "http://127.0.0.1:5173" -ForegroundColor Cyan
    Write-Host "📖 后端 API: http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
    Write-Host "`n按 Ctrl+C 停止前后端服务。`n" -ForegroundColor DarkYellow

    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:5173"
    }

    while (-not $BackendProc.HasExited -and -not $FrontendProc.HasExited) {
        Start-Sleep -Seconds 1
    }
} finally {
    Cleanup-Services
}
