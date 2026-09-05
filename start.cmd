@echo off
setlocal
set "AUTO_REFRESH_MARKET_DATA_ON_START=0"
set "AUTO_UPDATE_TW50_ON_START=0"
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Run setup.cmd before starting the service.
  exit /b 1
)
"%PYTHON_EXE%" -u -m equity start %*
exit /b %errorlevel%
