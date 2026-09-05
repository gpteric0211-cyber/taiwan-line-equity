@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "TARGET=%~dp0.env.line_bot"
set "TEMPLATE=%~dp0review_src\.env.line_bot.example"

if not exist "%TEMPLATE%" (
  echo Missing template: %TEMPLATE%
  pause
  exit /b 2
)

if not exist "%TARGET%" copy /Y "%TEMPLATE%" "%TARGET%" >nul
start "" notepad.exe "%TARGET%"
echo Fill LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN, then save the file.
exit /b 0
