@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
echo ============================================================
echo   CA bulk STATUS  (read-only - no ICAI harvest)
echo   To harvest more cities:  scripts\harvest_ca_from_icai.bat
echo ============================================================
echo.
python ca_bulk_import.py status
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
  echo   Finished OK
) else (
  echo   Finished with errors ^(exit code %EXIT_CODE%^)
)
echo   Harvest from ICAI:  scripts\harvest_ca_from_icai.bat
echo   Type EXIT and press Enter to close this window.
echo ============================================================
cmd /k
exit /b %EXIT_CODE%
