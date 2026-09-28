<#
.SYNOPSIS
    字幕工厂 - Windows 桌面应用启动脚本 (Tauri 桌面端)
.DESCRIPTION
    准备 Rust / Python / Node 环境后以开发模式启动 Tauri 桌面端。
    退出时按仓库约定清理可重建的构建产物。
.NOTES
    请在 Windows PowerShell 5.1 或 PowerShell 7 下运行。脚本以 UTF-8 (带 BOM) 保存，
    以便 Windows PowerShell 5.1 正确解码其中的中文与 emoji。
#>

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$BackendDir = Join-Path $ScriptDir "backend"
$FrontendDir = Join-Path $ScriptDir "frontend"
$TauriDir = Join-Path $FrontendDir "src-tauri"

. (Join-Path $ScriptDir "scripts\windows\common.ps1")

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

# 2. 检查或创建 backend/.env
$EnvFile = Join-Path $BackendDir ".env"
$EnvExample = Join-Path $BackendDir ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Write-Host "ℹ️ 未找到 backend/.env，正在从模板创建..." -ForegroundColor Yellow
    Copy-Item $EnvExample $EnvFile
}

# 3. 检查 Python 虚拟环境与依赖
$VenvPython = Ensure-BackendVenv -BackendDir $BackendDir

# 4. 检查前端依赖
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "📦 安装前端依赖..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        npm install
    } finally {
        Pop-Location
    }
}

# 5. 确保 Tauri 的 resources 目录存在。
#    tauri.conf.json 的 bundle.resources 引用了 frontend/src-tauri/backend-runtime，
#    该目录被 .gitignore 忽略、只能由 scripts/build-sidecar.sh (macOS) 产出。
#    Tauri 在编译期解析 resources 清单，目录缺失会导致 cargo/tauri 直接失败，
#    因此这里先建出空目录（与 .github/workflows/ci.yml 的做法一致）。
$RuntimeDir = Join-Path $TauriDir "backend-runtime"
if (-not (Test-Path $RuntimeDir)) {
    Write-Host "ℹ️ 创建 Tauri resources 占位目录 backend-runtime..." -ForegroundColor DarkGray
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
}

function Cleanup-BuildProducts {
    <#
    .SYNOPSIS
        按 AGENTS.md 的约定清理可重建产物（不触碰项目数据、.venv 与 node_modules）。
    #>
    $targets = @(
        (Join-Path $FrontendDir "dist"),
        (Join-Path $TauriDir "target"),
        (Join-Path $TauriDir "backend-runtime"),
        (Join-Path $BackendDir "build"),
        (Join-Path $BackendDir "dist"),
        (Join-Path $BackendDir ".pytest_cache")
    )
    foreach ($target in $targets) {
        if (Test-Path $target) {
            Remove-Item -Recurse -Force -Path $target -ErrorAction SilentlyContinue
        }
    }
    Get-ChildItem -Path $BackendDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# 6. 启动 Tauri 桌面端
Write-Host "🚀 启动 Tauri 桌面应用..." -ForegroundColor Green
Push-Location $FrontendDir
try {
    # 使用 npx.cmd：npm 的 npx.ps1 垫片会被默认的 Restricted 执行策略拦下。
    & npx.cmd tauri dev
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ tauri dev 退出码：$LASTEXITCODE" -ForegroundColor Red
    }
} finally {
    Pop-Location
    Write-Host "🧹 清理构建产物..." -ForegroundColor DarkGray
    Cleanup-BuildProducts
}
