@echo off
rem Advanced CLI only (export-csv, seed-searches, ...). Not for status or harvest.
setlocal EnableExtensions
cd /d "%~dp0\.."

if "%~1"=="" (
  echo ============================================================
  echo   ca_bulk_import.bat needs a command ^(not for double-click^)
  echo.
  echo   Check progress:     scripts\ca_bulk_status.bat
  echo   Harvest from ICAI:  scripts\harvest_ca_from_icai.bat
  echo   Send emails:        scripts\send_ca_emails.bat
  echo.
  echo   Examples:
  echo     scripts\ca_bulk_import.bat export-csv
  echo     scripts\ca_bulk_import.bat seed-searches
  echo ============================================================
  set "EXIT_CODE=0"
  goto :done
)

if not exist "config\ca_bulk_config.json" if exist "config\ca_bulk_config.example.json" (
  copy /Y config\ca_bulk_config.example.json config\ca_bulk_config.json >nul
)
python ca_bulk_import.py %*
set "EXIT_CODE=%ERRORLEVEL%"
goto :done

:done
echo.
echo Type EXIT and press Enter to close this window.
cmd /k
exit /b %EXIT_CODE%
