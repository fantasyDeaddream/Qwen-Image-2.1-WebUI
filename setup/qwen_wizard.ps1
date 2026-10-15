# ============================================================================
#  qwen_wizard.ps1 -- interactive front-end for the setup scripts.
#
#  It only *detects* and *dispatches*: everything is installed by qwen_setup.ps1,
#  which this script calls with the right -Action. Nothing is downloaded here.
#
#    setup_wizard.bat                        interactive menu (default)
#    setup_wizard.bat --yes                  also pass -Yes through (no per-file prompt)
#    setup_wizard.bat --plan mixed,turbo     non-interactive: run this selection
#
#  Detection reuses the shared QwenEnv.ps1 search (own dir -> sub dirs -> up to
#  6 parents), so it finds an environment that lives next to, or above, the
#  project -- the same rule every other script uses.
# ============================================================================
[CmdletBinding()]
param(
    [string] $StartDir = '',
    [string[]] $Plan = @(),
    [switch] $Yes
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
$SetupScript = Join-Path $PSScriptRoot 'qwen_setup.ps1'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'shared\QwenEnv.ps1')

if (-not (Test-Path -LiteralPath $SetupScript)) {
    Write-Host "[FAIL] 找不到 $SetupScript" -ForegroundColor Red
    exit 1
}

# The wizard draws its own status table, and the shared logger would otherwise
# interleave its per-step chatter with the menu on every redraw. Shadowing it
# here keeps the shared file untouched; the real installers run as child
# processes and still log in full.
function Write-QwenLog { param([string]$Message, [string]$Level = 'info') }

function Write-Wiz([string]$Message, [string]$Level = 'info') {
    $tag = switch ($Level) { 'ok' { '[ OK ]' } 'warn' { '[WARN]' } 'err' { '[FAIL]' } default { '[INFO]' } }
    $color = switch ($Level) { 'ok' { 'Green' } 'warn' { 'Yellow' } 'err' { 'Red' } default { 'Gray' } }
    Write-Host "$tag $Message" -ForegroundColor $color
}

function Format-QwenPad([string]$Text, [int]$Width) {
    <# Pad to a display width, counting CJK/fullwidth characters as two columns. #>
    $w = 0
    foreach ($ch in $Text.ToCharArray()) { $w += if ([int]$ch -ge 0x1100) { 2 } else { 1 } }
    return $Text + (' ' * [Math]::Max(0, $Width - $w))
}

function Format-QwenSize([double]$GiB) {
    <# "已就绪" reads better than "约 0.00 GiB" when nothing is missing. #>
    if ($GiB -le 0) { return '已就绪' }
    return ('约 {0,6:N2} GiB' -f $GiB)
}

# Prefer PowerShell 7, fall back to Windows PowerShell -- same rule as the .bat files.
$script:PSExe = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }

# Measured on-disk sizes of the ModelScope artifacts, in GiB.
$script:Sizes = @{
    'text_encoders/qwen3vl_8b_int8_convrot.safetensors'              = 8.71
    'vae/qwen_image_2.1_vae_bf16.safetensors'                        = 0.63
    'diffusion_models/qwen_image_2.1_int8_convrot.safetensors'       = 6.76
    'diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors' = 6.76
    'loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors'  = 0.85
}
$script:NativeGiB = 30.21   # transformer 13.25 + text encoder 16.33 + vae 0.63

$script:EncoderSpec = 'text_encoders/qwen3vl_8b_int8_convrot.safetensors'
$script:VaeSpec = 'vae/qwen_image_2.1_vae_bf16.safetensors'
$script:Int8Spec = 'diffusion_models/qwen_image_2.1_int8_convrot.safetensors'
$script:TurboSpecs = @(
    'diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors'
    'loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors'
)

function Write-Head([string]$text) {
    Write-Host ''
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 74) -ForegroundColor DarkCyan
}

$script:TorchCache = @{}
function Get-TorchVersion([string]$VenvDir) {
    <# torch version, or $null when the venv has no working torch. Cached per venv. #>
    if (-not $VenvDir) { return $null }
    if ($script:TorchCache.ContainsKey($VenvDir)) { return $script:TorchCache[$VenvDir] }
    $py = Join-Path $VenvDir 'Scripts\python.exe'
    $ver = $null
    if (Test-Path -LiteralPath $py) {
        try {
            $out = Invoke-PyProbe -Python $py -Code "import importlib.util as u;print(__import__('torch').__version__ if u.find_spec('torch') else '')"
            if ($LASTEXITCODE -eq 0 -and $out) { $ver = ([string]($out | Select-Object -First 1)).Trim() }
        } catch { }
    }
    $script:TorchCache[$VenvDir] = $ver
    return $ver
}

function Get-WizardState {
    <# One detection pass; everything the menu needs to decide with. #>
    $found = Initialize-QwenEnv -StartDir $StartDir -NeedVenv -NeedComfy -NeedModels -NeedWeights -Quiet
    $state = @{
        Found  = $found
        Venv   = $found.VenvDir
        Torch  = $null
        Comfy  = $found.ComfyDir
        Mixed  = $null
        Turbo  = $null
        Native = $false
        W4     = $null
    }
    $state.Torch = Get-TorchVersion $found.VenvDir

    # Same rule Invoke-ModelsMixed uses: a W4 "mixed balanced" checkpoint found
    # anywhere nearby means the diffusion slot is already filled.
    $state.W4 = Find-QwenComfyModelFile -StartDir $StartDir -Folder 'diffusion_models' `
        -Pattern '*mixed*balanced*.safetensors'

    $specs = @($script:EncoderSpec, $script:VaeSpec)
    if (-not $state.W4) { $specs += $script:Int8Spec }
    $state.Mixed = Resolve-QwenModelTargets -StartDir $StartDir -ComfyDir $state.Comfy -Specs $specs
    $state.Turbo = Resolve-QwenModelTargets -StartDir $StartDir -ComfyDir $state.Comfy -Specs $script:TurboSpecs

    if ($found.ModelsCache) { $state.Native = Test-QwenModelsCache -Path $found.ModelsCache -NeedWeights }
    return $state
}

function Get-MissingGiB($ModelPlan) {
    $sum = 0.0
    foreach ($spec in $ModelPlan.Missing) {
        if ($script:Sizes.ContainsKey($spec)) { $sum += $script:Sizes[$spec] }
    }
    return $sum
}

function Write-ModelRow([string]$Spec, [bool]$Present, [string]$Note = '') {
    $size = if ($script:Sizes.ContainsKey($Spec)) { '{0,5:N2} GiB' -f $script:Sizes[$Spec] } else { '          ' }
    Write-Host ("    [{0}] {1,-62} {2} {3}" -f $(if ($Present) { 'OK  ' } else { 'MISS' }), $Spec, $size, $Note) `
        -ForegroundColor $(if ($Present) { 'Green' } else { 'Yellow' })
}

function Show-State($State) {
    Write-Head '当前状态 / detected state'
    foreach ($pair in @(
            @('项目根目录', $State.Found.ProjectRoot),
            @('Python 环境', $State.Venv),
            @('ComfyUI', $State.Comfy),
            @('模型缓存', $State.Found.ModelsCache),
            @('torch', $State.Torch))) {
        Write-Host ("  [{0}] {1} {2}" -f $(if ($pair[1]) { 'OK  ' } else { 'MISS' }), `
                (Format-QwenPad $pair[0] 16), $(if ($pair[1]) { $pair[1] } else { '-' })) `
            -ForegroundColor $(if ($pair[1]) { 'Green' } else { 'DarkGray' })
    }

    Write-Host ''
    Write-Host '  量化路线 (mixed / mixed-vram / turbo):' -ForegroundColor Gray
    foreach ($spec in @($script:EncoderSpec, $script:VaeSpec)) {
        Write-ModelRow $spec $State.Mixed.Present.ContainsKey($spec)
    }
    if ($State.W4) {
        Write-ModelRow $script:Int8Spec $true '已有 W4 量化，无需下载'
    } else {
        Write-ModelRow $script:Int8Spec $State.Mixed.Present.ContainsKey($script:Int8Spec)
    }
    Write-Host '  Turbo 附加:' -ForegroundColor Gray
    foreach ($spec in $script:TurboSpecs) {
        Write-ModelRow $spec $State.Turbo.Present.ContainsKey($spec)
    }
    Write-Host '  全量路线 (native):' -ForegroundColor Gray
    Write-Host ("    [{0}] {1,-62} {2,5:N2} GiB" -f $(if ($State.Native) { 'OK  ' } else { 'MISS' }), `
            'models_cache\models--Qwen--Qwen-Image-2.1  (BF16 全套)', $script:NativeGiB) `
        -ForegroundColor $(if ($State.Native) { 'Green' } else { 'Yellow' })
    Write-Host ''
}

function Expand-Plan([string[]]$Items) {
    <# Add each selection's prerequisites and fix a safe execution order. #>
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($i in $Items) { if ($i) { [void]$set.Add($i.ToLower()) } }
    if ($set.Contains('comfyui') -or $set.Contains('mixed') -or $set.Contains('turbo') -or $set.Contains('native')) {
        [void]$set.Add('env')
    }
    if ($set.Contains('mixed') -or $set.Contains('turbo')) { [void]$set.Add('comfyui') }
    # 'turbo' runs models-mixed -WithGguf, which already covers the plain set.
    if ($set.Contains('turbo')) { [void]$set.Remove('mixed') }
    return @('env', 'comfyui', 'mixed', 'turbo', 'native', 'fix-torch') | Where-Object { $set.Contains($_) }
}

function Test-AlreadyDone([string]$Item, $State) {
    switch ($Item) {
        'env'     { return [bool]$State.Torch }
        'comfyui' { return [bool]$State.Comfy }
        'mixed'   { return ($State.Mixed.Missing.Count -eq 0) }
        'turbo'   { return ($State.Turbo.Missing.Count -eq 0) }
        'native'  { return [bool]$State.Native }
        default   { return $false }
    }
}

function Get-ItemTitle([string]$Item) {
    switch ($Item) {
        'env'     { return 'Python 环境 (venv + torch cu130)' }
        'comfyui' { return 'ComfyUI (含 Qwen-Image-2.1 支持校验)' }
        'mixed'   { return '量化模型三件套' }
        'turbo'   { return '量化三件套 + Turbo 附加' }
        'native'  { return '全量原生 BF16 模型' }
        'fix-torch' { return '修复 torch / torchaudio' }
        default   { return $Item }
    }
}

function Get-ItemSize([string]$Item, $State) {
    switch ($Item) {
        'mixed'  { return (Get-MissingGiB $State.Mixed) }
        'turbo'  { return ((Get-MissingGiB $State.Mixed) + (Get-MissingGiB $State.Turbo)) }
        'native' { return $script:NativeGiB }
        default  { return 0.0 }
    }
}

function Invoke-Setup([string]$Item) {
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $SetupScript,
           '-Action', $(if ($Item -eq 'turbo') { 'models-mixed' } else { $Item }),
           '-StartDir', $StartDir)
    if ($Item -eq 'turbo') { $a += '-WithGguf' }
    if ($Yes) { $a += '-Yes' }
    & $script:PSExe @a
    return $LASTEXITCODE
}

function Invoke-Plan([string[]]$Items) {
    $plan = @(Expand-Plan $Items)
    if (-not $plan.Count) { Write-Wiz '没有可执行的项目。' warn; return }

    $state = Get-WizardState
    Write-Head '执行计划 / plan'
    $todo = @()
    foreach ($item in $plan) {
        if (Test-AlreadyDone $item $state) {
            Write-Host ("  [跳过] {0}  -- 已就绪" -f (Get-ItemTitle $item)) -ForegroundColor DarkGray
            continue
        }
        $size = Get-ItemSize $item $state
        $extra = if ($size -gt 0) { '  约 {0:N2} GiB' -f $size } else { '' }
        Write-Host ("  [执行] {0}{1}" -f (Get-ItemTitle $item), $extra) -ForegroundColor Yellow
        $todo += $item
    }
    if (-not $todo.Count) {
        Write-Host ''
        Write-Wiz '选中的内容都已经就绪，不需要下载任何东西。' ok
        return
    }
    Write-Host ''
    $ans = Read-Host '  开始执行？(y/N)'
    if ($ans -notmatch '^(y|yes|是)$') { Write-Wiz '已取消。' warn; return }

    foreach ($item in $todo) {
        Write-Head ("-> $(Get-ItemTitle $item)")
        $rc = Invoke-Setup $item
        if ($rc -ne 0) {
            Write-Wiz "该步骤退出码 $rc，已停止后续步骤。" err
            Write-Host '  处理完问题后重新运行本脚本即可，已下载的文件会被跳过。' -ForegroundColor Yellow
            return
        }
        # The environment may have just changed; drop the cached probe.
        $script:TorchCache.Clear()
    }
    Write-Host ''
    Write-Wiz '全部完成。/ all done.' ok
    Show-State (Get-WizardState)
}

function Show-Menu($State) {
    Write-Head 'Qwen-Image-2.1 安装向导 / installer'
    Show-State $State

    $mixedGiB = Get-MissingGiB $State.Mixed
    $turboGiB = $mixedGiB + (Get-MissingGiB $State.Turbo)
    Write-Host '  请选择要安装的内容：' -ForegroundColor White
    Write-Host ("   [1] {0} {1}" -f (Format-QwenPad '量化模型三件套（文本编码器 + VAE + W4/int8 扩散）' 52), (Format-QwenSize $mixedGiB))
    Write-Host ("   [2] {0} {1}" -f (Format-QwenPad '三件套 + Turbo 附加（Turbo int8 扩散 + Turbo LoRA）' 52), (Format-QwenSize $turboGiB))
    Write-Host ("   [3] {0} {1}" -f (Format-QwenPad '全量原生 BF16 模型' 52), (Format-QwenSize $script:NativeGiB))
    Write-Host ("   [4] Python 环境（venv + torch cu130）                   $(if ($State.Torch) { '已就绪' } else { '缺失' })")
    Write-Host ("   [5] ComfyUI（含 Qwen-Image-2.1 支持校验）               $(if ($State.Comfy) { '已就绪' } else { '缺失' })")
    Write-Host ''
    Write-Host '   [A] 一键推荐：量化路线（自动跳过已就绪的部分）' -ForegroundColor Cyan
    Write-Host '   [S] 重新检测' -ForegroundColor DarkGray
    Write-Host '   [Q] 退出' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '   注：Turbo GGUF 与它的隔离运行时不在本向导范围内（模型来自 Abiray 仓库，' -ForegroundColor DarkGray
    Write-Host '   且需要单独的 ComfyUI 检出）。见 ..\gguf\README.md。' -ForegroundColor DarkGray
    Write-Host ''
}

# ---------------------------------------------------------------------------
#  non-interactive
# ---------------------------------------------------------------------------
if ($Plan.Count) {
    Invoke-Plan $Plan
    exit 0
}

# ---------------------------------------------------------------------------
#  interactive loop
# ---------------------------------------------------------------------------
while ($true) {
    $state = Get-WizardState
    Show-Menu $state
    $choice = (Read-Host '  输入选项').Trim().ToUpper()

    $handled = $true
    switch ($choice) {
        '1' { Invoke-Plan @('mixed') }
        '2' { Invoke-Plan @('turbo') }
        '3' { Invoke-Plan @('native') }
        '4' { Invoke-Plan @('env') }
        '5' { Invoke-Plan @('comfyui') }
        'A' { Invoke-Plan @('mixed') }
        'S' { }
        'Q' { Write-Wiz '退出。/ bye.'; exit 0 }
        '' { }
        default { Write-Wiz "无效选项：$choice" warn; $handled = $false }
    }
    if ($handled -and $choice -ne '') {
        Write-Host ''
        Read-Host '  按回车返回菜单 / press Enter to return' | Out-Null
    }
}
