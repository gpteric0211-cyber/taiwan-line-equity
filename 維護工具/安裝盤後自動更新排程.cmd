@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\install_windows_post_close_tasks.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Scheduled task installation failed. Exit code: %EXIT_CODE%
  echo Try running this file as Administrator.
  pause
)
exit /b %EXIT_CODE%
