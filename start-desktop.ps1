<#
.SYNOPSIS
    字幕工厂 - Windows 桌面应用启动脚本 (Tauri 桌面端)
#>

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$BackendDir = Join-Path $ScriptDir "backend"
$FrontendDir = Join-Path $ScriptDir "frontend"
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"

Write-Host "🎬 字幕工厂 - Windows 桌面端启动" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor DarkGray

# 1. 检查 Rust 环境
$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargo) {
    Write-Host "⚠️ 未检测到 cargo / rustc，正在检查默认用户安装路径..." -ForegroundColor Yellow
    $userCargo = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $userCargo "cargo.exe")) {
        $env:PATH = "$userCargo;$env:PATH"
    } else {
        Write-Host "❌ 未安装 Rust 工具链。Tauri 桌面端编译需要 Rust。" -ForegroundColor Red
        Write-Host "👉 请先安装 Rust: https://win.rustup.rs/ 或运行 'winget install Rustlang.Rustup'" -ForegroundColor Yellow
        Write-Host "💡 提示：您也可以直接运行 .\start.ps1 以浏览器模式原生使用字幕工厂！" -ForegroundColor Cyan
        exit 1
    }
}

# 2. 检查 Python 虚拟环境
if (-not (Test-Path $VenvPython)) {
    Write-Host "📦 创建 Python 虚拟环境..." -ForegroundColor Yellow
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        & uv venv --python 3.11 (Join-Path $BackendDir ".venv")
        & uv pip install -r (Join-Path $BackendDir "requirements.txt") --python $VenvPython
    } else {
        & python -m venv (Join-Path $BackendDir ".venv")
        & $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")
    }
}

# 3. 检查前端依赖
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "📦 安装前端依赖..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        npm install
    } finally {
        Pop-Location
    }
}

# 4. 启动 Tauri 桌面端
Write-Host "🚀 启动 Tauri 桌面应用..." -ForegroundColor Green
Push-Location $FrontendDir
try {
    npx tauri dev
} finally {
    Pop-Location
}
