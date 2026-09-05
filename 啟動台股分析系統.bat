@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Taiwan50 Dashboard Portable
echo ==============================================
echo Taiwan50 Dashboard Portable
echo Folder: %CD%
echo ==============================================
echo.

set "PY=%~dp0python\python.exe"
if exist "%~dp0app\review_src" (
  if exist "%~dp0python\python.exe" (
    set "PY=%~dp0python\python.exe"
  ) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
  ) else (
    echo Portable Python environment not found or incomplete.
    echo Please rebuild the portable package.
    pause
    exit /b 1
  )
) else (
  if exist "%~dp0python\python.exe" (
    set "PY=%~dp0python\python.exe"
  ) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
  ) else if exist "%~dp0review_src\.venv\Scripts\python.exe" (
    set "PY=%~dp0review_src\.venv\Scripts\python.exe"
  ) else (
    set "PY=python"
  )
)

rem Safe default: opening the dashboard must not fetch or overwrite market data.
set "AUTO_REFRESH_MARKET_DATA_ON_START=0"
"%PY%" "%~dp0start_dashboard.py" --host 127.0.0.1 --port 8000
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo Server stopped with exit code %EXIT_CODE%.
  echo If this is the first run, rebuild the portable folder so .venv contains all required packages.
  pause
  exit /b %EXIT_CODE%
)

echo Server stopped.
pause
