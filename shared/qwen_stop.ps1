# ============================================================================
#  qwen_stop.ps1 -- stop a strategy cleanly and release its VRAM.
#
#  Run it while a start.bat window is open. It
#    1. finds the running app by its pid file (outputs\run\<strategy>.pid),
#    2. asks it to shut down over POST /api/shutdown, which makes the app
#       unload the models, terminate the ComfyUI child process it spawned, and
#       exit with code 0 -- so the start script's terminal ends on request
#       rather than being killed from the outside,
#    3. waits for the ports to clear, then force-kills anything still holding
#       them (a fallback for an app that was already wedged),
#    4. verifies VRAM came back.
#
#  Usage:
#     qwen_stop.ps1 -Strategy mixed-vram
#     qwen_stop.ps1 -All
# ============================================================================
[CmdletBinding()]
param(
    [ValidateSet('native', 'mixed', 'mixed-vram', 'turbo', 'gguf', 'all')][string] $Strategy = 'all',
    [int] $TimeoutSeconds = 45,
    [switch] $Quiet
)
# --- force UTF-8 stdio -------------------------------------------------------
# A Chinese Windows leaves the console at codepage 936, which would turn every
# CJK path this script prints into bytes the launcher cannot decode. Pin it.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }


$ErrorActionPreference = 'Stop'

# $PSScriptRoot is populated when this file is run with -File (how the launcher
# and the .bat wrappers always call it) but is empty under -Command. Resolve it
# before anything derives a path from it: Join-Path/Split-Path throw on an
# empty base with "Cannot bind argument to parameter 'Path'".
if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $PSScriptRoot) { $PSScriptRoot = (Get-Location).ProviderPath }

$Shared = $PSScriptRoot
. (Join-Path $Shared 'QwenEnv.ps1')

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $ProjectRoot 'outputs\run'
$Ports = @{ native = @(7860); mixed = @(7860, 8188); 'mixed-vram' = @(7860, 8188); turbo = @(7860, 8188); gguf = @(7860, 8189) }
# Only these strategies drive a ComfyUI backend, so only they may touch 8188.
$UsesComfy = @{ native = $false; mixed = $true; 'mixed-vram' = $true; turbo = $true; gguf = $true }

function Get-StrategyList {
    if ($Strategy -eq 'all') { return @('native', 'mixed', 'mixed-vram', 'turbo', 'gguf') }
    return @($Strategy)
}

function Get-ListeningPid([int]$Port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

function Get-VramUsed {
    try {
        return [int](& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null |
                        Select-Object -First 1)
    } catch { return -1 }
}

$vramBefore = Get-VramUsed
if (-not $Quiet) {
    Write-Host ''
    Write-Host ('=' * 70) -ForegroundColor DarkCyan
    Write-Host '  停止 Qwen-Image-2.1 / stopping' -ForegroundColor Cyan
    Write-Host ('=' * 70) -ForegroundColor DarkCyan
    Write-QwenLog "VRAM before: $vramBefore MiB" info
}

$stoppedAny = $false
foreach ($name in (Get-StrategyList)) {
    $pidFile = Join-Path $RunDir "$name.pid"
    $appPid = $null
    if (Test-Path -LiteralPath $pidFile) {
        try { $appPid = (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json).pid } catch { }
    }

    $handled = $false

    # ---- 1. graceful request over the API -------------------------------
    foreach ($port in $Ports[$name]) {
        if ($handled) { break }
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3 -ErrorAction Stop
            if ($r.strategy -ne $name) { continue }
            Write-QwenLog "$name is running on port $port (backend=$($r.backend)) -- asking it to stop" step
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/shutdown" -Method Post `
                    -TimeoutSec 10 -ErrorAction SilentlyContinue | Out-Null
            } catch { }
            $handled = $true
        } catch { }
    }

    # ---- 2. wait for a clean exit ---------------------------------------
    if ($handled) {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            $still = $false
            foreach ($port in $Ports[$name]) { if (Get-ListeningPid $port) { $still = $true } }
            if (-not $still) { break }
        }
    }

    # ---- 3. force-kill whatever is left ---------------------------------
    # Only our own processes: the pid we wrote, plus a port owner whose command
    # line actually points at this strategy's folder. Without that check,
    # stop.bat for one strategy could kill a different strategy that happens to
    # be listening on the same port.
    $leftover = @()
    if ($appPid -and (Get-Process -Id $appPid -ErrorAction SilentlyContinue)) {
        $leftover += [pscustomobject]@{ Port = 0; Pid = $appPid }
    }
    if ($handled) {
        foreach ($port in $Ports[$name]) {
            $lp = Get-ListeningPid $port
            if (-not $lp) { continue }
            $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$lp" -ErrorAction SilentlyContinue).CommandLine
            if ($cmd -and ($cmd -like "*\$name\*" -or $cmd -like "*$name/app.py*")) {
                $leftover += [pscustomobject]@{ Port = $port; Pid = $lp }
            } else {
                Write-QwenLog "port $port is held by pid $lp which is not our $name app -- leaving it alone" warn
            }
        }
    }
    foreach ($item in ($leftover | Sort-Object Pid -Unique)) {
        Write-QwenLog "$name still alive (pid $($item.Pid)$(if($item.Port){" on port $($item.Port)"})) -- killing" warn
        try {
            # stop children first (the ComfyUI backend), then the parent
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$($item.Pid)" -ErrorAction SilentlyContinue |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Stop-Process -Id $item.Pid -Force -ErrorAction SilentlyContinue
        } catch { }
        $handled = $true
    }

    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }

    if ($handled) {
        Write-QwenLog "$name stopped" ok
        $stoppedAny = $true
    } elseif (-not $Quiet) {
        Write-QwenLog "$name was not running" info
    }
}

# ---- 4. any ComfyUI child left on 8188? --------------------------------
# Guarded: a native-only stop must not tear down another strategy's backend.
$scopeUsesComfy = $false
foreach ($n in (Get-StrategyList)) { if ($UsesComfy[$n]) { $scopeUsesComfy = $true } }
if ($scopeUsesComfy) {
    $comfyPid = Get-ListeningPid 8188
    if ($comfyPid) {
        Write-QwenLog "orphan ComfyUI on 8188 (pid $comfyPid) -- killing" warn
        Stop-Process -Id $comfyPid -Force -ErrorAction SilentlyContinue
        $stoppedAny = $true
    }
} elseif (Get-ListeningPid 8188) {
    Write-QwenLog 'ComfyUI is running on 8188 but belongs to another strategy -- not touching it' info
}

Start-Sleep -Seconds 2
$vramAfter = Get-VramUsed
if (-not $Quiet) {
    Write-Host ''
    Write-QwenLog "VRAM after : $vramAfter MiB  (released ~$([Math]::Max(0, $vramBefore - $vramAfter)) MiB)" ok
    $ports = foreach ($p in @(7860, 7861, 8188)) { if (Get-ListeningPid $p) { $p } }
    if ($ports) {
        Write-QwenLog "ports still listening: $($ports -join ', ')" warn
    } else {
        Write-QwenLog 'all ports released' ok
    }
    if ($stoppedAny) {
        Write-QwenLog '可以关闭启动脚本的终端了 / the start terminal can be closed' ok
    }
}
