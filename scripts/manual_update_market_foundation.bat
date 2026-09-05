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

set LOG_FILE=%LOG_DIR%\manual_update_%TIMESTAMP%.log
set REPORT_FILE=docs\DAILY_UPDATE_REPORT.txt

echo ============================================================
echo Manual official market foundation update
echo Default source mode: official-only
echo If --dry-run is NOT provided, this command writes to DB.
echo If --dry-run is provided, it verifies only and does not write DB.
echo Yahoo full-market and PChome are not enabled by default.
echo ============================================================
echo %* | findstr /I /C:"--dry-run" > nul
if not errorlevel 1 (
  echo Current mode: DRY RUN, no DB write.
) else (
  echo Current mode: FORMAL UPDATE, DB write may occur.
)
echo %* | findstr /I /C:"--codes" > nul
if not errorlevel 1 (
  echo Scope: specified stock codes only, not all-market.
) else (
  echo Scope: listed + OTC all-market official data.
)
echo.

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

if "%~1"=="" (
  set DEFAULT_ARGS=--official-only --report-file "%REPORT_FILE%" --log-file "%LOG_FILE%"
  echo Manual market foundation update started with default official-only mode.
  "%PYTHON_EXE%" scripts\update_daily_market_foundation.py --official-only --report-file "%REPORT_FILE%" --log-file "%LOG_FILE%"
) else (
  echo Manual market foundation update started with user arguments.
  "%PYTHON_EXE%" scripts\update_daily_market_foundation.py --report-file "%REPORT_FILE%" --log-file "%LOG_FILE%" %*
)
set EXIT_CODE=%ERRORLEVEL%
echo Report: %REPORT_FILE%
echo Log: %LOG_FILE%
echo Exit code: %EXIT_CODE%
echo Do not paste JSON output lines back into CMD as commands.
exit /b %EXIT_CODE%
