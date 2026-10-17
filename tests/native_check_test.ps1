$ErrorActionPreference = 'Stop'
$root = (Split-Path -Parent $PSScriptRoot)
. (Join-Path $root 'shared\QwenEnv.ps1')

$d = Join-Path $env:TEMP 'qwen_partial_fixture\Qwen-Image-2.1'

Write-Output '=== OLD rule: any *.safetensors larger than 1GB ==='
$probe = Join-Path $d 'transformer'
$w = Get-ChildItem -LiteralPath $probe -File |
     Where-Object { $_.Name -like '*.safetensors' -and $_.Length -gt 1GB }
if ($w) {
    Write-Output '  -> declared COMPLETE, download skipped   <-- this was the bug'
} else {
    Write-Output '  -> judged incomplete'
}

Write-Output '=== NEW rule: Test-QwenNativeWeights ==='
$chk = Test-QwenNativeWeights -Dir $d
Write-Output ('  Complete = ' + $chk.Complete)
Write-Output ('  Missing  = ' + ($chk.Missing -join ' | '))

Write-Output '=== control: a complete-looking tree ==='
$good = Join-Path $env:TEMP 'qwen_partial_fixture\good'
foreach ($sub in @('transformer', 'text_encoder', 'vae')) {
    $p = Join-Path $good $sub
    New-Item -ItemType Directory -Path $p -Force | Out-Null
    $f = Join-Path $p 'model.safetensors'
    $fs = [System.IO.File]::Create($f); $fs.SetLength(2MB); $fs.Close()
}
$chk2 = Test-QwenNativeWeights -Dir $good
Write-Output ('  Complete = ' + $chk2.Complete + '   Missing = [' + ($chk2.Missing -join ' | ') + ']')
