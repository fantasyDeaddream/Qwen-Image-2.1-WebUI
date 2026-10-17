$ErrorActionPreference = 'Continue'
$root = (Split-Path -Parent $PSScriptRoot)
$files = @(
    (Join-Path $root 'shared\QwenEnv.ps1'),
    (Join-Path $root 'shared\qwen_launch.ps1'),
    (Join-Path $root 'shared\qwen_stop.ps1'),
    (Join-Path $root 'shared\qwen_env.ps1'),
    (Join-Path $root 'setup\qwen_setup.ps1'),
    (Join-Path $root 'setup\qwen_wizard.ps1')
)
Write-Output ("PSVersion = " + $PSVersionTable.PSVersion)
foreach ($f in $files) {
    $err = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$err)
    if ($err -and $err.Count) {
        Write-Output ("  ERR  " + (Split-Path $f -Leaf) + "  " + $err[0].Message)
    } else {
        Write-Output ("  OK   " + (Split-Path $f -Leaf))
    }
}

Write-Output '--- 体积表的每一条规则都能命中 ---'
. (Join-Path $root 'shared\QwenEnv.ps1')
foreach ($n in @('x_mixed_balanced.safetensors', 'y_turbo_int8_convrot.safetensors',
                 'z_qwen3vl_8b_int8_convrot.safetensors', 'a_vae_bf16.safetensors',
                 'b_turbo_lora_avg_rank_178_bf16.safetensors',
                 'c_turbo_q4_k_m.gguf', 'd_int8_convrot.safetensors')) {
    $b = Get-QwenArtifactMinBytes -Name $n
    Write-Output ("  {0,-46} {1,12:N0} B" -f $n, $b)
}
