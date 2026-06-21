@echo off
setlocal EnableExtensions

cd /d "%~dp0\.."

rem Send only — no Google scrape (faster for clearing the email backlog).
rem Defaults live in config\sender_config.json:
rem   emails_per_run              = 32
rem   min_ensure_ca_connect_per_run = 8  (CA slots from ca_bulk.db)
rem Optional override (leave blank for full emails_per_run):

set "SEND_LIMIT="

echo ============================================================
echo   Partnership pipeline — SEND ONLY (no scrape)
echo   Folder: %CD%
echo.
echo   Sends from existing queue: ca_bulk.db + immigration.db
echo   Skips: Google scrape, CA Connect scrape, reply check
echo.
echo   Settings: config\sender_config.json
if not "%SEND_LIMIT%"=="" (
    echo   Send limit override: %SEND_LIMIT%
) else (
    echo   Send limit: emails_per_run from config\sender_config.json
)
echo ============================================================
echo.
pause

set "RUN_ARGS=send --skip-replies"
if not "%SEND_LIMIT%"=="" set "RUN_ARGS=%RUN_ARGS% --limit %SEND_LIMIT%"

python immigration_pipeline.py %RUN_ARGS%

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo   Finished OK ^(exit code 0^)
) else (
    echo   Finished with errors ^(exit code %EXIT_CODE%^)
)
echo   See RUN SUMMARY above for this run's counts.
echo   Logs: %CD%\logs\
echo   Optional: python immigration_pipeline.py audit-brevo --days 1
echo ============================================================
echo.
pause

endlocal
exit /b %EXIT_CODE%
