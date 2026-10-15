# ============================================================================
#  qwen_setup.ps1 -- all environment / model installation for this project.
#
#  One script, four actions, because they share the same discovery and mirror
#  logic (and used to be five near-duplicate .bat files):
#
#     -Action env        create the venv, install torch + diffusers + the app deps
#     -Action comfyui    clone/patch ComfyUI and install its filtered requirements
#     -Action models     download models from ModelScope (asks first)
#     -Action fix-torch  repair a broken torch/torchaudio cu130 install
#     -Action status     report what is present and where
#
#  Every action first searches the script folder, its sub folders and several
#  parent folders for an existing suitable environment (item 6) and reuses it,
#  caching the located directories as QWEN_* environment variables. git and
#  Python are bootstrapped automatically (item 5) -- the user never has to
#  install them by hand. Nothing is downloaded without an explicit yes.
# ============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('env', 'comfyui', 'models-mixed', 'models-native', 'fix-torch', 'status', 'probe')][string] $Action,
    [string] $StartDir = '',
    [switch] $Yes,
    [switch] $WithGguf,
    # Install into this folder instead of next to this script: .venv, ComfyUI
    # and models_cache are created directly inside it. Discovery also starts
    # here, so re-running after a partial install finds what it created.
    [string] $InstallRoot = ''
)

# --- resolve the default start directory -------------------------------------
# `$PSScriptRoot` is EMPTY while param() defaults are being evaluated on Windows
# PowerShell 5.1 (PowerShell 7 fixed this), so a default of `= $PSScriptRoot`
# silently produced ''. That made every later Join-Path fail with
#   Cannot bind argument to parameter 'Path' because it is an empty string
# and only machines without PowerShell 7 ever hit it. Resolve it here instead.
if (-not $StartDir) { $StartDir = $PSScriptRoot }
if (-not $StartDir) { $StartDir = (Get-Location).ProviderPath }
$StartDir = [System.IO.Path]::GetFullPath($StartDir)
# --- force UTF-8 stdio -------------------------------------------------------
# A Chinese Windows leaves the console at codepage 936, which would turn every
# CJK path this script prints into bytes the launcher cannot decode. Pin it.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }


$ErrorActionPreference = 'Stop'
$Shared = Join-Path (Split-Path -Parent $PSScriptRoot) 'shared'
. (Join-Path $Shared 'QwenEnv.ps1')

function Write-Head([string]$text) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
}

# Discovery starts at the install root when one was requested, so a second run
# finds what the first run put there.
$SearchDir = if ($InstallRoot) { $InstallRoot } else { $StartDir }
$Mirrors = Get-QwenMirrors
$PipIndex = $Mirrors.pip_primary
$PipFallback = $Mirrors.pip_fallback

function Invoke-Pip {
    param([string]$Python, [string[]]$PipArgs, [string]$Why)
    $base = @('-m', 'pip', 'install', '--disable-pip-version-check')
    foreach ($index in @($PipIndex, $PipFallback, $Mirrors.pip_last_resort)) {
        if (-not $index) { continue }
        Write-QwenLog "$Why  (index: $index)" step
        & $Python @base @PipArgs -i $index
        if ($LASTEXITCODE -eq 0) { return $true }
        Write-QwenLog "index $index failed, trying the next one" warn
    }
    return $false
}

# ---------------------------------------------------------------------------
function Resolve-Python311 {
    $py = Get-QwenPython311
    if ($py) { Write-QwenLog "found Python 3.11: $py" ok; return $py }

    Write-QwenLog '未找到 Python 3.11，需要自动下载安装 / Python 3.11 not found' warn
    $ver = $Mirrors.python_version
    $file = "python-$ver-amd64.exe"
    $url = "$($Mirrors.python_release)/$ver/$file"
    if (-not (Confirm-QwenDownload -What "Python $ver (官方安装包，静默按当前用户安装)" `
                -Source $url -ApproxSize '~25 MB' -AssumeYes:$Yes)) { return $null }

    $tools = Get-QwenToolsDir
    $exe = Join-Path $tools $file
    Write-QwenLog "downloading $url" step
    Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing -TimeoutSec 600
    Write-QwenLog 'installing silently (per-user, no admin needed)' step
    # PrependPath=0: we never touch the user's PATH. Include_launcher=1 gives us `py`.
    $args = @('/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=1',
              'Include_pip=1', 'Include_test=0', 'SimpleInstall=1')
    $p = Start-Process -FilePath $exe -ArgumentList $args -Wait -PassThru
    Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) {
        Write-QwenLog "Python 安装程序返回 $($p.ExitCode)" err
        return $null
    }
    $py = Get-QwenPython311
    if ($py) { Write-QwenLog "Python installed: $py" ok; return $py }
    Write-QwenLog '安装后仍未找到 Python 3.11' err
    return $null
}

# ---------------------------------------------------------------------------
function Invoke-Env {
    Write-Head '环境配置 / environment setup'

    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedDiffusers
    if ($found.VenvDir) {
        Write-Host ''
        Write-QwenLog "已经存在可用的 Python 环境：$($found.VenvDir)" ok
        Write-Host "  该目录已缓存为 QWEN_VENV_DIR。" -ForegroundColor Gray
        $ans = if ($Yes) { 'n' } else { Read-Host '  是否仍然重新安装依赖？[y/N]' }
        if ($ans -notmatch '^(y|Y|yes|YES|是)$') {
            Write-QwenLog '沿用现有环境，未改动。/ keeping the existing environment.' ok
            return
        }
    }

    $git = Get-QwenGit -Quiet
    if (-not $git) { $null = Install-QwenGit -AssumeYes:$Yes }

    $py = Resolve-Python311
    if (-not $py) { throw '无法获得 Python 3.11，环境配置中止。' }

    $venv = if ($found.VenvDir) { $found.VenvDir }
    elseif ($InstallRoot) { Join-Path $InstallRoot '.venv' }
    else { Join-Path $StartDir '..\.venv' }
    $venv = [System.IO.Path]::GetFullPath($venv)
    if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
        Write-QwenLog "creating venv: $venv" step
        & $py -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败" }
    }
    $vpy = Join-Path $venv 'Scripts\python.exe'
    Set-QwenEnvVar 'QWEN_VENV_DIR' $venv
    Set-QwenEnvVar 'QWEN_PYTHON' $vpy

    if (-not (Invoke-Pip -Python $vpy -Why 'upgrading pip/setuptools/wheel' `
                -PipArgs @('--upgrade', 'pip', 'setuptools', 'wheel'))) {
        throw 'pip 自更新失败'
    }

    # torch / torchvision / torchaudio from the CUDA 13.0 wheels
    $torchIndex = $Mirrors.pytorch_index
    Write-QwenLog "installing torch 2.10.0 + torchvision 0.25.0 (cu130)" step
    & $vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall `
        'torch==2.10.0' 'torchvision==0.25.0' --index-url $torchIndex
    if ($LASTEXITCODE -ne 0) {
        Write-QwenLog "国内 pytorch 镜像失败，回退官方源 / falling back to $($Mirrors.pytorch_fallback)" warn
        & $vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall `
            'torch==2.10.0' 'torchvision==0.25.0' --index-url $Mirrors.pytorch_fallback
    }
    & $vpy -m pip install --disable-pip-version-check --upgrade --no-deps `
        'torchaudio==2.10.0+cu130' --index-url $torchIndex

    # application requirements
    $req = Join-Path (Split-Path -Parent $PSScriptRoot) 'requirements.txt'
    if (Test-Path -LiteralPath $req) {
        if (-not (Invoke-Pip -Python $vpy -Why 'installing requirements.txt' -PipArgs @('-r', $req))) {
            Write-QwenLog 'requirements.txt 安装失败' warn
        }
    }

    # diffusers: the project needs a build with QwenImage21Pipeline. A released
    # wheel is preferred; the git build is the fallback (and is what the
    # original installer used). Override with QWEN_DIFFUSERS_SPEC.
    $spec = $env:QWEN_DIFFUSERS_SPEC
    if (-not $spec) { $spec = 'git+https://github.com/huggingface/diffusers.git' }
    Write-QwenLog "installing diffusers from: $spec" step
    if (-not (Invoke-Pip -Python $vpy -Why 'installing diffusers' -PipArgs @('--upgrade', $spec))) {
        Write-QwenLog 'diffusers 安装失败，请手动处理' warn
    }

    Write-Host ''
    $pyCode = @(
        "import sys, importlib.util as u"
        "print('python     ', sys.version.split()[0])"
        "if u.find_spec('torch'):"
        "    import torch"
        "    print('torch      ', torch.__version__)"
        "    print('cuda build ', torch.version.cuda)"
        "    print('cuda avail ', torch.cuda.is_available())"
        "else:"
        "    print('torch       MISSING')"
    ) -join "`n"
    $banner = Invoke-PyProbe -Python $vpy -Code $pyCode
    if ($banner) { $banner | ForEach-Object { Write-Host "  $_" } }
    $dv = Invoke-PyProbe -Python $vpy -Code "import importlib.util as u;print('diffusers  ', __import__('diffusers').__version__ if u.find_spec('diffusers') else 'MISSING')"
    if ($dv) { Write-Host "  $dv" }
    Write-QwenLog '环境配置完成 / environment ready' ok
}

# ---------------------------------------------------------------------------
function Invoke-ComfyUI {
    Write-Head 'ComfyUI 配置 / ComfyUI setup'

    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedComfy
    $venv = $found.VenvDir
    if (-not $venv) { throw '请先运行 -Action env 创建 Python 环境。' }
    $vpy = Join-Path $venv 'Scripts\python.exe'

    $target = $found.ComfyDir
    if (-not $target -and $InstallRoot) {
        $target = Join-Path $InstallRoot 'ComfyUI'
    } elseif (-not $target) {
        $root = $found.ProjectRoot
        if (-not $root) { $root = [System.IO.Path]::GetFullPath((Join-Path $StartDir '..')) }
        $target = Join-Path $root 'ComfyUI'
    }

    if (Test-QwenComfyDir -Path $target) {
        Write-QwenLog "已经存在 ComfyUI：$target" ok
        Set-QwenEnvVar 'QWEN_COMFYUI_DIR' $target
    } else {
        $git = Get-QwenGit -Quiet
        if (-not $git) { $git = Install-QwenGit -AssumeYes:$Yes }
        if (-not $git) { throw '无法获得 git，无法克隆 ComfyUI。' }
        # NOTE: gitee.com/mirrors/ComfyUI clones in 3 s but is a stale snapshot
        # without comfy/ldm/qwen_image21, so it cannot run Qwen-Image-2.1.
        # GitHub is the only source verified to carry the required commit.
        $repo = $Mirrors.comfyui_repo
        if (-not (Confirm-QwenDownload -What 'ComfyUI 源码（git clone，含 Qwen-Image-2.1 支持）' `
                    -Source $repo -ApproxSize '~50 MB' -AssumeYes:$Yes)) { return }
        Write-QwenLog "cloning $repo -> $target" step
        & $git clone --depth 1 --no-tags $repo $target
        if ($LASTEXITCODE -ne 0) {
            $alt = $Mirrors.comfyui_fallback
            Write-QwenLog "clone 失败，尝试备用地址 $alt" warn
            & $git clone --depth 1 --no-tags $alt $target
        }
        if (-not (Test-QwenComfyDir -Path $target)) { throw "ComfyUI 克隆后校验失败：$target" }
        Set-QwenEnvVar 'QWEN_COMFYUI_DIR' $target
    }

    # ---- capability check: this is the guard against a stale mirror -------
    $need = @('comfy\ldm\qwen_image21\model.py', 'comfy\text_encoders\qwen_image21.py',
              'comfy_extras\nodes_qwen.py')
    $missing = @()
    foreach ($rel in $need) {
        if (-not (Test-Path -LiteralPath (Join-Path $target $rel))) { $missing += $rel }
    }
    if ($missing.Count) {
        Write-QwenLog "该 ComfyUI 缺少 Qwen-Image-2.1 支持：$($missing -join ', ')" err
        Write-QwenLog '请更新到含 Qwen-Image-2.1 的版本（v0.37.0 / c194dd00 之后）。' err
        throw 'ComfyUI 能力校验失败'
    }
    $nodes = Get-Content -LiteralPath (Join-Path $target 'comfy_extras\nodes_qwen.py') -Raw
    if ($nodes -notmatch 'TextEncodeQwenImage21') {
        throw 'nodes_qwen.py 中缺少 TextEncodeQwenImage21 —— 版本过旧。'
    }
    Write-QwenLog 'ComfyUI 具备 Qwen-Image-2.1 支持（含 TextEncodeQwenImage21 / QwenImage21Cache）' ok

    # ---- requirements, minus torch (the venv already has the cu130 build) --
    $reqFile = Join-Path $target 'requirements.txt'
    $tmp = Join-Path $env:TEMP ("comfy_req_" + [guid]::NewGuid().ToString('N') + '.txt')
    (Get-Content -LiteralPath $reqFile) |
        Where-Object { $_ -notmatch '^\s*(torch|torchvision|torchaudio)\b' } |
        Set-Content -LiteralPath $tmp -Encoding UTF8
    if (-not (Invoke-Pip -Python $vpy -Why 'installing ComfyUI requirements (torch excluded)' `
                -PipArgs @('-r', $tmp))) {
        Write-QwenLog 'ComfyUI 依赖安装失败' warn
    }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue

    if ($WithGguf) {
        Write-QwenLog 'ComfyUI-GGUF 用于 Turbo 的 GGUF 量化模型' step
        $ggufDir = Join-Path $target 'custom_nodes\ComfyUI-GGUF'
        if (Test-Path -LiteralPath $ggufDir) {
            Write-QwenLog "已存在：$ggufDir" ok
        } elseif (Confirm-QwenDownload -What 'ComfyUI-GGUF 自定义节点（可选）' `
                    -Source 'https://github.com/city96/ComfyUI-GGUF' -AssumeYes:$Yes) {
            $git = Get-QwenGit -Quiet
            & $git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git $ggufDir
            if (Test-Path -LiteralPath (Join-Path $ggufDir 'requirements.txt')) {
                Invoke-Pip -Python $vpy -Why 'installing ComfyUI-GGUF requirements' `
                    -PipArgs @('-r', (Join-Path $ggufDir 'requirements.txt')) | Out-Null
            }
        }
    }

    Write-QwenLog 'ComfyUI 配置完成 / ComfyUI ready' ok
    Write-Host "  启动方式：打开 QwenImageLauncher.exe 选一个策略启动（或运行 <策略>\start.bat）" -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
#  ModelScope download helper
# ---------------------------------------------------------------------------
function Get-ModelScopePython {
    param([string]$VenvPython)
    $probe = Invoke-PyProbe -Python $VenvPython -Code "import importlib.util as u;print(__import__('modelscope').__version__ if u.find_spec('modelscope') else '')"
    if ($LASTEXITCODE -eq 0 -and $probe) { return $true }
    if (-not (Confirm-QwenDownload -What 'modelscope Python 包（用于从国内源下载模型）' `
                -Source $PipIndex -ApproxSize '~10 MB' -AssumeYes:$Yes)) { return $false }
    return (Invoke-Pip -Python $VenvPython -Why 'installing modelscope' -PipArgs @('--upgrade', 'modelscope'))
}

function Invoke-ModelScopeDownload {
    param([string]$VenvPython, [string]$Repo, [string]$LocalDir, [string[]]$Patterns, [string]$Label)
    $script = @'
import sys
from modelscope import snapshot_download
repo, local, label = sys.argv[1], sys.argv[2], sys.argv[3]
pats = sys.argv[4:] or None
print(f"[modelscope] {label}: {repo} -> {local}", flush=True)
if pats:
    print(f"[modelscope] files: {pats}", flush=True)
path = snapshot_download(repo, local_dir=local, allow_file_pattern=pats)
print(f"[modelscope] done: {path}", flush=True)
'@
    $tmp = Join-Path $env:TEMP ("ms_dl_" + [guid]::NewGuid().ToString('N') + '.py')
    Set-Content -LiteralPath $tmp -Value $script -Encoding UTF8
    $argv = @($tmp, $Repo, $LocalDir, $Label) + $Patterns
    & $VenvPython @argv
    $rc = $LASTEXITCODE
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    return ($rc -eq 0)
}

function Assert-ModelScopeReady {
    param([string]$VenvPython)
    Write-QwenLog 'huggingface.co 与 hf-mirror.com 均不可达，模型来源固定为 ModelScope' info
    if (-not (Get-ModelScopePython -VenvPython $VenvPython)) { throw 'modelscope 不可用，下载中止。' }
}

function Show-Present {
    param([hashtable]$Present)
    if ($Present -and $Present.Count) {
        Write-QwenLog '以下文件已存在，跳过下载 / already present, skipping:' ok
        foreach ($k in ($Present.Keys | Sort-Object)) {
            Write-Host ("        {0}" -f $k) -ForegroundColor DarkGray
            Write-Host ("          -> {0}" -f $Present[$k]) -ForegroundColor DarkGray
        }
    }
}

# ---------------------------------------------------------------------------
#  Quantized stack (ComfyUI) -- the script to run on devices that use
#  the mixed / mixed-vram strategies.
# ---------------------------------------------------------------------------
function Invoke-ModelsMixed {
    Write-Head '量化模型下载 / quantized model download (ModelScope)'

    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedComfy
    if (-not $found.VenvDir) { throw '请先配置 Python 环境（启动器第 3 步「配置环境」，或 -Action env）。' }
    $vpy = Join-Path $found.VenvDir 'Scripts\python.exe'
    $root = $found.ProjectRoot
    if (-not $root) { $root = [System.IO.Path]::GetFullPath((Join-Path $StartDir '..')) }
    $comfy = $found.ComfyDir
    if (-not $comfy) { $comfy = Join-Path $root 'ComfyUI' }
    if (-not (Test-QwenComfyDir -Path $comfy)) {
        Write-QwenLog 'ComfyUI 尚未安装，请先完成 ComfyUI 安装步骤。' err
        throw '缺少 ComfyUI'
    }
    Write-QwenLog "ComfyUI: $comfy" info

    $specs = New-Object System.Collections.Generic.List[string]
    $specs.Add('text_encoders/qwen3vl_8b_int8_convrot.safetensors')
    $specs.Add('vae/qwen_image_2.1_vae_bf16.safetensors')

    # Prefer the W4 "mixed balanced" checkpoint if it is already anywhere
    # nearby; only fall back to Comfy-Org's int8 build when it is not.
    # No MinBytes used to be passed, so a 50MB stub counted as the 3.91 GiB W4
    # checkpoint. Size comes from the artifact table now.
    $localMixed = Find-QwenComfyModelFile -StartDir $SearchDir -Folder 'diffusion_models' `
        -Pattern '*mixed*balanced*.safetensors' -MinBytes (Get-QwenArtifactMinBytes -Name 'mixed_balanced')
    if ($localMixed) {
        Write-QwenLog "已存在量化扩散模型（$([math]::Round((Get-Item -LiteralPath $localMixed).Length / 1GB, 2)) GiB），无需下载：$localMixed" ok
        Set-QwenEnvVar 'QWEN_UNET_MIXED' $localMixed
    } else {
        $specs.Add('diffusion_models/qwen_image_2.1_int8_convrot.safetensors')
    }
    if ($WithGguf) {
        $specs.Add('diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors')
        $specs.Add('loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors')
    }

    $plan = Resolve-QwenModelTargets -StartDir $SearchDir -ComfyDir $comfy -Specs $specs.ToArray()
    Show-Present -Present $plan.Present

    if ($plan.Missing.Count -eq 0) {
        Write-QwenLog '量化模型已齐全，本次不下载任何文件。/ nothing to download.' ok
        return
    }
    Write-QwenLog ("仍需下载 {0} 个文件 / still missing:" -f $plan.Missing.Count) warn
    foreach ($m in $plan.Missing) { Write-Host "        $m" -ForegroundColor Yellow }

    Assert-ModelScopeReady -VenvPython $vpy
    if (-not (Confirm-QwenDownload -What "Comfy-Org/Qwen-Image-2.1 缺失的量化文件（$($plan.Missing.Count) 个）" `
                -Source $Mirrors.modelscope_repos.'Comfy-Org/Qwen-Image-2.1' `
                -ApproxSize '按缺失文件计，约 1-18 GB' -AssumeYes:$Yes)) { return }

    Invoke-ModelScopeDownload -VenvPython $vpy -Repo 'Comfy-Org/Qwen-Image-2.1' `
        -LocalDir (Join-Path $comfy 'models') -Patterns $plan.Missing.ToArray() -Label 'quantized' | Out-Null

    if ($WithGguf -and (Confirm-QwenDownload -What 'Abiray/Qwen-Image-2.1-Turbo-GGUF Q4_K_M (3.90 GiB)' `
                -Source 'https://www.modelscope.cn/models/Abiray/Qwen-Image-2.1-Turbo-GGUF' `
                -ApproxSize '3.90 GiB' -AssumeYes:$Yes)) {
        Invoke-ModelScopeDownload -VenvPython $vpy -Repo 'Abiray/Qwen-Image-2.1-Turbo-GGUF' `
            -LocalDir (Join-Path $comfy 'models\unet') `
            -Patterns @('*Q4_K_M.gguf') -Label 'turbo-gguf' | Out-Null
    }
    Write-QwenLog '量化模型步骤完成 / quantized model step done' ok
}

# ---------------------------------------------------------------------------
#  Native stack (diffusers BF16) -- the script to run on devices that use
#  the native strategy.
# ---------------------------------------------------------------------------
function Invoke-ModelsNative {
    Write-Head '全量模型下载 / full native model download (ModelScope)'

    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedModels -NeedWeights
    if (-not $found.VenvDir) { throw '请先配置 Python 环境（启动器第 3 步「配置环境」，或 -Action env）。' }
    $vpy = Join-Path $found.VenvDir 'Scripts\python.exe'
    $root = $found.ProjectRoot
    if (-not $root) { $root = [System.IO.Path]::GetFullPath((Join-Path $StartDir '..')) }

    # (1) proper HuggingFace-style cache already nearby?
    if ($found.ModelsCache -and (Test-QwenModelsCache -Path $found.ModelsCache -NeedWeights)) {
        Write-QwenLog "已存在可用的原生模型缓存，无需下载：$($found.ModelsCache)" ok
        Write-QwenLog '本次不下载任何文件。/ nothing to download.' ok
        return
    }
    $cache = if ($found.ModelsCache) { $found.ModelsCache }
        elseif ($InstallRoot) { Join-Path $InstallRoot 'models_cache' }
        else { Join-Path $root 'models_cache' }
    $nativeDir = Join-Path $cache 'Qwen-Image-2.1'

    # (2) plain (non HF-cache) copy downloaded by an earlier run?
    $probe = Join-Path $nativeDir 'transformer'
    if (Test-Path -LiteralPath $probe) {
        # A single >1GB file used to count as "complete". It is not: the set is
        # sharded across transformer/ text_encoder/ vae/, so a download that died
        # partway looked finished and could never be resumed.
        $chk = Test-QwenNativeWeights -Dir $nativeDir
        if ($chk.Complete) {
            Write-QwenLog "已存在完整原生权重目录，无需下载：$nativeDir" ok
            Set-QwenEnvVar 'QWEN_MODEL_PATH' $nativeDir
            Write-QwenLog '本次不下载任何文件。/ nothing to download.' ok
            return
        }
        Write-QwenLog "原生权重不完整，将续传补齐（缺：$($chk.Missing -join '、')）" warn
    }

    # (3) weights in some other nearby models_cache / plain dir?
    $nearby = Find-QwenComfyModelFile -StartDir $SearchDir -Folder 'transformer' `
        -Pattern 'diffusion_pytorch_model*.safetensors' -MinBytes 1GB
    if ($nearby) {
        $guess = Split-Path -Parent (Split-Path -Parent $nearby)
        # same trap as above: one stray shard is not a complete model
        $chk = Test-QwenNativeWeights -Dir $guess
        if ($chk.Complete) {
            Write-QwenLog "附近已有完整原生权重，无需下载：$nearby" ok
            Set-QwenEnvVar 'QWEN_MODEL_PATH' $guess
            Write-QwenLog '本次不下载任何文件。/ nothing to download.' ok
            return
        }
        Write-QwenLog "附近原生权重不完整（$guess），改为下载到 $nativeDir（缺：$($chk.Missing -join '、')）" warn
    }

    Write-QwenLog '未在附近（当前目录 / 子目录 / 上级目录）找到原生 BF16 权重' warn
    Assert-ModelScopeReady -VenvPython $vpy
    if (-not (Confirm-QwenDownload -What 'Qwen/Qwen-Image-2.1 原生 BF16 全套（transformer + Qwen3-VL 文本编码器 + VAE）' `
                -Source $Mirrors.modelscope_repos.'Qwen/Qwen-Image-2.1' `
                -ApproxSize '约 30 GB' -AssumeYes:$Yes)) { return }
    if (-not (Invoke-ModelScopeDownload -VenvPython $vpy -Repo 'Qwen/Qwen-Image-2.1' `
                -LocalDir $nativeDir -Patterns @() -Label 'native-bf16')) {
        throw '原生模型下载失败'
    }
    if (Test-Path -LiteralPath $nativeDir) {
        Write-QwenLog "设置 QWEN_MODEL_PATH=$nativeDir" ok
        Set-QwenEnvVar 'QWEN_MODEL_PATH' $nativeDir
        [Environment]::SetEnvironmentVariable('QWEN_MODEL_PATH', $nativeDir, 'User')
    }
    Write-QwenLog '原生模型步骤完成 / native model step done' ok
}

# ---------------------------------------------------------------------------
function Invoke-FixTorch {
    Write-Head '修复 torch / torchaudio (cu130)'
    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv
    if (-not $found.VenvDir) { throw '未找到 Python 环境，请先运行 -Action env。' }
    $vpy = Join-Path $found.VenvDir 'Scripts\python.exe'

    Write-QwenLog '卸载现有 torch / torchvision / torchaudio' step
    & $vpy -m pip uninstall -y torch torchvision torchaudio | Out-Null

    Write-QwenLog "重装 torch 2.10.0 / torchvision 0.25.0 (cu130)" step
    & $vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall `
        'torch==2.10.0' 'torchvision==0.25.0' --index-url $Mirrors.pytorch_index
    if ($LASTEXITCODE -ne 0) {
        & $vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall `
            'torch==2.10.0' 'torchvision==0.25.0' --index-url $Mirrors.pytorch_fallback
    }
    # --no-deps so torchaudio cannot drag torch back to a different build
    Write-QwenLog '重装 torchaudio 2.10.0+cu130 (--no-deps 保护 torch)' step
    & $vpy -m pip install --disable-pip-version-check --upgrade --force-reinstall --no-deps `
        'torchaudio==2.10.0+cu130' --index-url $Mirrors.pytorch_index

    $tr = Invoke-PyProbe -Python $vpy -Code "import importlib.util as u;print('torch', __import__('torch').__version__, 'cuda', __import__('torch').version.cuda, 'avail', __import__('torch').cuda.is_available()) if u.find_spec('torch') else print('torch MISSING')"
    if ($tr) { Write-Host "  $tr" }
    if ($LASTEXITCODE -ne 0) { throw 'torch 导入失败，修复未成功。' }
    Write-QwenLog '修复完成 / torch fixed' ok
}

# ---------------------------------------------------------------------------
function Invoke-Status {
    Write-Head '当前状态 / status'
    $found = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedModels -NeedWeights -NeedComfy -Quiet:$false
    Write-Host ''
    foreach ($pair in @(
            @('项目根目录', $found.ProjectRoot),
            @('Python 环境', $found.VenvDir),
            @('模型缓存', $found.ModelsCache),
            @('ComfyUI', $found.ComfyDir),
            @('输出目录', $found.OutputDir))) {
        $mark = if ($pair[1]) { 'OK  ' } else { 'MISS' }
        Write-Host ("  [{0}] {1,-12} {2}" -f $mark, $pair[0], $pair[1]) `
            -ForegroundColor $(if ($pair[1]) { 'Green' } else { 'Yellow' })
    }
    if ($found.ComfyDir) {
        $mixed = Test-QwenComfyModels -ComfyDir $found.ComfyDir
        Write-Host ("  [{0}] {1,-12} {2}" -f $(if ($mixed) { 'OK  ' } else { 'MISS' }), '量化模型', 'models\diffusion_models') `
            -ForegroundColor $(if ($mixed) { 'Green' } else { 'Yellow' })
    }
    Write-Host ''
    Write-Host "  pip 源   : $PipIndex" -ForegroundColor Gray
    Write-Host "  模型源   : $($Mirrors.hf_endpoint)  (huggingface.co 不可达)" -ForegroundColor Gray
    # ProjectRoot is null until something is installed, and Join-Path throws on
    # a null/empty base -- which made -Action status fail on a fresh machine.
    $cacheShown = if ($found.ProjectRoot) {
        Join-Path $found.ProjectRoot '.qwen_env.json'
    } else { '(尚未确定 / not determined yet)' }
    Write-Host "  环境缓存 : $cacheShown" -ForegroundColor Gray
}

function Invoke-Probe {
    <#
      Machine-readable state for the GUI launcher. Writes one JSON object between
      sentinel lines; the GUI reads only that, so the human-readable logging on
      the way there cannot corrupt the parse.

      Detection goes through the same helpers every other action uses, so the GUI
      cannot drift away from what the CLI would actually do.
    #>
    $f = Initialize-QwenEnv -StartDir $SearchDir -NeedVenv -NeedDiffusers -NeedComfy -NeedModels -NeedWeights -Quiet

    $torch = $null
    if ($f.VenvDir) {
        $vpy = Join-Path $f.VenvDir 'Scripts\python.exe'
        if (Test-Path -LiteralPath $vpy) {
            try {
                $out = Invoke-PyProbe -Python $vpy -Code "import importlib.util as u;print(__import__('torch').__version__ if u.find_spec('torch') else '')"
                if ($LASTEXITCODE -eq 0 -and $out) { $torch = ([string]($out | Select-Object -First 1)).Trim() }
            } catch { }
        }
    }

    # A W4 checkpoint found anywhere nearby fills the diffusion slot.
    $w4 = Find-QwenComfyModelFile -StartDir $SearchDir -Folder 'diffusion_models' `
        -Pattern '*mixed*balanced*.safetensors'

    $specs = @('text_encoders/qwen3vl_8b_int8_convrot.safetensors',
               'vae/qwen_image_2.1_vae_bf16.safetensors',
               'diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors',
               'loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors')
    if (-not $w4) { $specs += 'diffusion_models/qwen_image_2.1_int8_convrot.safetensors' }
    $plan = Resolve-QwenModelTargets -StartDir $SearchDir -ComfyDir $f.ComfyDir -Specs $specs

    $encoder = $plan.Present['text_encoders/qwen3vl_8b_int8_convrot.safetensors']
    $vaeSpec = $plan.Present['vae/qwen_image_2.1_vae_bf16.safetensors']
    $turboD  = $plan.Present['diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors']
    $turboL  = $plan.Present['loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors']
    $int8    = $plan.Present['diffusion_models/qwen_image_2.1_int8_convrot.safetensors']
    $diffusion = if ($w4) { $w4 } else { $int8 }

    $gguf = $null
    if ($f.ProjectRoot) {
        $gd = Join-Path $f.ProjectRoot 'gguf\ComfyUI\models\unet'
        if (Test-Path -LiteralPath $gd) {
            $g = Get-ChildItem -LiteralPath $gd -File -Filter '*.gguf' -ErrorAction SilentlyContinue |
                    Sort-Object Length -Descending | Select-Object -First 1
            if ($g) { $gguf = $g.FullName }
        }
    }

    $state = [ordered]@{
        project_root = $f.ProjectRoot
        install_root = $(if ($InstallRoot) { [System.IO.Path]::GetFullPath($InstallRoot) } else { $null })
        search_dir   = $SearchDir
        venv         = [ordered]@{ path = $f.VenvDir; torch = $torch; ok = [bool]($f.VenvDir -and $torch) }
        comfyui      = [ordered]@{ path = $f.ComfyDir; ok = [bool]$f.ComfyDir }
        models_cache = [ordered]@{
            path   = $f.ModelsCache
            native = [bool]($f.ModelsCache -and (Test-QwenModelsCache -Path $f.ModelsCache -NeedWeights))
        }
        models       = [ordered]@{
            encoder         = [ordered]@{ present = [bool]$encoder; path = $encoder; gib = 8.71 }
            vae             = [ordered]@{ present = [bool]$vaeSpec; path = $vaeSpec; gib = 0.63 }
            diffusion       = [ordered]@{ present = [bool]$diffusion; path = $diffusion; gib = 6.76
                                          kind = $(if ($w4) { 'w4' } elseif ($int8) { 'int8' } else { 'missing' }) }
            turbo_diffusion = [ordered]@{ present = [bool]$turboD; path = $turboD; gib = 6.76 }
            turbo_lora      = [ordered]@{ present = [bool]$turboL; path = $turboL; gib = 0.85 }
            turbo_gguf      = [ordered]@{ present = [bool]$gguf; path = $gguf; gib = 3.90 }
        }
    }
    $json = $state | ConvertTo-Json -Depth 6

    # The machine-readable result must not depend on the console codepage, so it
    # is also written to a file with an explicit encoding. The launcher reads
    # this file in preference to stdout. -Encoding UTF8 means "UTF-8 with BOM"
    # on PowerShell 5.1 and "UTF-8 without BOM" on 7, so the reader strips it.
    try {
        $probeFile = Join-Path $SearchDir '.qwen_probe.json'
        Set-Content -LiteralPath $probeFile -Value $json -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-QwenLog "无法写入 .qwen_probe.json（$($_.Exception.Message)），改用 stdout 传递" warn
    }

    Write-Output '###QWEN-JSON-BEGIN###'
    Write-Output $json
    Write-Output '###QWEN-JSON-END###'
}

switch ($Action) {
    'env'       { Invoke-Env }
    'comfyui'   { Invoke-ComfyUI }
    'models-mixed'  { Invoke-ModelsMixed }
    'models-native' { Invoke-ModelsNative }
    'fix-torch' { Invoke-FixTorch }
    'status'    { Invoke-Status }
    'probe'     { Invoke-Probe }
}
