@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Taiwan50 Dashboard
echo ==============================================
echo Taiwan50 Dashboard
echo Folder: %CD%
echo ==============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [MISSING] .venv\Scripts\python.exe
  echo Please run the setup file first:
  echo   first_setup_environment.cmd
  echo or double-click the Chinese-named setup file in this folder.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m uvicorn --version >nul 2>&1
if errorlevel 1 (
  echo [MISSING] uvicorn or required Python packages are not installed.
  echo Please run the setup file first:
  echo   first_setup_environment.cmd
  echo or double-click the Chinese-named setup file in this folder.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" start_dashboard.py

echo.
echo Server stopped.
pause
