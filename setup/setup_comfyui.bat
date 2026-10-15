@echo off
rem ===========================================================================
rem  Qwen-Image-2.1 ComfyUI setup
rem
rem  Clones and verifies ComfyUI (needed by the mixed strategies only).
rem  Reuses an existing checkout found nearby. Options: --yes  --with-gguf
rem
rem  ASCII-only on purpose: cmd.exe parses batch files in the console codepage,
rem  where UTF-8 bytes can break command parsing. All Chinese output comes from
rem  PowerShell. See setup\README.md for the full install and run order.
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
set "OPTS="
for %%A in (%*) do (
    if /i "%%~A"=="--yes"       set "OPTS=!OPTS! -Yes"
    if /i "%%~A"=="--with-gguf" set "OPTS=!OPTS! -WithGguf"
)
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0qwen_setup.ps1" -Action comfyui !OPTS!
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAIL] exit code %RC%
)
pause
exit /b %RC%