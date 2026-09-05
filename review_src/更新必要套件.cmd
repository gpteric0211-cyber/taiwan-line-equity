@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo This helper does not delete files automatically.
echo.
echo To reinstall packages safely:
echo 1. Close the running server window if it is open.
echo 2. Run the setup file in this folder.
echo.
echo If you really need a clean rebuild:
echo 1. Manually delete the .venv folder.
echo 2. Run the setup file again.
echo.
pause
