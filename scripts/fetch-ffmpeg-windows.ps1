<#
.SYNOPSIS
    下载并缓存 Windows x64 发布版运行时二进制（FFmpeg / FFprobe / Deno）。
.DESCRIPTION
    对应 macOS 的 scripts/fetch-ffmpeg.sh：发布包需要自带 FFmpeg/FFprobe，
    直装版还需要 Deno 作为 YouTube 下载的 JS 运行时。这里把外部可执行文件
    放进 vendor\win-x64，供 scripts\build-sidecar.ps1 装配到
    frontend\src-tauri\backend-runtime\bin。
.NOTES
    以 UTF-8 (带 BOM) 保存，便于 Windows PowerShell 5.1 正确解码中文。
#>

[CmdletBinding()]
param(
    [string]$VendorDir = "",
    [string]$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    [string]$DenoUrl = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
    [switch]$SkipDeno,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $VendorDir) {
    $VendorDir = Join-Path $Root "vendor\win-x64"
}
$CacheDir = Join-Path $VendorDir "cache"
New-Item -ItemType Directory -Force -Path $VendorDir, $CacheDir | Out-Null

function Get-RemoteArchive {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$Destination
    )
    if ((Test-Path $Destination) -and -not $Force) {
        Write-Host "  已缓存 $(Split-Path -Leaf $Destination)" -ForegroundColor DarkGray
        return
    }
    Write-Host "  下载 $Url" -ForegroundColor Yellow
    $partial = "$Destination.part"
    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing
    Move-Item -Force $partial $Destination
}

# ── FFmpeg / FFprobe ──
$ffmpegExe = Join-Path $VendorDir "ffmpeg.exe"
$ffprobeExe = Join-Path $VendorDir "ffprobe.exe"
if ($Force -or -not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
    Write-Host "准备 FFmpeg..." -ForegroundColor Cyan
    $archive = Join-Path $CacheDir "ffmpeg-win64-essentials.zip"
    Get-RemoteArchive -Url $FfmpegUrl -Destination $archive
    $extract = Join-Path $CacheDir "ffmpeg-extract"
    Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
    Expand-Archive -Path $archive -DestinationPath $extract -Force
    $binary = Get-ChildItem -Path $extract -Recurse -File -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $binary) { throw "FFmpeg 压缩包中找不到 ffmpeg.exe" }
    $binDir = $binary.Directory
    Copy-Item (Join-Path $binDir "ffmpeg.exe") $ffmpegExe -Force
    Copy-Item (Join-Path $binDir "ffprobe.exe") $ffprobeExe -Force
    foreach ($name in @("LICENSE", "README.txt")) {
        $candidate = Join-Path $binDir $name
        if (-not (Test-Path $candidate)) {
            $candidate = Get-ChildItem -Path $extract -Recurse -File -Filter $name | Select-Object -First 1 -ExpandProperty FullName
        }
        if ($candidate -and (Test-Path $candidate)) {
            Copy-Item $candidate (Join-Path $VendorDir $name) -Force
        }
    }
    Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
} else {
    Write-Host "FFmpeg 已存在，跳过下载。" -ForegroundColor DarkGray
}

$ffmpegVersion = (& $ffmpegExe -hide_banner -version | Select-Object -First 1)
if (-not $ffmpegVersion) { throw "ffmpeg.exe 无法执行版本探测。" }
Write-Host "  $ffmpegVersion" -ForegroundColor DarkGray
Set-Content -Path (Join-Path $VendorDir "FFMPEG_VERSION.txt") -Value $ffmpegVersion -Encoding UTF8

# ── Deno ──
$denoExe = Join-Path $VendorDir "deno.exe"
if ($SkipDeno) {
    Write-Host "按要求跳过 Deno。" -ForegroundColor DarkGray
} elseif ($Force -or -not (Test-Path $denoExe)) {
    Write-Host "准备 Deno..." -ForegroundColor Cyan
    $archive = Join-Path $CacheDir "deno-win64.zip"
    Get-RemoteArchive -Url $DenoUrl -Destination $archive
    $extract = Join-Path $CacheDir "deno-extract"
    Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
    Expand-Archive -Path $archive -DestinationPath $extract -Force
    $binary = Get-ChildItem -Path $extract -Recurse -File -Filter "deno.exe" | Select-Object -First 1
    if (-not $binary) { throw "Deno 压缩包中找不到 deno.exe" }
    Copy-Item $binary.FullName $denoExe -Force
    Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
} else {
    Write-Host "Deno 已存在，跳过下载。" -ForegroundColor DarkGray
}
if (Test-Path $denoExe) {
    Write-Host "  $(& $denoExe --version | Select-Object -First 1)" -ForegroundColor DarkGray
}

Write-Host "Windows 发布运行时已就绪：$VendorDir" -ForegroundColor Green
