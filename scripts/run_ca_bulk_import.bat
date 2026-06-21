@echo off
setlocal EnableExtensions

cd /d "%~dp0\.."

rem --- Edit these overrides (leave blank to use config\ca_bulk_config.json defaults) ---
set "GOAL="
set "BATCH="
set "HEADED="

rem Defaults in config\ca_bulk_config.json: goal_emails_per_run=1000, profiles_per_batch=50

if not exist "config\ca_bulk_config.json" (
  if exist "config\ca_bulk_config.example.json" (
    echo Creating config\ca_bulk_config.json from example...
    copy /Y config\ca_bulk_config.example.json config\ca_bulk_config.json >nul
  ) else (
    echo ERROR: config\ca_bulk_config.example.json not found.
    pause
    exit /b 1
  )
)

if not exist "config\ca_connect_credentials.json" (
  echo ERROR: config\ca_connect_credentials.json not found.
  echo Copy config\ca_connect_credentials.example.json and add your CA Connect login.
  pause
  exit /b 1
)

echo ============================================================
echo   CA Bulk Import — resumable CA email harvest
echo   Folder: %CD%
echo   Database: data\db\ca_bulk.db  (separate from immigration.db)
echo.
echo   This run continues where the last run stopped.
echo   Config: config\ca_bulk_config.json
echo ============================================================
echo.
pause

rem First-time: seed city queue if database missing or empty
if not exist "data\db\ca_bulk.db" (
  echo [Setup] New database — seeding search queue...
  python ca_bulk_import.py seed-searches
  if errorlevel 1 goto :failed
)

rem Bootstrap from existing JSON once if bulk DB has no listings yet
python -c "from ca_bulk_db import CaBulkDB; db=CaBulkDB(); n=db.summary().get('listings_total',0); db.close(); raise SystemExit(0 if n else 1)" >nul 2>&1
if errorlevel 1 (
  if exist "data\ca_connect_results.json" (
    echo [Setup] Importing existing data\ca_connect_results.json ...
    python ca_bulk_import.py import-json
    if errorlevel 1 goto :failed
  ) else (
    echo [Setup] Seeding search queue...
    python ca_bulk_import.py seed-searches
    if errorlevel 1 goto :failed
  )
)

echo.
echo [Run] Enriching CA profiles (resume from last checkpoint)...
set "RUN_ARGS=run"
if not "%GOAL%"=="" set "RUN_ARGS=%RUN_ARGS% --goal %GOAL%"
if not "%BATCH%"=="" set "RUN_ARGS=%RUN_ARGS% --batch %BATCH%"
if /I "%HEADED%"=="1" set "RUN_ARGS=%RUN_ARGS% --headed"
if /I "%HEADED%"=="true" set "RUN_ARGS=%RUN_ARGS% --headed"

python ca_bulk_import.py %RUN_ARGS%
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo   Finished OK
echo   Logs: %CD%\logs\ca_bulk_*.log
echo   Export: python ca_bulk_import.py export-csv
echo ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo ============================================================
echo   Finished with errors (exit code %ERRORLEVEL%)
echo   Check logs: %CD%\logs\
echo   Status:  scripts\ca_bulk_status.bat
echo ============================================================
echo.
pause
exit /b 1
