# ============================================================================
#  qwen_launch.ps1 -- shared launcher for every strategy.
#
#  Resolves the python env and the models it needs by searching the strategy
#  folder, its sub folders and its parent folders. If everything is present it
#  exports the located directories as QWEN_* environment variables and starts
#  the app. If something is missing it says exactly what and offers to run the
#  matching setup script -- it never downloads anything by itself.
#
#  Usage:  pwsh -File shared\qwen_launch.ps1 -Strategy native [-ApiOnly] [-Yes]
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('native', 'mixed', 'mixed-vram', 'turbo', 'gguf')][string] $Strategy,
    [string] $PythonArgs = '',
    [switch] $ApiOnly,
    [switch] $NoBrowser,
    [switch] $Yes,
    # Anything the .bat wrapper forwards, e.g. `start.bat --api-only`.
    [Parameter(ValueFromRemainingArguments = $true)][string[]] $Rest = @()
)
# --- force UTF-8 stdio -------------------------------------------------------
# A Chinese Windows leaves the console at codepage 936, which would turn every
# CJK path this script prints into bytes the launcher cannot decode. Pin it.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }


foreach ($token in $Rest) {
    switch -Regex ($token) {
        '^--api-only$'   { $ApiOnly = $true;  continue }
        '^--no-browser$' { $NoBrowser = $true; continue }
        '^--yes$|^-y$'   { $Yes = $true;       continue }
        '^--'            { $PythonArgs = "$PythonArgs $token"; continue }
        default          { $PythonArgs = "$PythonArgs $token" }
    }
}

$ErrorActionPreference = 'Stop'
$ProjectSetupDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'setup'

. (Join-Path $PSScriptRoot 'QwenEnv.ps1')

function Write-Head([string]$text) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
}

$appDir = Join-Path (Split-Path -Parent $PSScriptRoot) $Strategy
if (-not (Test-Path -LiteralPath (Join-Path $appDir 'app.py'))) {
    Write-QwenLog "找不到策略目录中的 app.py：$appDir" err
    exit 1
}

Write-Head "Qwen-Image-2.1 WebUI  -  $Strategy"

# --------------------------------------------------------------------------
#  What does this strategy need?
# --------------------------------------------------------------------------
$needsComfy = $Strategy -in @('mixed', 'mixed-vram', 'turbo', 'gguf')
$needsDiffusers = $Strategy -eq 'native'

$found = Initialize-QwenEnv -StartDir $appDir `
    -NeedVenv -NeedDiffusers:$needsDiffusers -NeedComfyLib:$needsComfy `
    -NeedModels -NeedWeights:(!$needsComfy) -NeedComfy:$needsComfy

Write-Host ''
Write-QwenLog "strategy folder : $appDir" info
if ($found.ProjectRoot) { Write-QwenLog "project root    : $($found.ProjectRoot)" info }
if ($found.Python)      { Write-QwenLog "python          : $($found.Python)" info }
if ($found.ModelsCache) { Write-QwenLog "HF model cache  : $($found.ModelsCache)" info }
if ($found.ComfyDir)    { Write-QwenLog "ComfyUI         : $($found.ComfyDir)" info }

# --------------------------------------------------------------------------
#  Missing pieces -> offer the matching setup script (never auto-download)
# --------------------------------------------------------------------------
$missing = @()
if (-not $found.VenvDir) { $missing += 'python 环境 (venv)' }
if ($needsComfy) {
    if (-not $found.ComfyDir) { $missing += 'ComfyUI' }
    elseif (-not (Test-QwenComfyModels -ComfyDir $found.ComfyDir)) { $missing += 'ComfyUI 量化模型 (mixed)' }
} else {
    if (-not $found.ModelsCache) { $missing += 'HuggingFace 模型缓存' }
}

# The gguf strategy runs from its own isolated checkout (its ComfyUI-GGUF node
# must not leak into the primary ComfyUI). No setup_*.bat builds this, so check
# it explicitly and spell out the manual steps.
if ($Strategy -eq 'gguf') {
    $iso = Join-Path $appDir 'ComfyUI'
    if (-not (Test-Path -LiteralPath (Join-Path $iso 'main.py'))) {
        $missing += "隔离的 ComfyUI 检出：$iso"
    } elseif (-not (Test-Path -LiteralPath (Join-Path $iso 'custom_nodes\ComfyUI-GGUF\nodes.py'))) {
        $missing += "ComfyUI-GGUF 自定义节点：$iso\custom_nodes"
    } elseif (-not (Get-ChildItem (Join-Path $iso 'models\unet') -Filter '*.gguf' -ErrorAction SilentlyContinue)) {
        $missing += 'GGUF 扩散模型 qwen_image_2.1_turbo_Q4_K_M.gguf'
    }
}

if ($missing.Count) {    Write-Host ''
    Write-QwenLog ("缺少 / missing: " + ($missing -join '、')) warn
    # Pick the single setup script that fixes what is actually missing.
    if (-not $found.VenvDir)                            { $script = 'setup_env.bat' }
    elseif ($needsComfy -and -not $found.ComfyDir)       { $script = 'setup_comfyui.bat' }
    elseif ($needsComfy)                                { $script = 'setup_models_mixed.bat' }
    else                                                { $script = 'setup_models_native.bat' }
    $setupPath = Join-Path $ProjectSetupDir $script

    if ($Strategy -eq 'gguf') {
        Write-Host ''
        Write-Host '  gguf 策略使用隔离运行时，需要手动准备（不会影响其它策略）：' -ForegroundColor Yellow
        Write-Host '    1) 建隔离检出：见 gguf\README.md 的「首次准备」一节' -ForegroundColor Yellow
        Write-Host '    2) 下载 GGUF（3.90 GiB，ModelScope Abiray/Qwen-Image-2.1-Turbo-GGUF）：' -ForegroundColor Yellow
        Write-Host '       qwen_image_2.1_turbo_Q4_K_M.gguf  ->  gguf\ComfyUI\models\unet\' -ForegroundColor Yellow
        Write-Host '    3) 重新运行 start.bat' -ForegroundColor Yellow
        exit 3
    }    Write-Host ''
    Write-Host "  可以运行安装脚本处理：$setupPath" -ForegroundColor Yellow
    Write-Host "  安装脚本会在下载前询问你。" -ForegroundColor Yellow
    $ans = if ($Yes) { 'y' } else { Read-Host "  现在运行 $script ？/ Run it now? [y/N]" }
    if ($ans -match '^(y|Y|yes|YES|是)$') {
        if (Test-Path -LiteralPath $setupPath) {
            & cmd.exe /c "`"$setupPath`""
            Write-QwenLog "安装脚本返回 $LASTEXITCODE；请重新运行启动脚本。" warn
            exit $LASTEXITCODE
        }
        Write-QwenLog "找不到 $setupPath" err
    }
    Write-QwenLog '未启动。/ not starting.' warn
    exit 2
}

# --------------------------------------------------------------------------
#  Everything present -> export env vars and launch
# --------------------------------------------------------------------------
Write-Host ''
Write-QwenLog '离线环境已就绪，正在启动 / environment ready, starting' ok

$env:QWEN_PROJECT_ROOT = $found.ProjectRoot
$env:QWEN_OUTPUT_DIR   = $found.OutputDir
if ($found.ModelsCache) { $env:QWEN_MODELS_CACHE = $found.ModelsCache }
if ($found.ComfyDir)    { $env:QWEN_COMFYUI_DIR = $found.ComfyDir }
# make HF resolve against the located cache even when the app is started
# from somewhere unexpected
if ($found.ModelsCache) { $env:HF_HOME = $found.ModelsCache }

$args = @((Join-Path $appDir 'app.py'))
if ($ApiOnly)    { $args += '--api-only' }
if ($NoBrowser)  { $args += '--no-browser' }
if ($PythonArgs) { $args += ($PythonArgs -split '\s+' | Where-Object { $_ }) }

Write-QwenLog ("exec: " + $found.Python + ' ' + ($args -join ' ')) step
& $found.Python @args
exit $LASTEXITCODE
