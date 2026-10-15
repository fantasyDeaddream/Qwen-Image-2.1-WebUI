@echo off
rem ===========================================================================
rem  Qwen-Image-2.1 interactive installer wizard
rem
rem  Detects what is already installed, shows it, lets you pick which model set
rem  to install, and calls the matching setup script. Nothing is downloaded by
rem  this file itself.
rem
rem  Usage:
rem    setup_wizard.bat                      interactive menu
rem    setup_wizard.bat --yes                run without the per-file prompts
rem    setup_wizard.bat --plan mixed,turbo   non-interactive selection
rem
rem  Valid plan items: env, comfyui, mixed, turbo, native, fix-torch
rem
rem  ASCII-only on purpose: cmd.exe parses batch files in the console codepage,
rem  where UTF-8 bytes can break command parsing. All Chinese output comes from
rem  PowerShell. See setup\README.md for the full install and run order.
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "INTERACTIVE_ONLY="
if "%~1"=="" set "INTERACTIVE_ONLY=1"
set "PS="
where pwsh.exe >nul 2>nul && set "PS=pwsh.exe"
if not defined PS ( where powershell.exe >nul 2>nul && set "PS=powershell.exe" )
if not defined PS (
    echo [FAIL] PowerShell is required but was not found.
    pause
    exit /b 1
)

rem Translate the documented --flags into PowerShell parameters.
set "ARGS="
:parse
if "%~1"=="" goto run
if /i "%~1"=="--yes" goto arg_yes
if /i "%~1"=="-y" goto arg_yes
if /i "%~1"=="--plan" goto arg_plan
set "ARGS=%ARGS% %~1"
shift
goto parse
:arg_yes
set "ARGS=%ARGS% -Yes"
shift
goto parse
:arg_plan
set "ARGS=%ARGS% -Plan %~2"
shift
shift
goto parse

:run
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0qwen_wizard.ps1" %ARGS%
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAIL] exit code %RC%
)
if defined INTERACTIVE_ONLY (
    echo.
    pause
)
exit /b %RC%
