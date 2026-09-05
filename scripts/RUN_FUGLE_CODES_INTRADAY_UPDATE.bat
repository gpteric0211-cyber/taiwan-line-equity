@echo off
chcp 65001 > nul
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

set "CODES=%~1"
if "%CODES%"=="" set "CODES=2317,3491,2382"

set "PYTHON_EXE=%REPO_ROOT%\review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo ============================================================
echo Fugle intraday supplemental update
echo Repo: %REPO_ROOT%
echo Codes: %CODES%
echo.
echo This writes only:
echo - trades / time-sales
echo - price-volume distribution
echo - bid/ask volume summary
echo.
echo It does NOT update official daily OHLCV.
echo It is NOT a full-market update.
echo ============================================================
echo.

"%PYTHON_EXE%" scripts\update_fugle_intraday_supplemental.py --codes "%CODES%" --write --output docs\FUGLE_INTRADAY_UPDATE_REPORT.md
set "UPDATE_EXIT=%ERRORLEVEL%"
echo.
echo Update exit code: %UPDATE_EXIT%

if not "%UPDATE_EXIT%"=="0" (
    echo Fugle supplemental update failed. Verify step skipped.
    pause
    exit /b %UPDATE_EXIT%
)

echo.
echo Running market foundation verification...
"%PYTHON_EXE%" scripts\verify_market_foundation_update.py --min-count 1000 --zh --details
set "VERIFY_EXIT=%ERRORLEVEL%"
echo.
echo Verify exit code: %VERIFY_EXIT%

pause
exit /b %VERIFY_EXIT%
