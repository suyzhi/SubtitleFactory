<#
.SYNOPSIS
    字幕工厂 Windows 一键启动脚本 (开发/浏览器模式)
.DESCRIPTION
    检查并配置环境，同时启动 FastAPI 后端和 Vite 前端服务，并在浏览器中打开。
    按 Ctrl+C 自动安全停止前后端服务。
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$BackendDir = Join-Path $ScriptDir "backend"
$FrontendDir = Join-Path $ScriptDir "frontend"
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"

Write-Host "`n🎬 字幕工厂 - Windows 启动中..." -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor DarkGray

# 1. 检查或创建 backend/.env
$EnvFile = Join-Path $BackendDir ".env"
$EnvExample = Join-Path $BackendDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Write-Host "ℹ️ 未找到 backend/.env，正在从模板创建..." -ForegroundColor Yellow
    Copy-Item $EnvExample $EnvFile
}

# 2. 检查并准备 Python 虚拟环境
if (-not (Test-Path $VenvPython)) {
    Write-Host "📦 初始化 Python 虚拟环境..." -ForegroundColor Yellow
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Host "  使用 uv 创建 Python 3.11 虚拟环境..." -ForegroundColor DarkGray
        & uv venv --python 3.11 (Join-Path $BackendDir ".venv")
        & uv pip install -r (Join-Path $BackendDir "requirements.txt") --python $VenvPython
    } else {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if (-not $py) {
            Write-Error "❌ 未找到 Python 或 uv，请先安装 Python 3.10+ (推荐 3.11): https://www.python.org/"
            exit 1
        }
        Write-Host "  使用系统 Python 创建虚拟环境..." -ForegroundColor DarkGray
        & python -m venv (Join-Path $BackendDir ".venv")
        & $VenvPython -m pip install --upgrade pip
        & $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")
    }
}

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

# 4. 检查端口占用
foreach ($port in @(8000, 5173)) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        Write-Host "❌ 端口 $port 已被占用，请先停止占用进程或原有服务。" -ForegroundColor Red
        exit 1
    }
}

# 5. 启动前后端服务
$BackendProc = $null
$FrontendProc = $null

function Cleanup-Services {
    Write-Host "`n🛑 正在停止字幕工厂服务..." -ForegroundColor Yellow
    if ($FrontendProc -and -not $FrontendProc.HasExited) {
        Stop-Process -Id $FrontendProc.Id -Force -ErrorAction SilentlyContinue
    }
    if ($BackendProc -and -not $BackendProc.HasExited) {
        # 递归终止 uvicorn 及其子进程
        & taskkill /F /T /PID $BackendProc.Id 2>$null
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
    $BackendProc = [System.Diagnostics.Process]::Start($BackendStartInfo)

    Write-Host "🚀 启动前端服务 (http://127.0.0.1:5173)..." -ForegroundColor DarkCyan
    $FrontendStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $FrontendStartInfo.FileName = "node.exe"
    $FrontendStartInfo.Arguments = "node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort"
    $FrontendStartInfo.WorkingDirectory = $FrontendDir
    $FrontendStartInfo.EnvironmentVariables["VITE_API_BASE_URL"] = "same-origin"
    $FrontendStartInfo.UseShellExecute = $false
    $FrontendProc = [System.Diagnostics.Process]::Start($FrontendStartInfo)

    # 6. 等待就绪
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
            Start-Sleep -Seconds 1
        }
    }

    if (-not $Ready) {
        Write-Host "❌ 服务启动超时，正在清理..." -ForegroundColor Red
        exit 1
    }

    Write-Host "`n🎉 字幕工厂已就绪！" -ForegroundColor Green
    Write-Host "👉 访问地址: " -NoNewline
    Write-Host "http://127.0.0.1:5173" -ForegroundColor Cyan -Underline
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
