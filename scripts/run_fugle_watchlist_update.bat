@echo off
setlocal
chcp 65001 > nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

set "PYTHON_EXE="
if exist "%REPO_ROOT%\python\python.exe" set "PYTHON_EXE=%REPO_ROOT%\python\python.exe"
if not defined PYTHON_EXE if exist "%REPO_ROOT%\review_src\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_ROOT%\review_src\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%REPO_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "tokens=*" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

if not defined PYTHON_EXE (
    echo Python not found. Expected portable python, review_src\.venv, repo .venv, or PATH python.
    exit /b 2
)

if not exist "%REPO_ROOT%\logs\fugle_watchlist" mkdir "%REPO_ROOT%\logs\fugle_watchlist"
for /f "tokens=*" %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "RUN_STAMP=%%T"
set "LOG_FILE=%REPO_ROOT%\logs\fugle_watchlist\update_%RUN_STAMP%.log"

"%PYTHON_EXE%" "%REPO_ROOT%\scripts\run_fugle_watchlist_update.py" --mode watchlist %* >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo Fugle watchlist update exit code: %EXIT_CODE%
echo Log: %LOG_FILE%
exit /b %EXIT_CODE%

