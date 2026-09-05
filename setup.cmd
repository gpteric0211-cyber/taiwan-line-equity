@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Install uv or create a Python 3.11-3.13 virtual environment and install review_src\requirements-dev.txt.
  exit /b 1
)
uv sync --frozen
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\python.exe" -m equity init
