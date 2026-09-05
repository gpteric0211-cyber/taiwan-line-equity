@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

set "PYTHON_EXE=%REPO_ROOT%\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%\review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if not exist "%REPO_ROOT%\logs\post_close" mkdir "%REPO_ROOT%\logs\post_close"
for /f "tokens=*" %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "RUN_STAMP=%%T"
set "LOG_FILE=%REPO_ROOT%\logs\post_close\update_%RUN_STAMP%.log"

echo Post-close official market update started. Log: %LOG_FILE%
"%PYTHON_EXE%" "%REPO_ROOT%\scripts\run_post_close_daily_pipeline.py" --stage finalize %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo Exit code: %EXIT_CODE%
echo Log: %LOG_FILE%
exit /b %EXIT_CODE%
