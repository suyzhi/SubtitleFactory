<#
.SYNOPSIS
    字幕工厂 Windows 启动辅助函数（由 start.ps1 / start-desktop.ps1 / backend/run.ps1 点源加载）。
.DESCRIPTION
    解决三类 Windows 专有问题：
    1. Microsoft Store 的 python.exe 应用执行别名（0 字节重解析点）会让 Get-Command python
       看起来存在，实际只能在商店里打开；这里改为 py -3.11 → py -3 → python 逐级真实执行校验。
    2. .venv 已存在时不再安装依赖，导致 git pull 后新增依赖永远装不上；这里用时间戳标记文件
       判断 requirements 是否比上次安装更新。
    3. 缺少 FFmpeg/Deno 时给出可执行的安装提示，而不是等到导出/下载时才报错。
#>

function Test-PythonLauncher {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @()
    )
    try {
        $null = & $Exe @Arguments -c "import sys" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Resolve-PythonLauncher {
    <#
    .SYNOPSIS
        返回可用于创建虚拟环境的 Python 启动器数组，例如 @('C:\Windows\py.exe','-3.11')。
        找不到可用解释器时返回空数组。
    #>
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($version in @('-3.11', '-3')) {
            if (Test-PythonLauncher -Exe $py.Source -Arguments @($version)) {
                return @($py.Source, $version)
            }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and (Test-PythonLauncher -Exe $python.Source)) {
        return @($python.Source)
    }
    return @()
}

function Ensure-BackendVenv {
    <#
    .SYNOPSIS
        创建（必要时）后端虚拟环境并保证依赖与 requirements 文件同步。
    .OUTPUTS
        虚拟环境内 python.exe 的完整路径。
    #>
    param(
        [Parameter(Mandatory)][string]$BackendDir,
        [string]$Requirements = 'requirements.txt'
    )

    $venvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
    $requirementsPath = Join-Path $BackendDir $Requirements
    $stampPath = Join-Path $BackendDir '.venv\.requirements.stamp'

    if (-not (Test-Path $requirementsPath)) {
        Write-Host "❌ 缺少依赖清单：$requirementsPath" -ForegroundColor Red
        exit 1
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue

    if (-not (Test-Path $venvPython)) {
        if ($uv) {
            Write-Host '📦 使用 uv 创建 Python 3.11 虚拟环境...' -ForegroundColor Yellow
            & uv venv --python 3.11 (Join-Path $BackendDir '.venv')
        } else {
            $launcher = Resolve-PythonLauncher
            if ($launcher.Count -eq 0) {
                Write-Host '❌ 未找到可用的 Python（3.10+）或 uv。' -ForegroundColor Red
                Write-Host '   请安装 Python 3.11：https://www.python.org/downloads/' -ForegroundColor Yellow
                Write-Host '   或安装 uv：https://docs.astral.sh/uv/' -ForegroundColor Yellow
                Write-Host '   注意：Microsoft Store 提供的 python 别名无法用于创建虚拟环境，' -ForegroundColor DarkGray
                Write-Host '   请关闭「设置 → 应用 → 高级应用设置 → 应用执行别名」中的 python 别名。' -ForegroundColor DarkGray
                exit 1
            }
            Write-Host '📦 使用系统 Python 创建虚拟环境...' -ForegroundColor Yellow
            $exe = $launcher[0]
            $launcherArgs = @()
            if ($launcher.Count -gt 1) {
                $launcherArgs = $launcher[1..($launcher.Count - 1)]
            }
            & $exe @launcherArgs -m venv (Join-Path $BackendDir '.venv')
        }
        if (-not (Test-Path $venvPython)) {
            Write-Host '❌ 虚拟环境创建失败。' -ForegroundColor Red
            exit 1
        }
    }

    # requirements.txt 比上次安装新（或从未安装）时才重新安装：既能在 git pull
    # 之后补齐新依赖，又不会让离线启动因为一次多余的 pip 调用而失败。
    $needsInstall = -not (Test-Path $stampPath)
    if (-not $needsInstall) {
        $needsInstall = (Get-Item $requirementsPath).LastWriteTimeUtc -gt (Get-Item $stampPath).LastWriteTimeUtc
    }

    if ($needsInstall) {
        Write-Host '📦 同步后端依赖...' -ForegroundColor Yellow
        if ($uv) {
            & uv pip install -r $requirementsPath --python $venvPython
        } else {
            & $venvPython -m pip install --upgrade pip --quiet
            & $venvPython -m pip install -r $requirementsPath --quiet
        }
        Set-Content -Path $stampPath -Value (Get-Date).ToUniversalTime().ToString('o') -Encoding ASCII
    }

    return $venvPython
}

function Write-ExternalToolHint {
    <#
    .SYNOPSIS
        缺少外部命令行工具时给出可执行的安装提示（不阻断启动）。
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Hint
    )
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        return
    }
    Write-Host "⚠️ 未检测到 $Name：$Hint" -ForegroundColor Yellow
}
