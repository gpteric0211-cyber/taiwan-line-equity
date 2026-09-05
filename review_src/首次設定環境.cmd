@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Taiwan50 Dashboard Setup
echo ==============================================
echo Taiwan50 Dashboard setup
echo Folder: %CD%
echo ==============================================
echo.

if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found.
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo [INFO] Created .env from .env.example
  ) else (
    echo [INFO] Creating empty .env
    type nul > ".env"
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    python -m venv .venv
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Failed to create virtual environment.
  echo Please install Python 3.11+ and try again.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [INFO] pip is missing in .venv. Restoring pip...
  ".venv\Scripts\python.exe" -m ensurepip --upgrade
  if errorlevel 1 (
    echo [ERROR] Failed to restore pip in .venv.
    pause
    exit /b 1
  )
)

echo [2/2] Installing required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Package installation failed.
  pause
  exit /b 1
)

echo.
echo Setup complete. Run the dashboard launch file to start.
pause
