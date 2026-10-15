# ============================================================================
#  QwenEnv.ps1 -- shared path discovery / environment caching / mirror config
#  for the Qwen-Image-2.1 WebUI project.
#
#  Dot-source it from any script:
#      . "$PSScriptRoot\..\shared\QwenEnv.ps1"
#      Initialize-QwenEnv -NeedVenv -NeedModels
#
#  Search policy (what the user asked for):
#      script dir  ->  its sub directories (depth <= 2)  ->  up to 6 parent dirs
#  Every hit is validated by a precise marker (not by name alone) and then
#  cached into environment variables for the rest of the session, plus a JSON
#  cache file so the next run is instant. A stale cache entry is re-validated
#  and dropped if it no longer exists.
# ============================================================================

# --- force UTF-8 stdio -------------------------------------------------------
# A Chinese Windows leaves the console at codepage 936, which would turn every
# CJK path this script prints into bytes the launcher cannot decode. Pin it.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:QwenLogPrefix = 'qwen'

function Write-QwenLog {
    param([string]$Message, [ValidateSet('info','ok','warn','err','step')][string]$Level = 'info')
    $tag = switch ($Level) { 'ok' {'[ OK ]'} 'warn' {'[WARN]'} 'err' {'[FAIL]'} 'step' {'[ .. ]'} default {'[INFO]'} }
    $color = switch ($Level) { 'ok' {'Green'} 'warn' {'Yellow'} 'err' {'Red'} 'step' {'Cyan'} default {'Gray'} }
    Write-Host "$tag $Message" -ForegroundColor $color
}

# --------------------------------------------------------------------------
#  Mirror configuration (decided by _bench\mirror_speedtest.ps1 measurements)
# --------------------------------------------------------------------------
function Get-QwenMirrors {
    $cfgPath = Join-Path $PSScriptRoot 'mirrors.json'
    if (Test-Path $cfgPath) {
        try { return (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { }
    }
    # Fallback defaults: domestic mirrors, official as last resort.
    return [pscustomobject]@{
        pip_primary   = 'https://mirrors.aliyun.com/pypi/simple'
        pip_fallback  = 'https://pypi.org/simple'
        pip_host      = 'mirrors.aliyun.com'
        hf_endpoint   = 'https://hf-mirror.com'
        hf_fallback   = 'https://huggingface.co'
        modelscope    = 'https://www.modelscope.cn'
        pytorch_index = 'https://mirrors.aliyun.com/pytorch-wheels/cu130'
        pytorch_fallback = 'https://download.pytorch.org/whl/cu130'
        git_for_windows = 'https://registry.npmmirror.com/-/binary/git-for-windows'
        python_release  = 'https://mirrors.huaweicloud.com/python'
        python_fallback = 'https://www.python.org/ftp/python'
        comfyui_repo  = 'https://github.com/Comfy-Org/ComfyUI.git'
    }
}

# --------------------------------------------------------------------------
#  Search-space helpers
# --------------------------------------------------------------------------
function Get-QwenAncestorDirs {
    param([string]$Start, [int]$MaxUp = 6)
    $out = New-Object System.Collections.Generic.List[string]
    $cur = [System.IO.Path]::GetFullPath($Start)
    for ($i = 1; $i -le $MaxUp; $i++) {
        $parent = Split-Path -Parent $cur
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cur) { break }
        $out.Add($parent)
        $cur = $parent
    }
    return $out
}

function Get-QwenShallowSubDirs {
    param([string]$Start, [int]$MaxDepth = 2)
    # Sub directories of $Start down to $MaxDepth, pruning the heavy trees we
    # never want to walk (they are candidates themselves, not containers).
    $skip = @('.git', '.venv', 'venv', 'node_modules', '__pycache__', 'models_cache',
              'site-packages', 'ComfyUI', '.cache', 'outputs', 'temp', 'blobs', 'snapshots')
    $result = New-Object System.Collections.Generic.List[string]
    $frontier = @([System.IO.Path]::GetFullPath($Start))
    for ($d = 1; $d -le $MaxDepth; $d++) {
        $next = New-Object System.Collections.Generic.List[string]
        foreach ($dir in $frontier) {
            foreach ($sub in (Get-ChildItem -LiteralPath $dir -Directory -Force -ErrorAction SilentlyContinue)) {
                if ($skip -contains $sub.Name) { continue }
                if ($sub.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                $result.Add($sub.FullName)
                $next.Add($sub.FullName)
            }
        }
        $frontier = $next
    }
    return $result
}

# --------------------------------------------------------------------------
#  Validators -- precise markers, so a random folder named ".venv" never wins
# --------------------------------------------------------------------------
$script:QwenArtifactRules = @(
    # Measured on this deployment. A partially transferred file keeps its name, so
    # every gate multiplies the real size by 0.9 and demands at least that much.
    @{ Match = 'mixed.*balanced|mixed_balanced'; Bytes = 4198000000 },  # 3.91 GiB W4
    @{ Match = 'turbo.*q4_k_m|q4_k_m.*gguf';     Bytes = 4189346592 },  # 3.90 GiB GGUF
    @{ Match = 'turbo.*int8|turbo_int8';         Bytes = 7259000000 },  # 6.76 GiB
    @{ Match = 'qwen3vl.*int8';                  Bytes = 9353000000 },  # 8.71 GiB encoder
    @{ Match = 'vae.*bf16';                      Bytes = 676000000  },  # 0.63 GiB
    @{ Match = 'turbo.*lora';                    Bytes = 913314512   },  # 0.85 GiB LoRA
    @{ Match = 'int8_convrot';                   Bytes = 4198000000 }   # generic int8
)

function Get-QwenArtifactMinBytes {
    <# Minimum plausible size for an artifact, from the table above. #>
    param([string] $Name, [long] $Default = 50MB)
    if (-not $Name) { return $Default }
    foreach ($r in $script:QwenArtifactRules) {
        if ($Name -match $r.Match) { return [long]($r.Bytes * 0.9) }
    }
    return $Default
}

function Test-QwenArtifact {
    <#
    .SYNOPSIS
        Is this file present AND essentially complete?

    .DESCRIPTION
        Existence plus a token size threshold is not enough: an interrupted
        download leaves a correctly named but short file, which the installers
        then treated as done. Demand most of the known size instead.
    #>
    param([string] $Path, [string] $Name = '', [long] $MinBytes = 0)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $n = if ($Name) { $Name } else { Split-Path -Leaf $Path }
    $need = if ($MinBytes -gt 0) { $MinBytes } else { Get-QwenArtifactMinBytes -Name $n }
    try { return ((Get-Item -LiteralPath $Path).Length -ge $need) } catch { return $false }
}

function Test-QwenVenv {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    $py = Join-Path $Path 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) { return $false }
    # must actually carry a usable torch (that is what every strategy needs)
    $sp = Join-Path $Path 'Lib\site-packages'
    return (Test-Path -LiteralPath (Join-Path $sp 'torch\__init__.py'))
}

function Test-QwenVenvFull {
    param([string]$Path, [switch]$NeedDiffusers, [switch]$NeedComfy)
    if (-not (Test-QwenVenv $Path)) { return $false }
    $sp = Join-Path $Path 'Lib\site-packages'
    if ($NeedDiffusers -and -not (Test-Path -LiteralPath (Join-Path $sp 'diffusers\__init__.py'))) { return $false }
    if ($NeedComfy -and -not (Test-Path -LiteralPath (Join-Path $sp 'comfyui_frontend_package'))) { return $false }
    return $true
}

function Test-QwenModelsCache {
    param([string]$Path, [switch]$NeedWeights)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    $repo = Join-Path $Path 'models--Qwen--Qwen-Image-2.1'
    if (-not (Test-Path -LiteralPath $repo -PathType Container)) { return $false }
    if (-not $NeedWeights) { return $true }
    # require at least one snapshot that actually holds transformer weights
    foreach ($snap in (Get-ChildItem -LiteralPath (Join-Path $repo 'snapshots') -Directory -ErrorAction SilentlyContinue)) {
        $tf = Join-Path $snap.FullName 'transformer'
        if (-not (Test-Path -LiteralPath $tf)) { continue }
        # one >100MB shard is not a model: the native set is 30 GB over three
        # components, so this gate needs the same strictness as the others
        $miss = (Test-QwenNativeWeights -Dir $snap.FullName).Missing
        if ($miss.Count -eq 0) { return $true }
        $w = Get-ChildItem -LiteralPath $tf -File -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -like '*.safetensors' -and $_.Length -gt 1GB }
        if ($w -and (Test-Path -LiteralPath (Join-Path $snap.FullName 'text_encoder')) -and
            (Test-Path -LiteralPath (Join-Path $snap.FullName 'vae'))) { return $true }
    }
    return $false
}

function Test-QwenComfyDir {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    return ((Test-Path -LiteralPath (Join-Path $Path 'main.py')) -and
            (Test-Path -LiteralPath (Join-Path $Path 'comfy\model_management.py')) -and
            (Test-Path -LiteralPath (Join-Path $Path 'comfy_extras\nodes_qwen.py')))
}

function Test-QwenComfyModels {
    param([string]$ComfyDir)
    if (-not $ComfyDir) { return $false }
    $d = Join-Path $ComfyDir 'models\diffusion_models'
    if (-not (Test-Path -LiteralPath $d)) { return $false }
    # Demand most of the real 3.91 GiB. This function decides what the GUI shows
    # as installed, so a partial download used to look ready here.
    $f = Get-ChildItem -LiteralPath $d -File -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -match 'mixed|balanced' -and $_.Name -like '*.safetensors' } |
         Where-Object { Test-QwenArtifact -Path $_.FullName -Name $_.Name }
    return [bool]$f
}

function Get-QwenModelSearchDirs {
    <#
      Every plausible place a ComfyUI model folder could live, given the search
      space (script dir -> sub dirs -> parent dirs). This is what lets the setup
      scripts notice models that are already on the machine -- in this folder,
      in a sibling copy of the project, or in a parent folder -- and skip the
      download entirely.
    #>
    param([string]$StartDir, [string]$Folder)
    $seen = New-Object System.Collections.Generic.HashSet[string]
    foreach ($root in (Get-QwenSearchSpace -StartDir $StartDir)) {
        foreach ($rel in @("ComfyUI\models\$Folder", "ComfyUI\models", "models\$Folder",
                           "models", "ComfyUI\$Folder", "$Folder")) {
            $d = Join-Path $root $rel
            if (-not (Test-Path -LiteralPath $d -PathType Container)) { continue }
            $full = (Resolve-Path -LiteralPath $d).Path
            if ($seen.Add($full)) { $full }
        }
    }
}

function Find-QwenComfyModelFile {
    <#
      Locate one model file by wildcard anywhere in the search space.
      Returns the full path, or $null.
    #>
    param([string]$StartDir, [string]$Folder, [string]$Pattern, [long]$MinBytes = 50MB)
    foreach ($dir in (Get-QwenModelSearchDirs -StartDir $StartDir -Folder $Folder)) {
        $hit = Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -like $Pattern } |
                 Where-Object { Test-QwenArtifact -Path $_.FullName -Name $_.Name } |
                 Where-Object { $_.Length -gt $MinBytes } |
                 Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Resolve-QwenModelTargets {
    <#
      For a list of "folder/filename" specs, work out for each one whether it is
      already present somewhere nearby and, if not, where it should be placed.

      Returns an object with:
        Present  -- hashtable spec -> existing full path (already on disk)
        Missing  -- array of specs that still need downloading
        Targets  -- hashtable spec -> destination path for the download
    #>
    param([string]$StartDir, [string]$ComfyDir, [string[]]$Specs)
    $present = @{}
    $missing = New-Object System.Collections.Generic.List[string]
    $targets = @{}
    foreach ($spec in $Specs) {
        $parts = $spec -split '/', 2
        if ($parts.Count -ne 2) { continue }
        $folder, $file = $parts[0], $parts[1]
        $found = Find-QwenComfyModelFile -StartDir $StartDir -Folder $folder -Pattern $file
        if ($found) {
            $present[$spec] = $found
            continue
        }
        $missing.Add($spec)
        if ($ComfyDir) { $targets[$spec] = Join-Path $ComfyDir "models\$folder" }
    }
    return [pscustomobject]@{ Present = $present; Missing = $missing; Targets = $targets }
}

# --------------------------------------------------------------------------
#  Finders -- script dir, then sub dirs, then parent dirs
# --------------------------------------------------------------------------
function Get-QwenSearchSpace {
    param([string]$StartDir)
    if (-not $StartDir) { $StartDir = (Get-Location).Path }
    $space = New-Object System.Collections.Generic.List[string]
    $space.Add([System.IO.Path]::GetFullPath($StartDir))          # 1. script dir
    foreach ($d in (Get-QwenShallowSubDirs -Start $StartDir -MaxDepth 2)) { $space.Add($d) }  # 2. sub dirs
    foreach ($d in (Get-QwenAncestorDirs -Start $StartDir -MaxUp 6)) { $space.Add($d) }        # 3. parents
    return $space
}

function Find-QwenVenv {
    param([string]$StartDir, [switch]$NeedDiffusers, [switch]$NeedComfy, [switch]$Quiet)
    $names = @('.venv', 'venv', 'env', '.env')
    foreach ($root in (Get-QwenSearchSpace -StartDir $StartDir)) {
        foreach ($n in $names) {
            $cand = Join-Path $root $n
            if (Test-QwenVenvFull -Path $cand -NeedDiffusers:$NeedDiffusers -NeedComfy:$NeedComfy) {
                if (-not $Quiet) { Write-QwenLog "venv found: $cand" ok }
                return (Resolve-Path -LiteralPath $cand).Path
            }
        }
        # the search root itself may be a venv (e.g. you are sitting inside it)
        if (Test-QwenVenvFull -Path $root -NeedDiffusers:$NeedDiffusers -NeedComfy:$NeedComfy) {
            if (-not $Quiet) { Write-QwenLog "venv found: $root" ok }
            return $root
        }
    }
    return $null
}

function Find-QwenModelsCache {
    param([string]$StartDir, [switch]$NeedWeights, [switch]$Quiet)
    $names = @('models_cache', 'hf_cache', 'models\hf', 'models')
    foreach ($root in (Get-QwenSearchSpace -StartDir $StartDir)) {
        foreach ($n in $names) {
            $cand = Join-Path $root $n
            if (Test-QwenModelsCache -Path $cand -NeedWeights:$NeedWeights) {
                if (-not $Quiet) { Write-QwenLog "model cache found: $cand" ok }
                return (Resolve-Path -LiteralPath $cand).Path
            }
        }
        if (Test-QwenModelsCache -Path $root -NeedWeights:$NeedWeights) {
            if (-not $Quiet) { Write-QwenLog "model cache found: $root" ok }
            return $root
        }
    }
    return $null
}

function Find-QwenComfyDir {
    param([string]$StartDir, [switch]$Quiet)
    $names = @('ComfyUI', 'comfyui', 'Comfy')
    foreach ($root in (Get-QwenSearchSpace -StartDir $StartDir)) {
        foreach ($n in $names) {
            $cand = Join-Path $root $n
            if (Test-QwenComfyDir -Path $cand) {
                if (-not $Quiet) { Write-QwenLog "ComfyUI found: $cand" ok }
                return (Resolve-Path -LiteralPath $cand).Path
            }
        }
        if (Test-QwenComfyDir -Path $root) {
            if (-not $Quiet) { Write-QwenLog "ComfyUI found: $root" ok }
            return $root
        }
    }
    return $null
}

function Find-QwenProjectRoot {
    param([string]$StartDir)
    # The project root is the shallowest ancestor that owns the shared assets.
    foreach ($root in (Get-QwenSearchSpace -StartDir $StartDir)) {
        $score = 0
        if (Test-Path -LiteralPath (Join-Path $root 'models_cache')) { $score++ }
        if (Test-Path -LiteralPath (Join-Path $root 'ComfyUI\main.py')) { $score++ }
        if (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe')) { $score++ }
        if (Test-Path -LiteralPath (Join-Path $root 'shared\QwenEnv.ps1')) { $score++ }
        if ($score -ge 2) { return (Resolve-Path -LiteralPath $root).Path }
    }
    return $null
}

# --------------------------------------------------------------------------
#  Env cache
# --------------------------------------------------------------------------
function Get-QwenCachePath {
    <#
      The cache is stored *inside* the project it describes, so two copies of
      this tree can never read each other's cache -- a poisoned shared cache was
      the failure mode of the first design. Only when no project root can be
      resolved (the env lives somewhere unrelated) do we fall back to a
      per-start-directory file under LOCALAPPDATA.
    #>
    param([string]$ProjectRoot, [string]$StartDir)
    if ($ProjectRoot) { return (Join-Path $ProjectRoot '.qwen_env.json') }
    $dir = Join-Path $env:LOCALAPPDATA 'QwenImageWebUI'
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    $hash = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$StartDir)) |
                ForEach-Object { $_.ToString('x2') }) -join ''
    return (Join-Path $dir ("env-" + $hash.Substring(0, 12) + '.json'))
}

function Save-QwenEnvCache {
    param([hashtable]$Values, [string]$Path)
    if (-not $Path) { return }
    try {
        $obj = [ordered]@{ savedAt = (Get-Date -Format 's') }
        foreach ($k in $Values.Keys) { if ($Values[$k]) { $obj[$k] = $Values[$k] } }
        ($obj | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $Path -Encoding UTF8
    } catch { Write-QwenLog "could not write env cache: $_" warn }
}

function Read-QwenEnvCache {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return @{} }
    try {
        $o = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        $h = @{}
        foreach ($prop in $o.PSObject.Properties) { $h[$prop.Name] = $prop.Value }
        return $h
    } catch { return @{} }
}

function Set-QwenEnvVar {
    param([string]$Name, [string]$Value)
    if (-not $Value) { return }
    Set-Item -Path ("Env:" + $Name) -Value $Value
    Write-QwenLog ("cached  {0} = {1}" -f $Name, $Value) step
}

# --------------------------------------------------------------------------
#  Main entry point
# --------------------------------------------------------------------------
function Initialize-QwenEnv {
    <#
      Resolves and caches every path a strategy or setup script needs.
      Returns a hashtable with the resolved values (also exported as env vars).
      Never triggers a download -- callers decide that, after confirmation.
    #>
    param(
        [string]$StartDir,
        [switch]$NeedVenv,
        [switch]$NeedDiffusers,
        [switch]$NeedComfyLib,
        [switch]$NeedModels,
        [switch]$NeedWeights,
        [switch]$NeedComfy,
        [switch]$Quiet
    )
    if (-not $StartDir) { $StartDir = (Get-Location).Path }
    if (-not $Quiet) {
        Write-QwenLog "search start: $StartDir" info
    }

    $found = @{}

    # ---- project root (first: it decides where the cache lives) -----------
    $root = Find-QwenProjectRoot -StartDir $StartDir
    if ($root) { $found.ProjectRoot = $root; Set-QwenEnvVar 'QWEN_PROJECT_ROOT' $root }

    $cachePath = Get-QwenCachePath -ProjectRoot $root -StartDir $StartDir
    $cache = Read-QwenEnvCache -Path $cachePath

    # The cache is a memory aid, not an authority: only trust it while it still
    # describes THIS project. Otherwise a second copy of the tree would happily
    # reuse the first copy's venv and model cache.
    $cacheTrusted = $true
    if ($root -and $cache['QWEN_PROJECT_ROOT'] -and ($cache['QWEN_PROJECT_ROOT'] -ne $root)) {
        $cacheTrusted = $false
        if (-not $Quiet) {
            Write-QwenLog "env cache belongs to another project ($($cache['QWEN_PROJECT_ROOT'])); ignoring it" warn
        }
    }

    # ---- python environment ----------------------------------------------
    $venv = $null
    if ($NeedVenv -or $NeedDiffusers -or $NeedComfyLib) {
        # An explicit QWEN_VENV_DIR (set by the launcher, or by hand) wins over
        # both the cache and the directory search, so an environment installed
        # into a user-chosen folder is still found from the project tree.
        if ($env:QWEN_VENV_DIR -and
            (Test-QwenVenvFull -Path $env:QWEN_VENV_DIR -NeedDiffusers:$NeedDiffusers -NeedComfy:$NeedComfyLib)) {
            $venv = (Resolve-Path -LiteralPath $env:QWEN_VENV_DIR).Path
            if (-not $Quiet) { Write-QwenLog "venv from QWEN_VENV_DIR: $venv" ok }
        }
        if (-not $venv) {
            $venv = Find-QwenVenv -StartDir $StartDir -NeedDiffusers:$NeedDiffusers -NeedComfy:$NeedComfyLib -Quiet:$Quiet
        }
        if (-not $venv -and $cacheTrusted -and $cache['QWEN_VENV_DIR']) {
            if (Test-QwenVenvFull -Path $cache['QWEN_VENV_DIR'] -NeedDiffusers:$NeedDiffusers -NeedComfy:$NeedComfyLib) {
                $venv = $cache['QWEN_VENV_DIR']
                if (-not $Quiet) { Write-QwenLog "venv reused from cache: $venv" ok }
            }
        }
        if ($venv) {
            $found.VenvDir = $venv
            $found.Python  = Join-Path $venv 'Scripts\python.exe'
            Set-QwenEnvVar 'QWEN_VENV_DIR' $venv
            Set-QwenEnvVar 'QWEN_PYTHON'   $found.Python
        }
    }

    # ---- HuggingFace model cache -----------------------------------------
    if ($NeedModels) {
        $mc = $null
        if ($env:QWEN_MODELS_CACHE -and
            (Test-QwenModelsCache -Path $env:QWEN_MODELS_CACHE -NeedWeights:$NeedWeights)) {
            $mc = (Resolve-Path -LiteralPath $env:QWEN_MODELS_CACHE).Path
            if (-not $Quiet) { Write-QwenLog "model cache from QWEN_MODELS_CACHE: $mc" ok }
        }
        if (-not $mc) {
            $mc = Find-QwenModelsCache -StartDir $StartDir -NeedWeights:$NeedWeights -Quiet:$Quiet
        }
        if (-not $mc -and $cacheTrusted -and $cache['QWEN_MODELS_CACHE']) {
            if (Test-QwenModelsCache -Path $cache['QWEN_MODELS_CACHE'] -NeedWeights:$NeedWeights) {
                $mc = $cache['QWEN_MODELS_CACHE']
                if (-not $Quiet) { Write-QwenLog "model cache reused from cache: $mc" ok }
            }
        }
        if ($mc) { $found.ModelsCache = $mc; Set-QwenEnvVar 'QWEN_MODELS_CACHE' $mc }
    }

    # ---- ComfyUI checkout -------------------------------------------------
    if ($NeedComfy) {
        $cd = $null
        if ($env:QWEN_COMFYUI_DIR -and (Test-QwenComfyDir -Path $env:QWEN_COMFYUI_DIR)) {
            $cd = (Resolve-Path -LiteralPath $env:QWEN_COMFYUI_DIR).Path
            if (-not $Quiet) { Write-QwenLog "ComfyUI from QWEN_COMFYUI_DIR: $cd" ok }
        }
        if (-not $cd) { $cd = Find-QwenComfyDir -StartDir $StartDir -Quiet:$Quiet }
        if (-not $cd -and $cacheTrusted -and $cache['QWEN_COMFYUI_DIR']) {
            if (Test-QwenComfyDir -Path $cache['QWEN_COMFYUI_DIR']) {
                $cd = $cache['QWEN_COMFYUI_DIR']
                if (-not $Quiet) { Write-QwenLog "ComfyUI reused from cache: $cd" ok }
            }
        }
        if ($cd) { $found.ComfyDir = $cd; Set-QwenEnvVar 'QWEN_COMFYUI_DIR' $cd }
    }

    # ---- outputs ----------------------------------------------------------
    $outBase = if ($found.ProjectRoot) { $found.ProjectRoot } else { $StartDir }
    $outDir = Join-Path $outBase 'outputs'
    if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    $found.OutputDir = $outDir
    Set-QwenEnvVar 'QWEN_OUTPUT_DIR' $outDir

    # ---- persist ----------------------------------------------------------
    $toSave = @{
        QWEN_PROJECT_ROOT = $found.ProjectRoot
        QWEN_VENV_DIR     = $found.VenvDir
        QWEN_MODELS_CACHE = $found.ModelsCache
        QWEN_COMFYUI_DIR  = $found.ComfyDir
    }
    Save-QwenEnvCache -Values $toSave -Path $cachePath

    return $found
}

# --------------------------------------------------------------------------
#  Confirmation gate -- nothing is ever downloaded without an explicit yes
# --------------------------------------------------------------------------
function Confirm-QwenDownload {
    param(
        [Parameter(Mandatory)][string]$What,
        [string]$Source,
        [string]$ApproxSize = '',
        [switch]$AssumeYes
    )
    if ($AssumeYes) { Write-QwenLog "auto-confirmed (--yes): $What" warn; return $true }
    if ($env:QWEN_ASSUME_YES -eq '1') { Write-QwenLog "auto-confirmed (QWEN_ASSUME_YES=1): $What" warn; return $true }
    Write-Host ''
    Write-Host '--------------------------------------------------------------' -ForegroundColor Yellow
    Write-Host '  需要联网下载 / a download is required' -ForegroundColor Yellow
    Write-Host "  内容 what  : $What" -ForegroundColor Yellow
    if ($ApproxSize) { Write-Host "  体积 size  : $ApproxSize" -ForegroundColor Yellow }
    if ($Source)     { Write-Host "  来源 from  : $Source" -ForegroundColor Yellow }
    Write-Host '--------------------------------------------------------------' -ForegroundColor Yellow
    $ans = Read-Host '  继续下载？/ Download now? [y/N]'
    if ($ans -match '^(y|Y|yes|YES|是)$') { return $true }
    Write-QwenLog 'user declined -- nothing downloaded' warn
    return $false
}

# --------------------------------------------------------------------------
#  git / python bootstrap (item 5: hard-coded download strategy)
# --------------------------------------------------------------------------
function Test-QwenCommand { param([string]$Name) return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Test-QwenNativeWeights {
    <#
    .SYNOPSIS
        Is a native BF16 Qwen-Image-2.1 tree actually complete?

    .DESCRIPTION
        The original check accepted ANY single *.safetensors larger than 1 GB
        inside transformer\. The full set is transformer (13.25 GB) +
        text_encoder (16.33 GB) + vae (0.63 GB), all sharded, so a transfer that
        died partway left exactly such a file and every subsequent run reported
        "nothing to download" -- the download could never be resumed.

        This verifies the components, and when a shard index exists, every shard
        it names. Returns { Complete = <bool>; Missing = <string[]> }.
    #>
    param([Parameter(Mandatory)][string] $Dir)

    $missing = @()
    foreach ($sub in @('transformer', 'text_encoder', 'vae')) {
        $p = Join-Path $Dir $sub
        if (-not (Test-Path -LiteralPath $p)) { $missing += "$sub/"; continue }

        $shards = @(Get-ChildItem -LiteralPath $p -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like '*.safetensors' })
        if (-not $shards) { $missing += "$sub/*.safetensors"; continue }

        # an interrupted transfer leaves these behind
        $part = @(Get-ChildItem -LiteralPath $p -File -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -like '*.part' -or $_.Name -like '*.tmp' })
        if ($part) { $missing += "$sub/*.part($($part.Count))" }

        # if the shard index is here, every shard it references must be too
        $idx = Get-ChildItem -LiteralPath $p -File -Filter '*.index.json' -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($idx) {
            try {
                $j = Get-Content -LiteralPath $idx.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                $names = @()
                if ($j.weight_map) {
                    $names = @($j.weight_map.PSObject.Properties |
                               ForEach-Object { $_.Value } | Sort-Object -Unique)
                }
                $gone = @($names | Where-Object {
                    -not (Test-Path -LiteralPath (Join-Path $p [string]$_)) })
                if ($gone.Count) { $missing += "$sub 缺 $($gone.Count)/$($names.Count) 个分片" }
            } catch {
                $missing += "$sub index.json 无法解析"
            }
        }
    }
    return [pscustomobject]@{ Complete = ($missing.Count -eq 0); Missing = $missing }
}

function Invoke-PyProbe {
    <#
    .SYNOPSIS
        Run a python one-liner and return its stdout, or $null.

    .DESCRIPTION
        Windows PowerShell 5.1 raises a *terminating* error the moment a native
        command writes anything to stderr, as long as $ErrorActionPreference is
        'Stop' -- and `2>$null` does not prevent it. A single Python traceback
        (say `import modelscope` on a venv that lacks it) therefore aborted
        `-Action models-native` with "NativeCommandError".

        PowerShell 7 tolerates native stderr, so this only ever bit machines with
        a clean Windows install. Relax the preference for the call, merge stderr
        into the pipeline and drop the error records so nothing escapes.
    #>
    param(
        [Parameter(Mandatory)][string] $Python,
        [Parameter(Mandatory)][string] $Code
    )
    if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { return $null }
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Python -c $Code 2>&1 |
            Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }
        if ($null -eq $out) { return $null }
        return ($out | ForEach-Object { [string]$_ })
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $old
    }
}

function Get-QwenToolsDir {
    $d = Join-Path $PSScriptRoot 'tools'
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    return $d
}

function Get-QwenGit {
    <# Returns a path to git.exe, bootstrapping a portable MinGit if needed. #>
    param([switch]$Quiet)
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $local = Join-Path (Get-QwenToolsDir) 'git\cmd\git.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    return $null
}

function Install-QwenGit {
    param([switch]$AssumeYes)
    $m = Get-QwenMirrors
    $existing = Get-QwenGit -Quiet
    if ($existing) { Write-QwenLog "git already available: $existing" ok; return $existing }

    $ver = '2.47.1.windows.1'
    $file = 'MinGit-2.47.1-64-bit.zip'
    $url  = "$($m.git_for_windows)/v$ver/$file"
    if (-not (Confirm-QwenDownload -What '便携版 Git (MinGit, 免安装)' -Source $url -ApproxSize '~45 MB' -AssumeYes:$AssumeYes)) { return $null }

    $tools = Get-QwenToolsDir
    $zip = Join-Path $tools $file
    Write-QwenLog "downloading $url" step
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 300
    } catch {
        Write-QwenLog "download failed: $_" err
        return $null
    }
    $dest = Join-Path $tools 'git'
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    $git = Join-Path $dest 'cmd\git.exe'
    if (Test-Path -LiteralPath $git) {
        $env:PATH = "$(Join-Path $dest 'cmd');$env:PATH"
        Set-QwenEnvVar 'QWEN_GIT' $git
        Write-QwenLog "git bootstrapped: $git" ok
        return $git
    }
    Write-QwenLog 'MinGit extracted but cmd\git.exe is missing' err
    return $null
}

function Get-QwenPython311 {
    <# Returns a path to a CPython 3.11 interpreter, or $null. #>
    $cached = $env:QWEN_PY_BOOTSTRAP
    if ($cached -and (Test-Path -LiteralPath $cached)) { return $cached }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $p = & $py.Source -3.11 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    foreach ($n in @('python3.11.exe','python.exe')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) {
            $v = & $c.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($v -eq '3.11') { return $c.Source }
        }
    }
    return $null
}
