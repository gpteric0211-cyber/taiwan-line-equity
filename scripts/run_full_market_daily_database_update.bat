@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

set "PYTHON_EXE=%REPO_ROOT%\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%\review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "LOG_DIR=%REPO_ROOT%\logs\post_close_scheduler"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul
if exist "%LOG_DIR%" (
    for /f %%I in ('powershell -NoProfile -NonInteractive -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_STAMP=%%I"
)
if defined RUN_STAMP set "LOG_FILE=%LOG_DIR%\post_close_%RUN_STAMP%.log"

if defined LOG_FILE goto run_logged

"%PYTHON_EXE%" "%REPO_ROOT%\scripts\run_isolated_post_close_pipeline.py" %*
set "RUN_EXIT_CODE=%ERRORLEVEL%"
goto finish

:run_logged
>> "%LOG_FILE%" echo [%DATE% %TIME%] scheduled post-close update started
>> "%LOG_FILE%" echo Python: %PYTHON_EXE%
>> "%LOG_FILE%" echo Arguments: %*
"%PYTHON_EXE%" "%REPO_ROOT%\scripts\run_isolated_post_close_pipeline.py" %* >> "%LOG_FILE%" 2>&1
set "RUN_EXIT_CODE=%ERRORLEVEL%"
>> "%LOG_FILE%" echo [%DATE% %TIME%] scheduled post-close update finished with exit code %RUN_EXIT_CODE%

:finish
exit /b %RUN_EXIT_CODE%
