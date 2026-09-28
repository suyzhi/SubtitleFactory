<#
.SYNOPSIS
    构建 Windows 版本的冻结后端 sidecar（对应 macOS 的 scripts/build-sidecar.sh）。
.DESCRIPTION
    用 PyInstaller 把 backend 打成 frontend\src-tauri\backend-runtime 下的
    onedir 运行包，与 Tauri 配置的 resources 布局保持一致：

        backend-runtime\
          subtitle-backend.exe      ← Tauri 启动的后端进程
          _internal\...             ← PyInstaller 运行库
          bin\ffmpeg.exe
          bin\ffprobe.exe
          bin\deno.exe
          THIRD_PARTY_LICENSES\...

    构建完成后会真跑一次 subtitle-backend.exe --verify-runtime 自检，
    自检不通过就直接失败，不会产出半成品。
.NOTES
    以 UTF-8 (带 BOM) 保存，便于 Windows PowerShell 5.1 正确解码中文。
#>

[CmdletBinding()]
param(
    [switch]$SkipDependencySync,
    [switch]$SkipDeno,
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"

# 与 macOS 脚本一致：桌面构建可能继承开发者工具的 Python 路径，绝不能流入依赖解析。
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue

$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$OutputDir = Join-Path $Root "frontend\src-tauri\backend-runtime"
$BuildDir = Join-Path $BackendDir "build\sidecar"
$DistDir = Join-Path $BackendDir "dist\sidecar"
$VendorDir = Join-Path $Root "vendor\win-x64"

if (-not (Test-Path $Python)) {
    throw "缺少 backend\.venv，请先运行 .\start.ps1 或 .\backend\run.ps1 安装依赖。"
}

# uv 创建的虚拟环境默认不带 pip，因此优先用 uv 安装，退回 pip。
$Uv = Get-Command uv -ErrorAction SilentlyContinue

function Install-BackendPackage {
    param([Parameter(Mandatory)][string[]]$PackageArgs)
    if ($Uv) {
        & $Uv.Source pip install --quiet @PackageArgs --python $Python
    } else {
        & $Python -m pip install --quiet @PackageArgs
    }
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败：$($PackageArgs -join ' ')" }
}

if (-not $SkipDependencySync) {
    Write-Host "同步后端依赖..." -ForegroundColor Cyan
    Install-BackendPackage -PackageArgs @("-r", (Join-Path $BackendDir "requirements.txt"))
}
if (-not (Test-Path (Join-Path $BackendDir ".venv\Lib\site-packages\PyInstaller"))) {
    Write-Host "安装 PyInstaller..." -ForegroundColor Cyan
    Install-BackendPackage -PackageArgs @("pyinstaller>=6.11")
}

# ── 1. PyInstaller 打包 ──
if ($SkipPyInstaller) {
    if (-not (Test-Path (Join-Path $DistDir "subtitle-backend\subtitle-backend.exe"))) {
        throw "指定了 -SkipPyInstaller，但 $DistDir 下没有可复用的产物。"
    }
    Write-Host "复用已有的 PyInstaller 产物。" -ForegroundColor DarkGray
} else {
Write-Host "打包后端 sidecar（首次约需数分钟）..." -ForegroundColor Cyan
$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir",
    "--name", "subtitle-backend",
    "--distpath", $DistDir,
    "--workpath", $BuildDir,
    "--specpath", $BuildDir,
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "tiktoken",
    "--hidden-import", "scipy.signal",
    "--collect-all", "sherpa_onnx",
    "--collect-all", "av",
    "--collect-all", "uvicorn",
    "--collect-all", "pysubs2",
    "--collect-all", "PIL",
    "--collect-all", "yt_dlp",
    "--hidden-import", "app.main",
    "--hidden-import", "app.services.downloader",
    "--exclude-module", "torch",
    "--exclude-module", "matplotlib",
    "--exclude-module", "tkinter",
    "--exclude-module", "mlx",
    "--exclude-module", "mlx_whisper",
    "--exclude-module", "mlx_qwen3_asr"
)
Push-Location $BackendDir
try {
    & $Python @pyInstallerArgs sidecar_main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败。" }
} finally {
    Pop-Location
}
}

$builtExe = Join-Path $DistDir "subtitle-backend\subtitle-backend.exe"
if (-not (Test-Path $builtExe)) { throw "PyInstaller 未生成 $builtExe" }

# ── 2. 装配到 Tauri resources 目录 ──
Write-Host "装配到 $OutputDir ..." -ForegroundColor Cyan
if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $DistDir "subtitle-backend\*") $OutputDir

$outputExe = Join-Path $OutputDir "subtitle-backend.exe"
if (-not (Test-Path $outputExe)) { throw "装配后的 sidecar 缺少 subtitle-backend.exe" }

# ── 3. 外部运行时二进制 ──
& (Join-Path $PSScriptRoot "fetch-ffmpeg-windows.ps1") -VendorDir $VendorDir -SkipDeno:$SkipDeno
$binDir = Join-Path $OutputDir "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item (Join-Path $VendorDir "ffmpeg.exe") (Join-Path $binDir "ffmpeg.exe") -Force
Copy-Item (Join-Path $VendorDir "ffprobe.exe") (Join-Path $binDir "ffprobe.exe") -Force

$licenseDir = Join-Path $OutputDir "THIRD_PARTY_LICENSES\ffmpeg"
New-Item -ItemType Directory -Force -Path $licenseDir | Out-Null
foreach ($name in @("LICENSE", "README.txt", "FFMPEG_VERSION.txt")) {
    $candidate = Join-Path $VendorDir $name
    if (Test-Path $candidate) { Copy-Item $candidate (Join-Path $licenseDir $name) -Force }
}

if (-not $SkipDeno) {
    $denoSource = Join-Path $VendorDir "deno.exe"
    if (-not (Test-Path $denoSource)) { throw "缺少 deno.exe，请先运行 scripts\fetch-ffmpeg-windows.ps1。" }
    Copy-Item $denoSource (Join-Path $binDir "deno.exe") -Force
}

# ── 4. 冻结运行时自检 ──
Write-Host "运行冻结后端自检..." -ForegroundColor Cyan
$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("subtitle-factory-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
$previousDataDir = $env:SUBTITLE_FACTORY_DATA_DIR
$env:SUBTITLE_FACTORY_DATA_DIR = $smokeRoot
try {
    & $outputExe --verify-runtime
    if ($LASTEXITCODE -ne 0) { throw "冻结后端自检失败（退出码 $LASTEXITCODE）。" }
} finally {
    if ($null -eq $previousDataDir) { Remove-Item Env:SUBTITLE_FACTORY_DATA_DIR -ErrorAction SilentlyContinue }
    else { $env:SUBTITLE_FACTORY_DATA_DIR = $previousDataDir }
    Remove-Item -Recurse -Force $smokeRoot -ErrorAction SilentlyContinue
}

Write-Host "已生成 Windows 冻结后端：$OutputDir" -ForegroundColor Green
Write-Host "  subtitle-backend.exe + bin\ffmpeg.exe + bin\ffprobe.exe$(if ($SkipDeno) { '' } else { ' + bin\deno.exe' })" -ForegroundColor DarkGray
