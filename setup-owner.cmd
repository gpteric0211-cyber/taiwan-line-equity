@echo off
setlocal
pushd "%~dp0"
if exist "python\python.exe" (
  "python\python.exe" -m equity.membership_admin create-owner
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m equity.membership_admin create-owner
) else (
  echo Project Python runtime missing. Run the project setup first.
)
popd
pause
