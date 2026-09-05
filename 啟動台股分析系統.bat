@echo off
setlocal
set "AUTO_REFRESH_MARKET_DATA_ON_START=0"
call "%~dp0start.cmd" --open-browser %*
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
