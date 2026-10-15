@echo off
rem ===========================================================================
rem  Build QwenImageLauncher.exe
rem
rem  Uses PyInstaller from the project venv. Output: launcher\dist\QwenImageLauncher.exe
rem
rem  ASCII-only on purpose: cmd.exe parses batch files in the console codepage,
rem  where UTF-8 bytes can break command parsing.
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "PY=..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [FAIL] project venv not found: %PY%
    echo        run setup\setup_env.bat first.
    pause
    exit /b 1
)

"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [ .. ] installing PyInstaller
    "%PY%" -m pip install --disable-pip-version-check ^
        -i https://mirrors.cloud.tencent.com/pypi/simple pyinstaller
    if errorlevel 1 ( echo [FAIL] pip install failed & pause & exit /b 1 )
)

echo [ .. ] building (this takes a couple of minutes)
"%PY%" -m PyInstaller --clean --noconfirm qwen_launcher.spec
if errorlevel 1 ( echo [FAIL] build failed & pause & exit /b 1 )

echo.
echo [ OK ] dist\QwenImageLauncher.exe
for %%F in ("dist\QwenImageLauncher.exe") do echo        size: %%~zF bytes
echo.
echo  Copy the EXE into the Qwen-Image-2.1-WebUI folder (or any subfolder) and run it.
pause
exit /b 0
