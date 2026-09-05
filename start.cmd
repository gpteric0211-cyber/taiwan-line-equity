@echo off
setlocal
cd /d "%~dp0"
powershell -NoLogo -NoProfile -File "%~dp0tools\run_windows_service.ps1" -Interactive %*
exit /b %errorlevel%
