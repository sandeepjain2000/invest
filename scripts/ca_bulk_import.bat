@echo off
rem Pass-through to ca_bulk_import.py — e.g. scripts\ca_bulk_import.bat status
setlocal EnableExtensions
cd /d "%~dp0\.."
if not exist "config\ca_bulk_config.json" if exist "config\ca_bulk_config.example.json" (
  copy /Y config\ca_bulk_config.example.json config\ca_bulk_config.json >nul
)
python ca_bulk_import.py %*
set "EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %EXIT_CODE%
