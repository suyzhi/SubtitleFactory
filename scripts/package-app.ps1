<#
.SYNOPSIS
    构建 Windows 发布安装包（对应 macOS 的 scripts/package-app.sh）。
.DESCRIPTION
    1. 校验发布界面标记（源码 / Vite 产物 / 最终可执行文件）；
    2. 构建前端生产产物；
    3. 构建冻结后端 sidecar（含 FFmpeg / FFprobe / Deno）；
    4. 调用 tauri build --bundles nsis 产出安装包。
    任一校验失败即停止，不会产出半成品安装包。
.NOTES
    以 UTF-8 (带 BOM) 保存，便于 Windows PowerShell 5.1 正确解码中文。
#>

[CmdletBinding()]
param(
    [switch]$SkipSidecar,
    [switch]$SkipDeno
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $Root "frontend"
$TauriDir = Join-Path $FrontendDir "src-tauri"

$UiMarker = "subtitle-factory-ui:professional-v2"
$UiLayoutMarker = "subtitle-factory-ui:library-workspace-v2"
$OldUiMarker = "AISettingsDialog"

function Test-TextFileMarker {
    param([string]$Path, [string]$Marker)
    if (-not (Test-Path $Path)) { return $false }
    return ([System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)).Contains($Marker)
}

function Test-BinaryMarker {
    <#
    .SYNOPSIS
        在二进制文件（PE 可执行文件、打包后的 JS 产物）中按字节查找标记。
    #>
    param([string]$Path, [string]$Marker)
    if (-not (Test-Path $Path)) { return $false }
    # Latin1 保持字节到字符的一一对应，从而可以精确匹配 ASCII 标记。
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::GetEncoding(28591))
    return $text.Contains($Marker)
}

# Rust 刚安装完时，已经打开的会话 PATH 不会自动刷新；这里显式补上 cargo。
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    $cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
    if (Test-Path (Join-Path $cargoBin "cargo.exe")) {
        $env:PATH = "$cargoBin;$env:PATH"
    } else {
        throw "未找到 Rust 工具链（cargo）。请先安装：https://win.rustup.rs/ 或 winget install Rustlang.Rustup"
    }
}

# ── 1. 源码标记校验 ──
Write-Host "校验发布界面源码标记..." -ForegroundColor Cyan
$appTsx = Join-Path $FrontendDir "src\App.tsx"
$mainTsx = Join-Path $FrontendDir "src\main.tsx"
foreach ($marker in @($UiMarker, $UiLayoutMarker, "library-home", "workspace-active")) {
    if (-not (Test-TextFileMarker -Path $appTsx -Marker $marker)) {
        throw "App.tsx 缺少发布界面标记：$marker"
    }
}
if (-not (Test-TextFileMarker -Path $mainTsx -Marker "import App from './App.tsx'")) {
    throw "main.tsx 未使用发布入口链（import App from './App.tsx'）。"
}
if ((Test-TextFileMarker -Path $mainTsx -Marker $OldUiMarker) -or (Test-TextFileMarker -Path $appTsx -Marker $OldUiMarker)) {
    throw "发布入口错误地依赖旧 $OldUiMarker。"
}

# ── 2. 前端生产构建 ──
Write-Host "构建前端生产产物..." -ForegroundColor Cyan
Push-Location $FrontendDir
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
} finally {
    Pop-Location
}

$distDir = Join-Path $FrontendDir "dist"
if (-not (Test-BinaryMarker -Path (Join-Path $distDir "index.html") -Marker $UiMarker)) {
    # 标记可能只落在打包后的 JS 分块里，逐个扫描。
    $found = $false
    foreach ($file in Get-ChildItem -Path $distDir -Recurse -File) {
        if (Test-BinaryMarker -Path $file.FullName -Marker $UiMarker) { $found = $true; break }
    }
    if (-not $found) { throw "Vite 产物缺少 $UiMarker 标记。" }
}
$layoutFound = $false
foreach ($file in Get-ChildItem -Path $distDir -Recurse -File) {
    if (Test-BinaryMarker -Path $file.FullName -Marker $UiLayoutMarker) { $layoutFound = $true; break }
}
if (-not $layoutFound) { throw "Vite 产物缺少 $UiLayoutMarker 标记。" }
foreach ($file in Get-ChildItem -Path $distDir -Recurse -File) {
    if (Test-BinaryMarker -Path $file.FullName -Marker $OldUiMarker) {
        throw "Vite 产物包含旧 UI 标记：$OldUiMarker（$($file.Name)）。"
    }
}

# ── 3. 冻结后端 sidecar ──
if ($SkipSidecar) {
    Write-Host "按要求跳过 sidecar 构建。" -ForegroundColor DarkGray
    if (-not (Test-Path (Join-Path $TauriDir "backend-runtime\subtitle-backend.exe"))) {
        throw "缺少已构建的 backend-runtime，无法打包。"
    }
} else {
    Write-Host "构建冻结后端 sidecar..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "build-sidecar.ps1") -SkipDeno:$SkipDeno
    if ($LASTEXITCODE -ne 0) { throw "sidecar 构建失败。" }
}

# ── 4. Tauri NSIS 打包 ──
Write-Host "打包 Windows 安装程序（NSIS）..." -ForegroundColor Cyan
Push-Location $FrontendDir
try {
    & npx.cmd tauri build --bundles nsis
    if ($LASTEXITCODE -ne 0) { throw "tauri build 失败。" }
} finally {
    Pop-Location
}

$bundleDir = Join-Path $TauriDir "target\release\bundle\nsis"
if (-not (Test-Path $bundleDir)) { throw "未生成 NSIS 安装包目录：$bundleDir" }
$installer = Get-ChildItem -Path $bundleDir -File -Filter "*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "NSIS 目录中没有安装包。" }

# ── 5. 最终可执行文件标记校验 ──
$appExe = Get-ChildItem -Path (Join-Path $TauriDir "target\release") -File -Filter "*.exe" |
    Where-Object { $_.Name -notlike "*setup*" } |
    Sort-Object Length -Descending | Select-Object -First 1
if ($appExe) {
    Write-Host "校验最终可执行文件标记：$($appExe.Name)" -ForegroundColor Cyan
    if (-not (Test-BinaryMarker -Path $appExe.FullName -Marker $UiMarker)) {
        throw "最终可执行文件缺少 $UiMarker 标记。"
    }
    if (-not (Test-BinaryMarker -Path $appExe.FullName -Marker $UiLayoutMarker)) {
        throw "最终可执行文件缺少 $UiLayoutMarker 标记。"
    }
    if (Test-BinaryMarker -Path $appExe.FullName -Marker $OldUiMarker) {
        throw "最终可执行文件包含旧 UI 标记：$OldUiMarker"
    }
}

Write-Host ""
Write-Host "安装包已生成：" -ForegroundColor Green
Write-Host "  $($installer.FullName)  ($([math]::Round($installer.Length / 1MB, 1)) MB)" -ForegroundColor Green
