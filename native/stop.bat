@echo off
rem ===========================================================================
rem  Qwen-Image-2.1 native strategy  --  stop and release the model
rem
rem  Run this while the start.bat window is still open. It will:
rem    1. find the running instance via outputs\run\<strategy>.pid or the port,
rem    2. ask it to stop with POST /api/shutdown;
rem    3. the app unloads the models, terminates the ComfyUI child process it
rem       spawned, and exits with code 0 -- so the start terminal ends normally
rem       instead of being killed from the outside;
rem    4. wait for the ports to clear, then force-kill only as a fallback;
rem    5. report how much VRAM came back.
rem
rem  This file is ASCII-only on purpose: cmd.exe parses batch files in the
rem  console codepage, where UTF-8 bytes can break command parsing. All Chinese
rem  output comes from PowerShell.
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
    if not defined TARGET if exist "!CUR!shared\qwen_stop.ps1" set "TARGET=!CUR!shared\qwen_stop.ps1"
    if not defined TARGET (
        for /d %%D in ("!CUR!*") do (
            if not defined TARGET if exist "%%~fD\qwen_stop.ps1" set "TARGET=%%~fD\qwen_stop.ps1"
        )
    )
    for %%P in ("!CUR!..") do set "CUR=%%~fP"
    set "CUR=!CUR!\"
)
if not defined TARGET (
    echo [FAIL] shared\qwen_stop.ps1 not found in this folder, sub folders or 6 parent folders.
    pause
    exit /b 1
)
%PS% -NoProfile -ExecutionPolicy Bypass -File "!TARGET!" -Strategy native %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAIL] stop returned %RC%
)
pause
exit /b %RC%