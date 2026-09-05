@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0.."

for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set TIMESTAMP=%%i
set LOG_DIR=logs\market_foundation
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
  echo Failed to create %LOG_DIR%
  exit /b 2
)

set LOG_FILE=%LOG_DIR%\backfill_%TIMESTAMP%.log
set REPORT_FILE=docs\BACKFILL_UPDATE_REPORT.txt

set PYTHON_EXE=.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=review_src\.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" (
  for /f "tokens=*" %%p in ('where python 2^>nul') do (
    set PYTHON_EXE=%%p
    goto :found_python
  )
)
:found_python
if not exist "%PYTHON_EXE%" (
  echo Python not found. >> "%LOG_FILE%"
  echo Python not found. Install Python or create .venv first.
  exit /b 2
)
"%PYTHON_EXE%" -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Taipei'); ZoneInfo('America/New_York'); import requests, pandas" >nul 2>&1
if errorlevel 1 (
  echo Selected Python is missing required project dependencies. >> "%LOG_FILE%"
  echo Selected Python is unusable. Create .venv or review_src\.venv with project dependencies.
  exit /b 3
)

echo Backfill market foundation update started. Log: %LOG_FILE%
"%PYTHON_EXE%" scripts\backfill_market_foundation_600.py --official-only --days 600 --resume --report-file "%REPORT_FILE%" --log-file "%LOG_FILE%" %*
set EXIT_CODE=%ERRORLEVEL%
echo Report: %REPORT_FILE%
echo Log: %LOG_FILE%
echo Exit code: %EXIT_CODE%
exit /b %EXIT_CODE%
