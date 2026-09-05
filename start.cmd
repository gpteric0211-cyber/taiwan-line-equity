@echo off
setlocal
cd /d "%~dp0"
if exist "python\python.exe" goto bundled
if exist ".venv\Scripts\python.exe" goto virtual
echo Run setup.cmd once with a compatible Python installation.
exit /b 1
:bundled
"python\python.exe" -m equity run %*
exit /b %errorlevel%
:virtual
".venv\Scripts\python.exe" -m equity run %*
exit /b %errorlevel%
