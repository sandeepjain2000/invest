@echo off
rem Pass-through to ca_bulk_import.py — e.g. ca_bulk_import.bat status
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "ca_bulk_config.json" if exist "ca_bulk_config.example.json" (
  copy /Y ca_bulk_config.example.json ca_bulk_config.json >nul
)
python ca_bulk_import.py %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
