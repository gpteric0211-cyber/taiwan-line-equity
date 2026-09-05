@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

pushd "%~dp0.." || (
  echo Cannot open the project folder.
  pause
  exit /b 2
)

set "PYTHON_EXE=review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "scripts\start_line_bot_stack.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo LINE bot startup failed. Exit code: %EXIT_CODE%
  echo Check ..\.env.line_bot and ..\logs\line_bot\.
  pause
)

popd
exit /b %EXIT_CODE%
