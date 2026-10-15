@echo off
rem ===========================================================================
rem  Qwen-Image-2.1 environment setup
rem
rem  Creates the Python 3.11 venv and installs torch (cu130), diffusers and
rem  the application requirements. Python and git are downloaded automatically
rem  if missing. Reuses an existing environment found nearby.
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

%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0qwen_setup.ps1" -Action env %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAIL] exit code %RC%
)
pause
exit /b %RC%