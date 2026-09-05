@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo Cloudflare Tunnel is now included in the LINE bot one-click launcher.
echo Redirecting to the LINE bot one-click launcher.
echo.

set "PYTHON_EXE=%~dp0..\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\review_src\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%~dp0..\scripts\start_line_bot_stack.py"
exit /b %ERRORLEVEL%
