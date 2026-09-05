@echo off
setlocal
call "%~dp0start.cmd" %*
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
