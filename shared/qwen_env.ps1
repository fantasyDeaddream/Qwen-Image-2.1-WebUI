# ============================================================================
#  qwen_env.ps1 -- CLI front end for shared\QwenEnv.ps1
#
#  .bat files cannot dot-source PowerShell, so this wrapper prints the resolved
#  paths as KEY=VALUE lines on stdout (captured with `for /f`) and sends every
#  human-readable message to stderr so it never pollutes the capture.
#
#  Usage:
#     for /f "usebackq tokens=1,* delims==" %%A in (`powershell ... ^
#         -File "%SHARED%\qwen_env.ps1" -StartDir "%CD%" -NeedVenv -NeedModels`) ^
#     do set "%%A=%%B"
# ============================================================================
[CmdletBinding()]
param(
    [string] $StartDir = (Get-Location).Path,
    [switch] $NeedVenv,
    [switch] $NeedDiffusers,
    [switch] $NeedComfyLib,
    [switch] $NeedModels,
    [switch] $NeedWeights,
    [switch] $NeedComfy,
    [switch] $NeedComfyModels,
    [switch] $Json,
    [switch] $InstallGit,
    [switch] $Yes
)
# --- force UTF-8 stdio -------------------------------------------------------
# A Chinese Windows leaves the console at codepage 936, which would turn every
# CJK path this script prints into bytes the launcher cannot decode. Pin it.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }


$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'QwenEnv.ps1')

# Route logger output to stderr so stdout stays machine readable.
function Write-QwenLog {
    param([string]$Message, [ValidateSet('info','ok','warn','err','step')][string]$Level = 'info')
    $tag = switch ($Level) { 'ok' {'[ OK ]'} 'warn' {'[WARN]'} 'err' {'[FAIL]'} 'step' {'[ .. ]'} default {'[INFO]'} }
    [Console]::Error.WriteLine("$tag $Message")
}

if ($InstallGit) {
    $g = Install-QwenGit -AssumeYes:$Yes
    if ($g) { [Console]::Out.WriteLine("QWEN_GIT=$g") }
    exit 0
}

$found = Initialize-QwenEnv -StartDir $StartDir `
    -NeedVenv:$NeedVenv -NeedDiffusers:$NeedDiffusers -NeedComfyLib:$NeedComfyLib `
    -NeedModels:$NeedModels -NeedWeights:$NeedWeights -NeedComfy:$NeedComfy `
    -Quiet:$true

if ($NeedComfyModels -and $found.ComfyDir) {
    if (-not (Test-QwenComfyModels -ComfyDir $found.ComfyDir)) {
        [Console]::Error.WriteLine("[WARN] ComfyUI 存在但缺少 mixed 量化扩散模型")
    }
}

if ($Json) {
    $obj = [ordered]@{}
    foreach ($k in $found.Keys) { if ($found[$k]) { $obj[$k] = $found[$k] } }
    [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress))
    exit 0
}

foreach ($k in @('ProjectRoot','VenvDir','Python','ModelsCache','ComfyDir','OutputDir')) {
    if ($found.ContainsKey($k) -and $found[$k]) {
        $name = switch ($k) {
            'ProjectRoot' { 'QWEN_PROJECT_ROOT' }
            'VenvDir'     { 'QWEN_VENV_DIR' }
            'Python'      { 'QWEN_PYTHON' }
            'ModelsCache' { 'QWEN_MODELS_CACHE' }
            'ComfyDir'    { 'QWEN_COMFYUI_DIR' }
            'OutputDir'   { 'QWEN_OUTPUT_DIR' }
        }
        [Console]::Out.WriteLine("$name=$($found[$k])")
    }
}

# Report what is still missing so the caller can decide whether to offer a
# download (the caller is responsible for asking the user first).
$missing = @()
if ($NeedVenv -and -not $found.VenvDir) { $missing += 'venv' }
if ($NeedModels -and -not $found.ModelsCache) { $missing += 'models' }
if ($NeedComfy -and -not $found.ComfyDir) { $missing += 'comfyui' }
if ($missing.Count) { [Console]::Out.WriteLine("QWEN_MISSING=" + ($missing -join ',')) }
