@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0.."

for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set TIMESTAMP=%%i
set LOG_DIR=logs\market_foundation
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if errorlevel 1 (
  echo Failed to create %LOG_DIR%
  echo Cannot continue.
  pause
  exit /b 2
)

set LOG_FILE=%LOG_DIR%\all_market_official_update_%TIMESTAMP%.log
set REPORT_FILE=docs\DAILY_UPDATE_REPORT.txt

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
  echo Python not found. > "%LOG_FILE%"
  echo Python not found. Install Python or create .venv first.
  echo Log: %LOG_FILE%
  pause
  exit /b 2
)
"%PYTHON_EXE%" -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Taipei'); ZoneInfo('America/New_York'); import requests, pandas" >nul 2>&1
if errorlevel 1 (
  echo Selected Python is missing required project dependencies. > "%LOG_FILE%"
  echo Selected Python is unusable. Create .venv or review_src\.venv with project dependencies.
  pause
  exit /b 3
)

echo ============================================================
echo Taiwan50 Dashboard - all-market official update
echo This is a formal DB write.
echo This is not a dry run.
echo Scope: listed + OTC all stocks official data.
echo Yahoo full-market update is not enabled.
echo PChome is not enabled.
echo ============================================================
echo.
echo Running update...

"%PYTHON_EXE%" scripts\update_daily_market_foundation.py --official-only --report-file "%REPORT_FILE%" --log-file "%LOG_FILE%"
set UPDATE_EXIT=%ERRORLEVEL%

echo Report: %REPORT_FILE%
echo Log: %LOG_FILE%
echo Update exit code: %UPDATE_EXIT%

if not "%UPDATE_EXIT%"=="0" (
  echo.
  echo Update failed. Please check the report and log above.
  echo Do not paste JSON output lines back into CMD as commands.
  pause
  exit /b %UPDATE_EXIT%
)

echo.
echo Update completed. Verifying latest DB rows...
"%PYTHON_EXE%" scripts\verify_market_foundation_update.py --min-count 1000
set VERIFY_EXIT=%ERRORLEVEL%

echo.
echo Report: %REPORT_FILE%
echo Log: %LOG_FILE%
echo Exit code: %VERIFY_EXIT%
echo Next check command: python scripts\verify_market_foundation_update.py --min-count 1000

if "%VERIFY_EXIT%"=="0" (
  echo All-market official data imported into DB.
  echo.
  echo Chinese completeness check:
  "%PYTHON_EXE%" scripts\verify_market_foundation_update.py --min-count 1000 --zh --details
  set ZH_VERIFY_EXIT=%ERRORLEVEL%
  if not "%ZH_VERIFY_EXIT%"=="0" (
    echo Chinese completeness check failed. Please run:
    echo python scripts\verify_market_foundation_update.py --min-count 1000 --zh --details
    echo The formal update and DB verification result above are unchanged.
  )
) else (
  echo Update succeeded, but latest DB row count is insufficient. Please check report/log.
)

echo Do not paste JSON output lines back into CMD as commands.
pause
exit /b %VERIFY_EXIT%
