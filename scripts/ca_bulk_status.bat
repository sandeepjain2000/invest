@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
python ca_bulk_import.py status
echo.
pause
exit /b %ERRORLEVEL%
