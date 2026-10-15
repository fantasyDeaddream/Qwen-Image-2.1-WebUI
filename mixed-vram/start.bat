@echo off
rem ===========================================================================
rem  Qwen-Image-2.1 mixed-vram strategy  (ComfyUI, explicit placement)
rem
rem  Qwen3-VL text encoder (8.7 GiB, the only huge piece) in system RAM;
rem  W4 diffusion (3.91 GiB) and BF16 VAE (0.63 GiB) resident in VRAM.
rem
rem  On start it searches this folder, its sub folders and up to 6 parent
rem  folders for the Python environment and the offline model files. What it
rem  finds is cached as QWEN_* environment variables and the app starts. If
rem  something is missing it names it and offers to run the matching setup
rem  script; it never begins a download by itself.
rem
rem  All Chinese messages come from PowerShell, which handles UTF-8 correctly;
rem  this file is deliberately ASCII-only because cmd.exe parses batch files in
rem  the console codepage, where UTF-8 bytes can break command parsing.
rem
rem  Options:  --api-only     no WebUI, HTTP API only
rem            --no-browser   do not open a browser window
rem            --yes          do not prompt before running a setup script
rem
rem  To stop: run stop.bat in this same folder.
rem ===========================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "PS="
where pwsh.exe >nul 2>nul && set "PS=pwsh.exe"
if not defined PS ( where powershell.exe >nul 2>nul && set "PS=powershell.exe" )
if not defined PS (
    echo [FAIL] PowerShell is required but was not found.
    pause
    exit /b 1
)
rem --- locate a script in shared\: own folder, sub folders, then 6 parents
set "TARGET="
set "CUR=%~dp0"
for /l %%I in (1,1,7) do (
    if not defined TARGET if exist "!CUR!shared\qwen_launch.ps1" set "TARGET=!CUR!shared\qwen_launch.ps1"
    if not defined TARGET (
        for /d %%D in ("!CUR!*") do (
            if not defined TARGET if exist "%%~fD\qwen_launch.ps1" set "TARGET=%%~fD\qwen_launch.ps1"
        )
    )
    for %%P in ("!CUR!..") do set "CUR=%%~fP"
    set "CUR=!CUR!\"
)
if not defined TARGET (
    echo [FAIL] shared\qwen_launch.ps1 not found in this folder, sub folders or 6 parent folders.
    pause
    exit /b 1
)
%PS% -NoProfile -ExecutionPolicy Bypass -File "!TARGET!" -Strategy mixed-vram %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAIL] exit code %RC%
    pause
)
exit /b %RC%