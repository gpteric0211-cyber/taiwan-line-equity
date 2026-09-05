@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

set "PYTHON_EXE=%REPO_ROOT%python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo ============================================================
echo Manual one-click daily analysis data update
echo No Windows scheduled task will be created or enabled.
echo The live DB is replaced only after the isolated safe-publication gate passes.
echo Scope: ALL active listed and OTC stocks.
echo Data: missing-day catch-up, close, price-volume, technical, institutions, credit,
echo lending, valuation, chip momentum, plus Taiwan50 compatibility batch.
echo ============================================================
echo.

call "%PYTHON_EXE%" -X utf8 "%REPO_ROOT%scripts\run_isolated_manual_daily_analysis_update.py" %*
set "UPDATE_EXIT=%ERRORLEVEL%"
if not "%UPDATE_EXIT%"=="0" goto update_failed
if /I "%~1"=="--plan-only" goto success

echo.
echo Update and same-date live DB verification completed.
goto success

:update_failed
echo.
echo Update or completeness verification did not pass. Check the isolation report for published status.
set "FINAL_EXIT=%UPDATE_EXIT%"
goto report

:success
echo.
echo Update and verification completed successfully.
set "FINAL_EXIT=0"

:report
echo.
echo Update report: docs\MANUAL_DAILY_ANALYSIS_UPDATE_REPORT.json
echo Verification report: docs\MANUAL_DAILY_ANALYSIS_UPDATE_VERIFICATION.json
echo Isolation report: logs\manual_daily_update\isolated_update_latest.json
echo Exit code: %FINAL_EXIT%
if /I "%~1"=="--plan-only" goto done
pause

:done
exit /b %FINAL_EXIT%
